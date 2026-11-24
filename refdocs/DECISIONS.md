# DECISIONS

Dated, one entry per finding or departure from `SOW.md` or the `TECH-*.md` documents. Each entry states what was decided, why, and the evidence (spike output, log excerpt, test result). The spikes S1 to S6 are defined in `TECH-01-architecture-and-contracts.md` section 11.

## Open spikes

| ID | Question | Status | Result |
| --- | --- | --- | --- |
| S1 | Compressed `VideoFrame.buffer` format (Annex B or length-prefixed), parameter sets per keyframe, GOP, B-frames | open | |
| S2 | Name and behaviour of the compressed-video flag on the pinned SDK | open | |
| S3 | Audio and video `presentationTimeUs` shared timebase | open | |
| S4 | SRT and MPEG-TS library on Android that works with MediaMTX (else Plan B) | open | |
| S5 | Delivered IMU rate at the 60 Hz setting | open | |
| S6 | Behaviour of each stream and timestamp across a glasses-off pause | open | |

## Decisions

<!-- YYYY-MM-DD  <decision>. Why: ... Evidence: ... Affects: <documents and components> -->

2026-11-24  Telemetry receiver adds `uncompact_for_reopen`: when a closed (compacted) session reconnects, `imu.parquet` is moved back to `imu/part-000000.parquet` (or dropped if parts also exist) so new parts are never hidden by the loader's "prefer imu.parquet" rule. Why: TECH-04 section 5/7 leave reconnect-after-compaction undefined. Evidence: tests/test_compact_loader.py::test_reopen_after_compaction_keeps_everything. Affects: TECH-04 section 7, server/telemetry/app/compact.py.
2026-11-24  Telemetry receiver adds `Settings.idle_seconds` (300) and `meta_seconds` (30) as constructor fields, not environment variables. Why: TECH-04 fixes the values but tests need to shorten them; no TECH-01 section 8 change needed. Affects: server/telemetry/app/config.py.
2026-11-24  Telemetry receiver tests drive a real uvicorn server with the `websockets` sync client instead of Starlette's TestClient. Why: in the authoring environment pip refused to install `httpx` 0.28.1 (wheel contained a path escaping the target dir, so TestClient was unavailable); this approach also tests the real WebSocket stack. Evidence: 35 tests pass. Affects: TECH-04 section 11 (httpx is still listed in the dev extras).
2026-11-24  Authoring environment note: pip's index served an `httpx` wheel that failed pip's path-safety check ("installing outside the target directory"). It was not bypassed. Re-check the wheel's source on the real build machine before installing httpx there.
2026-11-24  gpu/ per-run IMU offsets use `offset_r = (A_imu_r - A_video0) + (T0 - T_r)` (A_video0 = first video anchor of the session) instead of TECH-05 section 5's `A_imu - A_video` per run. Why: segments sit on a session-wide wall-clock timeline (TECH-05 section 3), so a pause is a real gap and video time must not restart per run; the T0 - T_r term also holds if the IMU clock jumps across a pause. Identical to the spec for run 0 and for single-run sessions. A calibrated offset keeps run differences: `offset_r = anchor_r + (calibrated - anchor_0)`. Evidence: gpu/tests/test_offsets.py (pause and calibration cases, synthetic). Still [A] until spike S6 shows what the IMU clock does across a pause. Affects: TECH-05 sections 5, 6; gpu/glasses_reader/offsets.py.
2026-11-24  gpu/ `load_audio` returns `(samples, t_video_s per sample)` with gaps as jumps in `t` (no NaN markers, no silence fill); `find_gaps(t, rate, min_gap_s)` returns the `(start_s, end_s)` intervals. Why: TECH-05 section 1 and 4 ask for both NaN-time markers and a gap list; a per-sample time array already encodes both without a second return value. Affects: TECH-05 sections 1, 4.
2026-11-24  gpu/ builds the offline backends `pyav` (CPU reference) and `ffmpeg` (subprocess); PyNvVideoCodec is NOT built and `backend="auto"` skips it. Why: its calls must come from NVIDIA's package docs on the GPU server (TECH-05 section 2 marks it [A]); inventing the API here would be a guess. The ffmpeg backend and the GStreamer live reader are written but untested (no ffmpeg or GPU on the authoring machine). Affects: TECH-05 section 2.
2026-11-24  gpu/ is standalone: `glasses_reader.data` re-implements the session loader instead of importing server/telemetry. Why: the readers must install without the FastAPI service. Cost: two copies of `find_segments` logic; change both together. Affects: TECH-04 section 8, TECH-05 section 3.
2026-11-24  Deferred: the `alignment.png` overlay plot (TECH-05 section 10, row 4) needs matplotlib, which no TECH document names. Ask before adding. Also deferred: raw PCM sidecar (`audio.wav`, `audio_pts.csv`) in `load_audio`, because the `/pcm` endpoint is not built.
2026-11-24  Calibration accuracy on synthetic data: tap test on the fake session recovered imu_to_video 138 ms (truth 120 ms) and audio_to_video -28.6 ms (truth -30 ms), spread 16.7 ms. The IMU error is under one frame at 24 fps because the video activity signal is stamped per frame. Real glasses will differ; this only shows the algorithm works.

