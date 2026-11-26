#!/usr/bin/env python3
"""
Phase 2 Task 8: Reliable Audio + Video Startup (Delayed FFmpeg Start)

Delays FFmpeg startup until first video frame arrives to ensure codec config
(VPS/SPS/PPS) is available for immediate probing.
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
from collections import deque
from pathlib import Path
from typing import Optional, Deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, status
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger(__name__)

app = FastAPI(title="Media Relay - Task 8")

# Configuration
ENABLE_AUDIO_PRIMING = True  # Generate silence until real audio arrives
AUDIO_PRIME_DURATION_SEC = 2.0  # Seconds of silence to prime
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_SIZE = 2  # 16-bit = 2 bytes

# Active FFmpeg processes and state
ffmpeg_processes: dict[str, subprocess.Popen] = {}
session_pipes: dict[str, dict] = {}
session_state: dict[str, dict] = {}
ffmpeg_started: dict[str, bool] = {}  # Track if FFmpeg has been started

# Task 8: Independent queues for audio and video
video_queues: dict[str, Deque] = {}
audio_queues: dict[str, Deque] = {}

# Manifests
session_manifests: dict[str, any] = {}
session_sequences: dict[str, int] = {}


def add_event(session_id: str, event: str):
    """Record timestamped event"""
    if session_id not in session_state:
        session_state[session_id] = {
            'events': [],
            'start_time': time.time(),
            'video_frames': 0,
            'audio_frames': 0,
            'silence_samples_sent': 0,
            'real_audio_started': False
        }

    elapsed_ms = (time.time() - session_state[session_id]['start_time']) * 1000
    session_state[session_id]['events'].append((elapsed_ms, event))
    log.info(f"[{session_id[:8]}] +{elapsed_ms:8.1f}ms: {event}")


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
    Receive binary video/audio frames and relay to FFmpeg
    """

    await websocket.accept()
    add_event(session_id, "websocket_accepted")

    log.info("=" * 70)
    log.info(f"PHASE 2 TASK 8: Reliable Audio + Video Startup (Delayed FFmpeg)")
    log.info(f"Session: {session_id[:8]}")
    log.info(f"Strategy: Wait for first video frame before starting FFmpeg")
    log.info("=" * 70)

    # Initialize queues
    video_queues[session_id] = deque(maxlen=100)  # ~4 seconds at 24fps
    audio_queues[session_id] = deque(maxlen=500)  # ~10 seconds at 48kHz chunks

    # Initialize FFmpeg started flag
    ffmpeg_started[session_id] = False

    # Open integrity manifest
    manifest_path = f'/tmp/relay_video_manifest_{session_id[:8]}.jsonl'
    try:
        manifest_file = open(manifest_path, 'w')
        session_manifests[session_id] = manifest_file
        session_sequences[session_id] = 0
        add_event(session_id, "manifest_opened")
    except Exception as e:
        log.error(f"Failed to open manifest file: {e}")
        manifest_file = None

    # NOTE: FFmpeg will be started when first video frame arrives
    log.info(f"[{session_id[:8]}] Buffering frames, waiting for first video frame to start FFmpeg...")
    add_event(session_id, "waiting_for_first_frame")

    # Start FIFO writer threads (they will wait for FFmpeg to start)
    video_writer_thread = threading.Thread(
        target=video_writer_loop,
        args=(session_id,),
        daemon=True
    )
    audio_writer_thread = threading.Thread(
        target=audio_writer_loop,
        args=(session_id,),
        daemon=True
    )

    video_writer_thread.start()
    audio_writer_thread.start()
    add_event(session_id, "fifo_writer_threads_started")

    frames_received = 0

    try:
        while True:
            data = await websocket.receive_bytes()

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
                state = session_state[session_id]
                state['video_frames'] += 1

                if state['video_frames'] == 1:
                    add_event(session_id, "first_video_frame_received")
                    log.info(f"✅ First video frame received (size={len(payload)})")

                    # Start FFmpeg now that we have video data
                    if not ffmpeg_started.get(session_id):
                        log.info(f"[{session_id[:8]}] Starting FFmpeg with first video frame available...")
                        add_event(session_id, "ffmpeg_start_requested")
                        ffmpeg = await start_ffmpeg(session_id)
                        if ffmpeg:
                            ffmpeg_processes[session_id] = ffmpeg
                            ffmpeg_started[session_id] = True
                            add_event(session_id, "ffmpeg_started")
                        else:
                            log.error(f"Failed to start FFmpeg for session {session_id}")
                            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Failed to start FFmpeg")
                            return

                # Calculate checksum and sequence
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

                        if sequence <= 5:
                            log.info(f"Video frame seq={sequence} size={len(payload)} keyframe={is_keyframe}")
                    except Exception as e:
                        log.error(f"Error writing manifest entry: {e}")

                # Add to video queue (non-blocking)
                try:
                    video_queues[session_id].append((payload, pts_us))
                except Exception as e:
                    log.error(f"Error queueing video frame: {e}")

            elif stream_type == 2:  # Audio PCM
                state = session_state[session_id]
                state['audio_frames'] += 1

                if state['audio_frames'] == 1:
                    add_event(session_id, "first_audio_frame_received")
                    state['real_audio_started'] = True
                    log.info(f"✅ Real audio started (after {state['silence_samples_sent']} silence samples)")

                # Add to audio queue (non-blocking)
                try:
                    audio_queues[session_id].append((payload, pts_us))
                except Exception as e:
                    log.error(f"Error queueing audio frame: {e}")

            if frames_received % 100 == 0:
                state = session_state[session_id]
                log.info(f"Session {session_id[:8]}: {state['video_frames']} video, {state['audio_frames']} audio frames")

    except WebSocketDisconnect:
        log.info(f"WebSocket closed: session {session_id}")
    except Exception as e:
        log.error(f"Error in WebSocket handler: {e}", exc_info=True)
    finally:
        add_event(session_id, "websocket_closed")

        # Signal queues to stop
        if session_id in video_queues:
            video_queues[session_id].append(None)  # Sentinel
        if session_id in audio_queues:
            audio_queues[session_id].append(None)  # Sentinel

        # Wait for writer threads to finish
        video_writer_thread.join(timeout=5)
        audio_writer_thread.join(timeout=5)

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
        add_event(session_id, "cleanup_complete")

        # Print summary
        print_session_summary(session_id)

        # Cleanup
        video_queues.pop(session_id, None)
        audio_queues.pop(session_id, None)
        ffmpeg_started.pop(session_id, None)

        log.info(f"Session {session_id[:8]} ended. Received {frames_received} frames total")


def video_writer_loop(session_id: str):
    """Background thread to write video frames to FIFO"""
    log.info(f"[{session_id[:8]}] Video writer thread started (waiting for FFmpeg)")
    add_event(session_id, "video_writer_thread_started")

    frames_written = 0

    # Wait for FFmpeg to start
    while not ffmpeg_started.get(session_id):
        time.sleep(0.1)

    log.info(f"[{session_id[:8]}] Video writer: FFmpeg started, waiting for pipe...")

    while True:
        try:
            # Wait for pipe to be ready
            pipes = session_pipes.get(session_id)
            if not pipes or not pipes.get('video_fd'):
                time.sleep(0.1)
                continue

            # Get frame from queue
            queue = video_queues.get(session_id)
            if queue is None:
                break

            frame = queue.popleft()

            # Check for sentinel
            if frame is None:
                log.info(f"[{session_id[:8]}] Video writer received stop signal")
                break

            payload, pts_us = frame

            # Write to FIFO
            try:
                os.write(pipes['video_fd'], payload)
                frames_written += 1

                if frames_written == 1:
                    add_event(session_id, "first_video_payload_written_to_fifo")
                    log.info(f"✅ First video frame written to FIFO (size={len(payload)})")

            except (BrokenPipeError, OSError) as e:
                log.error(f"Video pipe error: {e}")
                break

        except IndexError:
            # Queue empty
            time.sleep(0.001)
        except Exception as e:
            log.error(f"Error in video writer loop: {e}", exc_info=True)
            break

    log.info(f"[{session_id[:8]}] Video writer thread stopped ({frames_written} frames written)")
    add_event(session_id, f"video_writer_stopped ({frames_written} frames)")


def audio_writer_loop(session_id: str):
    """Background thread to write audio frames to FIFO with optional priming"""
    log.info(f"[{session_id[:8]}] Audio writer thread started (waiting for FFmpeg)")
    add_event(session_id, "audio_writer_thread_started")

    frames_written = 0
    state = session_state.get(session_id, {})

    # Wait for FFmpeg to start
    while not ffmpeg_started.get(session_id):
        time.sleep(0.1)

    log.info(f"[{session_id[:8]}] Audio writer: FFmpeg started, proceeding...")

    # Phase 1: Audio priming (silence generation)
    if ENABLE_AUDIO_PRIMING:
        log.info(f"[{session_id[:8]}] Starting audio priming phase ({AUDIO_PRIME_DURATION_SEC}s of silence)")
        add_event(session_id, "audio_priming_started")

        # Wait for audio pipe to be ready (may take time while FFmpeg probes video)
        log.info(f"[{session_id[:8]}] Audio writer: waiting for audio pipe...")
        while True:
            pipes = session_pipes.get(session_id)
            if pipes and pipes.get('audio_fd'):
                log.info(f"[{session_id[:8]}] Audio writer: audio pipe ready")
                break
            time.sleep(0.1)

        # Calculate silence samples needed
        samples_per_chunk = 1024  # ~21ms at 48kHz
        total_samples_needed = int(AUDIO_PRIME_DURATION_SEC * AUDIO_SAMPLE_RATE)
        samples_sent = 0

        # Generate and write silence
        silence_chunk = b'\x00' * (samples_per_chunk * AUDIO_SAMPLE_SIZE)

        while samples_sent < total_samples_needed:
            # Check if real audio has started
            if state.get('real_audio_started'):
                log.info(f"[{session_id[:8]}] Real audio arrived, stopping priming early")
                break

            try:
                os.write(pipes['audio_fd'], silence_chunk)
                samples_sent += samples_per_chunk
                state['silence_samples_sent'] = samples_sent
                frames_written += 1

                if frames_written == 1:
                    add_event(session_id, "first_audio_silence_written_to_fifo")
                    log.info(f"✅ Audio priming started (writing silence)")

            except (BrokenPipeError, OSError) as e:
                log.error(f"Audio pipe error during priming: {e}")
                return

            time.sleep(0.02)  # ~20ms between chunks

        add_event(session_id, f"audio_priming_complete ({samples_sent} samples)")
        log.info(f"[{session_id[:8]}] Audio priming complete ({samples_sent}/{total_samples_needed} samples)")

    # Phase 2: Real audio forwarding
    log.info(f"[{session_id[:8]}] Switching to real audio forwarding")
    add_event(session_id, "real_audio_forwarding_started")

    while True:
        try:
            # Wait for pipe to be ready
            pipes = session_pipes.get(session_id)
            if not pipes or not pipes.get('audio_fd'):
                time.sleep(0.1)
                continue

            # Get frame from queue
            queue = audio_queues.get(session_id)
            if queue is None:
                break

            frame = queue.popleft()

            # Check for sentinel
            if frame is None:
                log.info(f"[{session_id[:8]}] Audio writer received stop signal")
                break

            payload, pts_us = frame

            # Write to FIFO
            try:
                os.write(pipes['audio_fd'], payload)
                frames_written += 1

                if not state.get('first_real_audio_written'):
                    state['first_real_audio_written'] = True
                    add_event(session_id, "first_real_audio_written_to_fifo")
                    log.info(f"✅ First real audio frame written to FIFO (size={len(payload)})")

            except (BrokenPipeError, OSError) as e:
                log.error(f"Audio pipe error: {e}")
                break

        except IndexError:
            # Queue empty
            time.sleep(0.001)
        except Exception as e:
            log.error(f"Error in audio writer loop: {e}", exc_info=True)
            break

    log.info(f"[{session_id[:8]}] Audio writer thread stopped ({frames_written} total frames, {state.get('silence_samples_sent', 0)} silence samples)")
    add_event(session_id, f"audio_writer_stopped ({frames_written} frames)")


async def start_ffmpeg(session_id: str) -> Optional[subprocess.Popen]:
    """
    Start FFmpeg with enhanced probe settings for dual-input processing
    """

    # Create named pipes
    video_pipe = f'/tmp/video_{session_id}.pipe'
    audio_pipe = f'/tmp/audio_{session_id}.pipe'

    try:
        # Remove old pipes
        for pipe in [video_pipe, audio_pipe]:
            if os.path.exists(pipe):
                os.remove(pipe)

        # Create new pipes
        os.mkfifo(video_pipe)
        os.mkfifo(audio_pipe)
        add_event(session_id, "fifos_created")
        log.info(f"✅ Created FIFOs: {video_pipe}, {audio_pipe}")

    except Exception as e:
        log.error(f"Failed to create named pipes: {e}")
        return None

    # Task 8: Enhanced probe configuration for dual-input
    cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'info',
        # Enhanced probe settings BEFORE inputs
        '-probesize', '10000000',
        '-analyzeduration', '5000000',
        # Video input
        '-f', 'hevc',
        '-i', video_pipe,
        # Audio input
        '-f', 's16le',
        '-ar', '48000',
        '-ac', '1',
        '-i', audio_pipe,
        # Explicit stream mapping
        '-map', '0:v:0',
        '-map', '1:a:0',
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

    try:
        # Start FFmpeg
        stderr_log = f'/tmp/ffmpeg_{session_id[:8]}_task8.log'
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=open(stderr_log, 'w'),
            bufsize=0
        )
        add_event(session_id, f"ffmpeg_process_created (PID {proc.pid})")
        log.info(f"✅ FFmpeg process started (PID {proc.pid})")
        log.info(f"📄 FFmpeg stderr: {stderr_log}")

        # Open pipes for writing (in background)
        def open_pipes():
            import fcntl
            max_wait = 15
            start_time = time.time()

            video_fd = None
            audio_fd = None

            try:
                log.info(f"[FIFO] Waiting for FFmpeg to open video pipe...")
                add_event(session_id, "fifo_open_wait_start")

                # Phase 1: Open video pipe and register it immediately
                while (time.time() - start_time) < max_wait:
                    try:
                        video_fd = os.open(video_pipe, os.O_WRONLY | os.O_NONBLOCK)
                        add_event(session_id, f"video_fifo_writer_opened (fd={video_fd})")
                        log.info(f"✅ Video pipe writer opened (fd={video_fd})")
                        flags = fcntl.fcntl(video_fd, fcntl.F_GETFL)
                        fcntl.fcntl(video_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                        break
                    except OSError as e:
                        if e.errno != 6:  # ENXIO
                            raise
                        time.sleep(0.1)

                if video_fd is None:
                    raise Exception("Timeout waiting for FFmpeg to open video pipe")

                # Register video pipe immediately so video writer can start
                session_pipes[session_id] = {
                    'video_fd': video_fd,
                    'audio_fd': None,  # Will be set later
                    'video_path': video_pipe,
                    'audio_path': audio_pipe,
                    'stderr_log': stderr_log
                }
                add_event(session_id, "video_fifo_ready")
                log.info(f"✅ Video pipe ready, video writer can start")

                # Phase 2: Wait for audio pipe (FFmpeg will open after probing video)
                log.info(f"[FIFO] Waiting for FFmpeg to open audio pipe...")
                audio_start = time.time()
                while (time.time() - audio_start) < max_wait:
                    try:
                        audio_fd = os.open(audio_pipe, os.O_WRONLY | os.O_NONBLOCK)
                        add_event(session_id, f"audio_fifo_writer_opened (fd={audio_fd})")
                        log.info(f"✅ Audio pipe writer opened (fd={audio_fd})")
                        flags = fcntl.fcntl(audio_fd, fcntl.F_GETFL)
                        fcntl.fcntl(audio_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                        break
                    except OSError as e:
                        if e.errno != 6:  # ENXIO
                            raise
                        time.sleep(0.1)

                if audio_fd is None:
                    raise Exception("Timeout waiting for FFmpeg to open audio pipe")

                # Update with audio fd
                session_pipes[session_id]['audio_fd'] = audio_fd
                add_event(session_id, "both_fifos_ready")
                log.info(f"✅ Both pipes opened successfully")

            except Exception as e:
                log.error(f"❌ Failed to open pipes: {e}", exc_info=True)
                add_event(session_id, f"fifo_open_error: {str(e)}")
                if video_fd is not None:
                    os.close(video_fd)
                if audio_fd is not None:
                    os.close(audio_fd)
                # Clear session_pipes on error
                session_pipes.pop(session_id, None)

        # Open pipes in background thread
        threading.Thread(target=open_pipes, daemon=True).start()

        # Wait for pipes to open
        await asyncio.sleep(2.0)

        if proc.poll() is not None:
            # Process already exited
            add_event(session_id, "ffmpeg_early_exit")
            with open(stderr_log, 'r') as f:
                stderr = f.read()
            log.error(f"❌ FFmpeg failed to start:\n{stderr}")
            for pipe in [video_pipe, audio_pipe]:
                if os.path.exists(pipe):
                    os.remove(pipe)
            return None

        log.info(f"✅ FFmpeg running (PID {proc.pid})")
        return proc

    except Exception as e:
        log.error(f"Failed to start FFmpeg: {e}")
        add_event(session_id, f"ffmpeg_start_error: {str(e)}")
        for pipe in [video_pipe, audio_pipe]:
            if os.path.exists(pipe):
                os.remove(pipe)
        return None


async def stop_ffmpeg(session_id: str):
    """Stop FFmpeg process and clean up pipes"""
    add_event(session_id, "ffmpeg_stop_requested")

    proc = ffmpeg_processes.pop(session_id, None)
    if proc:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
                add_event(session_id, "ffmpeg_terminated")
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                add_event(session_id, "ffmpeg_killed")
            log.info(f"FFmpeg stopped for session {session_id}")
        except Exception as e:
            log.error(f"Error stopping FFmpeg: {e}")

    # Clean up pipes
    pipes = session_pipes.pop(session_id, None)
    if pipes:
        try:
            if pipes.get('video_fd'):
                os.close(pipes['video_fd'])
            if pipes.get('audio_fd'):
                os.close(pipes['audio_fd'])

            if pipes.get('video_path') and os.path.exists(pipes['video_path']):
                os.remove(pipes['video_path'])
            if pipes.get('audio_path') and os.path.exists(pipes['audio_path']):
                os.remove(pipes['audio_path'])

            log.info(f"Cleaned up pipes for session {session_id}")
        except Exception as e:
            log.error(f"Error cleaning up pipes: {e}")


def print_session_summary(session_id: str):
    """Print comprehensive session summary"""
    state = session_state.get(session_id)
    if not state:
        return

    log.info("=" * 70)
    log.info(f"SESSION SUMMARY: {session_id[:8]}")
    log.info("=" * 70)
    log.info(f"Video frames: {state['video_frames']}")
    log.info(f"Audio frames: {state['audio_frames']}")
    log.info(f"Silence samples generated: {state['silence_samples_sent']}")
    log.info(f"Real audio started: {state['real_audio_started']}")
    log.info("")
    log.info("Timeline:")
    for elapsed_ms, event in state['events']:
        log.info(f"  +{elapsed_ms:8.1f}ms: {event}")
    log.info("=" * 70)


if __name__ == "__main__":
    import uvicorn
    log.info("=" * 70)
    log.info("PHASE 2 TASK 8: Reliable Audio + Video Startup (Delayed FFmpeg)")
    log.info(f"Strategy: Start FFmpeg only after first video frame arrives")
    log.info(f"Audio priming: {'ENABLED' if ENABLE_AUDIO_PRIMING else 'DISABLED'}")
    log.info(f"Prime duration: {AUDIO_PRIME_DURATION_SEC}s")
    log.info("=" * 70)
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
