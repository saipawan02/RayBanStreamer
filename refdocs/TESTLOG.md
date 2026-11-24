# TESTLOG

One entry per test run, newest first. Template and the acceptance matrix are in `TECH-06-testing-and-operations.md` (sections 3 and 6).

<!--
## YYYY-MM-DD HH:MM  <short title>
- Tester:
- Glasses firmware / phone model + OS / app version / SDK version:
- Server commit / MediaMTX version:
- Settings: video quality, fps, audio rate, imu rate
- Test: (matrix row or section)
- Result: PASS | FAIL | PARTIAL
- Numbers: (fps, imu Hz, drops, latency, offsets)
- Session ID(s):
- Notes / follow-ups:
-->

## TODO: Phase 1 Server Verification (fill after completing PHASE-1-VERIFICATION.md)
- Tester:
- Server: hostname, Ubuntu version, GPU model
- MediaMTX version: (from `docker logs mediamtx`)
- Python version:
- Test: SOW section 11 rows 3, 4; TECH-03 section 7; TECH-04 section 11; TECH-05 section 10
- Result: PASS | FAIL | PARTIAL
- Numbers:
  - MediaMTX synthetic stream: duration, segments created
  - Telemetry 5min synthetic: IMU rows (expected ~18000), rate Hz
  - GPU reader tests: X/26 passed
  - Fake session: duration, segments, IMU rows
  - Calibration estimate: imu_to_video_ms (truth 120), audio_to_video_ms (truth 80), spread_ms
  - Crash recovery: data loss in seconds
- Session IDs: (synthetic IMU session, fake session)
- Notes:
  - MediaMTX: [ ] starts, [ ] accepts SRT, [ ] records playable fMP4, [ ] serves RTSP
  - Telemetry: [ ] 35/35 tests pass, [ ] accepts WebSocket, [ ] writes Parquet
  - GPU readers: [ ] 26/26 tests pass, [ ] loads fake session, [ ] extracts frames
  - Calibration: [ ] finds 3 taps, [ ] within 5ms of ground truth
  - Firewall: [ ] ports only on tailscale0
- Next: Run Spikes S1-S6 before starting Android app

## 2026-11-24 16:05  GPU readers, tools, CPU only (no GPU, no ffmpeg, no GStreamer, no phone)
- Tester: Claude Code (authoring machine)
- Glasses firmware / phone model + OS / app version / SDK version: n/a (synthetic data from tools/fake_session.py)
- Server commit / MediaMTX version: no git history / not run
- Settings: fake session 128x224, 24 fps HEVC + AAC mono 48 kHz, IMU 60 Hz, 1 or 2 segments (4 s gap)
- Test: TECH-05 section 10 rows 1 (partly), 3, 4 (numbers only, no plot), 5 not run; TECH-06 section 2 tools
- Result: PARTIAL
- Numbers: 26 pytest tests pass, 1 skipped (ffmpeg vs PyAV comparison). Frames: strictly increasing pts_s, gap of 4.0 s marked at the segment boundary. Audio duration within 0.5 s of video. Tap test: imu_to_video 138 ms (truth 120), audio_to_video -28.6 ms (truth -30), spread 16.7 ms.
- Session ID(s): generated per test run (fake)
- Notes / follow-ups: NOT run: any GPU decode (NVDEC), `iter_live`, ffmpeg backend comparison, 10-minute offline and live runs, memory flatness, nvidia-smi decoder check, alignment.png, anything with real glasses data. Run on the GPU server: `cd gpu && pip install -e '.[dev]' && pytest -q`, then `python tools/fake_session.py --data-root /tmp/g` and `python tools/check_session.py <id> --data-root /tmp/g`.

## 2026-11-24 15:55  Telemetry receiver, unit and local end-to-end (no server hardware, no phone)
- Tester: Claude Code (authoring machine)
- Glasses firmware / phone model + OS / app version / SDK version: n/a
- Server commit / MediaMTX version: no git history / not run
- Settings: part_seconds 0.5 (tests), imu rate 60 Hz synthetic
- Test: TECH-04 section 11 unit and protocol tests; acceptance 1 (shortened to 8 s) and acceptance 3 (--drop-at)
- Result: PARTIAL
- Numbers: 35 pytest tests pass; 8 s synthetic run at 60 Hz within 1% of 480 rows, no duplicate samples, no seq gaps after a mid-run drop
- Session ID(s): 11111111-2222-4333-8444-555555555555 (synthetic)
- Notes / follow-ups: NOT run: 5-minute run, mid-stream `kill -9` (acceptance 2), 50 parallel sessions for 60 s (a 20-session smoke test passes), MediaMTX smoke test, firewall check. Run them on the server.
