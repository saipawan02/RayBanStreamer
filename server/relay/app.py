#!/usr/bin/env python3
"""
Media relay server for Plan B video upload

Receives binary video/audio frames over WebSocket and relays to FFmpeg,
which remuxes and publishes to MediaMTX.

TECH-01 section 7: Plan B uplink
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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, status
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
log = logging.getLogger(__name__)

app = FastAPI(title="Media Relay")

# Active FFmpeg processes and pipes by session_id
ffmpeg_processes: dict[str, subprocess.Popen] = {}
session_pipes: dict[str, dict[str, any]] = {}  # {session_id: {'video': fd, 'audio': fd, 'video_path': path, 'audio_path': path}}

# Phase 2 Task 4: Integrity validation
session_manifests: dict[str, any] = {}  # {session_id: file handle}
session_sequences: dict[str, int] = {}  # {session_id: sequence_number}


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

    Binary frame format (16-byte header + payload):
    - byte 0: stream type (1=video HEVC, 2=audio PCM 16-bit LE)
    - byte 1: flags (bit 0 = keyframe)
    - bytes 2-3: reserved
    - bytes 4-11: presentation_time_us (little-endian int64)
    - bytes 12-15: payload length (little-endian int32)
    - bytes 16+: payload

    Audio format: 16-bit PCM, little-endian, 48kHz, mono
    (DAT SDK delivers PCM directly, no AAC encoding)
    """

    # TODO: Validate token against environment variable

    await websocket.accept()
    log.info(f"Media WebSocket opened: session {session_id}")

    # Phase 2 Task 4: Open manifest file for integrity validation
    manifest_path = f'/tmp/relay_video_manifest_{session_id[:8]}.jsonl'
    try:
        manifest_file = open(manifest_path, 'w')
        session_manifests[session_id] = manifest_file
        session_sequences[session_id] = 0
        log.info(f"Opened relay manifest: {manifest_path}")
    except Exception as e:
        log.error(f"Failed to open manifest file: {e}")
        manifest_file = None

    # Start FFmpeg process for this session
    ffmpeg = await start_ffmpeg(session_id)
    if not ffmpeg:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Failed to start FFmpeg")
        return

    ffmpeg_processes[session_id] = ffmpeg

    frames_received = 0
    video_frames = 0
    audio_frames = 0

    try:
        while True:
            # Receive binary frame
            data = await websocket.receive_bytes()

            if len(data) < 16:
                log.warning(f"Frame too short: {len(data)} bytes")
                continue

            # Parse header (16 bytes total)
            # Format: stream_type(1), flags(1), reserved(2), pts_us(8), payload_len(4)
            stream_type, flags, reserved, pts_us, payload_len = struct.unpack(
                '<BBHqI',  # Little-endian: byte, byte, short, int64, uint32
                data[:16]
            )

            payload = data[16:]

            if len(payload) != payload_len:
                log.warning(f"Payload length mismatch: expected {payload_len}, got {len(payload)}")
                continue

            frames_received += 1

            if stream_type == 1:  # Video HEVC
                video_frames += 1

                # Phase 2 Task 4: Calculate checksum and write manifest entry
                session_sequences[session_id] = session_sequences.get(session_id, 0) + 1
                sequence = session_sequences[session_id]
                checksum = hashlib.sha256(payload).hexdigest()

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

                        # Log first 5 and every 50th frame
                        if sequence <= 5 or sequence % 50 == 0:
                            log.debug(f"Received frame seq={sequence} size={len(payload)} sha256={checksum[:16]}...")
                    except Exception as e:
                        log.error(f"Error writing manifest entry: {e}")

                # Write to video pipe
                try:
                    pipes = session_pipes.get(session_id)
                    if pipes and pipes['video_fd']:
                        os.write(pipes['video_fd'], payload)
                except (BrokenPipeError, OSError) as e:
                    log.error(f"Video pipe error: {e}")
                    break

            elif stream_type == 2:  # Audio PCM (16-bit, 48kHz, mono)
                audio_frames += 1
                # Write to audio pipe
                try:
                    pipes = session_pipes.get(session_id)
                    if pipes and pipes['audio_fd']:
                        os.write(pipes['audio_fd'], payload)
                except (BrokenPipeError, OSError) as e:
                    log.error(f"Audio pipe error: {e}")
                    # Don't break on audio errors, continue with video

            if frames_received % 100 == 0:
                log.info(f"Session {session_id}: {video_frames} video, {audio_frames} audio frames")

    except WebSocketDisconnect:
        log.info(f"WebSocket closed: session {session_id}")
    except Exception as e:
        log.error(f"Error in WebSocket handler: {e}", exc_info=True)
    finally:
        # Phase 2 Task 4: Close manifest file
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
        log.info(f"Session {session_id} ended. Received {frames_received} frames total")


async def start_ffmpeg(session_id: str) -> Optional[subprocess.Popen]:
    """
    Start FFmpeg with named pipes for video and audio

    FFmpeg command:
    - Video input: raw HEVC from named pipe
    - Audio input: raw PCM (16-bit, 48kHz, mono) from named pipe
    - Output: RTSP to MediaMTX (video copied, audio encoded to AAC)
    """

    # Create named pipes for video and audio
    video_pipe = f'/tmp/video_{session_id}.pipe'
    audio_pipe = f'/tmp/audio_{session_id}.pipe'

    try:
        # Remove old pipes if they exist
        for pipe in [video_pipe, audio_pipe]:
            if os.path.exists(pipe):
                os.remove(pipe)

        # Create new named pipes
        os.mkfifo(video_pipe)
        os.mkfifo(audio_pipe)
        log.info(f"Created named pipes: {video_pipe}, {audio_pipe}")

    except Exception as e:
        log.error(f"Failed to create named pipes: {e}")
        return None

    # FFmpeg command with both video and audio inputs
    # Output via RTMP to MediaMTX (most reliable for publishing)
    # Note: HEVC stream should contain SPS/PPS with size info
    cmd = [
        'ffmpeg',
        # Video input (raw HEVC)
        '-f', 'hevc',
        '-i', video_pipe,
        # Audio input (16-bit PCM, 48kHz, mono)
        '-f', 's16le',           # 16-bit signed PCM little-endian
        '-ar', '48000',          # Sample rate 48kHz
        '-ac', '1',              # Mono (1 channel)
        '-i', audio_pipe,
        # Video: copy HEVC directly (no re-encoding)
        '-c:v', 'copy',
        # Audio: encode PCM to AAC
        '-c:a', 'aac',
        '-b:a', '128k',          # 128 kbps audio bitrate
        # Low latency settings
        '-flush_packets', '1',
        # Output to RTMP (MediaMTX accepts RTMP publishing)
        '-f', 'flv',
        'rtmp://127.0.0.1:1935/glasses'
    ]

    log.info(f"Starting FFmpeg for session {session_id}")
    log.debug(f"FFmpeg command: {' '.join(cmd)}")

    try:
        # Start FFmpeg process with stderr logging
        stderr_log = f'/tmp/ffmpeg_{session_id}.log'
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=open(stderr_log, 'w'),
            bufsize=0
        )

        # Open pipes for writing using non-blocking mode initially, then switch to blocking
        # This avoids deadlock where FFmpeg and relay both wait for each other
        def open_pipes():
            import time
            max_wait = 10  # seconds
            start_time = time.time()

            try:
                # Try opening with O_NONBLOCK first, retry until FFmpeg opens for reading
                log.info(f"Waiting for FFmpeg to open pipes...")
                video_fd = None
                audio_fd = None

                while (time.time() - start_time) < max_wait:
                    try:
                        if video_fd is None:
                            video_fd = os.open(video_pipe, os.O_WRONLY | os.O_NONBLOCK)
                            log.info(f"Video pipe opened")
                            # Clear O_NONBLOCK after successful open
                            import fcntl
                            flags = fcntl.fcntl(video_fd, fcntl.F_GETFL)
                            fcntl.fcntl(video_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

                        if audio_fd is None:
                            audio_fd = os.open(audio_pipe, os.O_WRONLY | os.O_NONBLOCK)
                            log.info(f"Audio pipe opened")
                            # Clear O_NONBLOCK after successful open
                            import fcntl
                            flags = fcntl.fcntl(audio_fd, fcntl.F_GETFL)
                            fcntl.fcntl(audio_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

                        if video_fd is not None and audio_fd is not None:
                            break
                    except OSError as e:
                        if e.errno != 6:  # ENXIO - no reader yet
                            raise
                        time.sleep(0.1)

                if video_fd is None or audio_fd is None:
                    raise Exception("Timeout waiting for FFmpeg to open pipes")

                session_pipes[session_id] = {
                    'video_fd': video_fd,
                    'audio_fd': audio_fd,
                    'video_path': video_pipe,
                    'audio_path': audio_pipe,
                    'stderr_log': stderr_log
                }
                log.info(f"Both pipes opened successfully for session {session_id}")
            except Exception as e:
                log.error(f"Failed to open pipes: {e}", exc_info=True)
                if video_fd is not None:
                    os.close(video_fd)
                if audio_fd is not None:
                    os.close(audio_fd)

        # Open pipes in background thread
        threading.Thread(target=open_pipes, daemon=True).start()

        # Give FFmpeg time to start and open pipes
        await asyncio.sleep(2.0)

        if proc.poll() is not None:
            # Process already exited
            with open(stderr_log, 'r') as f:
                stderr = f.read()
            log.error(f"FFmpeg failed to start: {stderr}")
            # Clean up pipes
            for pipe in [video_pipe, audio_pipe]:
                if os.path.exists(pipe):
                    os.remove(pipe)
            return None

        log.info(f"FFmpeg started (PID {proc.pid}) with audio support for session {session_id}, stderr: {stderr_log}")
        return proc

    except Exception as e:
        log.error(f"Failed to start FFmpeg: {e}")
        # Clean up pipes
        for pipe in [video_pipe, audio_pipe]:
            if os.path.exists(pipe):
                os.remove(pipe)
        return None


async def stop_ffmpeg(session_id: str):
    """Stop FFmpeg process and clean up pipes for a session"""
    proc = ffmpeg_processes.pop(session_id, None)
    if proc:
        try:
            proc.terminate()
            # Wait up to 5 seconds for graceful shutdown
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.info(f"FFmpeg stopped for session {session_id}")
        except Exception as e:
            log.error(f"Error stopping FFmpeg: {e}")

    # Clean up pipes
    pipes = session_pipes.pop(session_id, None)
    if pipes:
        try:
            # Close file descriptors
            if pipes.get('video_fd'):
                os.close(pipes['video_fd'])
                log.debug(f"Closed video pipe FD for session {session_id}")
            if pipes.get('audio_fd'):
                os.close(pipes['audio_fd'])
                log.debug(f"Closed audio pipe FD for session {session_id}")

            # Remove pipe files
            if pipes.get('video_path') and os.path.exists(pipes['video_path']):
                os.remove(pipes['video_path'])
                log.debug(f"Removed video pipe: {pipes['video_path']}")
            if pipes.get('audio_path') and os.path.exists(pipes['audio_path']):
                os.remove(pipes['audio_path'])
                log.debug(f"Removed audio pipe: {pipes['audio_path']}")

            # Remove stderr log
            if pipes.get('stderr_log') and os.path.exists(pipes['stderr_log']):
                os.remove(pipes['stderr_log'])
                log.debug(f"Removed FFmpeg stderr log: {pipes['stderr_log']}")

            log.info(f"Cleaned up pipes for session {session_id}")
        except Exception as e:
            log.error(f"Error cleaning up pipes: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
