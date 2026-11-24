package com.glassesstream.uplink

import android.util.Log
import com.glassesstream.model.*
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.*
import java.util.concurrent.TimeUnit

/**
 * WebSocket uplink for telemetry (IMU batches and events)
 * Following TECH-01 section 5.4 connection protocol
 */
class TelemetryUplink(
    private val serverHost: String,
    private val token: String,
    private val sessionId: String,
    private val scope: CoroutineScope
) {
    private val TAG = "TelemetryUplink"

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS) // WebSocket doesn't timeout
        .build()

    private var webSocket: WebSocket? = null
    private val backoff = Backoff()

    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    val state: StateFlow<ConnectionState> = _state

    private val messageQueue = Channel<TelemetryMessage>(capacity = 3000) // ~5 minutes at 10/sec
    private var currentSeq = 0
    private var lastAckedSeq = -1
    private val unacknowledged = mutableListOf<TelemetryMessage>()

    private val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        classDiscriminator = "_class"  // Avoid conflict with protocol's "type" field
    }

    sealed class ConnectionState {
        object Disconnected : ConnectionState()
        object Connecting : ConnectionState()
        data class Connected(val nextSeq: Int) : ConnectionState()
        data class Error(val message: String) : ConnectionState()
    }

    /**
     * Start the uplink connection
     */
    fun start() {
        scope.launch {
            connect()
            processSendQueue()
        }
    }

    /**
     * Send a message (IMU batch or event)
     */
    suspend fun send(message: TelemetryMessage) {
        messageQueue.send(message)
    }

    private suspend fun connect() {
        while (scope.isActive) {
            try {
                _state.value = ConnectionState.Connecting
                Log.i(TAG, "Connecting to ws://$serverHost:8000/ingest/$sessionId")

                val url = "ws://$serverHost:8000/ingest/$sessionId?token=$token"
                val request = Request.Builder().url(url).build()

                val ws = client.newWebSocket(request, object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        Log.i(TAG, "WebSocket opened")
                    }

                    override fun onMessage(webSocket: WebSocket, text: String) {
                        handleServerMessage(text)
                    }

                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        Log.e(TAG, "WebSocket failure", t)
                        _state.value = ConnectionState.Error(t.message ?: "Unknown error")
                        scope.launch {
                            reconnect()
                        }
                    }

                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        Log.i(TAG, "WebSocket closed: $code $reason")
                        _state.value = ConnectionState.Disconnected
                        scope.launch {
                            reconnect()
                        }
                    }
                })

                webSocket = ws
                break // Wait for server hello

            } catch (e: Exception) {
                Log.e(TAG, "Connection failed", e)
                _state.value = ConnectionState.Error(e.message ?: "Connection failed")
                val delay = backoff.nextDelay()
                Log.i(TAG, "Retrying in ${delay}ms")
                delay(delay)
            }
        }
    }

    private fun handleServerMessage(text: String) {
        try {
            // Try to parse as hello
            if (text.contains("\"type\":\"hello\"")) {
                val hello = json.decodeFromString<ServerHello>(text)
                Log.i(TAG, "Received hello, next_seq=${hello.next_seq}")
                currentSeq = hello.next_seq
                _state.value = ConnectionState.Connected(hello.next_seq)
                backoff.reset()

                // Resend unacknowledged messages
                scope.launch {
                    resendFrom(hello.next_seq)
                }
                return
            }

            // Try to parse as ack
            if (text.contains("\"type\":\"ack\"")) {
                val ack = json.decodeFromString<ServerAck>(text)
                lastAckedSeq = ack.seq
                // Remove acknowledged messages
                unacknowledged.removeAll { (it as? ImuBatch)?.seq ?: (it as? Event)?.seq ?: -1 <= ack.seq }
                Log.d(TAG, "Acked up to seq ${ack.seq}, ${unacknowledged.size} unacked remaining")
                return
            }

        } catch (e: Exception) {
            Log.e(TAG, "Failed to parse server message", e)
        }
    }

    private suspend fun resendFrom(nextSeq: Int) {
        val toResend = unacknowledged.filter {
            val seq = (it as? ImuBatch)?.seq ?: (it as? Event)?.seq ?: -1
            seq >= nextSeq
        }
        Log.i(TAG, "Resending ${toResend.size} messages from seq $nextSeq")
        for (msg in toResend) {
            sendMessage(msg, resend = true)
        }
    }

    private suspend fun processSendQueue() {
        for (message in messageQueue) {
            if (_state.value !is ConnectionState.Connected) {
                // Queue message until connected
                delay(100)
                messageQueue.send(message) // Re-queue
                continue
            }

            sendMessage(message, resend = false)
        }
    }

    private fun sendMessage(message: TelemetryMessage, resend: Boolean) {
        val ws = webSocket ?: return

        try {
            // Assign seq if not already set (for new messages)
            val finalMessage = when (message) {
                is ImuBatch -> if (resend) message else message.copy(seq = currentSeq++)
                is Event -> if (resend) message else message.copy(seq = currentSeq++)
            }

            val text = json.encodeToString<TelemetryMessage>(finalMessage)
            val sent = ws.send(text)

            if (sent) {
                if (!resend) {
                    unacknowledged.add(finalMessage)
                }
                Log.d(TAG, "Sent ${finalMessage.type} seq=${finalMessage.seq}")
            } else {
                Log.w(TAG, "Failed to send message, queue full")
            }

        } catch (e: Exception) {
            Log.e(TAG, "Failed to send message", e)
        }
    }

    private suspend fun reconnect() {
        webSocket?.close(1000, "Reconnecting")
        webSocket = null
        val delay = backoff.nextDelay()
        Log.i(TAG, "Reconnecting in ${delay}ms, attempt ${backoff.getAttempt()}")
        delay(delay)
        connect()
    }

    /**
     * Stop the uplink
     */
    fun stop() {
        webSocket?.close(1000, "Stopped")
        webSocket = null
        messageQueue.close()
        client.dispatcher.executorService.shutdown()
        Log.i(TAG, "Telemetry uplink stopped")
    }
}
