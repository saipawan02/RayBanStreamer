# Phase 2 Task 1: Annex-B HEVC NAL Parser - Complete

**Date:** 2026-11-26
**Status:** ✅ IMPLEMENTED AND READY FOR TESTING

---

## Implementation Summary

### What Was Implemented

1. **NALUnit Data Class** with complete metadata:
   - `type`: NAL unit type (0-63)
   - `typeName`: Human-readable name (VPS_NUT, SPS_NUT, IDR_W_RADL, etc.)
   - `offset`: Start position in buffer
   - `size`: Total size including start code
   - `headerSize`: Start code size (3 or 4 bytes)
   - `payloadSize`: Actual NAL data size
   - `isValid`: Has complete 2-byte HEVC header
   - `isTruncated`: Appears incomplete

2. **parseAnnexBNALUnits()** - Comprehensive NAL Parser:
   - Supports both 3-byte (`00 00 01`) and 4-byte (`00 00 00 01`) start codes
   - Extracts all NAL units from buffer
   - Calculates NAL type using proper unsigned shift: `nalType = ((firstHeaderByte & 0xFF) ushr 1) & 0x3F`
   - Identifies all HEVC NAL types including:
     - VPS (32)
     - SPS (33)
     - PPS (34)
     - IDR (19, 20)
     - CRA (21)
     - Regular slices (0-3)
   - Validates complete 2-byte HEVC headers
   - Detects malformed or truncated NAL units

3. **parseAndLogNALUnits()** - Detailed Diagnostic Logging:
   - Called only for first 10 frames
   - Logs complete NAL unit breakdown:
     - Type, name, offset, sizes
     - Validation status
     - Hexdump of first 32 bytes of each NAL
     - HEVC header fields (layerId, temporalId)
     - forbidden_zero_bit validation
   - Summary statistics:
     - VPS/SPS/PPS counts
     - IDR/CRA counts
     - Malformed NAL counts
   - Clear indicators if codec config is present

4. **findNextStartCode()** - Helper Function:
   - Efficiently finds next Annex-B start code
   - Returns -1 if no start code found
   - Used for determining NAL unit boundaries

---

## Code Changes

**File:** `android/app/src/main/kotlin/com/glassesstream/capture/VideoPipeline.kt`

### Added:
- `NALUnit` data class with companion object for type names
- `parseAnnexBNALUnits()`
- `parseAndLogNALUnits()`
- `findNextStartCode()`

### Modified:
- `processFrame()` - Now uses NAL parser for first 10 frames
- Keyframe detection based on NAL analysis (types 16-21)
- Codec config detection based on NAL analysis (types 32-34)

### Removed:
- Old `detectHevcKeyframe()` method (replaced by NAL parser)

---

## Expected Log Output

### For Frame #1 (Expected to contain VPS/SPS/PPS):

```
=== PHASE 2 TASK 1: NAL UNIT ANALYSIS ===
Frame #1
  Total size: 9339 bytes
  SDK isCompressed: true
  SDK isCodecConfig: false
  SDK width: 504
  SDK height: 896
  SDK presentationTimeUs: 1440651933
  NAL units found: 3-5

  NAL #1:
    Type: 32 (VPS_NUT)
    Offset: 0
    Size: 150 bytes (header=4, payload=146)
    Valid: true
    Truncated: false
    First 32 bytes: 40 01 0c 01 ff ff 01 60 00 00 03 00 b0 00 00 03 00 00 03 00 99 ac 0c 09 ef 21 ac ee ...
    HEVC header: layerId=0, temporalId=1

  NAL #2:
    Type: 33 (SPS_NUT)
    Offset: 150
    Size: 200 bytes (header=4, payload=196)
    Valid: true
    Truncated: false
    First 32 bytes: 42 01 01 01 60 00 00 03 00 b0 00 00 03 00 00 03 00 99 a0 01 e0 20 02 1c 59 4e 49 ...
    HEVC header: layerId=0, temporalId=1

  NAL #3:
    Type: 34 (PPS_NUT)
    Offset: 350
    Size: 50 bytes (header=4, payload=46)
    Valid: true
    Truncated: false
    First 32 bytes: 44 01 c1 73 d1 89 ...
    HEVC header: layerId=0, temporalId=1

  NAL #4:
    Type: 19 (IDR_W_RADL)
    Offset: 400
    Size: 8939 bytes (header=4, payload=8935)
    Valid: true
    Truncated: false
    First 32 bytes: 26 01 af 09 60 20 e6 46 b3 69 40 3e 5f 46 ...
    HEVC header: layerId=0, temporalId=1

  === NAL UNIT SUMMARY ===
  VPS (32): 1
  SPS (33): 1
  PPS (34): 1
  IDR (19-20): 1
  CRA (21): 0
  Malformed/Truncated: 0
  ✅ CODEC CONFIG PRESENT
=== END NAL ANALYSIS ===
```

### For Regular Frames (Frame #2+):

```
=== PHASE 2 TASK 1: NAL UNIT ANALYSIS ===
Frame #2
  Total size: 934 bytes
  SDK isCompressed: true
  SDK isCodecConfig: false
  SDK width: 504
  SDK height: 896
  SDK presentationTimeUs: 1440693588
  NAL units found: 1

  NAL #1:
    Type: 1 (TRAIL_R)
    Offset: 0
    Size: 934 bytes (header=4, payload=930)
    Valid: true
    Truncated: false
    First 32 bytes: 02 01 d0 0f e6 29 80 f6 80 c1 32 43 3c 63 d6 d7 ...
    HEVC header: layerId=0, temporalId=1

  === NAL UNIT SUMMARY ===
  VPS (32): 0
  SPS (33): 0
  PPS (34): 0
  IDR (19-20): 0
  CRA (21): 0
  Malformed/Truncated: 0
  ❌ NO CODEC CONFIG (VPS/SPS/PPS missing)
=== END NAL ANALYSIS ===
```

---

## Validation Checklist

After running the first streaming session, verify:

### NAL Parser Functionality:
- [ ] All start codes detected (both 3-byte and 4-byte)
- [ ] NAL types correctly identified
- [ ] VPS (32), SPS (33), PPS (34) found in Frame #1
- [ ] NAL sizes match expected ranges
- [ ] No malformed or truncated NAL units (except possibly at end of frame)
- [ ] HEVC header validation passes (forbidden_zero_bit = 0)

### Frame #1 Analysis:
- [ ] Contains VPS NAL unit
- [ ] Contains SPS NAL unit (this has resolution info!)
- [ ] Contains PPS NAL unit
- [ ] May contain first IDR frame
- [ ] SDK still reports `isCodecConfig: false` (expected bug)

### Regular Frames:
- [ ] Contain only slice NAL units (types 0-3)
- [ ] No VPS/SPS/PPS in subsequent frames (unless on keyframe)
- [ ] NAL structure is consistent

### Errors to Watch For:
- [ ] No "forbidden_zero_bit is not 0" warnings
- [ ] No "NAL type mismatch" warnings
- [ ] No truncated NAL units (except at very end of buffer)
- [ ] All NAL units marked as `Valid: true`

---

## Next Steps After Task 1

Once NAL analysis confirms VPS/SPS/PPS presence in Frame #1:

**Task 2:** Parse SPS to extract actual resolution
- Implement SPS RBSP parsing
- Extract width and height from SPS
- Handle conformance window cropping
- Verify extracted dimensions match 504x896

**Task 3:** Capture raw HEVC bitstream
- Write `.h265` file with original payload bytes
- Test with `ffprobe` and `ffmpeg` standalone
- Verify FFmpeg can detect resolution from file

**Task 4:** Validate Android-to-relay integrity
- Add payload checksums
- Compare bytes at each stage
- Verify no data corruption

**Task 5-6:** Fix relay and codec config handling

---

## How to Test

### 1. Clear Logs and Start Capture:
```bash
adb logcat -c
nohup adb logcat -s VideoPipeline:I MediaUplink:I > /tmp/phase2-task1-logs.log 2>&1 &
```

### 2. Run Streaming Session:
- Open RayBanStreamer app
- START SESSION
- START STREAMING
- Let it stream for 30 seconds (captures first 10 frames with detailed NAL analysis)
- STOP STREAMING

### 3. Analyze Logs:
```bash
# Stop log capture
pkill -f "adb logcat"

# View results
less /tmp/phase2-task1-logs.log

# Or search for specific patterns
grep "NAL UNIT ANALYSIS" /tmp/phase2-task1-logs.log
grep "VPS_NUT\|SPS_NUT\|PPS_NUT" /tmp/phase2-task1-logs.log
grep "CODEC CONFIG PRESENT" /tmp/phase2-task1-logs.log
```

### 4. Expected Findings:

**Frame #1 should show:**
- VPS NAL (type 32)
- SPS NAL (type 33) ← Contains 504x896 resolution!
- PPS NAL (type 34)
- Possibly IDR NAL (type 19 or 20)

**Frames #2-10 should show:**
- Regular slice NALs (types 0-3)
- No VPS/SPS/PPS (unless keyframe interval is very short)

---

## Success Criteria

✅ **Task 1 is complete when:**

1. NAL parser successfully identifies all NAL units in first 10 frames
2. VPS (32), SPS (33), and PPS (34) are found in Frame #1
3. NAL types, offsets, and sizes are logged correctly
4. No malformed or truncated NAL units (validation passes)
5. HEVC header structure is validated
6. Summary statistics show codec config present

❌ **Task 1 fails if:**

- NAL parser crashes or throws exceptions
- VPS/SPS/PPS are not found in any frame
- Many NAL units show as malformed
- forbidden_zero_bit validation fails
- NAL type calculations are incorrect

---

## Files Modified

- `android/app/src/main/kotlin/com/glassesstream/capture/VideoPipeline.kt`

**Build Status:** ✅ Compiled successfully
**Installation:** ✅ Installed on device 32004e065e1e5693
**Ready for Testing:** ✅ YES

---

## Next: Collect Test Data

Run the streaming session and share the logs. We need to verify:
1. VPS/SPS/PPS are in Frame #1
2. SPS can be extracted for Task 2
3. All NAL units are valid
4. No data corruption

Then we proceed to **Task 2: SPS Parser** to extract the actual resolution.
