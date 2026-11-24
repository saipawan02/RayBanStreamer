# Ray-Ban Meta Glasses Streaming System

Stream video, audio, and IMU data from Ray-Ban Meta glasses to a GPU server for recording and analysis.

## System Overview

```
Ray-Ban Meta Glasses (via Bluetooth)
         ↓
    Android Phone App
         ↓
    ┌────┴────────────────┐
    ↓                     ↓
Telemetry (IMU)      Video/Audio
  WebSocket           WebSocket
  Port 8000           Port 8001
    ↓                     ↓
Telemetry Server    Relay Service
    ↓                     ↓
 Parquet Files         FFmpeg
                          ↓
                      MediaMTX
                          ↓
                   RTSP Stream
                   Port 8554
                          ↓
                   VLC / GPU Readers
```

## Quick Start

### 1. Start All Services

```bash
./start-all.sh
```

This starts:
- MediaMTX (streaming server)
- Telemetry receiver (IMU data)
- Relay service (video/audio)

**The script will display your local IP address** - save this for the next step!

### 2. Install Android App

```bash
cd android
./gradlew installDebug
```

### 3. Configure App on Phone

**Important:** The app needs your machine's **local IP address** (not `localhost`).

The `start-all.sh` script displays your IP when starting services, or find it manually:

```bash
hostname -I | awk '{print $1}'
# Example output: 192.168.1.28
```

**In the RayBanStreamer app:**
- **Server Host**: `192.168.1.28` (use YOUR actual IP, no port number)
- **WebSocket Token**: `test-token-change-me-in-production`
- Tap **SAVE SETTINGS**

The app will automatically connect to:
- Telemetry (IMU): `ws://192.168.1.28:8000/ingest/<session_id>`
- Media (Video): `ws://192.168.1.28:8001/media/<session_id>`

**Note:** Your phone and laptop must be on the same WiFi network.

### 4. Stream from Glasses

1. **REGISTER WITH GLASSES**
2. **REQUEST CAMERA ACCESS**
3. **START SESSION**
4. **START STREAMING**

### 5. View Stream

```bash
vlc rtsp://localhost:8554/glasses
```

Or via command line:
```bash
ffplay rtsp://localhost:8554/glasses
```

---

## Prerequisites

### Server Machine

- **OS**: Linux (tested on Ubuntu 24.04)
- **Docker**: For MediaMTX
- **Python 3.11+**: For telemetry and relay services
- **FFmpeg**: For video relay
- **VLC or ffplay**: For viewing streams

Install dependencies:
```bash
# Docker
sudo apt install docker.io docker-compose

# FFmpeg and VLC
sudo apt install ffmpeg vlc

# Python
sudo apt install python3 python3-venv python3-pip
```

### Android Phone

- **Android 10+** recommended
- **Ray-Ban Meta glasses** paired via Meta AI app
- **Developer mode enabled** for glasses (Meta Developer Portal)
- **USB debugging enabled** for app installation

---

## Configuration

### Server Configuration

**Telemetry Server** (`server/telemetry/.env`):
```env
DATA_ROOT=/tmp/glasses
TELEMETRY_TOKEN=test-token-change-me-in-production
TELEMETRY_BIND=0.0.0.0
PART_SECONDS=5
PART_ROWS=1000
LOG_LEVEL=info
```

**MediaMTX** (`server/mediamtx/.env`):
```env
DATA_ROOT=/tmp/glasses
# Passphrase disabled for local testing
```

### App Configuration

Configure in the app UI:
- **Server Host**: Your server's IP or hostname
- **WebSocket Token**: Must match `TELEMETRY_TOKEN` on server
- **Video Quality**: MEDIUM (504x896) or HIGH (720x1280)
- **Frame Rate**: 24 fps (default) or 30 fps
- **IMU Rate**: 60 Hz (default)

---

## Services

### MediaMTX (Port 8554 RTSP, Port 8890 SRT)

**Purpose**: Streaming server that receives video and serves via RTSP

**Start**:
```bash
cd server/mediamtx
docker compose up -d
```

**Stop**:
```bash
docker compose down
```

**Logs**:
```bash
docker logs -f mediamtx
```

### Telemetry Receiver (Port 8000)

**Purpose**: Receives IMU data and events over WebSocket, stores as Parquet

**Start**:
```bash
cd server/telemetry
./start.sh
```

**Stop**: Press `Ctrl+C`

**Logs**: Shown in terminal

**Data location**: `/tmp/glasses/data/<session_id>/`

### Relay Service (Port 8001)

**Purpose**: Receives video frames over WebSocket, relays to MediaMTX via FFmpeg

**Start**:
```bash
cd server/relay
./start.sh
```

**Stop**: Press `Ctrl+C`

**Logs**: `/tmp/relay.log`

---

## Testing

### Test with Synthetic Stream

Before using real glasses, verify the pipeline works:

```bash
# Synthetic video stream (test pattern)
./tools/synthetic_publisher.sh localhost test-passphrase-change-in-production

# View in VLC
vlc rtsp://localhost:8554/glasses
```

You should see animated color bars with a 440Hz tone.

### Test IMU Telemetry

```bash
# Generate synthetic IMU data
python tools/synthetic_imu_client.py --host localhost --token test-token-change-me-in-production --rate 60 --seconds 60

# Check data was written
ls -lah /tmp/glasses/data/
```

### Monitor Live Session

**Phone logs** (via USB):
```bash
adb logcat -s CameraManager:I VideoPipeline:I MediaUplink:I MotionPipeline:I TelemetryUplink:I
```

**Server health**:
```bash
# Telemetry
curl http://localhost:8000/health

# Relay
curl http://localhost:8001/health
```

---

## Data Storage

### Session Data

Each streaming session creates:
```
/tmp/glasses/data/<session_id>/
├── session.json          # Metadata and settings
├── events.parquet        # Event log
└── imu/
    ├── part-000001.parquet
    ├── part-000002.parquet
    └── ...
```

### Video Recordings

MediaMTX records to:
```
/tmp/glasses/recordings/glasses/
├── 2026-11-24_23-45-00-123456.mp4
└── ...
```

Format: fMP4 (fragmented MP4), 1-second fragments

---

## Troubleshooting

### App Can't Connect to Server

1. **Check network**: Ensure phone and server are on same network
2. **Check firewall**:
   ```bash
   sudo ufw allow 8000/tcp
   sudo ufw allow 8001/tcp
   sudo ufw allow 8554/tcp
   ```
3. **Verify services running**:
   ```bash
   ./check-services.sh
   ```
4. **Check server IP**: Make sure you're using the correct local IP, not 127.0.0.1

### No Video in VLC

1. **Check MediaMTX logs**:
   ```bash
   docker logs mediamtx | grep glasses
   ```
   Should show: "stream is available and online"

2. **Check relay logs**:
   ```bash
   tail -f /tmp/relay.log
   ```
   Should show: "Media WebSocket opened"

3. **Check FFmpeg process**:
   ```bash
   ps aux | grep ffmpeg
   ```

4. **Try test stream first**:
   ```bash
   ./tools/synthetic_publisher.sh localhost test-passphrase
   ```

### No IMU Data

1. **Check glasses firmware**: Needs firmware 129+ for Motion API
2. **Check phone logs**:
   ```bash
   adb logcat -s MotionPipeline:I
   ```
   Should see: "Motion added successfully"
3. **Check telemetry server**: Should show "Received imu_batch" in logs

### App Crashes

1. **Check logcat**:
   ```bash
   adb logcat | grep -E "AndroidRuntime|FATAL"
   ```
2. **Rebuild and reinstall**:
   ```bash
   cd android
   ./gradlew clean assembleDebug installDebug
   ```

### Low Frame Rate or Stuttering

1. **Reduce video quality**: Switch to MEDIUM or LOW in app settings
2. **Check network**: WiFi is better than mobile data
3. **Check CPU usage**: FFmpeg relay can be CPU-intensive

---

## Architecture Details

### Video Pipeline (Plan B)

The system uses "Plan B" architecture (TECH-01 section 7):

1. **Phone**: Captures HEVC frames from glasses SDK
2. **VideoPipeline**: Copies and queues frames
3. **MediaUplink**: Sends over WebSocket with binary protocol
4. **Relay Service**: Receives and feeds to FFmpeg
5. **FFmpeg**: Remuxes (no re-encode) to RTSP
6. **MediaMTX**: Records and serves via RTSP
7. **VLC/Readers**: Consume RTSP stream

### IMU Pipeline

1. **Phone**: Collects IMU samples at 60Hz from Motion API
2. **MotionPipeline**: Batches every 100ms or 64 samples
3. **TelemetryUplink**: Sends via WebSocket with seq numbering
4. **Telemetry Server**: Stores as Parquet files

---

## Development

### Build Android App

```bash
cd android
./gradlew assembleDebug
```

APK location: `android/app/build/outputs/apk/debug/app-debug.apk`

### Run Tests

**Telemetry server**:
```bash
cd server/telemetry
pytest -v
```

**GPU readers** (future):
```bash
cd gpu
pytest -v
```

### Code Structure

```
android/app/src/main/kotlin/com/glassesstream/
├── MainActivity.kt           # UI
├── CameraManager.kt         # SDK integration
├── capture/
│   ├── VideoPipeline.kt     # Video frame capture
│   ├── AudioPipeline.kt     # Audio frame capture
│   └── MotionPipeline.kt    # IMU batching
├── uplink/
│   ├── MediaUplink.kt       # Video/audio WebSocket
│   ├── TelemetryUplink.kt   # IMU/events WebSocket
│   └── Backoff.kt           # Reconnection logic
├── model/
│   ├── Settings.kt          # Configuration
│   └── Messages.kt          # Telemetry schemas
└── util/
    └── Clocks.kt            # Timestamp management
```

---

## References

- **Documentation**: See `refdocs/` for detailed technical specs
- **Testing Guide**: `TESTING_GUIDE.md`
- **Streaming Status**: `STREAMING_STATUS.md`
- **Meta SDK**: https://wearables.developer.meta.com/
- **MediaMTX**: https://github.com/bluenviron/mediamtx

---

## License

Personal project. See LICENSE file for details.
