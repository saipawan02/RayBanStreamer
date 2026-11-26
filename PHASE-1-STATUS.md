# Phase 1 Diagnostics - Implementation Status

**Date:** 2026-11-26
**Status:** ✅ Code Complete - Ready for Testing

---

## ✅ Completed Tasks

### 1. VideoPipeline.kt - Comprehensive Diagnostics Added

**Location:** `android/app/src/main/kotlin/com/glassesstream/capture/VideoPipeline.kt`

**Added Logging:**
- ✅ Frame counter with detailed logs every 30 frames + first 10 frames
- ✅ `isCompressed` verification (confirms `compressVideo = true`)
- ✅ `isCodecConfig` detection and logging
- ✅ Keyframe detection and counting
- ✅ Buffer size logging
- ✅ Hexdump of first 32 bytes of every diagnostic frame
- ✅ Full hexdump (128 bytes) of codec config frames
- ✅ Summary statistics on stop (total frames, codec configs, keyframes, dropped)

**What It Will Show:**
```
=== PHASE 1 DIAGNOSTIC ===
Frame #1
  isCompressed: true/false
  isCodecConfig: true/false
  buffer size: XXXX bytes
  width: 504/720
  height: 896/1280
  presentationTimeUs: XXXXXXX
  First 32 bytes:
  00 00 00 01 40 01 ... (hex dump)

Frame #X: CODEC CONFIG frame (total: Y)
  Config frame size: XXXX bytes
  Config data: 00 00 00 01 ... (full dump)

Frame #Z: KEYFRAME detected (total: N)
```

### 2. MediaUplink.kt - Codec Config Tracking

**Location:** `android/app/src/main/kotlin/com/glassesstream/uplink/MediaUplink.kt`

**Added:**
- ✅ Codec config frame counter
- ✅ Logging when codec config frames are sent to server
- ✅ Hexdump of first 32 bytes of codec config frames
- ✅ Summary statistics in stop() method

**What It Will Show:**
```
=== SENDING CODEC CONFIG FRAME #1 ===
  Size: XXXX bytes
  First 32 bytes: 00 00 00 01 40 01 ...

=== MEDIA UPLINK SUMMARY ===
Total frames sent: XXXX
Codec config frames: Y
Total bytes: XX.XX MB
```

### 3. VideoPacket - Added isCodecConfig Field

**Updated Data Class:**
```kotlin
data class VideoPacket(
    val data: ByteArray,
    val presentationTimeUs: Long,
    val isKeyframe: Boolean,
    val isCodecConfig: Boolean,  // NEW: VPS/SPS/PPS parameter sets
    val width: Int,
    val height: Int,
    val isCompressed: Boolean
)
```

### 4. Stream Configuration Verified

**Location:** `CameraManager.kt:540`

```kotlin
StreamConfiguration(
    videoQuality = MEDIUM/HIGH/LOW,
    frameRate = 24/30,
    compressVideo = true,  // ✅ Verified - HEVC compression enabled
    audioCodec = PCM(...)
)
```

---

## 📱 Installation Instructions

### Step 1: Authorize USB Debugging

The phone is currently UNAUTHORIZED. On your phone:

1. **Unplug and replug** the USB cable
2. A dialog should appear: **"Allow USB debugging?"**
3. Tap **"Always allow from this computer"**
4. Tap **"Allow"**

If no dialog appears:
```bash
# Kill and restart adb server
adb kill-server
adb start-server
adb devices
```

You should see:
```
List of devices attached
32004e065e1e5693    device     (not "unauthorized")
```

### Step 2: Install the App

```bash
cd /home/sai-pawan/Documents/RayBanStreamer/android
./gradlew installDebug
```

Expected output:
```
Installing APK 'com.glassesstream.debug-1.0.apk'...
Installed on 1 device.
BUILD SUCCESSFUL
```

### Step 3: Start Streaming and Collect Logs

**Terminal 1 - Start log collection:**
```bash
adb logcat -c  # Clear old logs
adb logcat -s VideoPipeline:I MediaUplink:I CameraManager:I > /tmp/phase1-diagnostics.log
```

**On Phone:**
1. Open RayBanStreamer app
2. START SESSION (wait for "✅ Session active")
3. START STREAMING
4. **Let it stream for 30 seconds**
5. STOP STREAMING

**Terminal 1 - Stop (Ctrl+C)**

The log will be saved to `/tmp/phase1-diagnostics.log`

---

## 🔍 What to Look For in Logs

### Critical Questions Phase 1 Answers:

**1. Is compressVideo working?**
```
Look for: "isCompressed: true"
If false: compressVideo setting is not working
```

**2. Are codec config frames being sent?**
```
Look for: "CODEC CONFIG frame (total: X)"
Expected: At least 1-3 codec config frames at start
If 0: SDK is not sending VPS/SPS/PPS
```

**3. What NAL unit format?**

Look at hexdump of first codec config frame:

**Annex-B format (good for FFmpeg):**
```
00 00 00 01 40 01 ...  (4-byte start code)
or
00 00 01 40 01 ...     (3-byte start code)
```

**Length-prefixed format (needs conversion):**
```
00 00 00 XX YY ZZ ...  (4-byte length, then NAL data)
```

**4. Are keyframes detected?**
```
Look for: "KEYFRAME detected (total: X)"
Expected: At 24fps, ~1 keyframe per 1-2 seconds
```

**5. What's in the codec config?**

Codec config should contain VPS/SPS/PPS NAL units:
- **VPS** (Video Parameter Set): NAL type 32 (0x40)
- **SPS** (Sequence Parameter Set): NAL type 33 (0x42)
- **PPS** (Picture Parameter Set): NAL type 34 (0x44)

```
Example good codec config hexdump:
00 00 00 01 40 01 ...  <- VPS starts here
00 00 00 01 42 01 ...  <- SPS starts here (contains resolution!)
00 00 00 01 44 01 ...  <- PPS starts here
```

---

## 📊 Expected Results

### If Everything Works:

```
=== PHASE 1 DIAGNOSTIC ===
Frame #1
  isCompressed: true  ✅
  isCodecConfig: true  ✅
  buffer size: 150-500 bytes  ✅
  ...
  First 32 bytes:
  00 00 00 01 40 01 ...  ✅ Annex-B start code

Frame #2
  isCodecConfig: false
  KEYFRAME detected

=== SENDING CODEC CONFIG FRAME #1 ===  ✅
  Size: XXX bytes
  First 32 bytes: 00 00 00 01 40 01 ...

=== VIDEO PIPELINE SUMMARY ===
Total frames processed: ~720 (30s @ 24fps)
Codec config frames: 1-3  ✅
Keyframes detected: 15-30  ✅
Frames dropped: 0  ✅
```

### If Codec Config Missing:

```
=== VIDEO PIPELINE SUMMARY ===
Codec config frames: 0  ❌ PROBLEM!
```

**This means:** SDK is not sending SPS/PPS, and FFmpeg cannot determine stream parameters.

**Solution:** Manually extract and prepend SPS/PPS (Phase 2)

---

## 🚦 Next Steps Based on Results

### Scenario A: Codec Config Present, Annex-B Format
✅ **Best case!**
- FFmpeg should be able to parse the stream
- Problem might be in relay service (Phase 3)
- Move to Phase 2: Ensure codec config is sent before first frame

### Scenario B: Codec Config Present, Length-Prefixed Format
⚠️ **Needs conversion**
- Phase 2: Convert length-prefix to Annex-B
- Add start codes (00 00 00 01) before each NAL unit

### Scenario C: No Codec Config Frames
❌ **Major issue**
- SDK might not be exposing VPS/SPS/PPS
- Phase 2: Need to extract from VideoFrame metadata
- Might need to call specific SDK method to get codec parameters

### Scenario D: Codec Config Not Sent to Server
⚠️ **Pipeline issue**
- Codec config detected but not uploaded
- Check if `frame.isCodecConfig` is being filtered somewhere
- Phase 2: Ensure codec config frames are forwarded

---

## 📝 Diagnostic Checklist

After running the test, check these:

**VideoPipeline Logs:**
- [ ] First frame shows `isCompressed: true`
- [ ] At least 1 frame shows `isCodecConfig: true`
- [ ] Codec config hexdump starts with `00 00 00 01` or `00 00 01`
- [ ] Multiple keyframes detected (not just codec config)
- [ ] No "uncompressed frame" warnings

**MediaUplink Logs:**
- [ ] "SENDING CODEC CONFIG FRAME" appears at least once
- [ ] Codec config is sent early (within first few frames)
- [ ] Regular frames are sent after codec config

**Summary Stats:**
- [ ] Codec config count matches between VideoPipeline and MediaUplink
- [ ] Keyframe count is reasonable (~1-2 per second)
- [ ] Dropped frames is low (< 10)

---

## 🛠️ Troubleshooting

### "Device is UNAUTHORIZED"
```bash
adb kill-server
adb start-server
# Replug phone, tap "Always allow"
```

### "No devices found"
```bash
adb devices
# Should show: 32004e065e1e5693    device
```

### App crashes on start
```bash
adb logcat | grep AndroidRuntime
# Check for crash stack trace
```

### No diagnostic logs appearing
Check log filters:
```bash
# Use broader filter
adb logcat | grep -E "VideoPipeline|MediaUplink|CameraManager"
```

---

## 📦 Build Artifacts

**APK Location:** `android/app/build/outputs/apk/debug/app-debug.apk`
**Build Status:** ✅ Compiled successfully
**Install Status:** ⏳ Waiting for USB authorization

---

## Next: Phase 2

Once Phase 1 diagnostics are collected, we'll move to **Phase 2: Fix the video pipeline**:

1. Forward codec configuration frames
2. Preserve VPS/SPS/PPS
3. Preserve keyframes
4. Verify/convert Annex-B format
5. Cache configuration for restarts

**Current Status:** Waiting for user to authorize USB and run Phase 1 diagnostics.
