package com.glassesstream

import android.content.Context
import android.util.Log
import android.view.Surface
import com.glassesstream.capture.AudioPipeline
import com.glassesstream.capture.MotionPipeline
import com.glassesstream.capture.VideoPipeline
import com.glassesstream.model.Event
import com.glassesstream.model.EventNames
import com.glassesstream.model.Settings
import com.glassesstream.uplink.MediaUplink
import com.glassesstream.uplink.TelemetryUplink
import com.glassesstream.util.Clocks
import com.glassesstream.util.SystemClocks
import com.meta.wearable.dat.camera.Camera
import com.meta.wearable.dat.camera.Stream
import com.meta.wearable.dat.camera.addCamera
import com.meta.wearable.dat.camera.types.*
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.motion.Motion
import com.meta.wearable.dat.motion.addMotion
import com.meta.wearable.dat.motion.types.MotionConfiguration
import com.meta.wearable.dat.motion.types.MotionSamplingRate
import com.meta.wearable.dat.motion.types.MotionState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.UUID

/**
 * Manages camera streaming and IMU capture from Ray-Ban Meta glasses
 * Now includes Motion, Audio, and Telemetry uplink integration
 */
class CameraManager(
    private val context: Context,
    private val coroutineScope: CoroutineScope,
    private var settings: Settings = Settings.DEFAULT,
    private val clocks: Clocks = SystemClocks()
) {
    private val TAG = "CameraManager"

    private val sessionId = UUID.randomUUID().toString()

    private val deviceSelector by lazy { AutoDeviceSelector() }
    private var session: DeviceSession? = null
    private var camera: Camera? = null
    private var stream: Stream? = null
    private var motion: Motion? = null

    // Pipelines and uplinks
    private var motionPipeline: MotionPipeline? = null
    private var audioPipeline: AudioPipeline? = null
    private var videoPipeline: VideoPipeline? = null
    private var telemetryUplink: TelemetryUplink? = null
    private var mediaUplink: MediaUplink? = null

    private val _status = MutableStateFlow<String>("Ready to stream")
    val status: StateFlow<String> = _status.asStateFlow()

    private val _isStreaming = MutableStateFlow(false)
    val isStreaming: StateFlow<Boolean> = _isStreaming.asStateFlow()

    private val _frameCount = MutableStateFlow(0)
    val frameCount: StateFlow<Int> = _frameCount.asStateFlow()

    private val _imuCount = MutableStateFlow(0)
    val imuCount: StateFlow<Int> = _imuCount.asStateFlow()

    private var videoSurface: Surface? = null
    private var hevcDecoder: HevcDecoder? = null
    private var eventSeq = 0

    init {
        // Initialize telemetry uplink if server is configured
        if (settings.serverHost.isNotEmpty() && settings.wsToken.isNotEmpty()) {
            initializeTelemetry()
        }
    }

    /**
     * Update settings and reinitialize uplinks
     * Call this after user changes server configuration
     */
    fun updateSettings(newSettings: Settings) {
        Log.i(TAG, "Updating settings: host=${newSettings.serverHost.take(20)}, token=${if(newSettings.wsToken.isNotEmpty()) "***" else "empty"}")

        // Stop existing uplinks
        telemetryUplink?.stop()
        mediaUplink?.stop()
        telemetryUplink = null
        mediaUplink = null

        // Update settings
        settings = newSettings

        // Reinitialize uplinks if configured
        if (settings.serverHost.isNotEmpty() && settings.wsToken.isNotEmpty()) {
            initializeTelemetry()
            Log.i(TAG, "Settings updated and uplinks reinitialized")
        } else {
            Log.w(TAG, "Settings updated but server not configured")
        }
    }

    private fun initializeTelemetry() {
        telemetryUplink = TelemetryUplink(
            serverHost = settings.serverHost,
            token = settings.wsToken,
            sessionId = sessionId,
            scope = coroutineScope
        )
        telemetryUplink?.start()
        Log.i(TAG, "Telemetry uplink initialized for session $sessionId")
    }

    private fun initializeMediaUplink() {
        if (settings.serverHost.isEmpty()) {
            Log.w(TAG, "Server host not configured - media upload disabled")
            return
        }

        mediaUplink = MediaUplink(
            serverHost = settings.serverHost,
            token = settings.wsToken,
            sessionId = sessionId,
            scope = coroutineScope
        )
        mediaUplink?.start()
        Log.i(TAG, "Media uplink initialized for session $sessionId")
    }

    private suspend fun emitEvent(eventName: String, detail: kotlinx.serialization.json.JsonObject? = null) {
        val uplink = telemetryUplink ?: return

        val event = Event(
            session_id = sessionId,
            seq = eventSeq++,
            phone_wall_ms = clocks.currentTimeMillis(),
            phone_elapsed_ns = clocks.elapsedRealtimeNanos(),
            event = eventName,
            detail = detail
        )

        uplink.send(event)
        Log.d(TAG, "Emitted event: $eventName")
    }

    fun setSurface(surface: Surface?) {
        videoSurface = surface
        Log.i(TAG, "Surface set: $surface")

        // Start decoder when surface is available
        if (surface != null && _isStreaming.value) {
            startDecoder(surface)
        } else {
            stopDecoder()
        }
    }

    private fun startDecoder(surface: Surface) {
        stopDecoder()
        hevcDecoder = HevcDecoder().apply {
            start(surface)
        }
        Log.i(TAG, "HEVC decoder started")
    }

    private fun stopDecoder() {
        hevcDecoder?.stop()
        hevcDecoder = null
        Log.i(TAG, "HEVC decoder stopped")
    }

    fun startSession() {
        if (session != null) {
            Log.i(TAG, "Session already active")
            return
        }

        _status.value = "Creating session..."
        Log.i(TAG, "Creating device session")

        Wearables.createSession(deviceSelector)
            .onSuccess { created ->
                session = created
                Log.i(TAG, "Session created successfully")

                // Monitor session state
                coroutineScope.launch {
                    created.state.collect { state ->
                        Log.i(TAG, "Session state: $state")
                        when (state) {
                            DeviceSessionState.STARTED -> {
                                _status.value = "✅ Session active - ready to stream"
                            }
                            DeviceSessionState.STOPPED -> {
                                _status.value = "Session stopped"
                                cleanup()
                            }
                            else -> {
                                _status.value = "Session: $state"
                            }
                        }
                    }
                }

                // Monitor session errors
                coroutineScope.launch {
                    created.errors.collect { error ->
                        Log.e(TAG, "Session error: ${error.description}")
                        _status.value = "❌ Session error: ${error.description}"
                    }
                }

                _status.value = "Starting session..."
                created.start()
            }
            .onFailure { error, _ ->
                Log.e(TAG, "Failed to create session: ${error.description}")
                _status.value = "❌ Failed to create session: ${error.description}"
            }
    }

    fun startStreaming() {
        val currentSession = session
        if (currentSession == null) {
            _status.value = "❌ Start session first"
            Log.e(TAG, "No active session")
            return
        }

        if (stream != null) {
            Log.i(TAG, "Stream already active")
            return
        }

        _status.value = "Starting camera stream..."
        Log.i(TAG, "Adding camera to session")

        currentSession.addCamera(
            StreamConfiguration(
                videoQuality = when (settings.videoQuality) {
                    "HIGH" -> VideoQuality.HIGH
                    "LOW" -> VideoQuality.LOW
                    else -> VideoQuality.MEDIUM
                },
                frameRate = settings.frameRate,
                compressVideo = true  // HEVC compressed
                // Audio configuration will be added after spike to determine actual API
            )
        )
        .onSuccess { addedCamera ->
            camera = addedCamera
            val addedStream = addedCamera.stream
            stream = addedStream

            Log.i(TAG, "Camera added successfully")

            // Monitor stream state
            coroutineScope.launch {
                addedStream.state.collect { state ->
                    Log.i(TAG, "Stream state: $state")
                    _isStreaming.value = (state == StreamState.STREAMING)
                    when (state) {
                        StreamState.STREAMING -> {
                            _status.value = "🎥 Streaming!"
                            // Start decoder when streaming begins
                            videoSurface?.let { startDecoder(it) }
                        }
                        StreamState.STOPPED, StreamState.CLOSED -> {
                            _status.value = "Stream stopped"
                            _isStreaming.value = false
                            stopDecoder()
                        }
                        else -> {
                            _status.value = "Stream: $state"
                        }
                    }
                }
            }

            // Initialize media uplink and video pipeline
            initializeMediaUplink()
            videoPipeline = VideoPipeline(sessionId, coroutineScope)
            videoPipeline?.start(addedStream)

            // Collect from video pipeline and send to media uplink
            coroutineScope.launch {
                videoPipeline?.packets?.collect { packet ->
                    mediaUplink?.sendVideoFrame(packet)
                }
            }

            // Collect video frames for display
            coroutineScope.launch(Dispatchers.Default) {
                Log.i(TAG, "Starting video frame collection")
                try {
                    addedStream.videoStream.collect { frame ->
                        handleVideoFrame(frame)
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Error collecting video frames", e)
                }
                Log.i(TAG, "Video frame collection stopped")
            }

            // Collect audio frames if enabled
            if (settings.audioEnabled) {
                audioPipeline = AudioPipeline(
                    sessionId = sessionId,
                    sampleRate = settings.audioSampleRate,
                    channels = settings.audioChannels
                )

                coroutineScope.launch(Dispatchers.Default) {
                    Log.i(TAG, "Starting audio frame collection")
                    try {
                        addedStream.audioStream.collect { frame ->
                            audioPipeline?.offer(frame)
                        }
                    } catch (e: Exception) {
                        Log.e(TAG, "Error collecting audio frames", e)
                    }
                    Log.i(TAG, "Audio frame collection stopped")
                }

                // Emit audio_started event
                coroutineScope.launch {
                    emitEvent(EventNames.AUDIO_STARTED, buildJsonObject {
                        put("sample_rate_hz", settings.audioSampleRate)
                        put("channels", settings.audioChannels)
                    })
                }
            }

            // Monitor stream errors
            coroutineScope.launch {
                addedStream.errorStream.collect { error ->
                    Log.e(TAG, "Stream error: ${error.description}")
                    _status.value = "❌ Stream error: ${error.description}"
                }
            }

            Log.i(TAG, "Starting stream")
            addedStream.start().onFailure { error, _ ->
                Log.e(TAG, "Failed to start stream: ${error.description}")
                _status.value = "❌ Failed to start stream: ${error.description}"
            }

            // Start Motion/IMU if enabled
            if (settings.imuEnabled) {
                startMotion()
            }
        }
        .onFailure { error, _ ->
            Log.e(TAG, "Failed to add camera: ${error.description}")
            _status.value = "❌ Failed to add camera: ${error.description}"
        }
    }

    private fun startMotion() {
        val currentSession = session ?: return

        Log.i(TAG, "Adding Motion capability")

        currentSession.addMotion(
            MotionConfiguration(
                samplingRate = when (settings.imuRate) {
                    5 -> MotionSamplingRate.HZ_5
                    10 -> MotionSamplingRate.HZ_10
                    15 -> MotionSamplingRate.HZ_15
                    24 -> MotionSamplingRate.HZ_24
                    30 -> MotionSamplingRate.HZ_30
                    60 -> MotionSamplingRate.HZ_60
                    else -> MotionSamplingRate.HZ_60
                }
            )
        )
        .onSuccess { addedMotion ->
            motion = addedMotion
            Log.i(TAG, "Motion added successfully")

            // Create motion pipeline
            motionPipeline = MotionPipeline(
                sessionId = sessionId,
                clocks = clocks,
                scope = coroutineScope,
                nominalRateHz = settings.imuRate
            )

            // Monitor motion state
            coroutineScope.launch {
                addedMotion.state.collect { state ->
                    Log.i(TAG, "Motion state: $state")
                    when (state) {
                        MotionState.STARTED -> {
                            coroutineScope.launch {
                                emitEvent(EventNames.IMU_STARTED, buildJsonObject {
                                    put("rate_hz", settings.imuRate)
                                })
                            }
                        }
                        else -> {}
                    }
                }
            }

            // Collect IMU samples with UNDISPATCHED to not miss first samples
            coroutineScope.launch(start = CoroutineStart.UNDISPATCHED) {
                try {
                    addedMotion.samples.collect { sample ->
                        motionPipeline?.offer(sample)
                        _imuCount.value = _imuCount.value + 1
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Error collecting IMU samples", e)
                }
                Log.i(TAG, "IMU sample collection stopped")
            }

            // Monitor motion errors
            coroutineScope.launch {
                addedMotion.errors.collect { error ->
                    error?.let {
                        Log.e(TAG, "Motion error: $it")
                        emitEvent(EventNames.IMU_ERROR, buildJsonObject {
                            put("error", it.toString())
                        })
                    }
                }
            }

            // Collect and send IMU batches to telemetry uplink
            coroutineScope.launch {
                motionPipeline?.batches?.collect { batch ->
                    telemetryUplink?.send(batch)
                }
            }

            // Start motion
            addedMotion.start()
        }
        .onFailure { error, _ ->
            Log.e(TAG, "Failed to add Motion: ${error.description}")
        }
    }

    private fun handleVideoFrame(frame: VideoFrame) {
        // Update frame count
        val count = _frameCount.value + 1
        _frameCount.value = count

        if (!frame.isCompressed) {
            Log.w(TAG, "Received uncompressed frame - not supported yet")
            return
        }

        // Log codec config frames
        if (frame.isCodecConfig) {
            Log.i(TAG, "Received codec config: ${frame.buffer.remaining()} bytes")
        }

        // Log first 10 frames and then periodically
        if (count <= 10 || count % 60 == 0) {
            Log.i(TAG, "Frame $count: ${frame.buffer.remaining()} bytes, " +
                "time=${frame.presentationTimeUs}us, config=${frame.isCodecConfig}")
        }

        // Decode and display frame
        hevcDecoder?.decodeFrame(
            frameData = frame.buffer,
            presentationTimeUs = frame.presentationTimeUs,
            isConfig = frame.isCodecConfig
        )
    }

    fun stopStreaming() {
        camera?.stop()
        _status.value = "Stopping stream..."
        Log.i(TAG, "Stopping camera")
    }

    fun stopSession() {
        session?.stop()
        _status.value = "Stopping session..."
        Log.i(TAG, "Stopping session")
    }

    private fun cleanup() {
        stopDecoder()

        // Stop pipelines
        motionPipeline?.stop()
        motionPipeline = null

        audioPipeline?.stop()
        audioPipeline = null

        videoPipeline?.stop()
        videoPipeline = null

        // Stop uplinks
        mediaUplink?.stop()
        mediaUplink = null

        // Stop motion
        motion?.stop()
        motion = null

        camera = null
        stream = null
        session = null
        _isStreaming.value = false
        _frameCount.value = 0
        _imuCount.value = 0
    }

    fun shutdown() {
        cleanup()
        telemetryUplink?.stop()
        telemetryUplink = null
    }
}
