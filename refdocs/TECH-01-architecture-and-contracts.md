# TECH-01: Architecture and Data Contracts

Companion to `SOW.md`. This document is the authority for **schemas, ports, names, folder layout and the time model**. Every other TECH document refers back to it. Where this document and `SOW.md` differ on a detail, this document wins (see "Precedence" in `CLAUDE.md`).

Last verified against Meta's public docs on 24 Nov 2026.

## 0. Evidence legend

Every technical claim in the TECH documents carries a tag. Trust them in this order.

| Tag | Meaning | What to do |
| --- | --- | --- |
| **[V]** | Verified in Meta's or the tool vendor's own documentation, read on 24 Nov 2026 | Use as written; still confirm against the SDK version you pin |
| **[R]** | Reported by Meta staff, GitHub issues, or community sources; not in official docs | Treat as likely; confirm with a spike |
| **[A]** | Assumption or engineering judgment; not verified | Must be proven by a spike before it is relied on |

Never invent SDK class or method names. If a name is not in this document, read it from the pinned SDK's source or sample app.

## 1. System context

```text
                    Bluetooth                      Tailscale (WireGuard) private network
 +--------------+  (Android: BT Classic)  +----------------------+            +-------------------------------------------+
 | Ray-Ban Meta | ----------------------> |  Android phone app   |  SRT/UDP   |  GPU SERVER                               |
 |  camera      |   video (HEVC or YUV)   |  (Kotlin, Meta DAT)  | ---------> |  MediaMTX :8890 --> records fMP4 to disk  |
 |  mic         |   audio (PCM)           |                      |  video+    |        |                                  |
 |  IMU         |   IMU samples           |  SDK -> mux -> uplink|  audio     |        +--> RTSP :8554 --> GPU readers   |
 +--------------+                         |                      |            |                                           |
                                          |                      |  WebSocket |  Telemetry receiver :8000 --> Parquet     |
                                          |                      | ---------> |  (IMU batches, events, optional raw PCM)  |
                                          +----------------------+  TCP       +-------------------------------------------+
```

## 2. Components and responsibilities

| Component | Runs on | Owns | Must not |
| --- | --- | --- | --- |
| Android app (`android/`) | Phone | SDK session, capture of video/audio/IMU, muxing, uplinks, local log, status UI | Transcode more than necessary; hide recording indicators; identify people |
| MediaMTX (`server/mediamtx/`) | GPU server | SRT ingest, recording raw video+audio, RTSP re-serve | Transcode recordings |
| Telemetry receiver (`server/telemetry/`) | GPU server | IMU and event storage, session registry, optional raw PCM | Rewrite or smooth sensor values |
| GPU readers (`gpu/`) | GPU server | Decode to GPU memory, align streams, expose a clean Python API | Contain analytics models |
| Tools (`tools/`) | Any tailnet machine | Synthetic publishers, calibration, offset estimation | Ship in production paths |

## 3. Network, ports and security

| Port | Proto | Service | Reachable from | Notes |
| --- | --- | --- | --- | --- |
| 8890 | UDP | MediaMTX SRT ingest | tailnet only | Publisher needs passphrase |
| 8554 | TCP | MediaMTX RTSP out | localhost and tailnet | Readers use `rtsp://127.0.0.1:8554/glasses` |
| 8000 | TCP | Telemetry receiver (HTTP + WebSocket) | tailnet only | Shared-secret token |
| 8000 | UDP | MediaMTX RTP (if RTSP over UDP is enabled) | localhost | Different protocol from the telemetry port; no clash, but see TECH-03 |

Security rules:

1. No port is exposed on the public interface. Firewall rules allow these ports only on `tailscale0` (and `lo` for localhost readers).
2. The SRT publish passphrase (10 to 79 characters) and the WebSocket token are random, stored in `.env` on the server and in the phone's settings, never in source control.
3. Tailscale gives encrypted transport between phone and server. SRT encryption is an additional layer, not a replacement.
4. Disk encryption on the recording volume is recommended. Retention is set explicitly (see TECH-03).

## 4. Streams, formats and timestamps

| Stream | Source (SDK) | On the wire phone to server | Timestamp the SDK gives | Clock |
| --- | --- | --- | --- | --- |
| Video | `Stream.videoStream: Flow<VideoFrame>` [V] | HEVC in MPEG-TS over SRT [A] | `VideoFrame.presentationTimeUs` [V] | Stream clock (microseconds) |
| Audio | `Stream.audioStream`, 16-bit PCM [V] | AAC-LC in the same MPEG-TS [A]; optional raw PCM on a WebSocket | `presentationTimeUs` [V]; Meta states it aligns with video [V] | Stream clock |
| IMU | `Motion.samples: SharedFlow<MotionSample>` [V] | JSON over WebSocket | `MotionSample.timestampNs`, device monotonic clock [V] | Glasses monotonic clock; relation to stream clock **undocumented** [V] |

Settings that apply by default (see SOW sections 2 and 5): video medium (504x896) at 24 fps with a setting up to 720x1280 at 30 fps [V: values exist]; audio PCM 48 kHz mono [V: rate options exist]; IMU 60 Hz [V: rate exists, target only].

### 4.1 The clocks

| Name | Source | Used for |
| --- | --- | --- |
| `stream_pts` | `presentationTimeUs` on video and audio frames | Aligning video with audio. Assumed shared; verify in Spike S3 |
| `imu_ns` | `MotionSample.timestampNs` | Ordering IMU samples and computing intervals. Never wall-clock |
| `phone_elapsed_ns` | `SystemClock.elapsedRealtimeNanos()` on the phone | Linking the three above through arrival times |
| `phone_wall_ms` | `System.currentTimeMillis()` on the phone | Cross-checking with server time |
| `recv_wall_ms` | Server clock at receipt | Linking to MediaMTX recording file names |

**Rule:** raw timestamps from the SDK are stored exactly as received. Alignment is computed later from anchors and calibration (TECH-05). Nothing in the pipeline rewrites them.

### 4.2 Clock anchors

At the first video frame, first audio frame and first IMU sample of every session, and again after every resume, the app emits a `clock_anchor` event (section 5.2) carrying the stream's own first timestamp and the phone's `phone_elapsed_ns` and `phone_wall_ms` at the moment of arrival. This lets the server estimate each stream's offset from phone time, within the Bluetooth arrival jitter. The calibration tap test (TECH-05) measures the remaining offset between IMU and video.

## 5. Message contracts (phone to server)

All JSON, UTF-8, one message per WebSocket text frame. Every message has `schema_version` (integer, currently `1`), `session_id` (UUID string), and `seq` (integer, starts at 0 and increases by 1 per message per session, shared across `imu_batch` and `event` so gaps are detectable).

### 5.1 `imu_batch`

```json
{
  "schema_version": 1,
  "type": "imu_batch",
  "session_id": "b6f0c2e4-8a31-4d0e-9b57-0a1c2d3e4f50",
  "seq": 1042,
  "phone_wall_ms": 1764000000123,
  "phone_elapsed_ns": 98765432100000,
  "dropped_since_last": 0,
  "samples": [
    {
      "timestamp_ns": 123456789012345,
      "source": "glasses",
      "accel": [0.01, 9.81, 0.02],
      "gyro": [0.001, 0.0, -0.002],
      "mag": [22.1, -3.4, 40.0],
      "quat": [0.99, 0.01, 0.0, 0.02]
    }
  ]
}
```

Field rules:

- `timestamp_ns`: integer, unchanged from `MotionSample.timestampNs` [V]. Always present.
- `accel`: metres per second squared, includes gravity [V]. `gyro`: radians per second [V]. `mag`: microtesla [V]. Each is `[x, y, z]` or `null`.
- `quat`: `[w, x, y, z]` with `w` the scalar [V] (SDK `orientation`), or `null`.
- `source`: `"glasses"`, `"neural_band"` or `"unknown"` [V: enum exists with these meanings]. Store it; do not filter silently.
- A missing reading is `null`; never drop the whole sample because one reading is null [V].
- `dropped_since_last`: count of samples the app knows it lost (buffer overflow or gaps in `timestamp_ns` larger than 3 times the nominal interval).
- Batching: flush every 100 ms or 64 samples, whichever is first.

### 5.2 `event`

```json
{
  "schema_version": 1,
  "type": "event",
  "session_id": "b6f0c2e4-8a31-4d0e-9b57-0a1c2d3e4f50",
  "seq": 1043,
  "phone_wall_ms": 1764000005000,
  "phone_elapsed_ns": 98770432100000,
  "event": "session_paused",
  "detail": {"reason": "glasses_removed"}
}
```

Event names (string enum, all lowercase snake case):

| Event | When | `detail` |
| --- | --- | --- |
| `session_started` | SDK session reached started state | `{"app_version", "sdk_version", "device_model", "android_version"}` |
| `settings_applied` | Stream or IMU configuration applied | `{"video_quality", "frame_rate", "audio_rate_hz", "audio_channels", "imu_rate_hz", "compressed_video"}` |
| `video_started` | First video frame received | `{"width", "height", "is_compressed"}` |
| `audio_started` | First audio frame received | `{"sample_rate_hz", "channels"}` |
| `imu_started` | First IMU sample received | `{"rate_hz"}` |
| `clock_anchor` | First frame or sample of a stream after start or resume | `{"stream": "video\|audio\|imu", "first_pts_us" \| "first_timestamp_ns": int}` |
| `stream_state` | `StreamState` changed | `{"state"}` |
| `motion_state` | Motion state changed | `{"state"}` |
| `session_paused` / `session_resumed` | SDK pause or resume | `{"reason"}` if known |
| `uplink_connected` / `uplink_reconnect` | SRT or WebSocket (re)connected | `{"channel": "srt\|ws", "attempt"}` |
| `imu_error` | `MotionError` received | `{"error"}` |
| `warning` | Recoverable problem | `{"message"}` |
| `error` | Unrecoverable problem for the session | `{"message"}` |
| `session_stopped` | Session ended | `{"reason", "stats": {...}}` |

### 5.3 Optional raw PCM channel

Endpoint `ws://<host>:8000/pcm/{session_id}?token=...`. Binary frames, each: 8-byte little-endian `presentation_time_us`, 4-byte little-endian `byte_count`, then `byte_count` bytes of 16-bit little-endian PCM. The server appends to `audio.wav` (header rewritten on close) and writes a sidecar `audio_pts.csv` of `(presentation_time_us, byte_offset)` rows. Sample rate and channel count come from the `settings_applied` event.

### 5.4 Connection protocol

1. App opens `ws://<host>:8000/ingest/{session_id}?token=<secret>`.
2. Server replies with a JSON text frame `{"type": "hello", "schema_version": 1, "next_seq": <int>}`. `next_seq` is 0 for a new session, or one more than the highest `seq` stored for a reconnect.
3. App resends any unacknowledged messages with `seq >= next_seq`, then continues. The server acks every 50 messages with `{"type": "ack", "seq": <int>}`. The app may discard messages up to the acked `seq`.
4. Duplicate `seq` values are ignored by the server. A `seq` gap is recorded as a `warning` row in `events` but accepted.
5. Wrong token: close with code 4401. Unknown `schema_version`: close with code 4400.

## 6. Storage contracts (server)

### 6.1 Folder layout

```text
$DATA_ROOT/                                  # default /srv/glasses
  recordings/glasses/<YYYY-MM-DD_HH-MM-SS-ffffff>.mp4      # MediaMTX segments (raw HEVC + AAC), never modified
  data/<session_id>/
    session.json
    events.parquet                           # or events/part-NNNNNN.parquet while the session is live
    imu/part-000001.parquet ...              # rolling parts, closed every 5 s or 1000 rows
    imu.parquet                              # optional compaction written when the session ends
    audio.wav, audio_pts.csv                 # only if the raw PCM channel was used
  derived/<session_id>/                      # anything computed; safe to delete
```

Rolling parts exist because a Parquet file without its footer is unreadable after a crash; closing small parts bounds loss to a few seconds. `load_session()` reads the `imu/` directory as one dataset, and prefers `imu.parquet` when present.

### 6.2 `imu` Parquet schema

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `seq` | int64 | no | Message `seq` |
| `timestamp_ns` | int64 | no | As received |
| `recv_wall_ms` | int64 | no | Server clock when the batch arrived |
| `phone_wall_ms` | int64 | no | From the batch |
| `phone_elapsed_ns` | int64 | no | From the batch |
| `source` | string | no | |
| `ax`, `ay`, `az` | float64 | yes | |
| `gx`, `gy`, `gz` | float64 | yes | |
| `mx`, `my`, `mz` | float64 | yes | |
| `qw`, `qx`, `qy`, `qz` | float64 | yes | |

Sort order within a part: arrival order. Do not sort by `timestamp_ns` on write; readers sort if they need to.

### 6.3 `events` Parquet schema

| Column | Type | Null |
| --- | --- | --- |
| `seq` | int64 | no |
| `recv_wall_ms` | int64 | no |
| `phone_wall_ms` | int64 | no |
| `phone_elapsed_ns` | int64 | no |
| `event` | string | no |
| `detail_json` | string (JSON text) | yes |

### 6.4 `session.json`

```json
{
  "schema_version": 1,
  "session_id": "b6f0c2e4-8a31-4d0e-9b57-0a1c2d3e4f50",
  "started_recv_wall_ms": 1764000000000,
  "ended_recv_wall_ms": 1764001800000,
  "app": {"version": "0.1.0", "sdk_version": "x.y.z", "device_model": "Pixel 8", "android_version": "15"},
  "settings": {"video_quality": "MEDIUM", "frame_rate": 24, "audio_rate_hz": 48000, "audio_channels": 1, "imu_rate_hz": 60, "compressed_video": true},
  "delivered": {"width": 504, "height": 896, "video_fps_mean": 23.4, "imu_rate_hz_mean": 57.9},
  "recordings": ["recordings/glasses/2026-11-24_14-40-01-123456.mp4"],
  "calibration": {"imu_to_video_ms": null, "audio_to_video_ms": null, "spread_ms": null, "method": "tap_v1"},
  "counts": {"imu_rows": 0, "events": 0, "seq_gaps": 0, "imu_dropped_reported": 0}
}
```

The receiver creates this file at session start and rewrites it atomically (write to temp file, rename) at least every 30 seconds and on close. `recordings` and `calibration` are filled by tools after the session.

## 7. Plan B uplink (if MPEG-TS over SRT cannot be built on the phone)

MediaMTX accepts MPEG-TS over SRT [A: confirm in its docs for your version]. Android's built-in `MediaMuxer` does not write MPEG-TS [A], so the phone needs a TS muxer library or hand-written packetizer. If Spike S4 shows no workable option, fall back to this design and record the decision in `DECISIONS.md`:

- The phone sends raw access units over a WebSocket: `ws://<host>:8000/media/{session_id}?token=...`, binary frames with a 16-byte header: 1 byte stream type (`1` video HEVC Annex B, `2` audio AAC ADTS), 1 byte flags (bit 0 = keyframe), 2 bytes reserved, 8-byte little-endian `presentation_time_us`, 4-byte little-endian payload length, then the payload.
- A server relay (`server/relay/`) feeds these into an FFmpeg process that remuxes without re-encoding and publishes to MediaMTX (`ffmpeg ... -c copy -f rtsp rtsp://127.0.0.1:8554/glasses` or MPEG-TS to the SRT/RTSP input).
- Latency is higher (TCP head-of-line blocking) but the phone code is much simpler.

## 8. Configuration

Phone settings (stored on the device): server host (Tailscale IP or MagicDNS name), SRT passphrase, WebSocket token, video quality, frame rate, audio rate and channels, IMU rate, session label.

Server `.env`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATA_ROOT` | `/srv/glasses` | Root of recordings and data |
| `TELEMETRY_TOKEN` | none (required) | WebSocket shared secret |
| `TELEMETRY_BIND` | Tailscale IP | Address to listen on |
| `SRT_PASSPHRASE` | none (required) | Must match MediaMTX `srtPublishPassphrase` |
| `PART_SECONDS` | 5 | Parquet part rotation interval |
| `PART_ROWS` | 1000 | Parquet part rotation row count |
| `LOG_LEVEL` | `info` | |

## 9. Repository layout

```text
CLAUDE.md
SOW.md  TECH-01 ... TECH-06  (this document set, at the repo root)
android/        # Gradle project, module :app
server/mediamtx/   server/telemetry/   server/relay/ (Plan B only)
gpu/            # glasses_reader package
tools/          # synthetic publishers, calibration, offset estimation
tests/          # pytest for server and gpu; Android tests live in android/
TESTLOG.md      # one entry per test run (template in TECH-06)
DECISIONS.md   # dated decisions that depart from or refine these documents
```

## 10. Non-functional targets

Taken from SOW section 11 and repeated here so code reviews can cite one place: video at least 90% of the requested fps; IMU at least 90% of requested rate; audio gaps under 200 ms outside pauses; reconnect within 15 s; IMU/video calibration spread under 50 ms; no data loss beyond 10 s on a receiver crash. All are proposed targets, not Meta figures.

## 11. Spikes that must precede design lock

| ID | Question | Method | Exit criterion |
| --- | --- | --- | --- |
| S1 | What is in a compressed `VideoFrame.buffer`: Annex B or length-prefixed? Are VPS/SPS/PPS present on each keyframe? GOP length? B-frames? | Dump 200 frames to files; inspect with `ffprobe -show_frames` and a hex viewer | Written note in `DECISIONS.md` |
| S2 | Which `StreamConfiguration` field turns on compressed HEVC on the pinned SDK (reported as `compressVideo`)? | Read the SDK source and sample; log `VideoFrame.isCompressed` | Flag name and behaviour recorded |
| S3 | Do audio `presentationTimeUs` and video `presentationTimeUs` share a timebase? | Clap test; compare first-audio and first-video pts and clap onset | Offset measured in ms |
| S4 | Which SRT and MPEG-TS library works on Android with HEVC + AAC and MediaMTX? | Publish a file-based test stream first, then live | MediaMTX records a playable file |
| S5 | What IMU rate is actually delivered at the 60 Hz setting? | Count samples over 5 minutes using `timestampNs` | Mean rate and jitter recorded |
| S6 | What does a glasses-off pause do to each stream and to the three timestamps? | Remove and replace glasses during a session with logging | State sequence table recorded |

## 12. References

- Meta, Android integration guide: https://wearables.developer.meta.com/docs/develop/dat/build-integration-android/ [V]
- Meta, microphones and speakers: https://wearables.developer.meta.com/docs/develop/dat/microphones-and-speakers/ [V]
- Meta, Motion (IMU): https://wearables.developer.meta.com/docs/develop/dat/motion/ [V]
- Meta, Stream API reference (Android, 0.7): https://wearables.developer.meta.com/docs/reference/android/dat/0.7/com_meta_wearable_dat_camera_stream [V]
- Meta, VideoFrame reference (Android, 0.6): https://wearables.developer.meta.com/docs/reference/android/dat/0.6/com_meta_wearable_dat_camera_types_videoframe [V]
- MediaMTX recording docs: https://mediamtx.org/docs/usage/record [V]
- Full link list: `SOW.md` section 17.
