#!/usr/bin/env python3
"""
Media relay server for Plan B video upload

Receives binary video/audio frames over WebSocket and relays to FFmpeg,
which remuxes and publishes to MediaMTX.

TECH-01 section 7: Plan B uplink
"""

import asyncio
import logging
import struct
import subprocess
import sys
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

# Active FFmpeg processes by session_id
ffmpeg_processes: dict[str, subprocess.Popen] = {}


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
    - byte 0: stream type (1=video HEVC, 2=audio AAC)
    - byte 1: flags (bit 0 = keyframe)
    - bytes 2-3: reserved
    - bytes 4-11: presentation_time_us (little-endian int64)
    - bytes 12-15: payload length (little-endian int32)
    - bytes 16+: payload
    """

    # TODO: Validate token against environment variable

    await websocket.accept()
    log.info(f"Media WebSocket opened: session {session_id}")

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

            # Parse header
            stream_type, flags, _, _, pts_us, payload_len = struct.unpack(
                '<BBHxQLI',  # Little-endian: byte, byte, short, padding, int64, int32
                data[:16]
            )

            payload = data[16:]

            if len(payload) != payload_len:
                log.warning(f"Payload length mismatch: expected {payload_len}, got {len(payload)}")
                continue

            frames_received += 1

            if stream_type == 1:  # Video HEVC
                video_frames += 1
                # Write to FFmpeg stdin (video pipe)
                try:
                    ffmpeg.stdin.write(payload)
                    ffmpeg.stdin.flush()
                except (BrokenPipeError, OSError) as e:
                    log.error(f"FFmpeg video pipe error: {e}")
                    break

            elif stream_type == 2:  # Audio AAC
                audio_frames += 1
                # TODO: Handle audio if needed
                # For now, video-only

            if frames_received % 100 == 0:
                log.info(f"Session {session_id}: {video_frames} video, {audio_frames} audio frames")

    except WebSocketDisconnect:
        log.info(f"WebSocket closed: session {session_id}")
    except Exception as e:
        log.error(f"Error in WebSocket handler: {e}", exc_info=True)
    finally:
        # Stop FFmpeg
        await stop_ffmpeg(session_id)
        log.info(f"Session {session_id} ended. Received {frames_received} frames total")


async def start_ffmpeg(session_id: str) -> Optional[subprocess.Popen]:
    """
    Start FFmpeg to remux HEVC from stdin and publish to MediaMTX via RTSP

    FFmpeg command:
    - Input: raw H265 stream from stdin
    - Output: RTSP to MediaMTX (no re-encoding, just remuxing)
    """

    # FFmpeg command: read raw HEVC from stdin, output to RTSP
    cmd = [
        'ffmpeg',
        '-f', 'hevc',              # Input format: raw HEVC
        '-i', 'pipe:0',            # Read from stdin
        '-c:v', 'copy',            # Don't re-encode
        '-f', 'rtsp',              # Output format
        '-rtsp_transport', 'tcp',  # Use TCP for RTSP
        'rtsp://127.0.0.1:8554/glasses'
    ]

    log.info(f"Starting FFmpeg for session {session_id}")
    log.debug(f"FFmpeg command: {' '.join(cmd)}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0  # Unbuffered
        )

        # Give it a moment to start
        await asyncio.sleep(0.5)

        if proc.poll() is not None:
            # Process already exited
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            log.error(f"FFmpeg failed to start: {stderr}")
            return None

        log.info(f"FFmpeg started (PID {proc.pid}) for session {session_id}")
        return proc

    except Exception as e:
        log.error(f"Failed to start FFmpeg: {e}")
        return None


async def stop_ffmpeg(session_id: str):
    """Stop FFmpeg process for a session"""
    proc = ffmpeg_processes.pop(session_id, None)
    if proc:
        try:
            proc.stdin.close()
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
