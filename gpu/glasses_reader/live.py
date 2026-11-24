"""Live reader over RTSP with GStreamer + NVDEC (TECH-05 section 2.1).

NOT TESTED in the authoring environment (needs GStreamer's nvcodec plugin and a GPU). Confirm
with ``gst-inspect-1.0 nvh265dec`` first, then run the live acceptance test (TECH-05 section 10).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator

from .types import Frame

log = logging.getLogger("glasses_reader")

BACKOFF_S = (1, 2, 4, 8)
STALL_S = 3.0


def build_pipeline_string(rtsp_url: str, latency_ms: int) -> str:
    return (
        f"rtspsrc location={rtsp_url} protocols=tcp latency={latency_ms} "
        "! rtph265depay ! h265parse ! nvh265dec "
        "! cudadownload ! videoconvert ! video/x-raw,format=RGB "
        "! appsink name=sink max-buffers=2 drop=true sync=false"
    )


def iter_live(rtsp_url: str = "rtsp://127.0.0.1:8554/glasses", device: str = "cuda",
              latency_ms: int = 200, assumed_latency_ms: int = 0, session_id: str = "live"
              ) -> Iterator[Frame]:
    """Yield the newest frames (older ones are dropped by the appsink, so the reader never lags).

    ``pts_s`` is relative to the first frame received. After a stall of more than 3 s the pipeline
    is rebuilt with backoff 1, 2, 4, 8 s and the next frame carries ``gap_before_s``.
    ``wall_ms`` is the receipt time minus ``assumed_latency_ms`` (set from the latency test).
    """
    import gi

    gi.require_version("Gst", "1.0")
    import numpy as np
    import torch
    from gi.repository import Gst

    Gst.init(None)
    t_start = None            # monotonic time of the first frame ever received
    last_rx = time.monotonic()
    attempt = 0
    pipeline = None
    try:
        while True:
            pipeline = Gst.parse_launch(build_pipeline_string(rtsp_url, latency_ms))
            sink = pipeline.get_by_name("sink")
            pipeline.set_state(Gst.State.PLAYING)
            first_buf_pts = None
            base_s = 0.0
            while True:
                sample = sink.emit("try-pull-sample", int(STALL_S * Gst.SECOND))
                now = time.monotonic()
                if sample is None:
                    log.warning("no sample for %.0f s; rebuilding the pipeline", STALL_S)
                    break
                attempt = 0
                buf = sample.get_buffer()
                caps = sample.get_caps().get_structure(0)
                w, h = caps.get_value("width"), caps.get_value("height")
                ok, info = buf.map(Gst.MapFlags.READ)
                if not ok:
                    continue
                try:
                    image = np.frombuffer(info.data, np.uint8).reshape(h, w, 3).copy()
                finally:
                    buf.unmap(info)
                silence = now - last_rx
                gap = silence if (t_start is not None and first_buf_pts is None) else 0.0
                if t_start is None:
                    t_start = now
                if first_buf_pts is None:
                    first_buf_pts = buf.pts
                    base_s = (now - t_start)
                pts_s = base_s + (buf.pts - first_buf_pts) / 1e9 if buf.pts != Gst.CLOCK_TIME_NONE \
                    else now - t_start
                last_rx = now
                tensor = torch.from_numpy(image).to(device)
                yield Frame(tensor, pts_s, int(time.time() * 1000) - assumed_latency_ms,
                            session_id, gap, w, h)
            pipeline.set_state(Gst.State.NULL)
            time.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
            attempt += 1
    finally:
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        if device.startswith("cuda"):
            torch.cuda.empty_cache()
