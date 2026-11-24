"""PyAV (CPU): the slow but trustworthy reference decoder."""
from __future__ import annotations

import importlib.util
import logging
from collections.abc import Iterator
from pathlib import Path

from .base import DecodedFrame

log = logging.getLogger("glasses_reader")


class PyAVBackend:
    name = "pyav"

    def available(self) -> bool:
        return importlib.util.find_spec("av") is not None

    def decode(self, path: Path) -> Iterator[DecodedFrame]:
        import av

        try:
            with av.open(str(path)) as c:
                if not c.streams.video:
                    return
                s = c.streams.video[0]
                s.thread_type = "AUTO"
                for frame in c.decode(s):
                    if frame.pts is None:
                        continue
                    yield DecodedFrame(float(frame.pts * frame.time_base),
                                       frame.to_ndarray(format="rgb24"))
        except av.error.FFmpegError as exc:      # truncated final segment: stop cleanly
            log.warning("%s: decode stopped early (%s)", path, exc)
