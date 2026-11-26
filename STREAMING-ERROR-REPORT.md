# Streaming Pipeline Error Report

**Date:** 2026-11-26
**Session ID:** b8080acc-c04c-43f9-b262-d6594a2b62c2

## Summary

The Android app is successfully sending video and audio frames to the relay service, but the RTSP stream is not appearing in MediaMTX. The root cause is that FFmpeg cannot determine the HEVC codec parameters (specifically video resolution) from the raw HEVC stream.

---

## Pipeline Status

### ✅ Working Components

1. **Android App → Relay WebSocket**
   - Status: ✅ Connected
   - Frames received: 2010 video, 3890 audio frames
   - Connection: `ws://10.70.24.86:8001/media/b8080acc-c04c-43f9-b262-d6594a2b62c2`
   - Source IP: 10.70.24.81

2. **Relay Service**
   - Status: ✅ Running (HTTP 200)
   - Active sessions: 1
   - WebSocket: Accepting frames successfully
   - Named pipes: Created successfully

### ❌ Failing Components

3. **FFmpeg Process**
   - Status: ❌ Running but stuck
   - PID: 22170
   - Error: Cannot determine HEVC stream parameters
   - Issue: "unspecified size" - cannot extract resolution from HEVC stream

4. **MediaMTX Publisher**
   - Status: ❌ Not connected
   - Path "glasses" exists but: `ready: false, available: false, online: false`
   - No RTSP/HLS stream available

---

## Detailed Error Analysis

### 1. FFmpeg Error Log

```
[hevc @ 0x59926c633780] Could not find codec parameters for stream 0 (Video: hevc, none): unspecified size
Consider increasing the value for the 'analyzeduration' (0) and 'probesize' (5000000) options
Input #0, hevc, from '/tmp/video_b8080acc-c04c-43f9-b262-d6594a2b62c2.pipe':
  Duration: N/A, bitrate: N/A
  Stream #0:0: Video: hevc, none, 25 fps, 1200k tbr, 1200k tbn
```

**Key Issues:**
- Pixel format: `none` (should be yuv420p or similar)
- Size: `unspecified` (should be 504x896 for MEDIUM quality)
- FFmpeg cannot proceed without knowing the video dimensions

### 2. FFmpeg Command Being Used

```bash
ffmpeg \
  -f hevc \
  -i /tmp/video_b8080acc-c04c-43f9-b262-d6594a2b62c2.pipe \
  -f s16le -ar 48000 -ac 1 \
  -i /tmp/audio_b8080acc-c04c-43f9-b262-d6594a2b62c2.pipe \
  -c:v copy \
  -c:a aac -b:a 128k \
  -flush_packets 1 \
  -f flv \
  rtmp://127.0.0.1:1935/glasses
```

**Command Analysis:**
- ✅ Correct format: `-f hevc` for video input
- ✅ Correct audio params: 16-bit PCM, 48kHz, mono
- ✅ Stream copy: `-c:v copy` (no re-encoding)
- ❌ Missing: Video resolution specification
- ❌ Issue: FFmpeg expects SPS/PPS NAL units in the HEVC stream

### 3. Relay Service Errors

```
2026-11-26 14:02:24,295 [INFO] FFmpeg started (PID 22170)
2026-11-26 14:02:32,319 [ERROR] Failed to open pipes: Timeout waiting for FFmpeg to open pipes
```

**Error Sequence:**
1. FFmpeg process starts (PID 22170)
2. Relay waits 10 seconds for FFmpeg to open named pipes for reading
3. FFmpeg fails to open pipes because it can't parse the HEVC stream
4. Relay times out but continues sending frames to closed pipes (data is lost)

### 4. MediaMTX Status

```json
{
  "name": "glasses",
  "ready": false,
  "available": false,
  "online": false,
  "source": null,
  "bytesReceived": 0
}
```

**Interpretation:**
- Path configured but no publisher connected
- FFmpeg never successfully connects via RTMP
- No stream available for RTSP/HLS viewers

---

## Root Cause Analysis

### Primary Issue: Missing HEVC Stream Metadata

The Android app is sending **raw HEVC NAL units** without proper **SPS (Sequence Parameter Set)** and **PPS (Picture Parameter Set)** NAL units, or these units lack resolution information.

**What HEVC needs:**
1. **VPS** (Video Parameter Set) - optional for HEVC Main profile
2. **SPS** (Sequence Parameter Set) - **REQUIRED** - contains resolution, profile, level
3. **PPS** (Picture Parameter Set) - **REQUIRED** - contains encoding parameters
4. **IDR/I-frames** with actual video data

**What FFmpeg expects:**
- SPS/PPS NAL units at the start of the stream
- Or embedded in the stream before the first I-frame
- These units contain the video resolution and codec parameters

**What's happening:**
- Android app may be sending only VCL (Video Coding Layer) NAL units (slices)
- Or sending SPS/PPS but they're malformed or incomplete
- FFmpeg cannot extract dimensions → cannot initialize encoder/muxer → pipe stays closed

---

## Attempted Fixes (Unsuccessful)

### Fix #1: Specify Resolution Explicitly
```bash
-s 504x896  # Added to FFmpeg command
```
**Result:** `Option video_size not found` error
**Reason:** Wrong syntax for raw HEVC input

### Fix #2: Remove Size Option, Use Stream Copy
```bash
-f hevc -i pipe  # Let FFmpeg auto-detect
-c:v copy        # No re-encoding
```
**Result:** Still fails - FFmpeg waits forever for SPS/PPS
**Reason:** HEVC stream lacks required metadata

---

## Evidence from Logs

### Timeline of Current Session

```
14:02:22 - WebSocket connection opened from Android app
14:02:22 - Relay creates named pipes
14:02:22 - FFmpeg process started (PID 22170)
14:02:22 - Relay begins waiting for FFmpeg to open pipes
14:02:24 - [2 seconds] Still waiting...
14:02:32 - [10 seconds] TIMEOUT: FFmpeg never opened pipes
14:02:33 - Relay continues receiving frames (1022 video, 1978 audio)
14:03:XX - Relay keeps logging frames but data is being discarded
14:04:14 - Now at 2010 video, 3890 audio frames (all lost)
```

### Frame Rate Analysis

- Video frames: 2010 in ~2 minutes = ~16.75 fps (expected 24 fps)
- Audio frames: 3890 in ~2 minutes
- Both are flowing but not being processed by FFmpeg

---

## Possible Solutions

### Solution A: Add SPS/PPS to Android App ⭐ **Recommended**

**In VideoPipeline.kt or CameraManager.kt:**

1. Check if Meta SDK provides SPS/PPS NAL units:
   ```kotlin
   // Look for methods like:
   videoFrame.sps  // Sequence Parameter Set
   videoFrame.pps  // Picture Parameter Set
   videoFrame.getParameterSets()
   ```

2. If available, send SPS/PPS before video frames:
   ```kotlin
   // On first frame or keyframe:
   if (videoFrame.isKeyframe) {
       val sps = videoFrame.sps
       val pps = videoFrame.pps

       // Send SPS NAL unit (type 33 for HEVC)
       mediaUplink.sendVideoFrame(sps, timestamp, isKeyframe = true)

       // Send PPS NAL unit (type 34 for HEVC)
       mediaUplink.sendVideoFrame(pps, timestamp, isKeyframe = false)
   }

   // Then send actual frame
   mediaUplink.sendVideoFrame(frame.buffer, timestamp, videoFrame.isKeyframe)
   ```

3. Update binary protocol to include NAL unit type in flags field

**Expected outcome:** FFmpeg can parse SPS/PPS and extract resolution

---

### Solution B: Use Annex-B Format

If the Android app is sending length-prefixed NAL units, convert to Annex-B:

**Current (length-prefixed):**
```
[4-byte length][NAL unit data]
```

**Annex-B format:**
```
[0x00 0x00 0x00 0x01][NAL unit data]
```

FFmpeg expects Annex-B for raw HEVC input.

**In VideoPipeline.kt:**
```kotlin
// Convert length-prefix to Annex-B start codes
val buffer = videoFrame.buffer
val nalStartCode = byteArrayOf(0x00, 0x00, 0x00, 0x01)

// Send start code + NAL unit
mediaUplink.send(nalStartCode + buffer.array())
```

---

### Solution C: Hardcode Resolution in FFmpeg ⚠️ **Workaround**

Since we know the resolution (504x896 for MEDIUM), force FFmpeg to use it:

**Update relay app.py:**
```python
cmd = [
    'ffmpeg',
    # Force resolution for raw HEVC
    '-f', 'hevc',
    '-video_size', '504x896',  # Different syntax
    '-framerate', '24',
    '-i', video_pipe,
    # ... rest of command
]
```

**Limitations:**
- Breaks if user changes video quality setting
- Doesn't fix underlying SPS/PPS issue
- Not recommended for production

---

### Solution D: Re-encode with libx264 (Brute Force) ⚠️ **Last Resort**

Force FFmpeg to decode and re-encode, losing the stream copy benefit:

```python
cmd = [
    'ffmpeg',
    '-f', 'hevc',
    '-video_size', '504x896',
    '-framerate', '24',
    '-i', video_pipe,
    # ... audio input ...
    '-c:v', 'libx264',  # Re-encode to H.264
    '-preset', 'ultrafast',
    '-tune', 'zerolatency',
    '-c:a', 'aac',
    '-f', 'flv',
    'rtmp://127.0.0.1:1935/glasses'
]
```

**Downsides:**
- High CPU usage (encoding on CPU)
- Defeats purpose of hardware HEVC from glasses
- Increases latency

---

## Diagnostic Commands

### Check if SPS/PPS are being sent

```bash
# Dump first 1000 bytes from video pipe (careful - blocks until data arrives)
timeout 5 head -c 1000 /tmp/video_*.pipe | hexdump -C | head -50

# Look for HEVC NAL unit start codes:
# 00 00 00 01 - Annex-B start code
# 40 01 - VPS NAL (type 32)
# 42 01 - SPS NAL (type 33)
# 44 01 - PPS NAL (type 34)
```

### Test FFmpeg with known resolution

```bash
# Stop current relay
pkill -f "python.*relay"

# Start FFmpeg manually with forced resolution
ffmpeg -video_size 504x896 -framerate 24 -f hevc \
  -i /tmp/video_test.pipe \
  -f s16le -ar 48000 -ac 1 \
  -i /tmp/audio_test.pipe \
  -c:v copy -c:a aac \
  -f flv rtmp://127.0.0.1:1935/glasses
```

### Check MediaMTX RTMP acceptance

```bash
# See if MediaMTX accepts RTMP connections
docker logs mediamtx 2>&1 | grep -i rtmp

# Check RTMP port
ss -tuln | grep 1935
```

---

## Next Steps

### Immediate Actions (Priority Order)

1. **Investigate Android app** - Check if Meta SDK provides SPS/PPS
   - Read `refdocs/TECH-02-android-app.md`
   - Look at VideoFrame API documentation
   - Check if `videoFrame.buffer` includes parameter sets

2. **Test NAL unit format** - Determine if length-prefixed or Annex-B
   - Dump first video frame from pipe
   - Look for `00 00 00 01` start codes vs `00 00 XX XX` length prefix

3. **Implement Solution A** - Add SPS/PPS transmission
   - Modify VideoPipeline.kt to send parameter sets
   - Update protocol if needed
   - Test with FFmpeg

4. **Fallback to Solution C** - Hardcode resolution if needed
   - Quick workaround to unblock
   - Add TODO to fix properly

### Documentation Needed

- [ ] Document Meta SDK VideoFrame structure
- [ ] Document HEVC NAL unit format from SDK
- [ ] Update DECISIONS.md with findings
- [ ] Add Spike S1 results (compressed-video byte format)

---

## References

- **TECH-01 section 7:** Plan B uplink protocol
- **TECH-02 section 8:** VideoPipeline implementation
- **FFmpeg HEVC docs:** https://ffmpeg.org/ffmpeg-codecs.html#hevc
- **HEVC NAL units:** ITU-T H.265 spec, Annex B

---

## Logs Archive

**Session:** b8080acc-c04c-43f9-b262-d6594a2b62c2
**FFmpeg log:** /tmp/ffmpeg_b8080acc-c04c-43f9-b262-d6594a2b62c2.log
**Relay log:** /tmp/relay.log
**MediaMTX log:** `docker logs mediamtx`

**Status:** Issue identified, awaiting Android app investigation
