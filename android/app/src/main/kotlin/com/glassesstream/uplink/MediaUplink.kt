package com.glassesstream.uplink

import android.util.Log
import com.glassesstream.capture.VideoPipeline
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit

/**
 * Sends video and audio frames over WebSocket using Plan B binary protocol
 *
 * TECH-01 section 7:
 * Binary frames with 16-byte header:
 * - 1 byte stream type (1=video HEVC, 2=audio AAC)
 * - 1 byte flags (bit 0 = keyframe)
 * - 2 bytes reserved
 * - 8 bytes little-endian presentation_time_us
 * - 4 bytes little-endian payload length
 * - payload bytes
 */
class MediaUplink(
    private val serverHost: String,
    private val token: String,
    private val sessionId: String,
    private val scope: CoroutineScope
) {
    private val TAG = "MediaUplink"

    companion object {
        const val STREAM_TYPE_VIDEO_HEVC = 1.toByte()
        const val STREAM_TYPE_AUDIO_AAC = 2.toByte()
        const val FLAG_KEYFRAME = 1.toByte()
    }

    sealed class ConnectionState {
        data object Disconnected : ConnectionState()
        data object Connecting : ConnectionState()
        data object Connected : ConnectionState()
        data class Error(val message: String) : ConnectionState()
    }

    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    val state: StateFlow<ConnectionState> = _state.asStateFlow()

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS) // No timeout for streaming
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private var framesSent = 0
    private var bytesSent = 0L

    fun start() {
        if (webSocket != null) {
            Log.w(TAG, "Already connected")
            return
        }

        connect()
    }

    private fun connect() {
        _state.value = ConnectionState.Connecting
        Log.i(TAG, "Connecting to media WebSocket: $serverHost")

        // Determine protocol based on host
        val protocol = if (serverHost.contains(".trycloudflare.com")) "wss" else "ws"
        val url = "$protocol://$serverHost/media/$sessionId?token=$token"

        val request = Request.Builder()
            .url(url)
            .build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                _state.value = ConnectionState.Connected
                Log.i(TAG, "Media WebSocket connected")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                _state.value = ConnectionState.Error(t.message ?: "Unknown error")
                Log.e(TAG, "WebSocket connection failed", t)
                this@MediaUplink.webSocket = null

                // TODO: Implement reconnection with backoff
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                _state.value = ConnectionState.Disconnected
                Log.i(TAG, "Media WebSocket closed: $code $reason")
                this@MediaUplink.webSocket = null
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                Log.d(TAG, "Received text message: $text")
            }
        })
    }

    /**
     * Send a video frame using Plan B binary protocol
     */
    fun sendVideoFrame(packet: VideoPipeline.VideoPacket) {
        val ws = webSocket ?: run {
            Log.w(TAG, "Cannot send frame - not connected")
            return
        }

        if (_state.value != ConnectionState.Connected) {
            return
        }

        try {
            val frame = buildBinaryFrame(
                streamType = STREAM_TYPE_VIDEO_HEVC,
                isKeyframe = packet.isKeyframe,
                presentationTimeUs = packet.presentationTimeUs,
                payload = packet.data
            )

            val success = ws.send(frame.toByteString())
            if (success) {
                framesSent++
                bytesSent += frame.size

                if (framesSent % 100 == 0) {
                    val mb = bytesSent / (1024.0 * 1024.0)
                    Log.d(TAG, "Sent $framesSent frames (${String.format("%.2f", mb)} MB)")
                }
            } else {
                Log.w(TAG, "Failed to send frame (queue full)")
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error sending video frame", e)
        }
    }

    /**
     * Send an audio frame using Plan B binary protocol
     */
    fun sendAudioFrame(data: ByteArray, presentationTimeUs: Long) {
        val ws = webSocket ?: return

        if (_state.value != ConnectionState.Connected) {
            return
        }

        try {
            val frame = buildBinaryFrame(
                streamType = STREAM_TYPE_AUDIO_AAC,
                isKeyframe = false,
                presentationTimeUs = presentationTimeUs,
                payload = data
            )

            ws.send(frame.toByteString())
        } catch (e: Exception) {
            Log.e(TAG, "Error sending audio frame", e)
        }
    }

    /**
     * Build binary frame with 16-byte header + payload
     *
     * Header format (TECH-01 section 7):
     * [0]     stream type (1=video, 2=audio)
     * [1]     flags (bit 0 = keyframe)
     * [2-3]   reserved (0x00)
     * [4-11]  presentation_time_us (little-endian 64-bit)
     * [12-15] payload length (little-endian 32-bit)
     * [16+]   payload data
     */
    private fun buildBinaryFrame(
        streamType: Byte,
        isKeyframe: Boolean,
        presentationTimeUs: Long,
        payload: ByteArray
    ): ByteArray {
        val header = ByteBuffer.allocate(16).apply {
            order(ByteOrder.LITTLE_ENDIAN)
            put(streamType)                              // [0] stream type
            put(if (isKeyframe) FLAG_KEYFRAME else 0)   // [1] flags
            put(0)                                       // [2] reserved
            put(0)                                       // [3] reserved
            putLong(presentationTimeUs)                  // [4-11] timestamp
            putInt(payload.size)                         // [12-15] payload length
        }.array()

        return header + payload
    }

    fun stop() {
        webSocket?.close(1000, "Session ended")
        webSocket = null
        _state.value = ConnectionState.Disconnected
        Log.i(TAG, "Media uplink stopped. Sent $framesSent frames total")
    }
}
