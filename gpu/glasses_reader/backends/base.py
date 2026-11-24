from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple, Protocol


class DecodedFrame(NamedTuple):
    pts_s: float     # presentation time in the segment's own container clock
    image: Any       # numpy uint8 (H, W, 3) RGB


class Backend(Protocol):
    name: str

    def available(self) -> bool: ...

    def decode(self, path: Path) -> Iterator[DecodedFrame]: ...


def _registry() -> dict[str, Backend]:
    from .ffmpeg import FFmpegBackend
    from .pyav import PyAVBackend

    # PyNvVideoCodec (fastest offline) is intentionally absent: its API must be taken from
    # NVIDIA's package docs on the GPU server (TECH-05 section 2 marks it [A]).
    return {b.name: b for b in (FFmpegBackend(), PyAVBackend())}


def backend_names() -> list[str]:
    return list(_registry())


def select_backend(name: str = "auto") -> Backend:
    reg = _registry()
    if name == "auto":
        for b in reg.values():          # order: FFmpeg subprocess, then PyAV
            if b.available():
                return b
        raise RuntimeError("no decode backend available: install PyAV (pip install av) or FFmpeg")
    if name not in reg:
        raise ValueError(f"unknown backend {name!r}; choose from {['auto', *reg]}")
    if not reg[name].available():
        raise RuntimeError(f"backend {name!r} is not available on this machine")
    return reg[name]
