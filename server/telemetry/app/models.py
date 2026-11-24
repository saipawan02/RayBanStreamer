"""Pydantic models mirroring TECH-01 section 5. Unknown extra fields are ignored."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SCHEMA_VERSION = 1

Vec3 = tuple[float, float, float] | None
Quat = tuple[float, float, float, float] | None  # w, x, y, z


class ImuSample(BaseModel):
    timestamp_ns: int
    source: str = "unknown"
    accel: Vec3 = None
    gyro: Vec3 = None
    mag: Vec3 = None
    quat: Quat = None


class ImuBatch(BaseModel):
    schema_version: int
    type: Literal["imu_batch"]
    session_id: str
    seq: int
    phone_wall_ms: int
    phone_elapsed_ns: int
    dropped_since_last: int = 0
    samples: list[ImuSample]


class Event(BaseModel):
    schema_version: int
    type: Literal["event"]
    session_id: str
    seq: int
    phone_wall_ms: int
    phone_elapsed_ns: int
    event: str  # free string; unknown names are stored, not rejected
    detail: dict | None = None
