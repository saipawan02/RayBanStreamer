# Phase 1 Diagnostics - Results & Analysis

**Date:** 2026-11-26
**Session ID:** 6df87d99-1170-4604-9b92-48e9c1a9470d
**Status:** ✅ DIAGNOSTIC COMPLETE - ROOT CAUSE IDENTIFIED

---

## Executive Summary

**Root Cause Identified:** The Meta SDK sends VPS/SPS/PPS codec parameters in the **first frame**, but does NOT mark it with `isCodecConfig = true`. This causes FFmpeg to fail because it cannot extract the stream parameters needed to initialize the decoder.

**Impact:** FFmpeg sits waiting for codec parameters that it can find, while they're buried in the first video frame mixed with actual video data.

---

## ✅ What Works

1. **Compression**: `isCompressed: true` for all frames ✅
2. **Annex-B Format**: All frames use `00 00 00 01` start codes ✅
3. **Pipeline**: Phone → MediaUplink → Relay (1087 video frames received) ✅
4. **Keyframe Detection**: 5 keyframes in ~300 frames ✅
5. **Frame Rate**: ~24 fps as expected ✅
6. **Resolution**: 504x896 (MEDIUM quality) ✅

---

## ❌ Critical Issue

### **SDK Does Not Flag Codec Config**

```
Frame #1: 9339 bytes
  isCompressed: true  ✅
  isCodecConfig: false  ❌ WRONG! This frame CONTAINS codec config
  First 32 bytes:
  00 00 00 01 40 01 0c 01 ff ff 01 60 ...
              ^^^^^
              VPS NAL type (0x40 = 32)
```

**What Frame #1 Contains:**
- **VPS** (Video Parameter Set): NAL type 0x40
- **SPS** (Sequence Parameter Set): Hidden in the 9339 bytes
- **PPS** (Picture Parameter Set): Hidden in the 9339 bytes
- Possibly first I-frame data as well

**All subsequent frames:**
```
00 00 00 01 02 01 ...
            ^^^^^
            Trail_R slice NAL type (0x02 = 1)
```

---

## Detailed Findings

### 1. Codec Config Frame Count

```
Codec config frames marked by SDK: 0
Actual codec config present: 1 (Frame #1)
```

**Grep Results:**
```bash
$ grep "isCodecConfig: true" /tmp/phase1-diagnostics.log
(no results)

$ grep "CODEC CONFIG" /tmp/phase1-diagnostics.log
(no results)
```

### 2. First Frame Analysis

**Frame #1 Properties:**
- Size: 9339 bytes (exceptionally large)
- SDK flags: `isCompressed=true, isCodecConfig=false`
- Presentation time: 1440651933 μs
- Marked as keyframe: YES (by our detector)

**Hexdump:**
```
Offset  Hex                                             ASCII
------  ----------------------------------------------  ----------------
0x0000  00 00 00 01 40 01 0c 01 ff ff 01 60 00 00 03  ....@......`...
0x0010  00 b0 00 00 03 00 00 03 00 99 ac 0c 09 ef 21  ..............!
0x0020  ac ee                                           ..
```

**NAL Unit Analysis:**
- Bytes 0-3: `00 00 00 01` = Annex-B start code ✅
- Bytes 4-5: `40 01` = NAL header
  - Bits 1-6 of 0x40 = `010000` = NAL type **32** (VPS)
  - Bit 0 (layer ID) = 0
  - Bit 7 (forbidden) = 0

**Expected NAL Units in Frame #1:**
1. VPS (0x40) - starts at byte 0 ✅
2. SPS (0x42) - should follow (not visible in first 32 bytes)
3. PPS (0x44) - should follow (not visible in first 32 bytes)

The 9339-byte size suggests all three are packed together, possibly with the first I-frame.

### 3. Regular Frame Pattern

**Frame #2-N:**
```
00 00 00 01 02 01 ...
            ^^
            NAL type 1 (Trail_R non-reference slice)
```

All regular frames are **Trail_R slices** (NAL type 1), which is correct for inter-frames.

### 4. Keyframe Pattern

**Keyframes Detected:** 5 in ~300 frames
- Frame #1 (first frame with VPS)
- Frame #217
- Frame #289
- Etc.

**Keyframe Interval:** ~72 frames = ~3 seconds @ 24 fps

This matches typical GOP (Group of Pictures) structure for HEVC.

### 5. Frame Size Distribution

**Sample Sizes:**
- Frame #1: 9339 bytes (codec config + I-frame)
- Regular frames: 934 - 3480 bytes
- Average: ~2000 bytes per frame

**Keyframes are larger** (expected for I-frames)

### 6. Media Pipeline Status

**MediaUplink:**
- Connected: ✅
- WebSocket: `ws://10.70.24.86:8001/media/6df87d99-1170-4604-9b92-48e9c1a9470d`
- State: Connected

**Relay Service:**
- Receiving frames: ✅
- Video frames: 1087
- Audio frames: 2113
- Session ID: 6df87d99-1170-4604-9b92-48e9c1a9470d

**FFmpeg Status:**
- Running: YES
- Parsing stream: NO (stuck on codec parameters)
- Error: "Could not find codec parameters for stream 0"

---

## Why FFmpeg Fails

**FFmpeg Expects:**
1. Pure codec config frames (VPS/SPS/PPS only) OR
2. Codec config at the very start before any frame data

**What FFmpeg Gets:**
1. Frame #1 with 9339 bytes containing:
   - VPS at the start ✅
   - SPS somewhere in the middle (probably)
   - PPS somewhere in the middle (probably)
   - **Mixed with actual frame data** ❌

**FFmpeg's Behavior:**
- Sees start code `00 00 00 01`
- Reads NAL type `0x40` (VPS)
- Starts parsing VPS
- Encounters next start code but it's buried in the 9339 bytes
- **Cannot find SPS with resolution info**
- Gives up: "unspecified size"

---

## Comparison: Expected vs Actual

### Expected Codec Config Delivery

**Option A: Separate Config Frame**
```
Frame #0: isCodecConfig=true, 150 bytes
  00 00 00 01 40 01 ... (VPS)
  00 00 00 01 42 01 ... (SPS with resolution!)
  00 00 00 01 44 01 ... (PPS)

Frame #1: isCodecConfig=false, 2000 bytes
  00 00 00 01 26 01 ... (I-frame IDR)

Frame #2: isCodecConfig=false, 800 bytes
  00 00 00 01 02 01 ... (P-frame)
```

**Option B: Config in First Frame (Current)**
```
Frame #1: isCodecConfig=false, 9339 bytes  ← SHOULD BE TRUE!
  00 00 00 01 40 01 ... (VPS)
  ... (SPS somewhere)
  ... (PPS somewhere)
  00 00 00 01 26 01 ... (I-frame data mixed in)
```

### What We Need to Extract

From Frame #1 (9339 bytes), we need to find and separate:

1. **VPS block**: Starts at byte 0
   - `00 00 00 01 40 01 ... 00 00 00 01` (until next start code)

2. **SPS block**: Somewhere after VPS
   - `00 00 00 01 42 01 ... 00 00 00 01` (until next start code)
   - **This contains the resolution (504x896)!**

3. **PPS block**: After SPS
   - `00 00 00 01 44 01 ... 00 00 00 01`

---

## Phase 2 Strategy

### Approach: Parse and Split Frame #1

Since the SDK won't give us `isCodecConfig=true`, we need to:

1. **Detect codec config in Frame #1**
   - Check if first NAL unit is VPS (0x40)
   - If yes, this frame contains codec config

2. **Parse Frame #1 to extract NAL units**
   - Find all `00 00 00 01` start codes
   - Extract VPS, SPS, PPS as separate units
   - Keep remaining data as the actual first frame

3. **Send codec config separately**
   - Create synthetic `isCodecConfig=true` packet with VPS+SPS+PPS
   - Send to MediaUplink FIRST
   - Then send the actual frame data

4. **Cache codec config**
   - Store VPS/SPS/PPS for reconnection scenarios
   - Resend if publisher restarts

---

## Recommended Fix (Phase 2)

### In VideoPipeline.kt

```kotlin
private var codecConfigData: ByteArray? = null
private var codecConfigExtracted = false

fun processFrame(frame: VideoFrame) {
    val data = extractBuffer(frame)

    // Special handling for first frame
    if (frameCounter == 1 && !codecConfigExtracted) {
        // Check if starts with VPS (0x40)
        if (data.size > 5 &&
            data[0] == 0x00 && data[1] == 0x00 &&
            data[2] == 0x00 && data[3] == 0x01 &&
            (data[4].toInt() and 0x7E) == 0x40) {

            // Extract codec config NAL units
            val (config, frameData) = splitCodecConfig(data)

            codecConfigData = config
            codecConfigExtracted = true

            // Send config first
            val configPacket = VideoPacket(
                data = config,
                isCodecConfig = true,  // Force true
                ...
            )
            frameChannel.send(configPacket)

            // Send frame data separately
            if (frameData.isNotEmpty()) {
                val framePacket = VideoPacket(
                    data = frameData,
                    isCodecConfig = false,
                    ...
                )
                frameChannel.send(framePacket)
            }
            return
        }
    }

    // Normal frame processing
    ...
}

fun splitCodecConfig(data: ByteArray): Pair<ByteArray, ByteArray> {
    val nalUnits = mutableListOf<NALUnit>()
    var pos = 0

    // Find all NAL units
    while (pos < data.size - 4) {
        if (isStartCode(data, pos)) {
            val nalStart = pos + 4
            val nalType = (data[nalStart].toInt() shr 1) and 0x3F

            // Find end of this NAL unit
            var nalEnd = findNextStartCode(data, pos + 4)
            if (nalEnd == -1) nalEnd = data.size

            nalUnits.add(NALUnit(type = nalType,
                                 start = pos,
                                 end = nalEnd))
            pos = nalEnd
        } else {
            pos++
        }
    }

    // Split config (VPS=32, SPS=33, PPS=34) from frame data
    val configUnits = nalUnits.filter { it.type in 32..34 }
    val frameUnits = nalUnits.filter { it.type !in 32..34 }

    val config = extractNALs(data, configUnits)
    val frameData = extractNALs(data, frameUnits)

    return Pair(config, frameData)
}
```

---

## Testing After Phase 2 Fix

**Expected Behavior:**

1. **First frame processed:**
   ```
   Frame #1 detected as VPS
   Extracted codec config: 150 bytes (VPS+SPS+PPS)
   Remaining frame data: 9189 bytes
   ```

2. **MediaUplink logs:**
   ```
   === SENDING CODEC CONFIG FRAME #1 ===
     Size: 150 bytes
     First 32 bytes: 00 00 00 01 40 01 ... 00 00 00 01 42 01 ...

   Sent regular frame #1
   ```

3. **Relay/FFmpeg logs:**
   ```
   Input #0, hevc:
     Stream #0:0: Video: hevc, yuv420p, 504x896, 24 fps  ✅

   [rtmp @ ...] Successfully connected to rtmp://127.0.0.1:1935/glasses
   ```

4. **MediaMTX status:**
   ```json
   {
     "name": "glasses",
     "ready": true,  ✅
     "online": true  ✅
   }
   ```

---

## Acceptance Criteria

**Phase 2 is complete when:**

- [ ] Codec config extracted from Frame #1
- [ ] Separate codec config packet sent before frame data
- [ ] FFmpeg logs show stream parameters detected
- [ ] MediaMTX shows path as "ready" and "online"
- [ ] RTSP stream is viewable in VLC/ffplay

---

## Files Modified in Phase 1

1. `android/app/src/main/kotlin/com/glassesstream/capture/VideoPipeline.kt`
   - Added diagnostic logging
   - Added codec config tracking
   - Added hexdump output

2. `android/app/src/main/kotlin/com/glassesstream/uplink/MediaUplink.kt`
   - Added codec config send logging
   - Added summary statistics

3. `android/app/src/main/kotlin/com/glassesstream/capture/VideoPipeline.kt` (VideoPacket)
   - Added `isCodecConfig` field

---

## Next: Phase 2 Implementation

Ready to implement the fix. The strategy is clear:

1. Detect VPS in Frame #1
2. Parse and split NAL units
3. Send codec config separately with `isCodecConfig=true`
4. Cache for reconnection

**Status:** Waiting for user approval to proceed with Phase 2.
