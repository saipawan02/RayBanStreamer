package com.glassesstream.capture

import android.util.Log
import com.meta.wearable.dat.camera.types.AudioFrame
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import java.nio.ByteBuffer

/**
 * Audio capture pipeline
 * Following TECH-02 section 6.2
 *
 * Captures 16-bit PCM audio from SDK and prepares for uplink.
 * For Plan B WebSocket approach, we send raw PCM or AAC-encoded frames.
 */
class AudioPipeline(
    private val sessionId: String,
    private val sampleRate: Int,
    private val channels: Int
) {
    private val TAG = "AudioPipeline"

    // Incoming audio frame channel
    private val audioChannel = Channel<AudioFrame>(
        capacity = 256,
        onBufferOverflow = kotlinx.coroutines.channels.BufferOverflow.DROP_OLDEST
    )

    private var frameCount = 0

    /**
     * Audio frame data with metadata
     */
    data class AudioFrameData(
        val presentationTimeUs: Long,
        val data: ByteArray,
        val sampleRate: Int,
        val channels: Int
    )

    /**
     * Offer an audio frame from the SDK collector
     */
    fun offer(frame: AudioFrame) {
        audioChannel.trySend(frame)
    }

    /**
     * Flow of audio frames ready for uplink
     */
    val frames: Flow<AudioFrameData> = flow {
        for (frame in audioChannel) {
            frameCount++

            // Copy the buffer data immediately (SDK buffers may be reused)
            val buffer = frame.buffer
            val data = ByteArray(buffer.remaining())
            buffer.get(data)

            if (frameCount <= 10 || frameCount % 100 == 0) {
                Log.d(TAG, "Audio frame $frameCount: ${data.size} bytes, pts=${frame.presentationTimeUs}us")
            }

            emit(AudioFrameData(
                presentationTimeUs = frame.presentationTimeUs,
                data = data,
                sampleRate = sampleRate,
                channels = channels
            ))
        }
    }

    /**
     * Stop the pipeline
     */
    fun stop() {
        audioChannel.close()
        Log.i(TAG, "Audio pipeline stopped, processed $frameCount frames")
    }
}
