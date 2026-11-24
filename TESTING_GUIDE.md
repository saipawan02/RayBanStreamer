# Testing Guide - IMU Streaming Pipeline

## Quick Start

### 1. Start the Telemetry Receiver

**Terminal 1 - Server:**
```bash
cd server/telemetry
./start.sh
```

You should see:
```
Starting telemetry receiver on 0.0.0.0:8000
WebSocket Token: test-token-change-me-in-production
Data Root: /tmp/glasses
```

The server is now listening for WebSocket connections.

---

### 2. Configure the Android App

**On your phone:**
1. Open the RayBanStreamer app
2. In the "⚙️ SERVER SETTINGS" section:
   - **Server Host**: Enter `localhost` or your machine's IP
     - If testing locally with USB: `10.0.2.2` (Android emulator) or actual IP
     - If over network: your machine's local IP (e.g., `192.168.1.100`)
   - **WebSocket Token**: Enter `test-token-change-me-in-production`
3. Tap **SAVE SETTINGS**

---

### 3. Run a Streaming Session

**On your phone:**
1. Tap **REGISTER WITH GLASSES** (if not already registered)
2. Tap **REQUEST CAMERA ACCESS** (if needed)
3. Tap **CHECK DEVICES** - verify glasses show as connected
4. Tap **START SESSION** - wait for "✅ Session active"
5. Tap **START STREAMING**

**You should see:**
- Video preview on phone screen ✓
- "Frames received:" counter increasing ✓
- "IMU samples:" counter increasing ✓
- Status showing "🎥 Streaming!"

---

### 4. Monitor Server Logs

**Terminal 1 - You should see:**
```
INFO: WebSocket connection opened: /ingest/<session_id>
INFO: Sent hello: next_seq=0
INFO: Received imu_batch seq=0, samples=12
INFO: Received imu_batch seq=1, samples=43
INFO: Sent ack: seq=50
...
```

---

### 5. Monitor Phone Logs

**Terminal 2 - From your computer:**
```bash
adb logcat -s MotionPipeline:I TelemetryUplink:I CameraManager:I | grep -E "(Motion|IMU|Telemetry|batch)"
```

**You should see:**
```
I MotionPipeline: Motion added successfully
I TelemetryUplink: WebSocket opened
I TelemetryUplink: Received hello, next_seq=0
I TelemetryUplink: Sent imu_batch seq=0
I TelemetryUplink: Acked up to seq 50
I CameraManager: Frame 120
```

---

### 6. Verify Data on Disk

**After streaming for 10+ seconds, check:**
```bash
# Find your session ID from server logs or phone logs
# It looks like: b6f0c2e4-8a31-4d0e-9b57-0a1c2d3e4f50

ls -lah /tmp/glasses/data/<session_id>/

# You should see:
# - session.json
# - events.parquet
# - imu/part-000001.parquet
# - imu/part-000002.parquet (etc.)
```

**Inspect the data:**
```bash
# View session metadata
cat /tmp/glasses/data/<session_id>/session.json | python3 -m json.tool

# Count IMU rows
python3 -c "
import pyarrow.parquet as pq
import glob
files = glob.glob('/tmp/glasses/data/<session_id>/imu/*.parquet')
total = sum(len(pq.read_table(f)) for f in files)
print(f'Total IMU samples: {total}')
"
```

---

## Troubleshooting

### App crashes on startup
- **Fixed!** CameraManager now initializes with default settings

### "WebSocket connection failed"
1. Check server is running (`./start.sh`)
2. Verify Server Host in app settings (use IP, not hostname)
3. Check firewall: `sudo ufw allow 8000/tcp` (if using firewall)
4. Test connection: `curl http://<your-ip>:8000/health`

### "IMU samples: 0" (not increasing)
1. Check phone logs: `adb logcat -s CameraManager:I`
2. Look for "Motion added successfully"
3. If missing, glasses may need firmware update (requires firmware 129+)
4. Try stopping and restarting the stream

### No data files created
1. Check server logs for errors
2. Verify DATA_ROOT directory exists and is writable: `ls -ld /tmp/glasses`
3. Check session.json was created: `ls /tmp/glasses/data/`

### Video shows but no IMU
- Settings have `imuEnabled: true` by default
- Motion requires no special permission on glasses
- Check CameraManager logs for Motion errors

---

## Expected Behavior

**At 60Hz IMU rate:**
- ~600 samples in 10 seconds
- ~3,600 samples in 1 minute
- Batched every 100ms or 64 samples (whichever first)
- ~10 batches per second sent to server

**WebSocket Protocol:**
- Server sends "hello" with next_seq
- Phone sends imu_batch messages with incrementing seq
- Server acks every 50 messages
- Automatic reconnection with backoff if disconnected

**File Structure:**
```
/tmp/glasses/data/<session_id>/
├── session.json          # Metadata, settings, counts
├── events.parquet        # Event log (imu_started, etc.)
└── imu/
    ├── part-000001.parquet  # First 5 seconds or 1000 rows
    ├── part-000002.parquet  # Next batch
    └── ...
```

---

## Success Criteria

✅ **Pipeline is working when:**
1. Server receives WebSocket connection
2. Phone sends imu_batch messages
3. Server acks messages (every 50)
4. Parquet files created with IMU data
5. No crashes or disconnections
6. IMU counter on phone increases continuously

---

## Next Steps

After confirming IMU streaming works:
1. **Add video upload** (Plan B WebSocket or Plan A MPEG-TS+SRT)
2. **Add audio streaming**
3. **Test with Tailscale** (put server on tailnet IP)
4. **Build foreground service** (stream while app backgrounded)
5. **Run soak test** (30+ minute session)

---

## Clean Up

**Stop server:**
```bash
# In Terminal 1, press Ctrl+C
```

**Clear test data:**
```bash
rm -rf /tmp/glasses/data/*
```

**Restart fresh:**
```bash
cd server/telemetry
./start.sh
```
