# SOW: Ray-Ban Meta to GPU Server Streaming

Nov 24, 2026 · @Sai Pawan

This SOW defines how to stream camera, microphone and IMU data from Ray-Ban Meta glasses to a self-hosted GPU server, for personal development use only, so that analytics can be built on the raw data later.

## 1. Objective and scope

**Objective.** Get live camera video, microphone audio and IMU motion data from Ray-Ban Meta glasses onto a GPU server you control, recorded raw and replayable, so analytics can be built on top later.

**In scope**

- An Android phone companion app built on Meta's Wearables Device Access Toolkit (DAT) 1.0.
- A private network link from phone to server (Tailscale).
- A server media ingest that records video and audio unchanged (MediaMTX).
- A telemetry receiver that stores IMU samples with timestamps.
- A GPU hand-off that decodes the recorded or live stream with NVDEC and exposes frames to analytics code.

**Out of scope**

- Production release, app stores, Wearables Developer Center registration, release channels, or any Meta approval. This build runs only in Developer Mode on your own glasses and phone.
- The analytics themselves (detection, ASR, LLM features). This SOW stops at clean, timestamped data on the GPU.
- Reverse-engineered or unofficial links to the glasses, and capture of Instagram, Facebook or WhatsApp live streams.
- Project Aria tooling. It does not work with Ray-Ban Meta.

**Definition of done.** A 30-minute walking session produces a recorded video file, an audio track and an IMU file on the server, all aligned to within the accuracy set in the acceptance table (section 11), and a GPU script can read frames from that session.

## 2. Fixed constraints from Meta's SDK

These limits come from Meta's DAT 1.0 documentation and repositories as of November 2026, and every design choice below follows from them. Any Claude session building this must treat them as fixed, and must re-check exact API names against the SDK repos and sample apps before coding.

| Stream | How you get it | Limits | Status |
| --- | --- | --- | --- |
| Camera video | Camera capability on a device session; stream configuration | 720x1280 portrait (high), 504x896 (medium), 360x640 (low); 2, 7, 15, 24 or 30 fps; fixed 9:16 crop, about 53 degrees horizontal field of view; Android returns decoded YUV by default, or HEVC with compression enabled | Supported |
| Microphone audio, in-stream | Audio codec set on the stream configuration | 16-bit PCM at 16, 44.1 or 48 kHz; not beamformed; timestamped to align with video | Beta, development builds only |
| Microphone audio, HFP | Standard Bluetooth Hands-Free Profile through the phone OS | Mono voice at 16 or 8 kHz, beamformed to the wearer; drops playback to voice quality; must be set up before the camera stream starts | Standard OS feature |
| IMU (Motion) | Motion capability on the session | Accelerometer, gyroscope, magnetometer (may be null), fused orientation quaternion (may be null); 5, 10, 15, 24, 30 or 60 Hz, default 10 Hz as a target only; each sample has a device monotonic timestamp in nanoseconds; 256-sample buffer drops the oldest samples if the app falls behind | Beta, development builds only |
| Photo capture | Photo capture on the stream | Higher resolution than video, slow over Bluetooth | Experimental |

**Behaviour to design around**

- **Video goes over Bluetooth.** Android video currently goes over Bluetooth only (Meta staff confirmed this for SDK 1.0.1; Wi-Fi is an open feature request), so expect lower quality than the glasses' own captures.
- **Lower settings look better.** Meta states that lower resolution and frame rate give higher visual quality because Bluetooth compression is lighter. When bandwidth drops the SDK reduces resolution first, then frame rate, never below 15 fps.
- **One session at a time.** The session pauses when the glasses come off, the hinge closes, the wearer says "Hey Meta", or Bluetooth drops. Treat pauses as gaps, not the end of the stream. Background IMU is not supported in 1.0.x, so the app stays in the foreground.
- **Firmware matters for IMU.** Meta staff report IMU streaming on firmware 129 but not 128. On Android the Motion stream can sit in a starting state indefinitely.
- **Your code runs on the phone.** There is no on-glasses app runtime, and Meta AI is not available to third-party apps. The Meta AI app must stay installed because it is the only way to pair.
- **Developer Mode needs no registration.** The app ID and client token can be set to 0 and attestation is skipped, per the AGENTS.md file in Meta's Android repo.

## 3. Architecture

```text
Ray-Ban Meta --Bluetooth--> Android app (DAT SDK) --SRT (video+audio)--> MediaMTX ----> GPU readers
                                     |                  [Tailscale]       (records)     (NVDEC)
                                     +--WebSocket (IMU, events)--> Telemetry receiver -> Parquet
                                                                   (all on the GPU server)
```

Video and audio leave the phone together in one SRT stream and land in MediaMTX. IMU samples travel separately over a WebSocket to the telemetry receiver. All traffic stays inside the Tailscale network, and the GPU readers decode what MediaMTX records or re-serves.

## 4. Prerequisites

Complete these before any code is written. None needs Meta's approval.

- [ ] Glasses are Ray-Ban Meta Gen 1 or Gen 2. Meta lists Gen 3 as unsupported by the toolkit as of September 2026, so check before using newer hardware.
- [ ] Glasses are paired in the Meta AI app, and the app and glasses firmware are fully updated (aim for firmware 129 or later for IMU).
- [ ] Developer Mode is switched on for the glasses inside the Meta AI app.
- [ ] Phone chosen: Android. You need Android Studio, an Android phone with USB debugging on, and an OS version that meets the Meta AI app's requirements (Meta says the SDK's OS requirements match the Meta AI app's). No paid developer account is needed.
- [ ] Meta's SDK and sample apps cloned from GitHub (links in section 17). The CameraAccess sample in each repo's samples folder is the starting point.
- [ ] Tailscale installed and signed in on the phone and on the GPU server, with both showing in the same tailnet.
- [ ] GPU server has an NVIDIA driver and CUDA working (`nvidia-smi` runs), Docker installed, and about 1 TB of free disk or whatever your retention plan needs. Raw video at 2 to 4 Mbps is roughly 1 to 2 GB an hour.
- [ ] Python 3.11 or later on the server, with `pyarrow`, `fastapi`, `uvicorn` and `av` (PyAV) installable.
- [ ] Optional: confirm the Wearables Developer Center page opens for your account in India. Meta says full toolkit access depends on the glasses' supported countries, and Ray-Ban Meta is sold in India with a developer there reporting Developer Mode working. Meta does not state this for India explicitly.

## 5. Workstream 1: Android companion app

**Goal.** A Kotlin app, run straight from Android Studio onto your phone, that receives video, audio and IMU from the glasses and forwards them to the server over the Tailscale network.

**Start from** Meta's Android SDK repository and its CameraAccess sample. Do not write the pairing or session code from memory: copy the working flow from the sample and the repo's AGENTS.md file.

**Functional requirements**

1. **Registration and session.** Configure the SDK, handle the callback from the Meta AI app, start registration, request the permissions Meta's sample requests (camera, Bluetooth, and microphone only if HFP audio is used), then select the glasses automatically, create a session and wait for it to reach the started state. Use app ID and client token "0" in Developer Mode.
2. **Camera stream.** Turn on the SDK's compressed-video option to receive HEVC. Default to medium resolution (504x896) at 24 fps, with settings that go up to 720x1280 at 30 fps. Lower values can look better over Bluetooth, so keep the settings screen and record the delivered resolution and fps.
3. **In-stream audio.** Request 16-bit PCM at 48 kHz, mono, on the same stream configuration. Store the PCM losslessly as well as the encoded copy sent for live use (48 kHz mono 16-bit is about 768 kbps).
4. **IMU.** Add the Motion capability at 60 Hz. Drain samples continuously on a background coroutine so the 256-sample buffer never overflows, and count dropped or missing samples.
5. **Video uplink.** Mux the HEVC frames without re-encoding into MPEG-TS and send over SRT to the server (primary). RTSP over Tailscale is the fallback. Use a maintained Android SRT client library that supports publish mode and a passphrase, and set a passphrase.
6. **Audio uplink.** Encode PCM to AAC (or Opus) and carry it in the same SRT stream as the video so the server gets one synchronised container.
7. **Telemetry uplink.** Send IMU samples to the server's telemetry receiver (section 8) over a WebSocket, batched every 100 ms, with the fields listed there.
8. **Gap handling.** Treat a paused session as a gap: stop sending, mark the gap, and resume without restarting the app. Reconnect to the server automatically if Tailscale or Wi-Fi drops.
9. **Sessions.** Create a session ID (UUID) on each start, send it with every media and telemetry connection, and show it on screen.
10. **Status screen.** Show glasses connection state, current fps, video bitrate, IMU rate, dropped IMU count, uplink state, and bytes sent. Show a persistent recording indicator so bystanders and you know it is live.
11. **Local log.** Write a small JSON log of session start and stop, pauses, errors and settings to the phone so failures can be diagnosed.

**Project and permissions setup**

- Declare the Bluetooth, internet and foreground-service permissions that Meta's sample and the SRT and WebSocket libraries need, and request them at runtime where Android requires it.
- Install straight from Android Studio over USB debugging. No store account or signing service is needed.
- Keep the screen on and the app in the foreground (or a foreground service) during streaming, since background IMU is unsupported.

**Out of scope for this app.** Meta AI features, face or voice identification, store packaging, and any UI beyond the status and settings screens.

**Done when** the app streams a 10-minute session to the server with video, audio and IMU all arriving, survives one glasses-off pause and one network drop, and matches the acceptance table in section 11.

## 6. Android platform notes

These notes add Android-specific detail to the app requirements in section 5.

- **Stack.** Kotlin with the DAT Android SDK from Maven Central, started from the CameraAccess sample in Meta's Android repository.
- **Video transport is Bluetooth only** in SDK 1.0.1 (Meta staff confirmed; Wi-Fi is an open feature request). Expect lower video quality than the glasses' own captures, so default to the medium (504x896) or low settings and test which looks best.
- **Video format.** The default output is decoded YUV 4:2:0. Either turn on the compressed-video option and forward the HEVC, or encode with MediaCodec to H.264 or H.265 before the SRT uplink.
- **IMU.** The Motion capability can stall in a starting state on some firmware. Add a 10-second timeout, log it, and retry the session once.
- **Foreground service.** Run streaming in a foreground service with a persistent notification to keep it alive, since background IMU is still unsupported.
- **Account.** No paid developer account is needed. Enable USB debugging and install straight from Android Studio.

Telemetry, session IDs, status screen, gap handling and acceptance criteria are as in sections 5, 8 and 11.

**Android-specific requirements to add**

- **Stream defaults.** Start at 504x896 and 24 fps, and offer 720x1280 at 30 fps as a setting. Over Bluetooth the SDK lowers resolution first and then frame rate (never below 15 fps) when bandwidth drops, so record the delivered resolution and fps in `session.json`.
- **SRT on Android.** Use a maintained Android SRT client library, for example one built on libsrt, that supports publish mode and a passphrase. Record which library was chosen and why in the README.
- **Battery and background.** Ask the user to exempt the app from battery optimisation, keep the screen on during a session, and keep the foreground service running, because background IMU is unsupported.
- **Known Android issues.** Meta's repository has reports of random stream failures and of Motion sitting in a starting state, so log every state change and add automatic session restart (at most once a minute).

**Done when** the Android app streams a 10-minute session to the server with video, audio and IMU all arriving, survives one glasses-off pause and one network drop, and meets the acceptance table in section 11 at the Bluetooth settings you chose.

## 7. Workstream 3: server ingest

**Goal.** A MediaMTX server on the GPU machine accepts the phone's SRT stream over Tailscale, records it to disk without re-encoding, and re-serves it over RTSP for GPU consumers.

**Steps**

1. **Network.** Install Tailscale on the server, note its tailnet IP, and allow ports 8890 (SRT), 8554 (RTSP) and 8000 (telemetry, section 8) only on the `tailscale0` interface. Do not expose these ports on the public interface. Tailscale works behind the carrier-grade NAT common on Indian home connections, so no public IP is needed.
2. **Run MediaMTX** from the official image, with host networking so UDP for SRT works cleanly:

```bash
docker run -d --name mediamtx --restart unless-stopped --network=host \
  -v $PWD/mediamtx.yml:/mediamtx.yml \
  -v $PWD/recordings:/recordings \
  bluenviron/mediamtx
```

3. **Configure** `mediamtx.yml` as below. Key names should be checked against the MediaMTX documentation for the version you pull, since options change between releases.

```yaml
logLevel: info
api: no
metrics: no
rtmp: no
hls: no
webrtc: no
rtsp: yes
rtspAddress: :8554
srt: yes
srtAddress: :8890

pathDefaults:
  record: yes
  recordPath: /recordings/%path/%Y-%m-%d_%H-%M-%S-%f
  recordFormat: fmp4
  recordPartDuration: 1s
  recordSegmentDuration: 15m
  recordDeleteAfter: 0s   # keep forever; set e.g. 168h to expire

paths:
  glasses:
    source: publisher
    srtPublishPassphrase: "replace-with-a-random-16-to-79-char-secret"
```

4. **Publish URL for the phone app:**

```text
srt://<server-tailscale-ip>:8890?streamid=publish:glasses&passphrase=<secret>&pkt_size=1316
```

5. **Smoke test before the app exists.** From any machine on the tailnet, publish a synthetic HEVC and AAC stream that matches the glasses format:

```bash
ffmpeg -re -f lavfi -i testsrc=size=720x1280:rate=30 -f lavfi -i sine=frequency=440 \
  -c:v libx265 -b:v 3M -c:a aac -f mpegts \
  "srt://<server-tailscale-ip>:8890?streamid=publish:glasses&passphrase=<secret>&pkt_size=1316"
```

6. **Verify.** A new `.mp4` segment appears under the recordings folder; `ffprobe` shows one HEVC video stream (720x1280) and one AAC audio stream; `ffplay rtsp://<server-tailscale-ip>:8554/glasses` plays the live stream.

**Operational requirements**

- Auto-restart on reboot (the `--restart unless-stopped` flag above).
- Recordings are never transcoded. Raw HEVC and AAC land on disk exactly as received.
- A cron job or a retention setting removes old recordings only when you decide on a retention period.
- Optional alternatives if SRT proves awkward on the phone: RTSP publish over Tailscale, or RTMP to port 1935.

**Done when** the smoke test records a playable file and a second machine can play the live RTSP stream.

## 8. Workstream 4: telemetry receiver

**Goal.** A small Python service on the server that receives IMU samples and session events from the phone over a WebSocket and writes them to Parquet files, one folder per session.

**Stack.** Python 3.11 or later, FastAPI with uvicorn for the WebSocket, PyArrow for Parquet, Pydantic for message validation. Run it with systemd or Docker, listening on port 8000 on the Tailscale interface only.

**Endpoint.** `ws://<server-tailscale-ip>:8000/ingest/{session_id}?token=<shared-secret>`. Reject connections with a wrong token. Accept reconnects for the same session ID and append to the same files.

**Message format.** JSON text frames (switch to protobuf only if volume demands it). Two message types:

```json
{
  "type": "imu_batch",
  "session_id": "b6f0c2e4-...",
  "phone_wall_ms": 1764000000123,
  "samples": [
    {
      "timestamp_ns": 123456789012345,
      "accel": [0.01, 9.81, 0.02],
      "gyro": [0.001, 0.0, -0.002],
      "mag": [22.1, -3.4, 40.0],
      "quat": [0.99, 0.01, 0.0, 0.02]
    }
  ]
}
```

```json
{
  "type": "event",
  "session_id": "b6f0c2e4-...",
  "phone_wall_ms": 1764000005000,
  "event": "session_paused",
  "detail": "glasses_removed"
}
```

Samples keep the glasses' monotonic `timestamp_ns` unchanged. `mag` and `quat` may be null. `phone_wall_ms` is the phone's wall clock when the batch was sent, used later for alignment. Event names to support: `session_started`, `session_paused`, `session_resumed`, `session_stopped`, `video_started`, `audio_started`, `imu_started`, `uplink_reconnect`, `error`.

**Storage layout.** Under `data/<session_id>/`:

| File | Contents |
| --- | --- |
| `imu.parquet` | One row per sample: `timestamp_ns`, `recv_wall_ms`, `phone_wall_ms`, accel x/y/z, gyro x/y/z, mag x/y/z, quat w/x/y/z |
| `events.parquet` | One row per event: `recv_wall_ms`, `phone_wall_ms`, `event`, `detail` |
| `session.json` | Session ID, start and end wall times, app version, stream settings reported by the phone |

Write Parquet in row groups flushed every 5 seconds or 1,000 rows, so a crash loses seconds, not the session. Add a `recv_wall_ms` column on arrival using the server clock.

**Also required**

- `GET /health` returning service status and active session count.
- Logging of connection, disconnection and rejected frames.
- A tiny CLI or Python helper `load_session(session_id)` that returns the IMU and event data as pandas DataFrames.
- A test client that replays a synthetic 60 Hz IMU stream for 5 minutes.

**Done when** the test client's 5-minute stream produces a Parquet file with the expected row count (within 1%), and killing and restarting the service mid-stream loses less than 10 seconds of data.

## 9. Workstream 5: GPU hand-off

**Goal.** Prove that recorded and live streams reach GPU memory as frames and audio samples, so analytics code can start from a clean interface. No analytics are built here.

**Deliverable A: offline reader.** A Python module `glasses_reader.py` that, given a session ID, opens the recorded fMP4 segments in order and yields `(frame_tensor, pts_seconds)` on the GPU. Use PyAV or FFmpeg with CUDA hardware decoding (`hevc_cuvid`), or NVIDIA's PyNvVideoCodec, and return CUDA tensors for PyTorch. It must also decode the audio track to 16 kHz mono float PCM for speech models, and join frames to IMU rows by nearest timestamp using the alignment rules in section 10.

**Deliverable B: live reader.** A second function that reads `rtsp://localhost:8554/glasses` from MediaMTX and yields the same `(frame_tensor, pts_seconds)` pairs. Two acceptable implementations:

- **GStreamer with NVDEC:** `rtspsrc latency=200 ! rtph265depay ! h265parse ! nvh265dec ! appsink`, then map frames to CUDA tensors.
- **DeepStream:** `nvurisrcbin` with the RTSP URI. Tune its `latency` (jitter buffer, default 100 ms) and `drop-on-latency`. If UDP jitter causes visual artefacts, set `select-rtp-protocol=4` to force TCP.

**Requirements**

- Hardware decode must be confirmed in use, for example by `nvidia-smi dmon -s u` showing decoder utilisation while a stream plays.
- The live reader reconnects automatically if the RTSP stream drops and resumes when the phone resumes, and marks the gap rather than failing.
- A demo script saves one JPEG per second from the live stream and prints decoder fps and end-to-end delay estimates.
- Frames are 720x1280 portrait, HEVC, up to 30 fps, matching section 2. The reader reads size and fps from the stream rather than hard-coding them, because the SDK may lower resolution or frame rate under load.

**Expected delay.** Glasses to server is roughly 150 to 300 ms with WebRTC and 300 to 800 ms with SRT or RTSP plus jitter buffers. These are engineering estimates, not Meta figures, so the demo script must measure the real number (see the clock tap test in section 10).

**Done when** both readers print frames with correct size and increasing timestamps for 10 minutes without a crash, and GPU decoder utilisation is non-zero.

## 10. Clock sync and data layout

Video and audio carry timestamps that Meta documents as aligned with each other. The IMU `timestamp_ns` comes from the glasses' monotonic clock, and **Meta does not document whether it shares a clock with the video and audio timestamps.** The design therefore records enough anchors to align after the fact, and includes a calibration test to measure the offset.

**Timestamps to record**

- Video and audio presentation timestamps inside the SRT stream, kept as sent.
- IMU `timestamp_ns`, kept as sent, never rewritten.
- Phone wall-clock time (`phone_wall_ms`) at the moment the phone receives the first video frame, first audio frame and first IMU sample, sent as events (`video_started`, `audio_started`, `imu_started`), and again on every batch.
- Server receive time (`recv_wall_ms`) on every row. Run `chrony` or systemd-timesyncd on the server and keep the phone on automatic network time.

**Calibration test (run once per firmware or app change).** Start a session and tap the glasses frame firmly with a finger three times, one second apart, in view of the camera. Each tap produces a visible camera jolt, an audible click and an IMU spike. The server tool `estimate_offset(session_id)` finds the three events in each stream (gyro magnitude peaks, audio energy peaks, frame-difference peaks) and reports the median offset between IMU and video time, and between audio and video time, in milliseconds. Save the result in `session.json`. Aim for the acceptance figure in section 11.

**Linking a session to its video files.** MediaMTX names recordings by server wall-clock start time. The phone's `video_started` event gives the session start in server time, so a session's video is the segments whose start times fall between that event and `session_stopped`. The `load_session` helper (section 8) must return the matching file list.

**Folder layout**

```text
recordings/glasses/2026-11-24_14-40-01-123456.mp4   # MediaMTX segments, raw HEVC + AAC
data/<session_id>/imu.parquet
data/<session_id>/events.parquet
data/<session_id>/session.json                       # includes calibration offsets
data/<session_id>/audio.wav                          # optional lossless PCM, if the phone sends it
```

**Rules**

- Never modify or transcode raw files. Derived outputs (JPEGs, resampled audio, aligned tables) go in `derived/<session_id>/`.
- Gaps from pauses are real: tables and readers must preserve them, not interpolate across them.
- Keep all times in UTC and in integer milliseconds or nanoseconds.

## 11. Deliverables and acceptance criteria

The numeric targets below are proposed starting points for a personal build, not Meta figures. Adjust them after the first measured session.

| # | Deliverable | Acceptance test |
| --- | --- | --- |
| 1 | Android app (Android Studio project, README) | 10-minute session shows video, audio and IMU arriving at the server; status screen values match server logs |
| 2 | Video settings sweep | Short clips recorded at 504x896 24 fps, 720x1280 24 fps and 720x1280 30 fps; quality, bitrate and dropped frames noted in TESTLOG.md and the best default chosen |
| 3 | MediaMTX config and run script | Smoke test (section 7) records a playable file with HEVC video and AAC audio; live RTSP plays from a second machine |
| 4 | Telemetry receiver service | 5-minute synthetic 60 Hz stream stored with row count within 1% of expected; restart mid-stream loses under 10 seconds |
| 5 | Video frame rate | Recorded video averages at least 90% of the requested fps over 10 minutes at 720x1280 |
| 6 | IMU rate | Recorded IMU rows average at least 90% of the requested rate (54 Hz at 60 Hz setting); dropped samples are counted and reported |
| 7 | Audio | Recorded audio has no gaps over 200 ms outside pauses, and is audible and in sync with a clap test |
| 8 | Glasses-off pause | Taking the glasses off and putting them on resumes all three streams without restarting the app; the gap appears in `events.parquet` |
| 9 | Network drop | Switching the phone off Wi-Fi for 30 seconds and back resumes uplink within 15 seconds; later data is intact |
| 10 | Clock calibration | `estimate_offset` reports IMU-to-video and audio-to-video offsets; the residual spread across the three taps is under 50 ms |
| 11 | GPU readers | Offline and live readers run 10 minutes with correct frame size and increasing timestamps; decoder utilisation is non-zero |
| 12 | Glasses-to-server delay | Measured with the tap test and reported in milliseconds; no pass or fail threshold, because Meta publishes no figure |
| 13 | Documentation | README for each component with setup steps, config, how to run tests, and the known limits in section 14 |

## 12. Test plan

Run the tests in this order, so each layer is proven before the next one depends on it.

1. **Server without glasses.** Publish the synthetic ffmpeg stream (section 7) and the synthetic IMU client (section 8). Confirm recording, RTSP playback, Parquet output and the GPU readers.
2. **Phone app with Meta's Mock Device Kit.** The kit feeds a phone camera or a video file in place of the glasses, so uplink, SRT, session handling and telemetry can be debugged without wearing them.
3. **Phone app with the real glasses, indoors, on the desk.** Check pairing, Developer Mode, that frames arrive, and that IMU samples begin within 10 seconds.
4. **Settings sweep.** Try 720x1280 at 30 fps, 720x1280 at 24 fps and 504x896 at 30 fps. Record a short clip of each, and note visual quality, bitrate and dropped frames. Pick the best default.
5. **Pause and drop tests.** Take the glasses off and on; switch the phone off Wi-Fi for 30 seconds; say "Hey Meta" mid-stream. Confirm each shows as a gap and the app recovers.
6. **Calibration test** (section 10) and a clap test for audio sync.
7. **Walking test, 30 minutes outdoors,** with cellular uplink if relevant, then run the definition of done in section 1.
8. **Battery check.** Note glasses battery drop per 10 minutes of streaming. Meta publishes no figure and unofficial reports suggest sustained streaming drains the battery quickly, so plan sessions accordingly.

Every test writes a short result into a `TESTLOG.md` in the repo: date, firmware version, app version, settings, pass or fail, and notes.

## 13. Build order

Each phase ends with something that works, so a stall in a later phase never loses the earlier gains.

1. **Phase 0, prerequisites** (section 4). Pair glasses, enable Developer Mode, set up Tailscale and the GPU server basics.
2. **Phase 1, server ingest** (section 7). MediaMTX running, smoke test recording. Also build the synthetic IMU client and the telemetry receiver (section 8). These can be built and tested with no phone.
3. **Phase 2, Meta's Android sample first.** Build and run the CameraAccess sample from Meta's Android repository on your phone with the glasses. Live video on the phone screen proves pairing, Developer Mode and the Bluetooth link before any uplink code exists. VisionClaw is an open-source example with an Android version, but it uses WebRTC with Gemini, so treat it as a reference, not a drop-in.
4. **Phase 3, custom Android app** (sections 5 and 6). Starting from the sample, add HEVC video over SRT first, then PCM audio, then IMU over the WebSocket. Test with the Mock Device Kit before the glasses.
5. **Phase 4, GPU readers** (section 9), built in parallel with phase 3 using the synthetic recordings from phase 1.
6. **Phase 5, calibration and acceptance** (sections 10 to 12). Run the full test plan and fill in the acceptance table.
7. **Phase 6, tune settings.** Compare resolution, frame rate and uplink options once the first pipeline works, and record the best defaults in TESTLOG.md.
8. **Phase 7, hardening, only if you hit problems.** Add reconnect refinements, on-phone buffering when the uplink drops, adaptive bitrate, and a WebRTC or LiveKit path if you need delay under about 300 ms. None is needed for a first working pipeline.

**Suggested effort.** Phases 0 to 2 are about a day. Phases 3 to 5 are about one to two weeks for one developer working with Claude.

## 14. Risks and unknowns

| Risk or unknown | Effect | Mitigation |
| --- | --- | --- |
| IMU and in-stream audio are beta APIs and may change before general availability | Code may need rework after an SDK update | Pin the SDK version, read the repo CHANGELOG before upgrading, keep SDK calls in one wrapper file |
| IMU clock relationship to video and audio is undocumented | Streams may be misaligned by an unknown offset | Record anchors and run the calibration test (section 10) |
| Camera output is a fixed 9:16 crop at 720x1280, about 53 degrees field of view, well below the glasses' native capture | Less scene coverage and detail than the glasses' own photos and videos | Accept as a hard limit; no fix exists in the SDK |
| Android video is Bluetooth only | Lower video quality than the glasses' own captures | Use lower settings and measure which looks best |
| Battery optimisation can stop the app in the background | Streaming stops without a visible error | Exempt the app from battery optimisation, keep the screen on, and use a foreground service |
| IMU needs firmware 129 or later; can stall on Android | No IMU data | Update firmware; add a 10-second timeout and one retry |
| Gen 3 glasses were reported unsupported by the toolkit as of September 2026 | Wrong hardware | Use Gen 1 or Gen 2, or re-check Meta's FAQ for your model |
| Sessions pause when glasses come off or Bluetooth drops; no background IMU | Gaps in data; app must stay in the foreground | Handle gaps explicitly; keep the screen on |
| Sustained streaming drains the glasses battery quickly | Short sessions | Measure it in the test plan (section 12); carry a charging case |
| India developer access is inferred, not stated by Meta | Possible blocker at Developer Mode or Developer Center step | Test early in phase 0; ask in Meta's SDK GitHub discussions if blocked |
| Random stream failures reported on Android | Long sessions may crash | Run the 30-minute soak test; restart sessions on a timer if needed |
| Latency, bandwidth and battery figures are estimates | Plans based on them may be off | Measure on your own network and glasses |

## 15. Privacy and Meta rules to design around

This is a high-level engineering summary, not legal advice. Take legal advice before collecting other people's images or voices for anything beyond personal use.

**Meta's developer rules** (Wearables Developer Acceptable Use Policy)

- Do not covertly record. The policy forbids apps that surreptitiously collect recognisable audio or video of the user or others.
- Do not cover, bypass or obscure the capture LED. It lights whenever the camera is in use, and Meta has disabled cameras on glasses with tampered LEDs.
- Do not use the sensors to identify people by face or voice without Meta's prior approval. Face or voice recognition features are therefore out of scope for this build.

**India's DPDP Act, 2023 and DPDP Rules, 2025**

- The Rules were notified on 14 November 2025 with an 18-month phased timeline. Core duties (notice, consent, security safeguards, breach reporting, data-principal rights) apply from 13 May 2027.
- The Act does not apply to personal data processed by an individual for a personal or domestic purpose. A purely personal recorder may fall outside it.
- If you later process bystanders' faces or voices for a business, research project or shared service, you are likely a Data Fiduciary and need notice, consent, purpose limits, security, retention limits and deletion.
- Separately, Indian law penalises capturing private images or voyeurism. Do not record in restrooms, changing areas, private homes without consent, or courtrooms.

**Engineering requirements that follow**

- The app shows a persistent on-screen recording indicator and never hides the LED.
- Traffic is encrypted end to end: Tailscale for the network, an SRT passphrase for video, and a shared token for telemetry. No endpoint is exposed to the public internet.
- Storage is on a server you physically control, with disk encryption and a defined retention period.
- No face, voice or person identification is built into the ingest or reader code.
- If you ever share a dataset, blur faces and remove audio of people who have not consented, at the ingest or export step.

## 16. Ready-to-paste prompts for Claude

Export this SOW (Markdown or Word) and attach it to each prompt. Use a separate Claude Code session for each: server first, then the phone app. Ask Claude to confirm the exact SDK and MediaMTX API names against the repos and docs in section 17, since those change.

**Prompt A: server side (workstreams 3, 4 and 5)**

```text
I have attached a statement of work (SOW) for streaming Ray-Ban Meta glasses data to my GPU server. Build the server side exactly as specified in sections 7, 8, 9 and 10, and meet the acceptance tests in section 11 that apply to the server.

Deliver, in one repository:
1. mediamtx/ : mediamtx.yml, a docker run script, and a ufw or firewall script that exposes ports 8890, 8554 and 8000 only on the tailscale0 interface.
2. telemetry/ : a FastAPI WebSocket service for IMU batches and events, writing Parquet per session as in section 8, with /health, a load_session helper, systemd unit and Dockerfile.
3. tools/ : a synthetic test publisher (ffmpeg script, section 7), a synthetic 60 Hz IMU test client, and estimate_offset(session_id) from section 10.
4. gpu/ : glasses_reader.py with the offline and live readers from section 9, and a demo script that saves one JPEG per second and prints decode fps.
5. README.md for each folder and a top-level TESTLOG.md template.

Rules: never transcode raw recordings; use Python 3.11 or later; keep secrets in an .env file, not in code; verify MediaMTX config key names against the MediaMTX documentation for the version you use and tell me any key you had to change. Run the smoke tests you can run here, and list the ones I must run on my GPU server. Stop and ask me before making any design change that departs from the SOW.
```

**Prompt B: Android app**

```text
I have attached a statement of work (SOW) for streaming Ray-Ban Meta glasses data to my GPU server. Build the Android companion app described in sections 5 and 6, within the constraints in section 2, and meet the app-relevant acceptance tests in section 11.

Start from the CameraAccess sample in Meta's meta-wearables-dat-android repository and its AGENTS.md; copy the working registration and session flow rather than inventing API calls. Use Developer Mode with app ID and client token 0, and the Mock Device Kit so I can test without glasses.

Implement a Kotlin app (Jetpack Compose is fine) with: video over Bluetooth, with a settings screen defaulting to 504x896 at 24 fps and selectable up to 720x1280 at 30 fps, using the SDK's compressed (HEVC) video option and forwarding it without re-encoding; in-stream PCM audio at 48 kHz mono encoded to AAC; Motion (IMU) at 60 Hz drained on a background coroutine, with dropped-sample counting and a 10-second start timeout with one retry; SRT uplink of video and audio to srt://<server-tailscale-ip>:8890 with streamid=publish:glasses and the passphrase from a settings screen, using a maintained Android SRT library that supports publish mode and a passphrase (tell me which you chose and why); an IMU WebSocket uplink to ws://<server-tailscale-ip>:8000/ingest/{session_id} using the JSON schema in section 8; pause and reconnect handling; a foreground service with a persistent notification; a status screen and persistent recording indicator; and a local JSON log.

If the SDK only gives decoded YUV frames and compressed output is unavailable, say so and use MediaCodec to encode H.265 (or H.264) before SRT. Explain every manual step I must do in Android Studio and on the phone (USB debugging, permissions, Bluetooth, battery-optimisation exemption). Do not add face or voice identification, and do not hide the glasses' LED or any recording indicator. Stop and ask me before any design change that departs from the SOW.
```

## 17. References

These links were gathered during research on 24 November 2026. The SDK is moving fast, so any Claude session building from this SOW should open the current repo README, AGENTS.md and docs pages first and trust them over this document where they differ.

| Source | Used for |
| --- | --- |
| [Meta Wearables Device Access Toolkit FAQ](https://developers.meta.com/wearables/faq/) | Status of DAT 1.0, supported glasses, country availability |
| [Wearables developer docs](https://wearables.developer.meta.com/docs/develop/) | Main documentation entry point |
| [Motion (IMU) docs](https://wearables.developer.meta.com/docs/develop/dat/motion/) | IMU sample fields, rates, buffer, beta status |
| [Microphones and speakers docs](https://wearables.developer.meta.com/docs/develop/dat/microphones-and-speakers/) | In-stream PCM audio and HFP audio |
| [Mock Device Kit docs](https://wearables.developer.meta.com/docs/develop/dat/mock-device-kit/) | Testing without glasses |
| [Introducing the toolkit (Meta blog)](https://developers.meta.com/blog/introducing-meta-wearables-device-access-toolkit/) | Background and announced capabilities |
| [Wearables Developer Acceptable Use Policy](https://wearables.developer.meta.com/acceptable-use-policy/) | Recording, LED and identification rules |
| [meta-wearables-dat-android (GitHub)](https://github.com/facebook/meta-wearables-dat-android) | Android SDK and sample |
| [Android AGENTS.md](https://github.com/facebook/meta-wearables-dat-android/blob/main/AGENTS.md) | Developer Mode and setup on Android |
| [Android issue 174 and issues list](https://github.com/facebook/meta-wearables-dat-android/issues) | Bluetooth-only video, Wi-Fi feature request |
| [Android issue 150](https://github.com/facebook/meta-wearables-dat-android/issues/150) | Developer in India with Developer Mode working |
| [Android issue 140](https://github.com/facebook/meta-wearables-dat-android/issues/140) | Field of view discussion |
| [VisionClaw](https://github.com/Intent-Lab/VisionClaw) | Example of WebRTC streaming from the glasses, with an Android version |
| [MediaMTX](https://github.com/bluenviron/mediamtx) | Ingest server: SRT, RTSP, RTMP, WebRTC, recording |
| [DeepStream nvurisrcbin docs](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvurisrcbin.html) | RTSP input, latency and protocol options |
| [Project Aria Gen 2 tools](https://facebookresearch.github.io/projectaria_tools/gen2/) | Research glasses, not compatible with Ray-Ban Meta |
| [Meta newsroom: Ray-Ban Meta in India](https://about.fb.com/news/2025/10/new-meta-ai-features-makes-ray-ban-glasses-in-india-more-useful-fun/) | Availability and Meta AI features in India |
| [Frames and Features: India](https://www.framesandfeatures.com/countries/in/) | Supported-country tracking by model |
| [DPDP Act FAQs (Cyril Amarchand Mangaldas)](https://www.cyrilshroff.com/wp-content/uploads/2025/12/FAQs-DPDPA.pdf) | India data protection rules and timeline |
