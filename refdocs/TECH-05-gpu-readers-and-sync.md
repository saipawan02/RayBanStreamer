# TECH-05: GPU Readers, Alignment and Calibration

Companion to `SOW.md` sections 9 and 10, and `TECH-01-architecture-and-contracts.md` (clocks and anchors, section 4; schemas, section 6). Evidence tags **[V] [R] [A]** are defined in TECH-01. Almost everything about GPU decode here is **[A]** until the acceptance tests in section 10 pass on your hardware.

Goal: give analytics code a clean Python interface to a session: video frames on the GPU, audio as float samples, IMU as a DataFrame, all on **one timeline**, with gaps marked. No analytics models live here.

## 1. Public API (what analytics code imports)

```python
# gpu/glasses_reader/__init__.py   (signatures; implement as described in later sections)
from dataclasses import dataclass
from typing import Iterator, Optional
import numpy as np, pandas as pd

@dataclass
class Frame:
    tensor: "torch.Tensor"        # uint8, shape (H, W, 3) RGB, on `device`
    pts_s: float                  # seconds on the video timeline (0 = first frame of the session)
    wall_ms: int                  # estimated UTC wall time of capture, ms (see section 5)
    session_id: str
    gap_before_s: float           # 0.0 if contiguous, else seconds of missing video before this frame
    width: int
    height: int

@dataclass
class Session:
    session_id: str
    meta: dict                    # session.json
    imu: pd.DataFrame             # TECH-01 6.2 columns plus `t_video_s` (IMU time on the video timeline)
    events: pd.DataFrame
    segments: list[str]           # recording files in time order
    offsets: "Offsets"            # section 6

def open_session(session_id: str, data_root: str = "/srv/glasses") -> Session: ...
def iter_frames(s: Session, device: str = "cuda", size: Optional[tuple[int, int]] = None,
                backend: str = "auto") -> Iterator[Frame]: ...
def iter_live(rtsp_url: str = "rtsp://127.0.0.1:8554/glasses", device: str = "cuda",
              latency_ms: int = 200) -> Iterator[Frame]: ...
def load_audio(s: Session, sample_rate: int = 16000) -> tuple[np.ndarray, np.ndarray]:
    """Returns (samples float32 mono, t_video_s per sample start) with gaps preserved as NaN-time markers."""
def imu_window(s: Session, t0_s: float, t1_s: float) -> pd.DataFrame: ...
```

Conventions: times are **seconds on the video timeline** unless the name ends in `_ms` or `_ns`. The timeline's zero is the first decoded frame of the first segment of the session. Frames from different sessions never share a timeline.

## 2. Decoding strategy

| Backend | Use for | How | Notes |
| --- | --- | --- | --- |
| **GStreamer + NVDEC** | Live (`iter_live`) | `rtspsrc ! rtph265depay ! h265parse ! nvh265dec ! appsink`, then map frames to CUDA tensors | Needs GStreamer's `nvcodec` plugin; confirm with `gst-inspect-1.0 nvh265dec` [A] |
| **PyNvVideoCodec** (NVIDIA) | Offline (`iter_frames`), fastest | Demux the fMP4 segment, decode with NVDEC, receive device surfaces, convert to CUDA tensors through DLPack | Follow NVIDIA's package documentation for the exact calls [A] |
| **FFmpeg subprocess** | Offline fallback, simple | `ffmpeg -hwaccel cuda -i seg.mp4 -f rawvideo -pix_fmt rgb24 -`, read frames from the pipe, upload with `torch.from_numpy(...).to(device)` | Works if your FFmpeg has CUDA; timestamps from `ffprobe` (section 3) [A] |
| **PyAV (CPU)** | Correctness tests, CI without GPU | `av.open(path)`, decode, `frame.to_ndarray(format="rgb24")` | Slow but trustworthy reference [A] |

`backend="auto"` picks the first available in the order above for each mode. A test compares each backend's first 100 frames against the PyAV reference (mean absolute pixel difference below 3 of 255, same count, pts within 1 ms).

### 2.1 Live reader (GStreamer)

```text
rtspsrc location=rtsp://127.0.0.1:8554/glasses protocols=tcp latency=200
  ! rtph265depay ! h265parse ! nvh265dec
  ! cudadownload ! videoconvert ! video/x-raw,format=RGB
  ! appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false
```

`protocols=tcp` avoids UDP loss on the local hop; `latency` is the RTSP jitter buffer (start at 200 ms, lower with care); `max-buffers=2 drop=true` keeps the reader from falling behind (the newest frame wins). If your GStreamer version delivers CUDA memory directly, skip `cudadownload` and import the buffer on the GPU [A]. Use PyGObject (`gi.repository.Gst`) and pull samples from the `new-sample` signal; copy into a tensor immediately.

Reconnect: if no sample arrives for 3 s, tear down and rebuild the pipeline with backoff (1, 2, 4, 8 s). Mark the next frame with `gap_before_s` equal to the observed silence. Live frames have `pts_s` relative to the first frame the reader received, and `wall_ms` from the local clock at receipt minus a configurable `assumed_latency_ms` (default 0, set after the latency test in section 8).

### 2.2 DeepStream option

For multi-model pipelines, replace the Python reader with DeepStream's `nvurisrcbin` on the same RTSP URL [V: element exists with `latency`, `drop-on-latency` and `select-rtp-protocol` settings per NVIDIA's plugin docs]. Set `latency=200`; set `select-rtp-protocol=4` (TCP) if UDP jitter produces artefacts; follow it with `nvstreammux` (`live-source=1`, `batch-size=1`, `width=<w>` `height=<h>`). Treat this as a later optimisation; the Python readers come first.

## 3. Segment handling and timestamps (offline)

1. `open_session` finds segments (TECH-04 section 8: files overlapping the session window, in time order).
2. For each segment get frame timestamps without decoding:

```bash
ffprobe -v error -select_streams v:0 -show_entries frame=pts_time -of csv=p=0 <segment.mp4>
```

Some ffprobe versions call the field `pkt_pts_time`; use whichever prints values [A].

3. Segment start wall time comes from the file name (UTC; the server runs in UTC, TECH-03 section 1).
4. **Video timeline:** `pts_s = (segment_start_wall_ms - session_first_segment_start_wall_ms)/1000 + (frame_pts_in_segment - first_pts_in_segment)`. This places later segments after earlier ones using wall-clock file names, so reconnect gaps appear as real gaps.
5. **Gap detection:** a gap exists wherever consecutive frames are more than `3 / nominal_fps` apart (nominal fps from `settings_applied`, or measured median), or across a segment boundary with a wall-clock jump of more than that. Set `gap_before_s` to the missing time.
6. **Mid-stream resolution changes:** the SDK may lower resolution under bandwidth pressure [V]. Check each frame's size; if `size` is given, resize on the GPU; if not, the tensor shape may change between frames. Re-initialise the decoder at segment boundaries.
7. `wall_ms = segment_start_wall_ms + (frame_pts_in_segment - first_pts_in_segment) * 1000` (milliseconds). This is accurate to the Bluetooth, mux and SRT delay and not better; the calibration (section 6) only aligns streams to **each other**.

## 4. Audio

- Extract with FFmpeg to mono float32 at the requested rate: `ffmpeg -i seg.mp4 -vn -ac 1 -ar 16000 -f f32le -`.
- Place samples on the same timeline as video: audio pts equals video pts within a segment if the shared timebase assumption holds (spike S3) [A]; apply `offsets.audio_to_video_s` from calibration when it is non-zero.
- Gaps: segment boundaries or pts jumps produce gaps; return a list of `(start_s, end_s)` gap intervals alongside the samples. Never fill gaps with silence silently; a consumer chooses.
- If `audio.wav` and `audio_pts.csv` exist (raw PCM channel), prefer them for lossless analysis; place samples by interpolating `(presentation_time_us, byte_offset)`.

## 5. Putting IMU on the video timeline

IMU samples carry `timestamp_ns` on the glasses' monotonic clock; the relation to the stream clock is undocumented [V]. Therefore:

- Define `t_imu_s = (timestamp_ns - first_timestamp_ns) / 1e9` for each session (first sample after the first `imu_started` event).
- Define `t_video_s = t_imu_s + offsets.imu_to_video_s`.
- **Uncalibrated estimate** (no tap test): `offsets.imu_to_video_s = (A_imu_elapsed_ns - A_video_elapsed_ns) / 1e9`, where `A_*` are the `phone_elapsed_ns` values of the `clock_anchor` events for the first IMU sample and first video frame. This assumes both arrive with the same Bluetooth delay, which is not guaranteed; expect hundreds of milliseconds of error [A].
- **Calibrated:** `offsets.imu_to_video_s` from section 6, stored in `session.json -> calibration`. Calibrated values always override estimates.
- After a pause, the anchors are re-emitted (TECH-01 section 4.2). Repeat the same computation per **run** (a continuous span between pauses), so each run has its own offset; `imu.t_video_s` is computed run by run. Runs are delimited by `session_paused` and `session_resumed` events.

`Offsets` holds `imu_to_video_s`, `audio_to_video_s`, `spread_ms`, and `source` (`"tap_v1"` or `"anchors"`).

## 6. Calibration: tap test and `estimate_offset`

### 6.1 Procedure (about 30 seconds, repeat after firmware or app updates)

1. Start a session with the glasses worn and pointed at a plain, well-lit surface 40 to 60 cm away; lighting and motion in frame should be otherwise still.
2. Wait 5 seconds.
3. Tap the **frame of the glasses** (near the temple) firmly with a fingertip three times, about one second apart. Each tap causes a visible camera jolt, an audible click, and an IMU spike.
4. Wait 5 seconds. Stop the session.

### 6.2 Algorithm

For each stream compute a 1-D "activity" signal, find peaks, pick the three taps, then compare times.

| Stream | Signal | Peak picking |
| --- | --- | --- |
| IMU | magnitude of the high-pass-filtered accelerometer (subtract a 0.5 s moving median), or the gyro-magnitude derivative | `scipy.signal.find_peaks` with `height = median + 8 × MAD`, `distance ≥ 0.5 s` |
| Audio | short-time energy of the 16 kHz mono signal, 5 ms hop, onset = positive energy derivative | Same rule; take the earliest rising edge of each peak |
| Video | mean absolute difference between consecutive frames after grayscale and downsample to 64x112 | Same rule |

```python
# tools/estimate_offset.py (sketch)
import numpy as np
from scipy.signal import find_peaks

def mad(x): return np.median(np.abs(x - np.median(x))) + 1e-12

def peaks(t, x, min_gap_s=0.5, k=8.0):
    thr = np.median(x) + k * mad(x)
    idx, props = find_peaks(x, height=thr, distance=max(1, int(min_gap_s / np.median(np.diff(t)))),
                            prominence=thr * 0.3)
    top = idx[np.argsort(props["peak_heights"])[-3:]]     # the 3 strongest
    return np.sort(t[top])

def match_three(a, b, max_offset_s=3.0):
    """a, b: sorted arrays of 3 times; returns per-tap offsets b - a, or None if spacing disagrees."""
    if len(a) < 3 or len(b) < 3: return None
    if np.max(np.abs(np.diff(a) - np.diff(b))) > 0.25: return None   # tap spacing must agree
    d = b - a
    return d if np.max(np.abs(d)) <= max_offset_s else None

def estimate(imu_t, imu_sig, video_t, video_sig, audio_t, audio_sig):
    ti, tv, ta = peaks(imu_t, imu_sig), peaks(video_t, video_sig), peaks(audio_t, audio_sig)
    d_iv = match_three(ti, tv)      # video time - IMU time (both measured from their own first sample)
    d_av = match_three(ta, tv)
    out = {"method": "tap_v1"}
    for name, d in (("imu_to_video_s", d_iv), ("audio_to_video_s", d_av)):
        out[name] = None if d is None else float(np.median(d))
    out["spread_ms"] = None if d_iv is None else float((d_iv.max() - d_iv.min()) * 1000)
    return out
```

Times passed in are each stream's own seconds from its first sample or frame (`t_imu_s`; `t_video_s` before offsets; audio relative to its first sample). The result is the value to **add** to the stream's time to land on the video timeline, which is exactly what `Offsets` stores. Write it into `session.json` under `calibration` (TECH-01 6.4). If any detection fails (`None`), report which stream failed and why (no peak above threshold, spacing mismatch) instead of guessing.

Acceptance: spread across the three taps under 50 ms (SOW section 11, row 10). Also run the clap test: clap once in view of the glasses and confirm the audio onset and the video change agree within the audio-to-video offset.

## 7. Verifying GPU decode is real

- While a reader runs: `nvidia-smi dmon -s u` shows non-zero decoder utilisation in the `dec` column [A: column naming varies by driver].
- CPU sanity: `top` should show the reader process well under one core for 24 fps portrait video.
- Memory: run the reader for 10 minutes and confirm GPU and host memory stay flat (the HEVC memory-growth reports in Meta's repositories concern the phone SDK, but verify the server side too).

## 8. Measuring glass-to-server latency

No official figure exists, so measure it once and store the result (`DECISIONS.md`).

1. On the server host, run a page or script that shows the server's wall clock in milliseconds, large, updating every frame. Display it on a screen (a laptop on the tailnet showing the server-hosted page works if its clock is synced to the same time source; otherwise use a monitor attached to the server).
2. Point the glasses at the screen and stream with the live reader running.
3. For each sampled frame, the reader records `T_recv` (server clock when the frame arrives in the reader). Read the clock value shown in the saved frame (manually for 20 frames, or by OCR with a small script [A]).
4. Latency = `T_recv - T_clock_in_image`. Report median and 95th percentile. This is end-to-end (capture, Bluetooth, phone, SRT, ingest, RTSP, decode).
5. Component hints: phone to server network delay shows up as `recv_wall_ms - phone_wall_ms` on telemetry batches (affected by phone-to-server clock offset; keep both on network time); SRT stats (RTT, loss) come from the SRT library on the phone and from MediaMTX logs at `debug` level.

## 9. Reader robustness checklist

- Handle a stream that ends mid-segment (truncated final part): decode what exists and stop cleanly.
- Handle an empty session (no segments) and a session with IMU but no video: `iter_frames` yields nothing and says why.
- Handle clock steps on the server (`chrony` adjusting): file-name times are the reference; log if consecutive segments overlap.
- Do not hold more than 2 frames of backlog in live mode; count and report drops.
- Release GPU memory when the iterator is closed (generators must clean up in `finally`).

## 10. Acceptance tests (SOW section 11, row 11)

1. **Offline:** `iter_frames` on a recorded 10-minute session yields frames with correct width and height and strictly increasing `pts_s`; backend comparison test passes; decoder utilisation is non-zero.
2. **Live:** `iter_live` runs 10 minutes against a live stream without crashing; survives a publisher restart with a marked gap.
3. **Audio:** `load_audio` returns samples whose duration matches the video duration within 0.5 s for a gap-free session.
4. **IMU alignment:** after the tap test, the plotted IMU gyro magnitude and the video frame-difference signal overlay with taps coinciding (save the plot to `derived/<session_id>/alignment.png`).
5. **Memory:** flat over 10 minutes.

## 11. References

- Clocks, anchors, schemas: `TECH-01-architecture-and-contracts.md`.
- NVIDIA DeepStream `nvurisrcbin` plugin: https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvurisrcbin.html [V for the plugin and its properties]
- Meta Motion API (timestamps, units): https://wearables.developer.meta.com/docs/develop/dat/motion/ [V]
- Meta in-stream audio (timestamps): https://wearables.developer.meta.com/docs/develop/dat/microphones-and-speakers/ [V]
- NVIDIA PyNvVideoCodec, GStreamer `nvcodec` plugin and PyAV documentation: consult each project's current docs before use.
