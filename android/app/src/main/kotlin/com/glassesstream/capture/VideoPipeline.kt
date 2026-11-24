package com.glassesstream.capture

import android.util.Log
import com.meta.wearable.dat.camera.Stream
import com.meta.wearable.dat.camera.types.VideoFrame
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch
import java.nio.ByteBuffer

/**
 * Captures video frames from the SDK and prepares them for upload
 *
 * TECH-02 section 6.1: Copy ByteBuffer immediately, never hold SDK buffer past callback
 */
class VideoPipeline(
    private val sessionId: String,
    private val scope: CoroutineScope
) {
    private val TAG = "VideoPipeline"

    data class VideoPacket(
        val data: ByteArray,
        val presentationTimeUs: Long,
        val isKeyframe: Boolean,
        val width: Int,
        val height: Int,
        val isCompressed: Boolean
    )

    private val frameChannel = Channel<VideoPacket>(
        capacity = 48, // ~2 seconds at 24fps
        onBufferOverflow = BufferOverflow.DROP_OLDEST
    )

    private var droppedFrames = 0

    /**
     * Flow of video packets ready for upload
     */
    val packets: Flow<VideoPacket> = flow {
        for (packet in frameChannel) {
            emit(packet)
        }
    }

    /**
     * Start collecting frames from the SDK stream
     */
    fun start(stream: Stream) {
        scope.launch {
            stream.videoStream.collect { frame ->
                try {
                    processFrame(frame)
                } catch (e: Exception) {
                    Log.e(TAG, "Error processing frame", e)
                }
            }
        }
        Log.i(TAG, "Video pipeline started for session $sessionId")
    }

    private fun processFrame(frame: VideoFrame) {
        // Copy buffer immediately - SDK may reuse it after callback
        // Use duplicate() to avoid modifying SDK's buffer position
        val buffer = frame.buffer.duplicate()
        buffer.position(0)  // Start from beginning of this duplicate

        val size = buffer.limit()
        if (size == 0) {
            Log.w(TAG, "Received frame with 0 bytes")
            return
        }

        val data = ByteArray(size)
        buffer.get(data)

        // Check if compressed HEVC or raw YUV
        if (!frame.isCompressed) {
            Log.w(TAG, "Received uncompressed frame - compression should be enabled!")
            // TODO: Implement MediaCodec encoding path if needed
            return
        }

        // Detect keyframe by looking for HEVC NAL unit types
        // NAL type is in bits 1-6 of the first byte after start code
        val isKeyframe = detectHevcKeyframe(data)

        val packet = VideoPacket(
            data = data,
            presentationTimeUs = frame.presentationTimeUs,
            isKeyframe = isKeyframe,
            width = frame.width,
            height = frame.height,
            isCompressed = frame.isCompressed
        )

        val success = frameChannel.trySend(packet).isSuccess
        if (!success) {
            droppedFrames++
            if (droppedFrames % 10 == 0) {
                Log.w(TAG, "Dropped $droppedFrames frames (queue full)")
            }
        }
    }

    /**
     * Detect if this is a keyframe (IRAP picture) by inspecting HEVC NAL units
     *
     * HEVC keyframe NAL types: 16-21 (BLA, IDR, CRA)
     * NAL unit structure: [00 00 00 01] [type in bits 1-6 of next byte]
     *
     * Note: This is heuristic until Spike S1 confirms the exact format
     */
    private fun detectHevcKeyframe(data: ByteArray): Boolean {
        if (data.size < 5) return false

        // Look for Annex B start code: 00 00 00 01 or 00 00 01
        var pos = 0
        while (pos < data.size - 4) {
            if (data[pos] == 0.toByte() && data[pos + 1] == 0.toByte()) {
                val hasStartCode = if (data[pos + 2] == 0.toByte() && data[pos + 3] == 1.toByte()) {
                    pos += 4
                    true
                } else if (data[pos + 2] == 1.toByte()) {
                    pos += 3
                    true
                } else {
                    false
                }

                if (hasStartCode && pos < data.size) {
                    // NAL unit type is in bits 1-6 (0-indexed) of the first byte
                    val nalHeader = data[pos].toInt() and 0xFF
                    val nalType = (nalHeader shr 1) and 0x3F

                    // HEVC IRAP picture types: 16-21
                    if (nalType in 16..21) {
                        return true
                    }
                }
            }
            pos++
        }

        return false
    }

    fun stop() {
        frameChannel.close()
        Log.i(TAG, "Video pipeline stopped. Dropped $droppedFrames frames total")
    }
}
