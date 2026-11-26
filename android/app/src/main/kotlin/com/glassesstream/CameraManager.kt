package com.glassesstream

import android.content.Context
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.util.Log
import android.view.Surface
import androidx.annotation.RequiresApi
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
import com.meta.wearable.dat.camera.types.AudioCodec
import com.meta.wearable.dat.camera.types.AudioSampleRate
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.selectors.AutoDeviceSelector
import com.meta.wearable.dat.core.session.DeviceSession
import com.meta.wearable.dat.core.session.DeviceSessionState
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
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

    // Collector jobs for proper cancellation
    private var videoCollectorJob: kotlinx.coroutines.Job? = null
    private var audioCollectorJob: kotlinx.coroutines.Job? = null
    private var audioPipelineJob: kotlinx.coroutines.Job? = null
    private var videoPipelineJob: kotlinx.coroutines.Job? = null

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

    // Upload toggles (all streams active, these control server upload)
    private var uploadVideo = true
    private var uploadAudio = true
    private var uploadImu = true

    // HFP audio capture
    private var audioManager: AudioManager? = null
    private var audioRecord: AudioRecord? = null
    private var hfpConfigured = false

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

    /**
     * Enable/disable video upload to server
     * Video still streams from glasses and displays locally
     */
    fun setVideoUploadEnabled(enabled: Boolean) {
        uploadVideo = enabled
        Log.i(TAG, "Video upload ${if (enabled) "enabled" else "disabled"}")
        if (_isStreaming.value) {
            updateUploadStatus()
        }
    }

    /**
     * Enable/disable audio upload to server
     * Audio still streams from glasses
     */
    fun setAudioUploadEnabled(enabled: Boolean) {
        uploadAudio = enabled
        Log.i(TAG, "Audio upload ${if (enabled) "enabled" else "disabled"}")
        if (_isStreaming.value) {
            updateUploadStatus()
        }
    }

    /**
     * Enable/disable IMU upload to server
     * IMU still streams from glasses
     */
    fun setImuUploadEnabled(enabled: Boolean) {
        uploadImu = enabled
        Log.i(TAG, "IMU upload ${if (enabled) "enabled" else "disabled"}")
        if (_isStreaming.value) {
            updateUploadStatus()
        }
    }

    /**
     * Update status based on what's being uploaded to server
     */
    private fun updateUploadStatus() {
        val uploading = mutableListOf<String>()
        if (uploadVideo) uploading.add("Video")
        if (uploadAudio) uploading.add("Audio")
        if (uploadImu) uploading.add("IMU")

        _status.value = if (uploading.isEmpty()) {
            "🎥 Streaming locally (not uploading)"
        } else {
            "📤 Uploading: ${uploading.joinToString(" + ")}"
        }
    }

    /**
     * Configure HFP (Hands-Free Profile) audio route for microphone capture
     * Must be called BEFORE starting camera stream (Meta ordering constraint)
     *
     * Supports both:
     * - Android S+ (API 31+): AudioManager.setCommunicationDevice()
     * - Android 10+ (API 29+): Legacy Bluetooth SCO
     */
    private suspend fun configureHfpAudio(): Boolean {
        if (!settings.audioEnabled) return true
        if (hfpConfigured) return true

        try {
            audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                // Android S+ (API 31+): Modern API
                Log.i(TAG, "Configuring HFP using modern API (Android S+)")

                val bluetoothDevice = audioManager?.availableCommunicationDevices?.firstOrNull { device ->
                    device.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO
                }

                if (bluetoothDevice == null) {
                    Log.w(TAG, "No Bluetooth SCO device found for HFP audio")
                    return false
                }

                audioManager?.mode = AudioManager.MODE_IN_COMMUNICATION
                val success = audioManager?.setCommunicationDevice(bluetoothDevice) ?: false

                if (success) {
                    Log.i(TAG, "HFP audio route configured (modern), waiting for settlement...")
                    kotlinx.coroutines.delay(2000)
                    hfpConfigured = true
                    Log.i(TAG, "HFP audio route ready")
                } else {
                    Log.e(TAG, "Failed to set HFP communication device")
                }

                return success
            } else {
                // Android 10-11 (API 29-30): Legacy Bluetooth SCO API
                Log.i(TAG, "Configuring HFP using legacy Bluetooth SCO (Android 10-11)")

                audioManager?.let { am ->
                    // Enable Bluetooth SCO
                    am.mode = AudioManager.MODE_IN_COMMUNICATION
                    am.startBluetoothSco()
                    am.isBluetoothScoOn = true

                    Log.i(TAG, "Bluetooth SCO started, waiting for route settlement...")
                    // Wait for SCO connection to establish
                    kotlinx.coroutines.delay(2000)

                    // Check if SCO is connected
                    if (am.isBluetoothScoOn) {
                        hfpConfigured = true
                        Log.i(TAG, "HFP audio route ready (legacy SCO)")
                        return true
                    } else {
                        Log.w(TAG, "Bluetooth SCO failed to connect")
                        return false
                    }
                }

                return false
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error configuring HFP audio: $e", e)
            return false
        }
    }

    /**
     * Start capturing audio via HFP using AudioRecord
     */
    private fun startHfpAudioCapture() {
        if (!settings.audioEnabled || !hfpConfigured) return

        try {
            val sampleRate = settings.audioSampleRate
            val channelConfig = if (settings.audioChannels == 1)
                AudioFormat.CHANNEL_IN_MONO else AudioFormat.CHANNEL_IN_STEREO
            val audioFormat = AudioFormat.ENCODING_PCM_16BIT

            val bufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)

            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                sampleRate,
                channelConfig,
                audioFormat,
                bufferSize * 2
            )

            audioRecord?.startRecording()
            Log.i(TAG, "HFP AudioRecord started: $sampleRate Hz, buffer=$bufferSize")

            // Capture audio in background
            coroutineScope.launch(Dispatchers.IO) {
                val buffer = ByteArray(bufferSize)
                var frameCount = 0

                while (audioRecord?.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                    val bytesRead = audioRecord?.read(buffer, 0, buffer.size) ?: 0

                    if (bytesRead > 0) {
                        frameCount++
                        if (frameCount <= 5 || frameCount % 50 == 0) {
                            Log.i(TAG, "HFP audio frame $frameCount: $bytesRead bytes (PCM)")
                        }

                        // Send raw PCM audio directly to MediaUplink
                        // Note: This sends PCM, not AAC - relay server needs to handle this
                        if (uploadAudio) {
                            val presentationTimeUs = System.nanoTime() / 1000
                            mediaUplink?.sendAudioFrame(buffer.copyOf(bytesRead), presentationTimeUs)
                        }
                    }
                }
                Log.i(TAG, "HFP audio capture stopped after $frameCount frames")
            }

        } catch (e: Exception) {
            Log.e(TAG, "Error starting HFP audio capture: $e", e)
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

        // Check DAT permissions before starting
        coroutineScope.launch {
            if (!checkDatPermissions()) {
                return@launch
            }

            // For initial DAT audio testing, HFP is disabled by default
            // HFP should only be used as explicit fallback, not automatically
            if (settings.audioEnabled && false) { // HFP disabled for DAT audio validation
                Log.i(TAG, "[HFP_AUDIO] HFP fallback is disabled - using DAT in-stream audio only")
                val hfpReady = configureHfpAudio()
                if (hfpReady) {
                    Log.i(TAG, "[HFP_AUDIO] HFP configured, starting camera stream...")
                    startCameraStream()
                } else {
                    Log.w(TAG, "[HFP_AUDIO] HFP not available, falling back to video-only")
                    startCameraStream()
                }
            } else {
                Log.i(TAG, "[DAT_AUDIO] Starting camera stream with DAT in-stream audio")
                startCameraStream()
            }
        }
    }

    /**
     * Check required DAT permissions before starting audio-enabled stream
     * Returns true if permissions are granted, false otherwise
     */
    private suspend fun checkDatPermissions(): Boolean {
        Log.i(TAG, "[DAT_PERMISSION] Checking required DAT permissions")

        // Always need CAMERA permission
        val cameraStatus = Wearables.checkPermissionStatus(Permission.CAMERA)
        Log.i(TAG, "[DAT_PERMISSION] Camera permission: $cameraStatus")

        if (!cameraStatus.isSuccess || cameraStatus.getOrNull() != PermissionStatus.Granted) {
            _status.value = "❌ Camera permission required. Grant in Devices tab."
            Log.e(TAG, "[DAT_PERMISSION] Camera permission not granted")
            return false
        }

        // Need MICROPHONE permission for audio-enabled streams
        if (settings.audioEnabled) {
            val micStatus = Wearables.checkPermissionStatus(Permission.MICROPHONE)
            Log.i(TAG, "[DAT_PERMISSION] Microphone permission: $micStatus")

            if (!micStatus.isSuccess || micStatus.getOrNull() != PermissionStatus.Granted) {
                _status.value = "❌ Microphone permission required for audio. Grant in Devices tab."
                Log.e(TAG, "[DAT_PERMISSION] Microphone permission not granted")
                return false
            }
        }

        Log.i(TAG, "[DAT_PERMISSION] All required permissions granted")
        return true
    }

    private fun startCameraStream() {
        val currentSession = session
        if (currentSession == null) {
            _status.value = "❌ Session not available"
            Log.e(TAG, "[DAT_CAMERA] Session is null in startCameraStream")
            return
        }

        // CRITICAL: Wait for session to reach STARTED state before adding camera
        val currentState = currentSession.state.value
        if (currentState != DeviceSessionState.STARTED) {
            _status.value = "❌ Session not ready (state: $currentState)"
            Log.e(TAG, "[DAT_SESSION] Cannot add camera - session state is $currentState, must be STARTED")
            return
        }

        _status.value = "Starting camera stream..."
        Log.i(TAG, "[DAT_CAMERA] Adding camera to session (state: $currentState)")

        currentSession.addCamera(
            StreamConfiguration(
                videoQuality = when (settings.videoQuality) {
                    "HIGH" -> VideoQuality.HIGH
                    "LOW" -> VideoQuality.LOW
                    else -> VideoQuality.MEDIUM
                },
                frameRate = settings.frameRate,
                compressVideo = true,  // HEVC compressed
                // Try in-stream audio with SDK 1.1.0
                audioCodec = if (settings.audioEnabled) {
                    AudioCodec.PCM(
                        sampleRate = when (settings.audioSampleRate) {
                            16000 -> AudioSampleRate.RATE_16000
                            44100 -> AudioSampleRate.RATE_44100
                            48000 -> AudioSampleRate.RATE_48000
                            else -> AudioSampleRate.RATE_48000
                        },
                        numberOfChannels = settings.audioChannels
                    )
                } else {
                    null  // Video-only streaming
                }
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
                            updateUploadStatus()
                            // Start decoder when streaming begins
                            videoSurface?.let { startDecoder(it) }
                            // Start HFP audio capture if configured
                            if (hfpConfigured) {
                                startHfpAudioCapture()
                            }
                        }
                        StreamState.STOPPED, StreamState.CLOSED -> {
                            _status.value = "✅ Stream stopped - Ready to restart"
                            _isStreaming.value = false
                            Log.i(TAG, "Stream ended")
                            // Don't call cleanup() here - let stopStreaming() handle it
                            // This way session stays alive for restart
                        }
                        else -> {
                            _status.value = "Stream: $state"
                        }
                    }
                }
            }

            // Initialize media uplink
            initializeMediaUplink()

            // Always start video pipeline (from glasses to phone)
            videoPipeline = VideoPipeline(context, sessionId, coroutineScope)
            videoPipeline?.start(addedStream)

            // Collect from video pipeline and send to server if upload enabled
            videoPipelineJob = coroutineScope.launch {
                videoPipeline?.packets?.collect { packet ->
                    if (uploadVideo) {
                        mediaUplink?.sendVideoFrame(packet)
                    }
                }
            }

            // Always collect video frames for local display
            videoCollectorJob = coroutineScope.launch(Dispatchers.Default) {
                Log.i(TAG, "[DAT_CAMERA] Starting video frame collection")
                try {
                    addedStream.videoStream.collect { frame ->
                        // Always display locally, even if not uploading
                        handleVideoFrame(frame)
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "[DAT_CAMERA] Error collecting video frames", e)
                }
                Log.i(TAG, "[DAT_CAMERA] Video frame collection stopped")
            }

            // Always collect audio frames from glasses
            audioPipeline = AudioPipeline(
                sessionId = sessionId,
                sampleRate = settings.audioSampleRate,
                channels = settings.audioChannels
            )

            audioCollectorJob = coroutineScope.launch(Dispatchers.Default) {
                Log.i(TAG, "[DAT_AUDIO] Starting audio frame collection from glasses audioStream")
                var audioFrameCount = 0
                try {
                    addedStream.audioStream.collect { frame ->
                        audioFrameCount++

                        // CRITICAL: Copy buffer bytes IMMEDIATELY before SDK reuses it
                        // Do NOT pass AudioFrame to async processing - copy first!
                        val source = frame.buffer.duplicate() // Don't modify original position
                        val audioBytes = ByteArray(source.remaining())
                        source.get(audioBytes)

                        if (audioFrameCount <= 5 || audioFrameCount % 50 == 0) {
                            Log.i(TAG, "[DAT_AUDIO] Audio frame $audioFrameCount: ${audioBytes.size} bytes, pts=${frame.presentationTimeUs}us")
                        }

                        // Create application-owned frame data with copied bytes
                        val frameData = AudioPipeline.AudioFrameData(
                            presentationTimeUs = frame.presentationTimeUs,
                            data = audioBytes,
                            sampleRate = settings.audioSampleRate,
                            channels = settings.audioChannels
                        )

                        // Now safe to pass to pipeline (no SDK buffer references)
                        audioPipeline?.offer(frameData)
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "[DAT_AUDIO] Error collecting audio frames", e)
                }
                Log.i(TAG, "[DAT_AUDIO] Audio frame collection stopped after $audioFrameCount frames")
            }

            // Collect from audio pipeline and send to server if upload enabled
            audioPipelineJob = coroutineScope.launch {
                audioPipeline?.frames?.collect { frameData ->
                    if (uploadAudio) {
                        mediaUplink?.sendAudioFrame(frameData.data, frameData.presentationTimeUs)
                    }
                }
            }

            // Emit audio_started event
            coroutineScope.launch {
                emitEvent(EventNames.AUDIO_STARTED, buildJsonObject {
                    put("sample_rate_hz", settings.audioSampleRate)
                    put("channels", settings.audioChannels)
                })
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

            // Always start Motion/IMU from glasses
            startMotion()

            // Update status based on what's being uploaded
            updateUploadStatus()
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
                        // Only upload to server if enabled
                        if (uploadImu) {
                            motionPipeline?.offer(sample)
                        }
                        // Always count samples locally
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

    /**
     * Stop HFP audio capture and reset audio manager
     */
    private fun stopHfpAudio() {
        try {
            audioRecord?.stop()
            audioRecord?.release()
            audioRecord = null

            audioManager?.let { am ->
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    // Modern API cleanup
                    am.clearCommunicationDevice()
                } else {
                    // Legacy SCO cleanup
                    am.isBluetoothScoOn = false
                    am.stopBluetoothSco()
                }
                am.mode = AudioManager.MODE_NORMAL
            }
            audioManager = null
            hfpConfigured = false

            Log.i(TAG, "HFP audio stopped and cleaned up")
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping HFP audio: $e", e)
        }
    }

    fun stopStreaming() {
        Log.i(TAG, "[DAT_CAMERA] Stopping streaming")
        _status.value = "Stopping stream..."

        // CRITICAL: Cancel collector jobs FIRST to stop data flow
        audioCollectorJob?.cancel()
        audioCollectorJob = null
        videoCollectorJob?.cancel()
        videoCollectorJob = null
        audioPipelineJob?.cancel()
        audioPipelineJob = null
        videoPipelineJob?.cancel()
        videoPipelineJob = null
        Log.i(TAG, "[DAT_AUDIO] Cancelled all collector jobs")

        // Stop camera stream (this will stop SDK from delivering frames)
        camera?.stop()

        // Stop motion
        motion?.stop()

        // Stop HFP audio
        stopHfpAudio()

        // Clean up pipelines and uplinks but KEEP session alive
        stopDecoder()

        // Stop pipelines (this closes their channels)
        motionPipeline?.stop()
        motionPipeline = null

        audioPipeline?.stop()
        audioPipeline = null

        videoPipeline?.stop()
        videoPipeline = null

        // Stop uplinks
        mediaUplink?.stop()
        mediaUplink = null

        // Clear camera and stream references
        camera = null
        stream = null
        motion = null

        // Keep session alive for restart!
        // session = null  // DON'T clear this

        _isStreaming.value = false
        _frameCount.value = 0
        _imuCount.value = 0

        _status.value = "✅ Stopped - Ready to restart"
        Log.i(TAG, "[DAT_CAMERA] Streaming stopped, session still active, ready for restart")
    }

    fun stopSession() {
        Log.i(TAG, "Stopping session")
        _status.value = "Stopping session..."

        // Stop streaming first if active
        if (_isStreaming.value) {
            stopStreaming()
        }

        // Stop and cleanup session
        session?.stop()

        // cleanup() will be called by session state monitor when it reaches STOPPED

        _status.value = "✅ Session stopped - Start new session to stream"
        Log.i(TAG, "Session stopped")
    }

    private fun cleanup() {
        Log.i(TAG, "[DAT_CAMERA] Starting cleanup")

        // Cancel all collector jobs
        audioCollectorJob?.cancel()
        audioCollectorJob = null
        videoCollectorJob?.cancel()
        videoCollectorJob = null
        audioPipelineJob?.cancel()
        audioPipelineJob = null
        videoPipelineJob?.cancel()
        videoPipelineJob = null

        stopDecoder()
        stopHfpAudio()

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

        Log.i(TAG, "[DAT_CAMERA] Cleanup completed")
    }

    fun shutdown() {
        cleanup()
        telemetryUplink?.stop()
        telemetryUplink = null
    }
}
