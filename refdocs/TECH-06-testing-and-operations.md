# TECH-06: Testing, Operations and Runbook

Companion to `SOW.md` sections 11 to 13 and all other TECH documents. Evidence tags **[V] [R] [A]** are defined in `TECH-01-architecture-and-contracts.md`.

## 1. Test layers

| Layer | Needs glasses | Needs phone | Needs GPU server | Where defined |
| --- | --- | --- | --- | --- |
| Server unit and API tests | No | No | No (CPU only) | TECH-04 section 11 |
| Server ingest smoke test (synthetic ffmpeg publisher) | No | No | Yes | TECH-03 section 4 |
| Reader backend tests | No | No | Yes for GPU backends | TECH-05 section 10 |
| App unit tests | No | No | No | TECH-02 section 10 |
| App with mock device | No | Yes | Yes | TECH-02 section 10 |
| Real-glasses tests | Yes | Yes | Yes | section 3 below |
| Soak test | Yes | Yes | Yes | section 4 below |

Order of execution follows `SOW.md` section 12: server without glasses, then app with mock device, then real glasses, then settings sweep, pause and drop tests, calibration, walking test, battery check.

## 2. Synthetic tools (build these first, in `tools/`)

| Tool | Purpose | Spec |
| --- | --- | --- |
| `synthetic_publisher.sh` | Publish test video and audio to MediaMTX | The ffmpeg command in TECH-03 section 4; arguments for host, passphrase, size, fps, duration |
| `synthetic_imu_client.py` | Exercise the telemetry receiver | TECH-04 section 11 |
| `estimate_offset.py` | Compute IMU and audio offsets to video | TECH-05 section 6 |
| `latency_clock.html` (served by the telemetry app or a static server) | Show the server clock in milliseconds for the latency test | TECH-05 section 8 |
| `check_session.py` | Print a one-page health report for a session | Row counts, seq gaps, mean IMU rate from `timestamp_ns`, video segment list and durations, mean fps from `ffprobe`, audio presence, calibration status, gaps list |
| `fake_session.py` | Generate a complete fake session on disk (IMU parquet, events, a short video segment) | Lets reader and analytics code be developed with no phone |

`check_session.py` output example fields: `imu_rate_mean_hz`, `imu_rate_p5_hz`, `imu_dropped_reported`, `seq_gaps`, `video_fps_mean`, `video_gaps`, `audio_present`, `calibration.spread_ms`, `warnings[]`. Exit code 1 if any acceptance threshold from section 3 fails.

## 3. Acceptance matrix (mirrors SOW section 11)

| # | Check | How to measure | Pass |
| --- | --- | --- | --- |
| 1 | 10-minute session, all three streams arrive | `check_session.py` | Video, audio, IMU all present; status screen matches server logs |
| 3 | MediaMTX smoke test | TECH-03 section 4 | Playable fMP4 with HEVC and AAC; live RTSP plays |
| 4 | Telemetry receiver | TECH-04 section 11 | Rows within 1%; kill-restart loses under 10 s |
| 5 | Video frame rate | `ffprobe` frame count divided by duration | At least 90% of requested fps |
| 6 | IMU rate | Mean from `timestamp_ns` deltas | At least 90% of requested rate; drops reported |
| 7 | Audio | Gap scan of decoded audio | No gaps over 200 ms outside pauses; clap test in sync |
| 8 | Glasses-off pause | Remove and replace glasses | All three streams resume; gap visible in `events` |
| 9 | Network drop | Phone off Wi-Fi 30 s | Uplink resumes within 15 s; data after the gap intact |
| 10 | Calibration | `estimate_offset.py` | Spread under 50 ms |
| 11 | GPU readers | TECH-05 section 10 | Passes |
| 12 | Latency | TECH-05 section 8 | Measured and recorded (no threshold) |
| 13 | Documentation | Review | README per component with setup, run, test, limits |

Row numbers follow `SOW.md` section 11 (row 2 there is the video settings sweep, defined in section 5 below).

## 4. Soak test (30 minutes, outdoors or walking)

1. Fully charge glasses and phone. Note starting battery levels and firmware versions.
2. Start a session; leave the status screen on.
3. Walk for 30 minutes; at minute 10 take the glasses off for 10 seconds; at minute 20 switch the phone's network off for 30 seconds.
4. Stop. Run `check_session.py`. Record battery drop for glasses and phone.
5. Watch for: app memory growth (Android Studio profiler or `adb shell dumpsys meminfo <package>`), stream restarts, recording segment count, reconnect counts in the app log.
6. Pass if the definition of done in `SOW.md` section 1 holds and the acceptance matrix passes.

Battery drain of sustained streaming is not published by Meta and unofficial reports suggest it is fast, so treat the measured value as the planning figure [R].

## 5. Video settings sweep (SOW section 11, row 2)

Record 60-second clips with the same scene at: MEDIUM 24 fps, HIGH 24 fps, HIGH 30 fps, and LOW 30 fps. For each, record delivered width and height, mean fps, mean bitrate, dropped frames, and a visual quality note. Meta states lower resolution and frame rate can look better over Bluetooth because compression is lighter [V]. Choose the default and write the result in `TESTLOG.md`.

## 6. `TESTLOG.md` entry template

```markdown
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
```

## 7. Daily runbook

Before a session:

- [ ] `tailscale status` on server and phone shows both online.
- [ ] `docker ps` shows `mediamtx` up; `systemctl status glasses-telemetry` is active; `curl http://<tailscale-ip>:8000/health` returns ok.
- [ ] Disk free above 15% (`df -h /srv/glasses`).
- [ ] Glasses charged, paired, Developer Mode on, firmware current.
- [ ] Phone: app updated, Tailscale connected, battery optimisation exemption in place, screen timeout irrelevant (app keeps screen on).
- [ ] Anyone who will be recorded has been told. Do not cover the glasses' LED.

After a session:

- [ ] `check_session.py <session_id>` shows no failures.
- [ ] Run the tap calibration if firmware or app changed since the last one.
- [ ] Copy or archive what you need; set retention for the rest.

## 8. End-to-end troubleshooting

| Symptom | First checks | Likely fix |
| --- | --- | --- |
| App shows no glasses | Meta AI app paired? Developer Mode on? Registration done? | Re-register; update glasses firmware |
| Session starts, no video frames | Stream state stuck before `STREAMING`; camera permission; another app using the camera; glasses on the head | Check permission flow (TECH-02 4.1); close Meta AI features that hold the camera |
| Video blocky or low rate | Bluetooth congestion; settings too high | Use MEDIUM or LOW at 24 fps; keep phone near glasses |
| No IMU samples | Firmware below the needed version; Motion stuck in `starting` | Update firmware; watchdog restart (TECH-02 5.2) |
| SRT connect fails | Tailscale, firewall, passphrase | TECH-03 section 6 |
| Audio missing in recording | Audio codec not set on the stream; AAC encoder error | Confirm `settings_applied`; check app log |
| Audio out of sync | Timebase assumption wrong or encoder drift | Spike S3; resync rule in TECH-02 6.2 |
| IMU and video misaligned | Using uncalibrated estimate | Run the tap test (TECH-05 section 6) |
| Receiver loses data after a crash | Part rotation too slow, disk full | Lower `PART_SECONDS`; check disk and logs |
| Reader falls behind | GPU busy; backlog | Lower resolution, drop frames by design, check `dec` utilisation |

## 9. Monitoring (minimal)

- `/health` polled every 30 s by a cron job or Uptime-style check; alert if down.
- Disk free percentage; alert under 15%.
- MediaMTX log lines containing "error" or "panic".
- Per session: `check_session.py` run automatically when a session ends (the receiver can call it on idle close).

## 10. Release hygiene for this personal project

- Tag a git commit whenever a test matrix row first passes; record the tag in `TESTLOG.md`.
- Dependencies pinned: SDK version, MediaMTX image tag, Python lock file, Gradle versions.
- `DECISIONS.md`: one dated line per departure from these documents, with the reason and the evidence (spike output, log excerpt).

## 11. References

- `SOW.md` sections 11 to 14 (acceptance, tests, build order, risks).
- Meta, stream quality note (lower resolution and frame rate look better over Bluetooth): the camera streaming skill page linked in `TECH-02-android-app.md` section 13 [V].
