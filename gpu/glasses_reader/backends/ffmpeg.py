"""FFmpeg subprocess backend (TECH-05 section 2). Untested without ffmpeg; run the backend
comparison test on the GPU server.

Frames come from a rawvideo pipe, which carries no timestamps, so they are paired in order with
the packet timestamps read from the file. If your FFmpeg has CUDA (`ffmpeg -hwaccels` lists
``cuda``) decoding uses NVDEC; otherwise it silently decodes on the CPU.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from ..timeline import read_video_pts
from .base import DecodedFrame

log = logging.getLogger("glasses_reader")


def has_cuda_hwaccel() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-hwaccels"], capture_output=True, text=True,
                         check=False).stdout
    return "cuda" in out.split()


class FFmpegBackend:
    name = "ffmpeg"

    def available(self) -> bool:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    @staticmethod
    def _size(path: Path) -> tuple[int, int]:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True).stdout.strip()
        w, h = out.split(",")[:2]
        return int(w), int(h)

    def decode(self, path: Path) -> Iterator[DecodedFrame]:
        w, h = self._size(path)
        pts = read_video_pts(path)
        cmd = ["ffmpeg", "-v", "error"]
        if has_cuda_hwaccel():
            cmd += ["-hwaccel", "cuda"]
        cmd += ["-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        nbytes = w * h * 3
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            assert proc.stdout is not None
            i = 0
            while i < len(pts):
                buf = proc.stdout.read(nbytes)
                if len(buf) < nbytes:             # truncated: decode what exists
                    break
                yield DecodedFrame(float(pts[i]), np.frombuffer(buf, np.uint8).reshape(h, w, 3))
                i += 1
        finally:
            proc.kill()
            proc.wait()
