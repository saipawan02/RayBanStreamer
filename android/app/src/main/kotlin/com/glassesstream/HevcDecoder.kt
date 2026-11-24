package com.glassesstream

import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.util.Log
import android.view.Surface
import java.nio.ByteBuffer

/**
 * Simple HEVC decoder for Ray-Ban Meta glasses video
 */
class HevcDecoder(
    private val width: Int = 504,
    private val height: Int = 896
) {
    private val TAG = "HevcDecoder"

    private var decoder: MediaCodec? = null
    private var surface: Surface? = null

    fun start(outputSurface: Surface) {
        try {
            surface = outputSurface

            // Create HEVC decoder
            decoder = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_HEVC)

            // Configure decoder
            val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_HEVC, width, height)
            format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 1024 * 1024) // 1MB
            format.setInteger(MediaFormat.KEY_WIDTH, width)
            format.setInteger(MediaFormat.KEY_HEIGHT, height)
            // Allow decoder to work without explicit codec-specific-data
            format.setInteger(MediaFormat.KEY_MAX_WIDTH, width)
            format.setInteger(MediaFormat.KEY_MAX_HEIGHT, height)

            Log.i(TAG, "Configuring decoder: ${width}x${height}, surface=$outputSurface")
            decoder?.configure(format, outputSurface, null, 0)

            // Set video scaling mode to fit surface
            decoder?.setVideoScalingMode(MediaCodec.VIDEO_SCALING_MODE_SCALE_TO_FIT)

            decoder?.start()

            // Log initial output format
            val outputFormat = decoder?.outputFormat
            Log.i(TAG, "Decoder started successfully, codec=${decoder?.name}")
            Log.i(TAG, "Initial output format: $outputFormat")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start decoder", e)
            stop()
        }
    }

    fun decodeFrame(frameData: ByteBuffer, presentationTimeUs: Long, isConfig: Boolean) {
        val currentDecoder = decoder ?: return

        try {
            // Get input buffer
            val inputBufferIndex = currentDecoder.dequeueInputBuffer(10000)
            if (inputBufferIndex >= 0) {
                val inputBuffer = currentDecoder.getInputBuffer(inputBufferIndex)
                inputBuffer?.clear()

                // Copy frame data
                val size = frameData.remaining()
                inputBuffer?.put(frameData)

                // Queue input buffer - decode all frames, let codec find config
                val flags = 0 // Don't mark as config, let decoder handle it
                currentDecoder.queueInputBuffer(inputBufferIndex, 0, size, presentationTimeUs, flags)
            } else {
                Log.w(TAG, "No input buffer available")
            }

            // Release output buffers (render to surface)
            val bufferInfo = MediaCodec.BufferInfo()
            var outputBufferIndex = currentDecoder.dequeueOutputBuffer(bufferInfo, 0)
            var renderedCount = 0
            var firstFrameLogged = false
            while (outputBufferIndex >= 0) {
                when (outputBufferIndex) {
                    MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                        val newFormat = currentDecoder.outputFormat
                        Log.i(TAG, "Output format changed: $newFormat")
                    }
                    MediaCodec.INFO_OUTPUT_BUFFERS_CHANGED -> {
                        Log.i(TAG, "Output buffers changed")
                    }
                    else -> {
                        // Log first decoded frame details
                        if (!firstFrameLogged && bufferInfo.size > 0) {
                            Log.i(TAG, "First output frame: size=${bufferInfo.size}, " +
                                "pts=${bufferInfo.presentationTimeUs}, flags=${bufferInfo.flags}")
                            firstFrameLogged = true
                        }
                        // Render frame to surface
                        currentDecoder.releaseOutputBuffer(outputBufferIndex, true)
                        renderedCount++
                    }
                }
                outputBufferIndex = currentDecoder.dequeueOutputBuffer(bufferInfo, 0)
            }

            if (renderedCount > 0) {
                Log.d(TAG, "Rendered $renderedCount frame(s)")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error decoding frame", e)
        }
    }

    fun stop() {
        try {
            decoder?.stop()
            decoder?.release()
            decoder = null
            surface = null
            Log.i(TAG, "Decoder stopped")
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping decoder", e)
        }
    }
}
