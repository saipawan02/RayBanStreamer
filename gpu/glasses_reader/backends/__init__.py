"""Offline decode backends (TECH-05 section 2). Each yields ``(raw_pts_s, rgb_ndarray)``."""
from __future__ import annotations

from .base import Backend, DecodedFrame, backend_names, select_backend

__all__ = ["Backend", "DecodedFrame", "backend_names", "select_backend"]
