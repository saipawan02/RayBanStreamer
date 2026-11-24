package com.glassesstream

import android.app.Activity
import android.content.Context
import android.util.Log
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.DeviceIdentifier
import com.meta.wearable.dat.core.types.RegistrationState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Manages Meta Wearables SDK integration
 */
class WearablesManager(
    private val context: Context,
    private val coroutineScope: CoroutineScope
) {
    private val TAG = "WearablesManager"

    private val _status = MutableStateFlow<String>("Not initialized")
    val status: StateFlow<String> = _status.asStateFlow()

    private val _devices = MutableStateFlow<Set<DeviceIdentifier>>(emptySet())
    val devices: StateFlow<Set<DeviceIdentifier>> = _devices.asStateFlow()

    private val _registrationState = MutableStateFlow<RegistrationState?>(null)
    val registrationState: StateFlow<RegistrationState?> = _registrationState.asStateFlow()

    private var monitoringStarted = false

    fun initialize() {
        try {
            _status.value = "Initializing SDK..."
            Wearables.initialize(context)
            _status.value = "✅ SDK Ready"
            Log.i(TAG, "SDK initialized successfully")
            startMonitoring()
        } catch (e: Exception) {
            _status.value = "❌ SDK Error: ${e.message}"
            Log.e(TAG, "SDK initialization failed", e)
        }
    }

    private fun startMonitoring() {
        if (monitoringStarted) {
            Log.i(TAG, "Monitoring already started")
            return
        }
        monitoringStarted = true
        Log.i(TAG, "Starting device monitoring")

        // Monitor registration state
        coroutineScope.launch {
            Wearables.registrationState.collect { state ->
                Log.i(TAG, "Registration state changed: $state")
                _registrationState.value = state
                updateStatus()
            }
        }

        // Monitor connected devices
        coroutineScope.launch {
            Wearables.devices.collect { deviceSet ->
                Log.i(TAG, "Devices updated: ${deviceSet.size} device(s)")
                _devices.value = deviceSet
                updateStatus()
            }
        }
    }

    private fun updateStatus() {
        val regState = _registrationState.value
        val regStateStr = regState?.toString() ?: "Unknown"
        val deviceCount = _devices.value.size

        _status.value = when {
            regStateStr.contains("Unregistered", ignoreCase = true) ->
                "⚠️ Not registered. Tap REGISTER to pair with Meta AI app."
            regStateStr.contains("Registered", ignoreCase = true) && deviceCount == 0 ->
                "✅ Registered. Turn on glasses and ensure Bluetooth is enabled."
            deviceCount > 0 ->
                "✅ Connected to $deviceCount device(s)"
            else ->
                "Status: $regStateStr, Devices: $deviceCount"
        }
    }

    fun startRegistration(activity: Activity) {
        Log.i(TAG, "Starting registration flow")
        Wearables.startRegistration(activity)
    }

    fun startUnregistration(activity: Activity) {
        Log.i(TAG, "Starting unregistration flow")
        Wearables.startUnregistration(activity)
    }

    fun openFirmwareUpdate(activity: Activity): Boolean {
        Log.i(TAG, "Opening firmware update")
        val result = Wearables.openFirmwareUpdate(activity)
        result.onFailure { error, _ ->
            Log.e(TAG, "Failed to open firmware update: ${error.description}")
        }
        return result.isSuccess
    }

    fun getDeviceList(): String {
        Log.i(TAG, "getDeviceList() called")
        return try {
            val deviceSet = _devices.value
            val regState = _registrationState.value
            val regStateStr = regState?.toString() ?: "Unknown"

            if (deviceSet.isEmpty()) {
                "📱 DEVICE STATUS:\n\n" +
                "Registration: $regStateStr\n" +
                "Connected devices: 0\n\n" +
                if (regStateStr.contains("Unregistered", ignoreCase = true)) {
                    "Tap REGISTER button to pair with Meta AI app"
                } else {
                    "Turn on your Ray-Ban Meta glasses\n" +
                    "Ensure Bluetooth is enabled\n" +
                    "Glasses should be in range"
                }
            } else {
                "📱 CONNECTED DEVICES:\n\n" +
                "Registration: $regStateStr\n" +
                "Found ${deviceSet.size} device(s):\n\n" +
                deviceSet.joinToString("\n") { device -> "• $device" }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error in getDeviceList", e)
            "Error: ${e.message}"
        }
    }
}
