package com.glassesstream.capture

import android.util.Log
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * Audio capture pipeline
 * Following TECH-02 section 6.2
 *
 * Captures 16-bit PCM audio from SDK and prepares for uplink.
 * For Plan B WebSocket approach, we send raw PCM frames.
 *
 * IMPORTANT: This pipeline accepts application-owned AudioFrameData,
 * not SDK AudioFrame objects. Buffer copying must happen in the collector
 * before data enters this pipeline to avoid SDK buffer lifetime issues.
 */
class AudioPipeline(
    private val sessionId: String,
    private val sampleRate: Int,
    private val channels: Int
) {
    private val TAG = "AudioPipeline"

    // Incoming audio frame channel - now accepts owned data, not SDK objects
    private val audioChannel = Channel<AudioFrameData>(
        capacity = 256,
        onBufferOverflow = kotlinx.coroutines.channels.BufferOverflow.DROP_OLDEST
    )

    private var frameCount = 0
    private var droppedFrames = 0

    // Phase 2 Task 8: Audio startup timing
    private val startTime = System.currentTimeMillis()
    private var firstFrameTime: Long? = null

    /**
     * Audio frame data with metadata
     * This is application-owned data with copied bytes
     */
    data class AudioFrameData(
        val presentationTimeUs: Long,
        val data: ByteArray,
        val sampleRate: Int,
        val channels: Int
    )

    /**
     * Offer an audio frame from the SDK collector
     * Accepts application-owned AudioFrameData with already-copied bytes
     */
    fun offer(frameData: AudioFrameData) {
        // Phase 2 Task 8: Log first frame timing
        if (firstFrameTime == null) {
            firstFrameTime = System.currentTimeMillis()
            val elapsedMs = firstFrameTime!! - startTime
            Log.i(TAG, "=== PHASE 2 TASK 8: AUDIO STARTUP TIMING ===")
            Log.i(TAG, "First audio frame offered +${elapsedMs}ms after AudioPipeline creation")
            Log.i(TAG, "Frame size: ${frameData.data.size} bytes")
            Log.i(TAG, "Sample rate: ${frameData.sampleRate} Hz")
            Log.i(TAG, "Channels: ${frameData.channels}")
        }

        val result = audioChannel.trySend(frameData)
        if (result.isFailure) {
            droppedFrames++
            if (droppedFrames % 10 == 0) {
                Log.w(TAG, "Dropped $droppedFrames audio frames due to queue overflow")
            }
        }
    }

    /**
     * Flow of audio frames ready for uplink
     * Data is already copied, just forward it
     */
    val frames: Flow<AudioFrameData> = flow {
        for (frameData in audioChannel) {
            frameCount++

            if (frameCount <= 10 || frameCount % 100 == 0) {
                Log.d(TAG, "Audio frame $frameCount: ${frameData.data.size} bytes, pts=${frameData.presentationTimeUs}us")
            }

            emit(frameData)
        }
    }

    /**
     * Stop the pipeline
     */
    fun stop() {
        audioChannel.close()
        Log.i(TAG, "Audio pipeline stopped, processed $frameCount frames, dropped $droppedFrames frames")
    }
}
