# Quick Start: Server Verification

**Goal:** Get MediaMTX + Telemetry + GPU readers working in ~30 minutes.

**Full guide:** See `PHASE-1-VERIFICATION.md` for detailed explanations.

---

## On Your GPU Server

### 1. Setup (once)

```bash
# Check prerequisites
nvidia-smi && docker --version && tailscale status

# Create directories
sudo mkdir -p /srv/glasses/{recordings,data,derived}
sudo chown -R "$USER": /srv/glasses

# Set timezone to UTC
sudo timedatectl set-timezone UTC

# Get your Tailscale IP
export SERVER_IP=$(tailscale ip -4)
echo "Server IP: $SERVER_IP"
```

### 2. Configure MediaMTX

```bash
cd server/mediamtx

# Generate passphrase and create .env
SRT_PASS=$(openssl rand -base64 32 | tr -d '/+=')
cat > .env << EOF
MTX_PATHS_GLASSES_SRTPUBLISHPASSPHRASE=$SRT_PASS
DATA_ROOT=/srv/glasses
EOF

echo "SRT Passphrase: $SRT_PASS"
echo "$SRT_PASS" > ../passphrase.txt && chmod 600 ../passphrase.txt

# Start MediaMTX
docker compose up -d
docker logs mediamtx  # should show SRT on :8890, RTSP on :8554
```

### 3. Configure Telemetry

```bash
cd ../telemetry

# Generate token and create .env
TELEM_TOKEN=$(openssl rand -hex 24)
cat > .env << EOF
DATA_ROOT=/srv/glasses
TELEMETRY_TOKEN=$TELEM_TOKEN
TELEMETRY_BIND=$SERVER_IP
LOG_LEVEL=info
EOF

echo "Telemetry Token: $TELEM_TOKEN"
echo "$TELEM_TOKEN" > ../telemetry_token.txt && chmod 600 ../telemetry_token.txt

# Install and test
pip install -e '.[dev]'
pytest -q  # should show 35 passed

# Start telemetry receiver
uvicorn app.main:app --host "$SERVER_IP" --port 8000 --ws-max-size 1048576
```

### 4. Firewall

```bash
sudo ufw allow in on tailscale0 to any port 8890 proto udp
sudo ufw allow in on tailscale0 to any port 8000 proto tcp
sudo ufw enable
```

---

## Test the Pipeline

### Test 1: Synthetic Video (15+ minutes)

**Terminal 1:**
```bash
# Publish synthetic video stream
SERVER_IP=$(tailscale ip -4)
SRT_PASS=$(cat server/passphrase.txt)
tools/synthetic_publisher.sh $SERVER_IP "$SRT_PASS"

# Let it run for 15+ minutes (or until a segment closes)
```

**Terminal 2 (while publisher running):**
```bash
# Watch live stream
ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/glasses

# After 15+ mins, check recording
ls -lh /srv/glasses/recordings/glasses/
ffprobe $(ls /srv/glasses/recordings/glasses/*.mp4 | head -1)
```

**Expected:** 504x896 HEVC video + 48kHz AAC audio

---

### Test 2: Synthetic IMU (5 minutes)

```bash
SERVER_IP=$(tailscale ip -4)
TOKEN=$(cat server/telemetry_token.txt)

python tools/synthetic_imu_client.py --host $SERVER_IP --token "$TOKEN" --rate 60 --seconds 300

# Check session created
SESSION_ID=<from-output>
ls -lh /srv/glasses/data/$SESSION_ID/
```

**Expected:** `session.json`, `events.parquet`, `imu/part-*.parquet`

---

### Test 3: GPU Readers

```bash
cd gpu
pip install -e '.[dev]'
pytest -q  # should show 26 passed

# Create fake session with known calibration
cd ..
python tools/fake_session.py --data-root /srv/glasses

# Test calibration
FAKE_SESSION=$(ls -t /srv/glasses/data/ | head -1)
python tools/estimate_offset.py $FAKE_SESSION --data-root /srv/glasses --write

# Check session health
python tools/check_session.py $FAKE_SESSION --data-root /srv/glasses
```

**Expected:** Calibration estimates within 5ms of ground truth (120ms, 80ms)

---

## Success Criteria

✅ **Phase 1 Complete When:**
- MediaMTX records playable fMP4 with HEVC + AAC
- Telemetry stores 18000 IMU rows in 5 minutes (~60 Hz)
- GPU readers pass 26 tests and load fake session
- Calibration tool finds tap offsets within 5ms

❌ **Blocked? Check:**
- Firewall: `sudo ufw status`
- MediaMTX logs: `docker logs mediamtx`
- Telemetry: `curl http://$SERVER_IP:8000/health`
- Ports: `ss -tuln | grep -E '8890|8000|8554'`

---

## What's Next

**DO NOT start Android app yet!**

After Phase 1 passes:
1. Run Spikes S1-S6 (requires phone + glasses) to verify Meta SDK behavior
2. Record findings in `refdocs/DECISIONS.md`
3. Only then build Android app (see `refdocs/TECH-02-android-app.md`)

**Current blockers before Android:**
- Need paired Ray-Ban Meta glasses in Developer Mode
- Need Android phone with Tailscale + USB debugging
- Need to clone Meta's DAT Android SDK and CameraAccess sample

---

## Quick Commands Reference

```bash
# Start services
docker compose -f server/mediamtx/docker-compose.yml up -d
cd server/telemetry && uvicorn app.main:app --host "$SERVER_IP" --port 8000 --ws-max-size 1048576

# Stop services
docker compose -f server/mediamtx/docker-compose.yml down
pkill -f "uvicorn app.main"

# Check health
docker logs mediamtx
curl http://$SERVER_IP:8000/health
ss -tuln | grep -E '8890|8000|8554'

# Run tests
cd server/telemetry && pytest -q
cd gpu && pytest -q
ruff check server gpu tools

# View recordings
ls -lh /srv/glasses/recordings/glasses/
ffplay /srv/glasses/recordings/glasses/<file>.mp4

# Load session
python -c "
from server.telemetry.app.loader import load_session
imu, events, meta = load_session('<session_id>', '/srv/glasses')
print(f'IMU: {len(imu)} rows, Events: {len(events)}')
"
```
