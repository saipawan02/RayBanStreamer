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
