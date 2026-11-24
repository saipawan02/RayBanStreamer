"""Environment settings (TECH-01 section 8, TECH-04 section 3)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

MIN_TOKEN_LEN = 16


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    token: str
    data_root: Path = Path("/srv/glasses")
    part_seconds: float = 5.0
    part_rows: int = 1000
    log_level: str = "info"
    idle_seconds: float = 300.0      # close an idle session after 5 minutes (TECH-04 section 5)
    meta_seconds: float = 30.0       # rewrite session.json at least this often while active

    def __post_init__(self) -> None:
        if len(self.token) < MIN_TOKEN_LEN:
            raise ConfigError(f"TELEMETRY_TOKEN must be set and at least {MIN_TOKEN_LEN} characters")

    @property
    def data_dir(self) -> Path:
        return self.data_root / "data"

    @property
    def recordings_dir(self) -> Path:
        return self.data_root / "recordings" / "glasses"


def load_settings() -> Settings:
    token = os.environ.get("TELEMETRY_TOKEN", "")
    return Settings(
        token=token,
        data_root=Path(os.environ.get("DATA_ROOT", "/srv/glasses")),
        part_seconds=float(os.environ.get("PART_SECONDS", "5")),
        part_rows=int(os.environ.get("PART_ROWS", "1000")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )
