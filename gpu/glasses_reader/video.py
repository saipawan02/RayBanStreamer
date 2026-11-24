"""Offline frame iteration (TECH-05 sections 1 to 3 and 9)."""
from __future__ import annotations

import logging
from collections.abc import Iterator

from .backends import select_backend
from .data import segment_start_ms
from .timeline import FrameClock, nominal_fps
from .types import Frame, Session, TimedFrame

log = logging.getLogger("glasses_reader")


def iter_timed_frames(s: Session, backend: str = "auto") -> Iterator[TimedFrame]:
    """Decode every segment in order and stamp frames. CPU arrays; no torch needed.

    Yields nothing (and logs why) for a session with no segments.
    """
    if not s.segments:
        log.warning("session %s has no recording segments: no frames to read", s.session_id)
        return
    be = select_backend(backend)
    starts = [segment_start_ms(p) for p in s.segments]
    if any(x is None for x in starts):
        raise ValueError("segment file name does not carry a start time")
    clock = FrameClock(nominal_fps(s.meta, s.segments), starts[0])
    for i, (path, start) in enumerate(zip(s.segments, starts, strict=True)):
        clock.new_segment(start)
        for raw_pts, image in be.decode(path):
            pts_s, wall_ms, gap = clock.stamp(raw_pts)
            yield TimedFrame(image, pts_s, wall_ms, gap, i)


def _to_tensor(image, device: str, size: tuple[int, int] | None):
    try:
        import torch
        import torch.nn.functional as F
    except ImportError as exc:
        raise RuntimeError("iter_frames needs PyTorch; install it matching your CUDA version "
                           "(or use iter_timed_frames for CPU arrays)") from exc
    t = torch.from_numpy(image).to(device)
    if size is not None and (t.shape[1], t.shape[0]) != tuple(size):
        w, h = size
        x = t.permute(2, 0, 1)[None].float()
        x = F.interpolate(x, size=(h, w), mode="bilinear", antialias=True, align_corners=False)
        t = x[0].round().clamp(0, 255).to(torch.uint8).permute(1, 2, 0).contiguous()
    return t


def iter_frames(s: Session, device: str = "cuda", size: tuple[int, int] | None = None,
                backend: str = "auto") -> Iterator[Frame]:
    """Yield ``Frame`` objects with uint8 RGB (H, W, 3) tensors on ``device``.

    ``size`` is (width, height); frames are resized on the GPU. Without it the tensor shape may
    change mid-stream if the SDK lowered the resolution (TECH-05 section 3, step 6).
    """
    try:
        for tf in iter_timed_frames(s, backend):
            t = _to_tensor(tf.image, device, size)
            yield Frame(t, tf.pts_s, tf.wall_ms, s.session_id, tf.gap_before_s,
                        int(t.shape[1]), int(t.shape[0]))
    finally:                       # generators must release GPU memory when closed
        try:
            import torch
            if device.startswith("cuda") and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
