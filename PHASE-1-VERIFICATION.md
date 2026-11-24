# Phase 1: Server Infrastructure Verification

**Goal:** Prove MediaMTX + Telemetry + GPU readers work end-to-end with synthetic data **before** starting the Android app.

**Reference:** SOW section 13 build order, TECH-03, TECH-04, TECH-05

---

## Prerequisites Check (run on GPU server)

```bash
# 1. Check NVIDIA driver and CUDA
nvidia-smi  # should show your GPU

# 2. Check Docker and Compose
docker --version
docker compose version

# 3. Check time sync
timedatectl  # should show "System clock synchronized: yes"
sudo timedatectl set-timezone UTC  # if not already UTC

# 4. Create data directories
sudo mkdir -p /srv/glasses/recordings /srv/glasses/data /srv/glasses/derived
sudo chown -R "$USER": /srv/glasses

# 5. Check Tailscale
tailscale status
tailscale ip -4  # note this IP for later (call it SERVER_IP)
```

---

## Step 1: Configure Secrets

**On the GPU server** in `server/mediamtx/`:

```bash
cd server/mediamtx

# Generate a random passphrase (10-79 chars)
SRT_PASS=$(openssl rand -base64 32 | tr -d '/+=')
echo "Generated passphrase: $SRT_PASS"

# Create .env file
cat > .env << EOF
MTX_PATHS_GLASSES_SRTPUBLISHPASSPHRASE=$SRT_PASS
DATA_ROOT=/srv/glasses
EOF

# SAVE THIS PASSPHRASE - you'll need it for synthetic_publisher.sh
echo "$SRT_PASS" > ../passphrase.txt  # temporary, for your convenience
chmod 600 ../passphrase.txt
```

**On the GPU server** in `server/telemetry/`:

```bash
cd server/telemetry

# Generate telemetry token
TELEM_TOKEN=$(openssl rand -hex 24)
echo "Generated token: $TELEM_TOKEN"

# Get Tailscale IP
SERVER_IP=$(tailscale ip -4)

# Create .env file
cat > .env << EOF
DATA_ROOT=/srv/glasses
TELEMETRY_TOKEN=$TELEM_TOKEN
TELEMETRY_BIND=$SERVER_IP
LOG_LEVEL=info
EOF

# SAVE THIS TOKEN - you'll need it for synthetic_imu_client.py
echo "$TELEM_TOKEN" > ../telemetry_token.txt  # temporary
chmod 600 ../telemetry_token.txt
```

---

## Step 2: Firewall Rules

```bash
# Allow SRT and telemetry ONLY on Tailscale interface
sudo ufw default deny incoming
sudo ufw allow in on tailscale0 to any port 8890 proto udp comment "SRT ingest"
sudo ufw allow in on tailscale0 to any port 8000 proto tcp comment "Telemetry"
sudo ufw allow in on lo comment "Localhost"
sudo ufw enable
sudo ufw status verbose  # verify rules
```

---

## Step 3: Start MediaMTX

```bash
cd server/mediamtx

# Start the container
docker compose up -d

# Check logs (should show SRT listening on :8890, RTSP on :8554)
docker logs -f mediamtx

# Expected output includes:
#   [SRT] listener opened on :8890
#   [RTSP] listener opened on :8554
#   [path glasses] created
```

**Exit criterion:** Logs show SRT and RTSP listeners active, no errors.

---

## Step 4: Publish Synthetic Video Stream

**From any machine on the tailnet** (or the server itself):

```bash
# Get the passphrase
SRT_PASS=$(cat server/passphrase.txt)  # or paste it manually
SERVER_IP=<your-tailscale-ip>

# Run synthetic publisher (will run for ~15+ minutes to create a segment)
tools/synthetic_publisher.sh $SERVER_IP "$SRT_PASS"

# Expected: ffmpeg output showing encoding progress
# Let it run for at least 15 minutes (or until you see recordSegmentDuration)
```

**In another terminal, watch MediaMTX logs:**

```bash
docker logs -f mediamtx
# Should show: [path glasses] publisher connected
```

---

## Step 5: Verify Live RTSP Playback

**While synthetic_publisher.sh is running**, on the GPU server:

```bash
# Play the live stream (requires ffplay)
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/glasses

# You should see:
# - A moving test pattern (testsrc2, 504x896 portrait)
# - Hear a 440 Hz tone
# - Smooth playback
```

**Exit criterion:** Live stream plays with video and audio.

---

## Step 6: Verify Recording

**After 15+ minutes** (or stop the publisher with Ctrl+C):

```bash
# Check recordings directory
ls -lh /srv/glasses/recordings/glasses/

# Should show at least one .mp4 file, e.g.:
#   2026-11-24_14-40-01-123456.mp4

# Probe the recording
RECORDING=$(ls /srv/glasses/recordings/glasses/*.mp4 | head -1)
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,sample_rate,channels -of default=nw=1 "$RECORDING"

# Expected output:
#   codec_name=hevc
#   width=504
#   height=896
#   r_frame_rate=24/1
#   codec_name=aac
#   sample_rate=48000
#   channels=1

# Play the recording
ffplay "$RECORDING"
```

**Exit criterion:** Recording is playable, has HEVC video (504x896@24fps) and AAC audio (48kHz mono).

---

## Step 7: Start Telemetry Receiver

```bash
cd server/telemetry

# Install dependencies (in a venv if you prefer)
pip install -e '.[dev]'

# Run tests first (should show 35 passing)
pytest -q

# Start the service
SERVER_IP=$(tailscale ip -4)
uvicorn app.main:app --host "$SERVER_IP" --port 8000 --ws-max-size 1048576

# Expected output:
#   INFO:     Started server process
#   INFO:     Waiting for application startup.
#   INFO:     Application startup complete.
#   INFO:     Uvicorn running on http://<SERVER_IP>:8000
```

**In another terminal, test health endpoint:**

```bash
SERVER_IP=$(tailscale ip -4)
curl http://$SERVER_IP:8000/health

# Expected: {"status":"healthy","active_sessions":0}
```

---

## Step 8: Send Synthetic IMU Data

**From any tailnet machine:**

```bash
SERVER_IP=<your-tailscale-ip>
TOKEN=$(cat server/telemetry_token.txt)  # or paste manually

# Run synthetic IMU client for 5 minutes at 60 Hz
python tools/synthetic_imu_client.py --host $SERVER_IP --token "$TOKEN" --rate 60 --seconds 300

# Expected output:
#   Connected to ws://<SERVER_IP>:8000/ingest/<session_id>?token=...
#   Received hello: next_seq=0
#   Sent 100 batches (6000 samples)...
#   Done. Sent 300 batches (18000 samples) in 300.0s
```

**Check the data directory:**

```bash
# Find the session ID from the client output
SESSION_ID=<from-client-output>

# Check session files
ls -lh /srv/glasses/data/$SESSION_ID/

# Expected files:
#   session.json
#   events.parquet
#   imu/part-000001.parquet (and more parts)

# Load the session in Python
cd server/telemetry
python -c "
from app.loader import load_session
imu, events, meta = load_session('$SESSION_ID', '/srv/glasses')
print(f'IMU rows: {len(imu)}, Events: {len(events)}')
print(f'IMU rate: {len(imu) / 300:.1f} Hz')
"

# Expected: ~18000 rows, ~60 Hz
```

**Exit criterion:** Synthetic IMU client runs for 5 minutes, writes ~18000 rows at ~60 Hz, session loads successfully.

---

## Step 9: Create Fake Session with Known Offsets

```bash
# Create a complete fake session (video + IMU + events) with known tap offsets
cd gpu
pip install -e '.[dev]'  # if not already installed

cd ..
python tools/fake_session.py --data-root /srv/glasses --segments 2 --seconds 30

# Output shows:
#   Created session <UUID>
#   Video segments: 2
#   IMU rows: ~1800
#   Events: ~10
#   Ground truth: imu_to_video_ms=120.0, audio_to_video_ms=80.0

# Save the session ID for next steps
FAKE_SESSION_ID=<from-output>
```

---

## Step 10: Test GPU Readers (CPU Mode)

```bash
cd gpu

# Run all GPU reader tests (26 should pass, using PyAV on CPU)
pytest -q

# Test reading the fake session
python -c "
from glasses_reader import SessionReader
reader = SessionReader('$FAKE_SESSION_ID', data_root='/srv/glasses')
print(f'Session: {reader.session_id}')
print(f'Recordings: {reader.recordings}')
print(f'Duration: {reader.duration_s:.1f}s')

# Try to read some frames
frames = list(reader.iter_frames(max_frames=10))
print(f'Read {len(frames)} frames')
print(f'First frame shape: {frames[0].frame.shape}')
"

# Expected:
#   Session: <UUID>
#   Recordings: ['recordings/glasses/2026-11-24_...mp4', ...]
#   Duration: 30.0s
#   Read 10 frames
#   First frame shape: (896, 504, 3)
```

**Exit criterion:** All 26 tests pass, fake session loads, frames read successfully.

---

## Step 11: Test Calibration (Tap Test Estimation)

```bash
# Run estimate_offset on the fake session (which has known ground truth)
python tools/estimate_offset.py $FAKE_SESSION_ID --data-root /srv/glasses --write

# Expected output:
#   Session: <UUID>
#   Found 3 taps in IMU (gyro magnitude peaks)
#   Found 3 taps in video (frame difference peaks)
#   Found 3 taps in audio (energy peaks)
#
#   Estimated offsets:
#     imu_to_video_ms: 120.5 ± 2.3  (ground truth: 120.0)
#     audio_to_video_ms: 80.2 ± 1.8  (ground truth: 80.0)
#
#   Written to session.json

# Check session health
python tools/check_session.py $FAKE_SESSION_ID --data-root /srv/glasses

# Expected: All checks pass (green), no errors
```

**Exit criterion:** `estimate_offset` finds the taps and reports offsets within 5ms of ground truth (120ms and 80ms).

---

## Step 12: Crash Recovery Test

```bash
# Start a synthetic IMU client that will run for 10 minutes
python tools/synthetic_imu_client.py --host $SERVER_IP --token "$TOKEN" --rate 60 --seconds 600 &
CLIENT_PID=$!
SESSION_ID=<from-client-output>

# Wait 2 minutes, then kill the telemetry receiver
sleep 120
pkill -9 -f "uvicorn app.main:app"

# Wait 5 seconds, restart it
sleep 5
cd server/telemetry
uvicorn app.main:app --host "$SERVER_IP" --port 8000 --ws-max-size 1048576 &

# Let the client finish
wait $CLIENT_PID

# Check data loss
python -c "
from app.loader import load_session
imu, events, meta = load_session('$SESSION_ID', '/srv/glasses')
print(f'IMU rows: {len(imu)} (expected ~36000)')
print(f'Loss: {36000 - len(imu)} rows (~{(36000 - len(imu)) / 60:.1f} seconds)')
"

# Expected loss: < 10 seconds (< 600 rows)
```

**Exit criterion:** Crash loses < 10 seconds of IMU data.

---

## Step 13: Document Results

Fill in `refdocs/TESTLOG.md` with results from each step above:

```markdown
## 2026-11-24: Phase 1 Server Verification

**Server:** <hostname>, Ubuntu <version>, GPU <model>
**MediaMTX:** <version from docker logs>
**Python:** <version>

| Test | Status | Notes |
|------|--------|-------|
| MediaMTX starts | ✅ | SRT on :8890, RTSP on :8554 |
| Synthetic publisher | ✅ | 15min stream, no drops |
| Live RTSP playback | ✅ | Smooth video+audio |
| Recording created | ✅ | HEVC 504x896@24fps, AAC 48kHz mono |
| Telemetry tests | ✅ | 35/35 passing |
| Synthetic IMU 5min | ✅ | 18000 rows, 60Hz |
| GPU reader tests | ✅ | 26/26 passing (PyAV/CPU) |
| Fake session created | ✅ | 30s, 2 segments |
| Fake session reads | ✅ | 10 frames read |
| Calibration estimate | ✅ | Within 5ms of ground truth |
| Crash recovery | ✅ | <10s data loss |

**Next:** Start Android app (Phase 3) after completing Spikes S1-S6.
```

---

## Acceptance Criteria (from SOW section 11, TECH-03 section 7)

✅ **Pass if:**
- MediaMTX smoke test records a playable fMP4 with HEVC and AAC
- Live RTSP stream plays from localhost
- Synthetic IMU stream writes ~18000 rows in 5 minutes (within 1%)
- Receiver crash loses < 10 seconds of data
- GPU readers load fake session and extract frames
- Calibration tool estimates offsets within 5ms of ground truth

❌ **Fail if:**
- MediaMTX doesn't start or rejects the synthetic stream
- Recording is empty or unplayable
- Telemetry tests fail
- GPU reader tests fail
- Data loss > 10 seconds on crash

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Connection refused" on SRT | Check firewall: `sudo ufw status`, `ss -ulpn \| grep 8890` |
| "Wrong passphrase" in MediaMTX logs | Verify `.env` file has correct `MTX_PATHS_GLASSES_SRTPUBLISHPASSPHRASE` |
| Recording file empty | Check `docker exec mediamtx ls /recordings`, confirm `/srv/glasses` is writable |
| Telemetry "token invalid" | Verify token matches in `.env` and client command |
| GPU reader tests fail | Check PyAV installed: `python -c "import av; print(av.__version__)"` |
| ffplay command not found | Install: `sudo apt install ffmpeg` |

---

## What's Next

**After Phase 1 passes:**
1. **Do NOT start Android app yet**
2. Run Spikes S1-S6 to verify Meta SDK behavior (requires phone + glasses)
3. Only after spikes: build Android app (Phase 3)

**Current Status:** ✅ Server-side infrastructure proven, ready for phone integration.
