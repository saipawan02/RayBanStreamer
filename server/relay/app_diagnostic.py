#!/usr/bin/env python3
"""
Phase 2 Task 5: FFmpeg Named-Pipe Integration Diagnostic

Comprehensive instrumentation to identify why FFmpeg cannot detect codec parameters.
"""

import asyncio
import hashlib
import json
import logging
import os
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, status
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

app = FastAPI(title="Media Relay - Diagnostic")

# Active FFmpeg processes and pipes by session_id
ffmpeg_processes: dict[str, subprocess.Popen] = {}
session_pipes: dict[str, dict[str, any]] = {}

# Phase 2 Task 4: Integrity validation
session_manifests: dict[str, any] = {}
session_sequences: dict[str, int] = {}

# Phase 2 Task 5: FIFO diagnostics
fifo_write_logs: dict[str, any] = {}  # {session_id: file handle for FIFO write log}
fifo_captures: dict[str, any] = {}  # {session_id: file handle for FIFO-bound capture}
session_timelines: dict[str, dict] = {}  # {session_id: {event: timestamp}}

# Configuration
VIDEO_ONLY_MODE = True  # Task 5.4: Video-only test
ENABLE_FIFO_CAPTURE = True  # Task 5.5: Capture FIFO-bound data


def add_timeline_event(session_id: str, event: str):
    """Record timeline event with microsecond precision"""
    if session_id not in session_timelines:
        session_timelines[session_id] = {}
    session_timelines[session_id][event] = time.time()
    log.info(f"[TIMELINE] {session_id[:8]}: {event}")


def print_timeline(session_id: str):
    """Print complete timeline for session"""
    if session_id not in session_timelines:
        log.warning(f"No timeline for session {session_id}")
        return

    timeline = session_timelines[session_id]
    if not timeline:
        return

    log.info("=" * 70)
    log.info(f"TIMELINE FOR SESSION {session_id[:8]}")
    log.info("=" * 70)

    # Sort events by timestamp
    start_time = min(timeline.values())
    for event in sorted(timeline.keys(), key=lambda e: timeline[e]):
        timestamp = timeline[event]
        elapsed_ms = (timestamp - start_time) * 1000
        log.info(f"  +{elapsed_ms:8.1f}ms: {event}")

    log.info("=" * 70)


@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(ffmpeg_processes)}


@app.websocket("/media/{session_id}")
async def media_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...)
):
    """
    Receive binary video/audio frames and relay to FFmpeg -> MediaMTX
    """

    await websocket.accept()
    add_timeline_event(session_id, "websocket_accepted")
    log.info(f"Media WebSocket opened: session {session_id}")

    # Open integrity manifest
    manifest_path = f'/tmp/relay_video_manifest_{session_id[:8]}.jsonl'
    try:
        manifest_file = open(manifest_path, 'w')
        session_manifests[session_id] = manifest_file
        session_sequences[session_id] = 0
        log.info(f"Opened relay manifest: {manifest_path}")
    except Exception as e:
        log.error(f"Failed to open manifest file: {e}")
        manifest_file = None

    # Task 5.2: Open FIFO write log
    fifo_log_path = f'/tmp/fifo_write_log_{session_id[:8]}.jsonl'
    try:
        fifo_write_log = open(fifo_log_path, 'w')
        fifo_write_logs[session_id] = fifo_write_log
        log.info(f"Opened FIFO write log: {fifo_log_path}")
    except Exception as e:
        log.error(f"Failed to open FIFO write log: {e}")
        fifo_write_log = None

    # Task 5.5: Open FIFO-bound capture
    if ENABLE_FIFO_CAPTURE:
        capture_path = f'/tmp/relay_fifo_capture_{session_id[:8]}.h265'
        try:
            fifo_capture_file = open(capture_path, 'wb')
            fifo_captures[session_id] = fifo_capture_file
            log.info(f"Opened FIFO-bound capture: {capture_path}")
        except Exception as e:
            log.error(f"Failed to open FIFO capture: {e}")
            fifo_capture_file = None
    else:
        fifo_capture_file = None

    # Start FFmpeg process
    add_timeline_event(session_id, "ffmpeg_start_requested")
    ffmpeg = await start_ffmpeg(session_id)
    if not ffmpeg:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Failed to start FFmpeg")
        return

    ffmpeg_processes[session_id] = ffmpeg
    add_timeline_event(session_id, "ffmpeg_started")

    frames_received = 0
    video_frames = 0
    audio_frames = 0
    first_vps_sps_pps_sent = False
    first_keyframe_sent = False

    try:
        while True:
            # Receive binary frame
            data = await websocket.receive_bytes()
            add_timeline_event(session_id, f"frame_{frames_received}_received")

            if len(data) < 16:
                log.warning(f"Frame too short: {len(data)} bytes")
                continue

            # Parse header
            stream_type, flags, reserved, pts_us, payload_len = struct.unpack(
                '<BBHqI',
                data[:16]
            )

            payload = data[16:]

            if len(payload) != payload_len:
                log.warning(f"Payload length mismatch: expected {payload_len}, got {len(payload)}")
                continue

            frames_received += 1

            if stream_type == 1:  # Video HEVC
                video_frames += 1

                # Calculate checksum
                session_sequences[session_id] = session_sequences.get(session_id, 0) + 1
                sequence = session_sequences[session_id]
                checksum = hashlib.sha256(payload).hexdigest()

                # Write manifest entry
                manifest_file = session_manifests.get(session_id)
                if manifest_file:
                    try:
                        is_keyframe = bool(flags & 0x01)
                        entry = {
                            "session": session_id,
                            "sequence": sequence,
                            "size": len(payload),
                            "sha256": checksum,
                            "timestamp": pts_us,
                            "isKeyframe": is_keyframe
                        }
                        manifest_file.write(json.dumps(entry) + '\n')
                        manifest_file.flush()

                        # Log first 5 frames
                        if sequence <= 5:
                            log.info(f"Frame seq={sequence} size={len(payload)} keyframe={is_keyframe} sha256={checksum[:16]}...")
                    except Exception as e:
                        log.error(f"Error writing manifest entry: {e}")

                # Task 5.5: Write to FIFO-bound capture
                if fifo_capture_file:
                    try:
                        fifo_capture_file.write(payload)
                        fifo_capture_file.flush()
                    except Exception as e:
                        log.error(f"Error writing FIFO capture: {e}")

                # Task 5.2: Detailed FIFO write logging
                write_start = time.time()
                bytes_written = 0
                write_success = False
                write_error = None

                # Check for VPS/SPS/PPS (NAL types 32-34)
                has_codec_config = False
                if len(payload) >= 5:
                    # Check first NAL unit type
                    if (payload[0] == 0 and payload[1] == 0 and
                        (payload[2] == 0 or payload[2] == 1)):
                        start_code_size = 4 if payload[2] == 0 else 3
                        if len(payload) > start_code_size:
                            nal_header = payload[start_code_size]
                            nal_type = (nal_header >> 1) & 0x3F
                            if nal_type in [32, 33, 34]:  # VPS, SPS, PPS
                                has_codec_config = True
                                if not first_vps_sps_pps_sent:
                                    first_vps_sps_pps_sent = True
                                    add_timeline_event(session_id, f"first_vps_sps_pps_frame_{sequence}")
                                    log.info(f"🔑 First VPS/SPS/PPS frame (seq={sequence}, type={nal_type})")

                # Check for keyframe
                is_keyframe = bool(flags & 0x01)
                if is_keyframe and not first_keyframe_sent:
                    first_keyframe_sent = True
                    add_timeline_event(session_id, f"first_keyframe_{sequence}")
                    log.info(f"🎬 First keyframe (seq={sequence})")

                # Write to video pipe
                try:
                    pipes = session_pipes.get(session_id)
                    if pipes and pipes.get('video_fd'):
                        bytes_written = os.write(pipes['video_fd'], payload)
                        write_success = True

                        if sequence == 1:
                            add_timeline_event(session_id, "first_payload_written_to_fifo")
                            log.info(f"✅ First payload written to FIFO (seq=1, size={bytes_written})")
                    else:
                        write_error = "Video pipe not ready"
                        log.warning(f"⚠️  Frame seq={sequence} DISCARDED: video pipe not ready")
                except (BrokenPipeError, OSError) as e:
                    write_error = str(e)
                    log.error(f"Video pipe error: {e}")
                    break

                write_end = time.time()

                # Log FIFO write details
                fifo_write_log = fifo_write_logs.get(session_id)
                if fifo_write_log:
                    try:
                        log_entry = {
                            "sequence": sequence,
                            "size": len(payload),
                            "sha256": checksum,
                            "write_start": write_start,
                            "write_end": write_end,
                            "write_duration_us": (write_end - write_start) * 1_000_000,
                            "bytes_written": bytes_written,
                            "success": write_success,
                            "error": write_error,
                            "has_codec_config": has_codec_config,
                            "is_keyframe": is_keyframe
                        }
                        fifo_write_log.write(json.dumps(log_entry) + '\n')
                        fifo_write_log.flush()
                    except Exception as e:
                        log.error(f"Error writing FIFO log entry: {e}")

            elif stream_type == 2 and not VIDEO_ONLY_MODE:  # Audio PCM
                audio_frames += 1
                # Write to audio pipe (only if not in video-only mode)
                try:
                    pipes = session_pipes.get(session_id)
                    if pipes and pipes.get('audio_fd'):
                        os.write(pipes['audio_fd'], payload)
                except (BrokenPipeError, OSError) as e:
                    log.error(f"Audio pipe error: {e}")

            if frames_received % 100 == 0:
                log.info(f"Session {session_id[:8]}: {video_frames} video, {audio_frames} audio frames")

    except WebSocketDisconnect:
        log.info(f"WebSocket closed: session {session_id}")
    except Exception as e:
        log.error(f"Error in WebSocket handler: {e}", exc_info=True)
    finally:
        add_timeline_event(session_id, "websocket_closed")

        # Close FIFO write log
        fifo_write_log = fifo_write_logs.pop(session_id, None)
        if fifo_write_log:
            try:
                fifo_write_log.close()
                log.info(f"FIFO write log closed")
            except Exception as e:
                log.error(f"Error closing FIFO write log: {e}")

        # Close FIFO-bound capture
        fifo_capture_file = fifo_captures.pop(session_id, None)
        if fifo_capture_file:
            try:
                fifo_capture_file.close()
                log.info(f"FIFO-bound capture closed")
            except Exception as e:
                log.error(f"Error closing FIFO capture: {e}")

        # Close manifest
        manifest_file = session_manifests.pop(session_id, None)
        if manifest_file:
            try:
                manifest_file.close()
                total_seq = session_sequences.get(session_id, 0)
                log.info(f"Relay manifest closed: {total_seq} video frames logged")
            except Exception as e:
                log.error(f"Error closing manifest: {e}")
        session_sequences.pop(session_id, None)

        # Stop FFmpeg
        await stop_ffmpeg(session_id)
        add_timeline_event(session_id, "cleanup_complete")

        # Print timeline
        print_timeline(session_id)

        log.info(f"Session {session_id} ended. Received {frames_received} frames total")


async def start_ffmpeg(session_id: str) -> Optional[subprocess.Popen]:
    """
    Start FFmpeg with named pipes for video (and optionally audio)

    Task 5.4: Video-only mode with debug output
    """

    # Create named pipes
    video_pipe = f'/tmp/video_{session_id}.pipe'
    audio_pipe = f'/tmp/audio_{session_id}.pipe' if not VIDEO_ONLY_MODE else None

    try:
        # Remove old pipes if they exist
        for pipe in [video_pipe, audio_pipe]:
            if pipe and os.path.exists(pipe):
                os.remove(pipe)

        # Create new named pipes
        add_timeline_event(session_id, "video_fifo_create_start")
        os.mkfifo(video_pipe)
        add_timeline_event(session_id, "video_fifo_created")
        log.info(f"✅ Created video pipe: {video_pipe}")

        if not VIDEO_ONLY_MODE:
            os.mkfifo(audio_pipe)
            log.info(f"Created audio pipe: {audio_pipe}")

    except Exception as e:
        log.error(f"Failed to create named pipes: {e}")
        return None

    # Task 5.4: Video-only FFmpeg command with debug output
    if VIDEO_ONLY_MODE:
        cmd = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'debug',
            # Probe settings BEFORE -i
            '-probesize', '10000000',
            '-analyzeduration', '5000000',
            # Video input
            '-f', 'hevc',
            '-i', video_pipe,
            # Map video only
            '-map', '0:v:0',
            # Copy video directly
            '-c:v', 'copy',
            # Output to null for diagnosis
            '-f', 'null',
            '-'
        ]
    else:
        # Original dual-input command
        cmd = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel', 'debug',
            # Video input
            '-f', 'hevc',
            '-i', video_pipe,
            # Audio input
            '-f', 's16le',
            '-ar', '48000',
            '-ac', '1',
            '-i', audio_pipe,
            # Video: copy
            '-c:v', 'copy',
            # Audio: encode to AAC
            '-c:a', 'aac',
            '-b:a', '128k',
            # Low latency
            '-flush_packets', '1',
            # Output to RTMP
            '-f', 'flv',
            'rtmp://127.0.0.1:1935/glasses'
        ]

    log.info(f"FFmpeg command: {' '.join(cmd)}")
    add_timeline_event(session_id, "ffmpeg_process_start")

    try:
        # Start FFmpeg with detailed logging
        stderr_log = f'/tmp/ffmpeg_{session_id[:8]}_debug.log'
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=open(stderr_log, 'w'),
            bufsize=0
        )
        add_timeline_event(session_id, f"ffmpeg_pid_{proc.pid}_created")
        log.info(f"✅ FFmpeg process started (PID {proc.pid})")
        log.info(f"📄 FFmpeg stderr: {stderr_log}")

        # Open pipes for writing
        def open_pipes():
            import time
            import fcntl
            max_wait = 10
            start_time = time.time()

            try:
                log.info(f"[FIFO] Waiting for FFmpeg to open video pipe for reading...")
                add_timeline_event(session_id, "video_fifo_writer_open_start")

                video_fd = None
                audio_fd = None

                while (time.time() - start_time) < max_wait:
                    try:
                        if video_fd is None:
                            video_fd = os.open(video_pipe, os.O_WRONLY | os.O_NONBLOCK)
                            add_timeline_event(session_id, "video_fifo_writer_opened")
                            log.info(f"✅ Video pipe writer opened (fd={video_fd})")
                            # Clear O_NONBLOCK
                            flags = fcntl.fcntl(video_fd, fcntl.F_GETFL)
                            fcntl.fcntl(video_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

                        if not VIDEO_ONLY_MODE and audio_fd is None:
                            audio_fd = os.open(audio_pipe, os.O_WRONLY | os.O_NONBLOCK)
                            log.info(f"Audio pipe writer opened (fd={audio_fd})")
                            flags = fcntl.fcntl(audio_fd, fcntl.F_GETFL)
                            fcntl.fcntl(audio_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

                        if video_fd is not None and (VIDEO_ONLY_MODE or audio_fd is not None):
                            break
                    except OSError as e:
                        if e.errno != 6:  # ENXIO - no reader yet
                            raise
                        time.sleep(0.1)

                if video_fd is None:
                    raise Exception("Timeout waiting for FFmpeg to open video pipe")

                session_pipes[session_id] = {
                    'video_fd': video_fd,
                    'audio_fd': audio_fd,
                    'video_path': video_pipe,
                    'audio_path': audio_pipe,
                    'stderr_log': stderr_log
                }
                add_timeline_event(session_id, "pipes_ready")
                log.info(f"✅ All pipes opened successfully for session {session_id[:8]}")
            except Exception as e:
                log.error(f"❌ Failed to open pipes: {e}", exc_info=True)
                add_timeline_event(session_id, f"pipe_open_error: {e}")
                if video_fd is not None:
                    os.close(video_fd)
                if audio_fd is not None:
                    os.close(audio_fd)

        # Open pipes in background thread
        threading.Thread(target=open_pipes, daemon=True).start()

        # Wait for pipes to open
        await asyncio.sleep(2.0)

        if proc.poll() is not None:
            # Process already exited
            add_timeline_event(session_id, "ffmpeg_early_exit")
            with open(stderr_log, 'r') as f:
                stderr = f.read()
            log.error(f"❌ FFmpeg failed to start:\n{stderr}")
            for pipe in [video_pipe, audio_pipe]:
                if pipe and os.path.exists(pipe):
                    os.remove(pipe)
            return None

        log.info(f"✅ FFmpeg running (PID {proc.pid})")
        return proc

    except Exception as e:
        log.error(f"Failed to start FFmpeg: {e}")
        add_timeline_event(session_id, f"ffmpeg_start_error: {e}")
        for pipe in [video_pipe, audio_pipe]:
            if pipe and os.path.exists(pipe):
                os.remove(pipe)
        return None


async def stop_ffmpeg(session_id: str):
    """Stop FFmpeg process and clean up pipes"""
    add_timeline_event(session_id, "ffmpeg_stop_requested")

    proc = ffmpeg_processes.pop(session_id, None)
    if proc:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
                add_timeline_event(session_id, "ffmpeg_terminated")
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                add_timeline_event(session_id, "ffmpeg_killed")
            log.info(f"FFmpeg stopped for session {session_id}")
        except Exception as e:
            log.error(f"Error stopping FFmpeg: {e}")

    # Clean up pipes
    pipes = session_pipes.pop(session_id, None)
    if pipes:
        try:
            if pipes.get('video_fd'):
                os.close(pipes['video_fd'])
                log.debug(f"Closed video pipe FD")
            if pipes.get('audio_fd'):
                os.close(pipes['audio_fd'])
                log.debug(f"Closed audio pipe FD")

            if pipes.get('video_path') and os.path.exists(pipes['video_path']):
                os.remove(pipes['video_path'])
            if pipes.get('audio_path') and os.path.exists(pipes['audio_path']):
                os.remove(pipes['audio_path'])

            log.info(f"Cleaned up pipes for session {session_id}")
        except Exception as e:
            log.error(f"Error cleaning up pipes: {e}")


if __name__ == "__main__":
    import uvicorn
    log.info("=" * 70)
    log.info("PHASE 2 TASK 5: FFmpeg Named-Pipe Integration Diagnostic")
    log.info("=" * 70)
    log.info(f"VIDEO_ONLY_MODE: {VIDEO_ONLY_MODE}")
    log.info(f"ENABLE_FIFO_CAPTURE: {ENABLE_FIFO_CAPTURE}")
    log.info("=" * 70)
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
