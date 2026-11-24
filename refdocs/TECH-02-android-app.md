# TECH-02: Android App

Companion to `SOW.md` sections 5 and 6, and `TECH-01-architecture-and-contracts.md` (schemas, clocks, event names). Read TECH-01 first. Evidence tags **[V] [R] [A]** are defined there.

Goal: a Kotlin app, installed from Android Studio over USB debugging, that opens a session with Ray-Ban Meta glasses through Meta's Wearables Device Access Toolkit (DAT), captures video, in-stream audio and IMU, and sends them to the GPU server over Tailscale.

## 1. Ground rules

1. **Developer Mode only.** Application ID and client token are both `0`; attestation is skipped [V]. No Developer Center account, no release channel, no store.
2. **Pin the SDK version** and copy API names from that version's source and the CameraAccess sample. Names below come from Meta's docs for versions 0.6 to 1.x and may differ slightly in the version you pin [R]. Where this document and the pinned SDK disagree, the SDK wins and the difference goes in `DECISIONS.md`.
3. **Foreground only.** Background Motion is not supported in 1.0.x [R]. Keep the screen on and run a foreground service.
4. **No identification features.** No face or voice recognition. Never hide a recording indicator.
5. **Spikes first** (TECH-01 section 11, S1 to S6). Do not write the muxer until S1 has told you what the compressed frame bytes look like.

## 2. Project setup

### 2.1 Dependencies [V]

Distributed on Maven Central, group `com.meta.wearable`. Artifacts: `mwdat-core`, `mwdat-camera`, `mwdat-motion`, `mwdat-mockdevice` (debug builds only), `mwdat-display` (not used). All share one version, defined once as `mwdat` in `libs.versions.toml`.

```toml
# gradle/libs.versions.toml (version value comes from Maven Central; pin an exact release)
[versions]
mwdat = "<pin exact version>"
[libraries]
mwdat-core = { group = "com.meta.wearable", name = "mwdat-core", version.ref = "mwdat" }
mwdat-camera = { group = "com.meta.wearable", name = "mwdat-camera", version.ref = "mwdat" }
mwdat-motion = { group = "com.meta.wearable", name = "mwdat-motion", version.ref = "mwdat" }
mwdat-mockdevice = { group = "com.meta.wearable", name = "mwdat-mockdevice", version.ref = "mwdat" }
```

Additional libraries (standard choices, not from Meta): OkHttp (WebSocket), kotlinx-coroutines, kotlinx-serialization-json, AndroidX DataStore, an SRT client and an MPEG-TS muxer (see section 6.1).

### 2.2 Manifest [V]

Permissions: `android.permission.BLUETOOTH`, `android.permission.BLUETOOTH_CONNECT`, `android.permission.INTERNET`; `android.permission.CAMERA` only if you use the phone camera as a mock device feed; `uses-feature android.hardware.camera` with `required="false"`.

Meta-data, filled from manifest placeholders:

```xml
<meta-data android:name="com.meta.wearable.mwdat.APPLICATION_ID" android:value="${mwdat_application_id}" />
<meta-data android:name="com.meta.wearable.mwdat.CLIENT_TOKEN"  android:value="${mwdat_client_token}" />
```

In `build.gradle.kts` `defaultConfig`, set both placeholders to `"0"` (the CameraAccess sample reads them from `local.properties`) [V].

Callback activity: the registration flow returns to your app through an intent filter with action `VIEW`, categories `DEFAULT` and `BROWSABLE`, and a `data` scheme of your own choosing (for example `glassesstream`) [V].

Additional Android permissions you will need that are **not** from Meta [A: standard Android, confirm for the target OS level]: `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_CONNECTED_DEVICE` (foreground service type `connectedDevice`), `POST_NOTIFICATIONS` (Android 13 and above), `WAKE_LOCK`. Battery-optimisation exemption is requested through the system settings screen, not silently.

## 3. Module layout

```text
android/app/src/main/kotlin/<package>/
  App.kt                      # Application: Wearables.initialize(context) once
  ui/       MainActivity.kt, StatusScreen.kt, SettingsScreen.kt
  service/  StreamingService.kt          # foreground service owning a SessionController
  session/  SessionController.kt         # state machine; owns DAT session and capabilities
            Registration.kt              # registration + permission flows
  capture/  VideoPipeline.kt, AudioPipeline.kt, MotionPipeline.kt
  mux/      TsMuxer.kt (or library adapter), HevcUtil.kt, AdtsUtil.kt
  uplink/   SrtUplink.kt, TelemetryUplink.kt, PcmUplink.kt, Backoff.kt
  model/    Messages.kt (TECH-01 section 5), Settings.kt
  util/     Clocks.kt, Log.kt (JSONL), Counters.kt
```

One `SessionController` owns everything tied to a DAT session. Pipelines are created per session and cancelled with it.

## 4. SDK integration (names from Meta docs)

### 4.1 Initialization and registration [V]

- Call `Wearables.initialize(context)` once per process, in `Application.onCreate`. It returns a `DatResult`. A second call fails with `WearablesError.ALREADY_INITIALIZED`; calling other APIs first throws `WearablesException` with `WearablesError.NOT_INITIALIZED`.
- `Wearables.startRegistration(activity)` opens the Meta AI app to approve; the callback returns through your intent filter. `Wearables.startUnregistration(activity)` reverses it.
- Observe `Wearables.registrationState` and `Wearables.devices` (both flows) to drive the UI.
- Camera permission: `Wearables.checkPermissionStatus(Permission.CAMERA)` (suspend, returns a `DatResult`; compare with `PermissionStatus.Granted`). If not granted, request with `Wearables.RequestPermissionContract` registered via `registerForActivityResult`. Serialise requests with a `Mutex` (Meta's sample wraps this in a helper called `requestWearablesPermission`).
- Motion needs **no** permission prompt; do not call permission APIs for it [V].
- In-stream audio needs no extra Android microphone permission on the phone, because audio arrives through the SDK [A: confirm in Spike S3]. The HFP route (not used here) does need microphone permission and API 31 or higher [V].

### 4.2 Session lifecycle [V]

```kotlin
// Skeleton: confirm every name against the pinned SDK before compiling.
val session = Wearables.createSession(AutoDeviceSelector()).getOrElse { e ->
    error("createSession failed: ${e.description}")
}
scope.launch { session.state.collect { onSessionState(it) } }     // DeviceSessionState
scope.launch { session.errors.collect { onSessionError(it) } }
session.start()                       // returns immediately; connects in the background
// Add capabilities ONLY after state == DeviceSessionState.STARTED
```

Rules:

- Collect `session.state` and `session.errors` **before** calling `session.start()`.
- Add the stream and Motion only after `DeviceSessionState.STARTED`.
- On `DeviceSessionState.STOPPED` a session cannot be restarted: drop every capability reference and create a **new** session (and new pipelines) [V].
- `SpecificDeviceSelector` exists if you need to pick a pair of glasses explicitly; `AutoDeviceSelector()` is fine for one pair [V].
- Pauses (glasses removed, hinge closed, "Hey Meta", Bluetooth drop) are surfaced as state changes. Treat them as gaps, not as the end of the app session [R].

### 4.3 Camera and audio [V unless tagged]

```kotlin
val stream: Stream = session.addStream(
    StreamConfiguration(
        videoQuality = VideoQuality.MEDIUM,          // HIGH 720x1280, MEDIUM 504x896, LOW 360x640
        frameRate = 24,                              // one of 2, 7, 15, 24, 30
        audioCodec = AudioCodec.PCM,                 // turns audio on; null or unset = video only
        audioSampleRate = AudioSampleRate.RATE_48000,// [A] Meta's docs show AudioSampleRate.RATE_16000 and numberOfChannels = 1 passed alongside AudioCodec.PCM;
        numberOfChannels = 1                         //     confirm whether these are separate fields or part of the codec, and the enum names for 44.1k and 48k
        // compressed-video flag: reported as `compressVideo`; confirm field name (Spike S2)
    )
).getOrElse { e -> error(e.description) }
```

Ordering [V]: start collecting `stream.videoStream` and `stream.audioStream` in coroutines first, **then** call `stream.start()`.

- `Stream.videoStream: Flow<VideoFrame>` delivers frames only while `StreamState.STREAMING`. When your collector is slow, the SDK drops the **oldest** frames [V].
- `VideoFrame` fields [V, from the 0.6 reference]: `buffer: ByteBuffer`, `width: Int`, `height: Int`, `presentationTimeUs: Long`, `isCompressed: Boolean` (`true` = HEVC data, `false` = decoded YUV pixel data).
- Audio frames expose `buffer` (PCM bytes) and `presentationTimeUs` [V]. Format: uncompressed 16-bit PCM at the configured rate and channel count [V]. Byte order and frame size: confirm in Spike S3 [A].
- `Stream.state: StateFlow<StreamState>` moves `STOPPED -> STARTING -> STARTED -> STREAMING -> STOPPING -> STOPPED -> CLOSED` [V]. `Stream.errorStream: Flow<StreamError>`: `StreamError.STREAM_ERROR` is non-critical; `StreamError.CRITICAL_STREAM_ERROR` means terminate the stream [V].
- `stream.stop()` releases resources and removes the stream from its session; `session.removeStream()` removes the capability before re-adding [V].
- Lower resolution and frame rate look better over Bluetooth because compression is lighter [V]. Defaults here: MEDIUM at 24 fps; offer HIGH at 30 fps in settings.
- Photo capture is not used.

### 4.4 Motion (IMU) [V]

```kotlin
val motion = session
    .addMotion(MotionConfiguration(samplingRate = MotionSamplingRate.HZ_60))
    .onFailure { error, _ -> log.warn("addMotion", error.description) }
    .getOrNull() ?: return

val samplesJob = scope.launch(start = CoroutineStart.UNDISPATCHED) {
    motion.samples.collect { s -> motionPipeline.offer(s) }   // SharedFlow, no replay
}
val stateJob  = scope.launch { motion.state.collect { /* stopped, starting, started, stopping, paused */ } }
val errorJob  = scope.launch { motion.errors.collect { it?.let(::onMotionError) } }
motion.start()
```

Facts:

- Needs the separate artifact `mwdat-motion`. Rates: `HZ_5, HZ_10 (default), HZ_15, HZ_24, HZ_30, HZ_60`; the rate is a **target**, so compute intervals from `timestampNs`.
- `MotionSample`: `timestampNs` (device monotonic clock, always present), `accelerometer` (m/s², includes gravity, nullable), `gyroscope` (rad/s, nullable), `magnetometer` (µT, nullable), `orientation` (quaternion, `w` scalar, nullable), `source` (glasses, Meta Neural Band, or unknown, always present).
- `samples` is a `SharedFlow` **without replay**: launch the collector with `CoroutineStart.UNDISPATCHED` **before** `start()`, or you lose the first samples.
- States: `stopped, starting, started, stopping, paused`. `paused` is Android-only: it appears when the device session pauses and the capability restarts automatically on resume. `stop()` is synchronous on Android.
- Errors (`MotionError`): `SENSOR_UNAVAILABLE` ends the current start attempt, so call `start()` again once the state is `stopped`; `DEVICE_DISCONNECTED` is auto-recovered on reconnect, so do **not** re-add; `CAPABILITY_CLOSED` is terminal for that instance, so add Motion again to a started session.
- **Android has no start timeout.** The capability stays in `starting` until a sample arrives or an error occurs [V]. Implement your own watchdog (section 7).
- Reported: Motion works on Ray-Ban Meta firmware 129 but not 128 [R].
- Teardown: cancel collector jobs, `motion.stop()`, `session.removeMotion()`, check its `DatResult`.

## 5. State machines

### 5.1 App-level

```text
IDLE -> NEEDS_REGISTRATION -> REGISTERED -> NEEDS_CAMERA_PERMISSION -> READY
READY --(Start pressed)--> SESSION_STARTING --(STARTED)--> CAPTURING
CAPTURING --(session/stream paused)--> PAUSED --(resumed)--> CAPTURING
CAPTURING | PAUSED --(Stop pressed)--> STOPPING -> READY
any --(session STOPPED unexpectedly)--> RECOVERING --(new session ok)--> SESSION_STARTING
RECOVERING --(3 failures in 5 min)--> FAILED (needs user action)
```

### 5.2 Recovery rules

| Situation | Action |
| --- | --- |
| Session `STOPPED` unexpectedly | Emit `session_stopped` (reason), drop pipelines, create a new session after 2 s; new `clock_anchor` events follow. Limit: once a minute |
| `StreamError.CRITICAL_STREAM_ERROR` | Stop stream, `removeStream`, `addStream` again on the same session if still `STARTED`; else new session |
| Motion `SENSOR_UNAVAILABLE` | Wait for `stopped`, call `start()` again, max 3 times, then `warning` event and continue without IMU |
| Motion `CAPABILITY_CLOSED` | Add Motion again if session is `STARTED` |
| Motion stuck in `starting` for more than 10 s | `motion.stop()`, `motion.start()` once; then `warning` and continue without IMU |
| Collector falls behind (video) | Drop oldest from the app queue, count in `frames_dropped_app`; never block the SDK collector |
| SRT disconnect | Reconnect with backoff 1, 2, 4, 8, 15 s; buffer up to 10 s of muxed data; beyond that drop oldest and count |
| WebSocket disconnect | Reconnect with the same backoff; on `hello`, resend from `next_seq` |

## 6. Pipelines

### 6.1 Video pipeline (the risky part)

```text
SDK collector (Dispatchers.Default)
  -> copy buffer into pooled byte array         # never hold the SDK ByteBuffer past the callback [A]
  -> bounded queue (capacity ~48 frames, drop oldest)
  -> mux thread: if isCompressed -> HEVC path ; else -> YUV encode path
  -> MPEG-TS PES (stream type 0x24 HEVC), PTS = presentationTimeUs * 90_000 / 1_000_000
  -> SRT sender thread (ring buffer ~10 s)
```

Details:

- **Copy first.** Copy `VideoFrame.buffer` bytes immediately; buffer reuse after the callback is unknown [A].
- **Compressed path.** Frame bytes are HEVC [V]. Whether they are Annex B (start codes `00 00 00 01`) or length-prefixed, and whether VPS (NAL type 32), SPS (33) and PPS (34) appear before each IRAP picture (types 16 to 21), is **unknown until Spike S1** [A]. Write `HevcUtil` to: detect the format by inspecting the first bytes; convert length-prefixed to Annex B; cache the latest VPS/SPS/PPS; and prepend them to every IRAP access unit that lacks them (a TS stream must be decodable from any keyframe).
- **Timestamps.** Use `presentationTimeUs` converted to the 90 kHz TS clock for both video and audio so the TS carries the SDK's own alignment. Assume DTS = PTS (no B-frames) until S1 shows otherwise [A].
- **Uncompressed path (fallback).** If only decoded YUV 4:2:0 is available, encode with `MediaCodec` (`video/hevc`, or `video/avc` if HEVC encode is unavailable), surface or buffer input, bitrate 2 to 3 Mbps, keyframe interval 1 s, and use the SDK frame timestamps as encoder input timestamps. This costs battery and latency; prefer the compressed path.
- **MPEG-TS and SRT libraries.** MediaMTX carries MPEG-TS over SRT [A: confirm]. Android's `MediaMuxer` does not output MPEG-TS [A]. Candidates to evaluate in Spike S4 (verify maintenance and licence before adopting): a libsrt build via the NDK (Haivision), `srtdroid` (Android wrapper for libsrt), and `StreamPack` (Android streaming library with SRT and TS support). If none works, use Plan B in TECH-01 section 7.
- **SRT settings.** Caller mode; `streamid=publish:glasses`; `passphrase=<secret>`; `pkt_size=1316`; start with receiver latency 200 to 500 ms. Units differ between libraries, so read the library's docs [A].

### 6.2 Audio pipeline

```text
SDK audio collector -> copy bytes -> (a) AAC-LC MediaCodec encoder -> ADTS frames -> TS audio PES (PTS from presentationTimeUs)
                                  -> (b) optional raw PCM to PcmUplink (TECH-01 section 5.3)
```

- Encoder: `audio/mp4a-latm`, AAC-LC, sample rate and channels exactly as configured, 64 to 96 kbps for mono.
- PCM input pacing: feed whole PCM frames; keep a running sample counter and derive output PTS from the **first frame's** `presentationTimeUs` plus `samples / sampleRate`, but re-sync to the SDK timestamp if drift exceeds 20 ms, and log it [A].
- In-stream audio is not beamformed, so ambient sound is natural [V]. In-stream audio is beta and only works in development and beta channels [V]; this build is always a development build.
- Do not start HFP at the same time. If HFP is ever used, it must settle before the camera stream starts [V]; it is not used in this design.

### 6.3 Motion pipeline

```text
motion.samples (UNDISPATCHED collector) -> Channel(capacity 1024, DROP_OLDEST, onUndeliveredElement counts drops)
  -> batcher: flush every 100 ms or 64 samples -> ImuBatch JSON (TECH-01 5.1) -> TelemetryUplink
```

- Keep `timestampNs` exactly. Map `accelerometer`, `gyroscope`, `magnetometer` to arrays, `orientation` to `[w, x, y, z]`, and nulls to JSON `null`.
- Detect gaps: if the delta between consecutive `timestampNs` exceeds 3 times the nominal interval, add the estimated missing count to `dropped_since_last`.
- Never block the collector. Use a dedicated single-thread dispatcher for batching and serialising.

### 6.4 Telemetry uplink

- OkHttp WebSocket to `ws://<host>:8000/ingest/{session_id}?token=...`.
- Follow TECH-01 section 5.4: wait for `hello`, resend from `next_seq`, ack handling, duplicate-safe.
- Keep unacknowledged messages in memory, up to 5 minutes (about 3,000 batches at 10 per second, small), then drop oldest and emit a `warning`.
- Message order: `session_started`, `settings_applied`, then streams.

### 6.5 Clocks and anchors

- On the **first** frame or sample of each stream after start or resume, emit `clock_anchor` with the stream's first timestamp and `SystemClock.elapsedRealtimeNanos()` and `System.currentTimeMillis()` read at that instant.
- Put all clock reads in `util/Clocks.kt` so tests can fake them.

## 7. Foreground service and screen

- `StreamingService` (type `connectedDevice`) owns `SessionController`, holds a partial wake lock while streaming, and shows a persistent notification with status.
- `MainActivity` sets `FLAG_KEEP_SCREEN_ON` while streaming.
- A banner on the status screen reads "RECORDING" whenever the camera stream is `STREAMING`. It cannot be disabled in settings.
- Prompt the user (once) to exempt the app from battery optimisation through the system settings intent.

## 8. Status screen and settings

Status screen fields: glasses connection, registration state, `DeviceSessionState`, `StreamState`, Motion state, delivered resolution, video fps (1 s moving average), video bitrate, IMU rate, IMU dropped count, audio rate, SRT state and bytes sent, WebSocket state and last acked `seq`, session ID, elapsed time, last error.

Settings (DataStore): server host, SRT passphrase, WebSocket token, video quality, frame rate, audio rate (16000, 44100, 48000) and channels, IMU rate, enable raw PCM channel, session label. Changing stream settings requires restarting the stream.

## 9. Logging

- JSONL at `files/logs/<session_id>.jsonl`: every event sent, every SDK state change, every error with `error.description`, counters every 10 s.
- Pull with `adb exec-out run-as <package> cat files/logs/<id>.jsonl`.
- Do not log frame contents, raw audio or token values.

## 10. Testing

| Level | Tooling | What it proves |
| --- | --- | --- |
| Unit | JUnit, fake `Clocks` | Batching, seq numbering, backoff, gap counting, HEVC NAL parsing, ADTS wrapping |
| Mock device | `mwdat-mockdevice` (debug only) [V]: feed a phone camera or a video file as a fake glasses stream; `setMotionFeed(samples, loop)` or CSV (14 numeric values per row, no header, replayed at the configured rate, not recorded timestamps) | Session lifecycle, uplinks, UI, without glasses |
| Server loopback | Run TECH-03/TECH-04 stack, app on mock device | End-to-end plumbing |
| Real glasses | Test plan in `SOW.md` section 12 | Pauses, battery, Bluetooth quality |

The phone camera is only used as a mock device feed; keep the `CAMERA` permission request behind the mock path.

## 11. Build order inside this app

1. Project, manifest, Gradle, `Wearables.initialize`, registration flow, camera permission. **Exit:** registered and listed device on screen.
2. Session + stream, show frames in the UI (or just counters). Run Spikes S1, S2, S3, S5, S6. **Exit:** spike notes in `DECISIONS.md`.
3. Video: copy, queue, HEVC utilities, TS muxing to a **local file**, play it with `ffplay`. **Exit:** a local `.ts` file plays.
4. SRT uplink of that TS to MediaMTX (Spike S4). **Exit:** MediaMTX records a playable file.
5. Audio: AAC encode, add to the TS. **Exit:** recorded file has synced audio (clap test).
6. Motion: batcher and WebSocket uplink. **Exit:** Parquet rows on the server.
7. Anchors, pauses, recovery, foreground service, status screen. **Exit:** SOW section 11 rows 1, 8, 9.
8. Settings sweep and soak test (TECH-06).

## 12. Known issues to design for

| Issue | Source | Handling |
| --- | --- | --- |
| Video over Bluetooth only, lower quality | Meta staff, SDK 1.0.1 [R] | Default MEDIUM at 24 fps; measure |
| Random stream failures on Android | GitHub reports [R] | Recovery rules in section 5.2; log everything |
| Motion stuck in `starting` | Documented absence of Android timeout [V] | Watchdog |
| Slow collector causes frame drops inside the SDK | Documented [V] | Never block the collector; copy and enqueue only |
| Firmware 128 lacks Motion | Meta staff [R] | Update glasses firmware; show firmware warning when no samples arrive |

## 13. References

- Meta, integrate DAT into your Android app: https://wearables.developer.meta.com/docs/develop/dat/build-integration-android/
- Meta, in-stream audio and HFP: https://wearables.developer.meta.com/docs/develop/dat/microphones-and-speakers/
- Meta, Motion API: https://wearables.developer.meta.com/docs/develop/dat/motion/
- Meta, Stream reference (Android 0.7): https://wearables.developer.meta.com/docs/reference/android/dat/0.7/com_meta_wearable_dat_camera_stream
- Meta, VideoFrame reference (Android 0.6): https://wearables.developer.meta.com/docs/reference/android/dat/0.6/com_meta_wearable_dat_camera_types_videoframe
- Meta, AI-assisted development and AGENTS.md: https://wearables.developer.meta.com/docs/develop/dat/ai-assisted-agents-md/
- Meta, Android SDK repository (CameraAccess sample, `AGENTS.md`): https://github.com/facebook/meta-wearables-dat-android
- Camera streaming skill (Kotlin examples): https://skillsmp.com/skills/facebook-meta-wearables-dat-android-plugins-mwdat-android-skills-camera-streaming-skill-md
