# Phase 2 Task 8: Audio + Video Startup - Current Status

**Date:** 2026-11-26
**Objective:** Fix FFmpeg dual-input initialization to publish HEVC video + AAC audio to MediaMTX

---

## Current State: NEARLY COMPLETE - Ready for Final Test

### What Works Now

1. **FFmpeg Successfully Detects HEVC Video**
   - Resolution: 504x896 detected correctly
   - Codec: hevc (Main), yuv420p
   - Frame rate: 24-25 fps

2. **FFmpeg Successfully Detects PCM Audio**
   - Format: pcm_s16le, 48000 Hz, mono
   - Encoding to AAC works

3. **Stream Published to MediaMTX**
   - RTMP connection successful
   - Path: `glasses` went online
   - Recording started: H265 video + MPEG-4 Audio
   - Confirmed in MediaMTX logs at 2026/11/26 13:04:17

4. **End-to-End Pipeline Functional**
   - Android app → WebSocket → Relay → FFmpeg → MediaMTX
   - All components communicate correctly

---

## Root Causes Identified and Fixed

### Issue 1: FFmpeg Started Too Early (FIXED)
**Problem:** FFmpeg started when WebSocket connected (before streaming began), so it tried to probe empty pipes for 28+ seconds and failed.

**Solution:** Delay FFmpeg startup until first video frame arrives (which contains VPS/SPS/PPS).

**File:** `server/relay/app_task8.py`
**Change:** FFmpeg now starts at first video frame reception, ensuring codec config is immediately available.

### Issue 2: Pipe Opening Deadlock (FIXED)
**Problem:** Video pipe registered only after BOTH video and audio pipes opened. FFmpeg couldn't probe video (no data written), so audio pipe never opened.

**Solution:** Register video pipe immediately when it opens, allowing video writer to start. Audio pipe registration happens separately after video probing completes.

**File:** `server/relay/app_task8.py` lines 524-595
**Change:** Two-phase pipe opening in `open_pipes()` function.

### Issue 3: Writer Threads Exit Prematurely (FIXED - NOT YET TESTED)
**Problem:** Empty deque evaluates to False in Python, so `if not queue:` broke the loop when queue was temporarily empty.

**Solution:** Changed to `if queue is None:` to only break when queue doesn't exist.

**File:** `server/relay/app_task8.py`
- Line ~299: Video writer queue check
- Line ~397: Audio writer queue check

**Status:** Fix applied, service restarted, **awaiting final test**.

---

## Test Results Summary

### Session: ade69039 (Most Recent - Partial Success)

**Timeline:**
- +0ms: WebSocket connected
- +28305ms: First video frame received → FFmpeg started
- +28407ms: Video pipe opened and registered
- +30344ms: First video frame written to FIFO
- +30357ms: Video writer stopped (57 frames) ← BUG: exited on empty queue
- +30411ms: Audio pipe opened
- +30444ms: First audio frame written to FIFO
- +30624ms: Audio writer stopped (127 frames) ← BUG: exited on empty queue
- +41303ms: WebSocket closed (13 seconds of streaming)

**FFmpeg Output:**
```
Stream #0:0: Video: hevc (Main), yuv420p(tv, bt709), 504x896, 25 fps
Stream #1:0: Audio: pcm_s16le, 48000 Hz, 1 channels
Output #0, flv, to 'rtmp://127.0.0.1:1935/glasses':
  Stream #0:0: Video: hevc (Main), 504x896, 25 fps
  Stream #0:1: Audio: aac (LC), 48000 Hz, mono, 128 kb/s
size=190kB time=00:00:02.30 bitrate=675.4kbits/s
```

**MediaMTX Logs:**
```
13:04:17 [RTMP] [conn 127.0.0.1:58940] opened
13:04:17 [path glasses] stream is available and online, 2 tracks (H265, MPEG-4 Audio)
13:04:17 [RTMP] is publishing to path 'glasses'
13:04:17 [recorder] recording 2 tracks
13:04:27 [recorder] recording stopped (i/o timeout)
```

**Result:** Stream published successfully but lasted only 2.3 seconds due to writer threads exiting early. Recording created but very short.

---

## Current Relay Service

**File:** `server/relay/app_task8.py`
**PID:** 131626
**Port:** 8001
**Log:** `/tmp/task8_final.log`
**Status:** Running with all fixes applied

**Key Features:**
- Delayed FFmpeg start (waits for first video frame)
- Two-phase pipe opening (video first, then audio)
- Independent video/audio queues (bounded, non-blocking)
- Audio priming (2 seconds of silence if needed)
- Background FIFO writer threads
- Enhanced FFmpeg probe settings: `-probesize 10000000 -analyzeduration 5000000`

**FFmpeg Command:**
```bash
ffmpeg -hide_banner -loglevel info \
  -probesize 10000000 -analyzeduration 5000000 \
  -f hevc -i /tmp/video_<session>.pipe \
  -f s16le -ar 48000 -ac 1 -i /tmp/audio_<session>.pipe \
  -map 0:v:0 -map 1:a:0 \
  -c:v copy -c:a aac -b:a 128k \
  -flush_packets 1 \
  -f flv rtmp://127.0.0.1:1935/glasses
```

---

## MediaMTX Configuration

**Service:** Docker container (bluenviron/mediamtx:latest)
**Container Name:** mediamtx
**Network Mode:** host
**RTMP Port:** 1935
**API Port:** 9997
**Path:** `glasses`
**Recording:** Enabled to `/srv/glasses/recordings/`

**Status:** Running and accepting RTMP connections

---

## Next Steps

### Immediate: Final Test Required

1. Start streaming session on Android app
2. Stream for exactly 10 seconds
3. Verify full session recorded

**Expected Outcome:**
- Video writer continues throughout session (not just 57 frames)
- Audio writer continues throughout session (not just 127 frames)
- FFmpeg processes full 10 seconds
- MediaMTX records complete 10-second file
- Stream remains online for full duration

### Verification Commands

Check MediaMTX stream status:
```bash
curl http://localhost:9997/v3/paths/list | jq '.items[] | select(.name == "glasses")'
```

Check MediaMTX logs:
```bash
docker logs mediamtx --tail 50 | grep -i 'rtmp\|glasses'
```

Check relay logs:
```bash
tail -f /tmp/task8_final.log
```

Check FFmpeg output:
```bash
# After session, find log file
ls -lt /tmp/ffmpeg_*.log | head -1
# Read it
cat /tmp/ffmpeg_<session>_task8.log
```

Play recorded stream:
```bash
# Find recording
ls -lt /srv/glasses/recordings/glasses/
# Play with VLC or ffplay
ffplay /srv/glasses/recordings/glasses/<timestamp>.mp4
```

---

## Known Issues (Not Blockers)

1. **Timestamps unset warning:** FFmpeg logs "Timestamps are unset in a packet for stream 0". This is a deprecation warning but doesn't prevent streaming. Can be fixed later by setting packet PTS in relay.

2. **Thread queue size warning:** `[in#1/s16le] Thread message queue blocking; consider raising the thread_queue_size option (current value: 8)`. This suggests adding `-thread_queue_size 512` to FFmpeg command for better buffering.

3. **Clock skew:** Android device and server have ~5 minute time difference. Not affecting functionality but makes log correlation harder.

---

## Files Modified

1. **server/relay/app_task8.py** (all fixes)
   - Delayed FFmpeg startup
   - Two-phase pipe opening
   - Fixed queue empty check (line ~299, ~397)

2. **android/app/src/main/kotlin/com/glassesstream/capture/VideoPipeline.kt** (Task 1-4)
   - NAL parser
   - SPS parser
   - Raw HEVC capture
   - Integrity validation with SHA-256

3. **android/app/src/main/kotlin/com/glassesstream/capture/AudioPipeline.kt** (Task 8)
   - Audio startup timing instrumentation

---

## Critical Success Indicators

When the final test succeeds, you should see:

1. **Relay log:** Both writer threads running throughout session, no early exit
2. **FFmpeg log:** Processing continues beyond 2 seconds, no broken pipe errors
3. **MediaMTX log:** Stream stays online for full duration, no i/o timeout
4. **MediaMTX API:** `"ready": true, "online": true` for glasses path
5. **Recording file:** 10-second MP4 file created in `/srv/glasses/recordings/glasses/`

---

## Resuming Work

If you need to resume this debugging:

1. **Check service status:**
   ```bash
   ss -tlnp | grep :8001  # Relay should be on PID 131626
   docker ps | grep mediamtx  # MediaMTX should be running
   ```

2. **Restart relay if needed:**
   ```bash
   cd /home/sai-pawan/Documents/RayBanStreamer/server/relay
   source venv/bin/activate
   python app_task8.py > /tmp/task8_final.log 2>&1 &
   ```

3. **Run test streaming session** (10 seconds)

4. **Analyze results** using verification commands above

5. **If still failing:** Check `/tmp/task8_final.log` for new error patterns

---

## References

- **Main task document:** Provided by user as "Phase 2 — Task 8: Implement Reliable Audio + Video Startup"
- **Previous tasks:** Tasks 1-7 documented in conversation history
- **Project structure:** See `CLAUDE.md` and `refdocs/TECH-*.md`
- **Git status:** Clean, last commit: "Implement audio streaming and enhance Android app functionality"

---

**Last Updated:** 2026-11-26 18:36:08
**Next Action:** Run final 10-second streaming test to verify complete fix
