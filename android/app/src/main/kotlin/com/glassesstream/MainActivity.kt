package com.glassesstream

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.widget.*
import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.glassesstream.model.Settings
import com.glassesstream.util.SettingsManager
import com.meta.wearable.dat.core.Wearables
import com.meta.wearable.dat.core.types.Permission
import com.meta.wearable.dat.core.types.PermissionStatus
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlin.coroutines.resume
import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

class MainActivity : AppCompatActivity() {
    private lateinit var statusText: TextView
    private lateinit var cameraStatusText: TextView
    private lateinit var frameCountText: TextView
    private lateinit var imuCountText: TextView
    private lateinit var manager: WearablesManager
    private lateinit var cameraManager: CameraManager
    private lateinit var settingsManager: SettingsManager

    private lateinit var serverHostInput: EditText
    private lateinit var wsTokenInput: EditText

    private val REQUIRED_PERMISSIONS = arrayOf(
        Manifest.permission.BLUETOOTH_CONNECT,
        Manifest.permission.CAMERA,
        Manifest.permission.RECORD_AUDIO,
        Manifest.permission.INTERNET
    )

    private var permissionContinuation: CancellableContinuation<PermissionStatus>? = null
    private val permissionMutex = Mutex()
    private val permissionsResultLauncher =
        registerForActivityResult(Wearables.RequestPermissionContract()) { result ->
            val permissionStatus = result.getOrDefault(PermissionStatus.Denied)
            permissionContinuation?.resume(permissionStatus)
            permissionContinuation = null
        }

    suspend fun requestWearablesPermission(permission: Permission): PermissionStatus {
        return permissionMutex.withLock {
            suspendCancellableCoroutine { continuation ->
                permissionContinuation = continuation
                continuation.invokeOnCancellation { permissionContinuation = null }
                permissionsResultLauncher.launch(permission)
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        settingsManager = SettingsManager(this)
        manager = WearablesManager(this, lifecycleScope)

        // Initialize CameraManager with default settings
        cameraManager = CameraManager(this, lifecycleScope, Settings.DEFAULT)

        // Load settings from DataStore and update CameraManager
        lifecycleScope.launch {
            val loadedSettings = settingsManager.settingsFlow.first()
            if (loadedSettings.serverHost.isNotEmpty()) {
                cameraManager.updateSettings(loadedSettings)
                android.util.Log.i("MainActivity", "Settings loaded and applied: host=${loadedSettings.serverHost.take(20)}")
            } else {
                android.util.Log.i("MainActivity", "No saved settings found, using defaults")
            }
        }

        // SUPER SIMPLE LAYOUT
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 32, 32, 32)
            setBackgroundColor(Color.WHITE)
        }

        // SETTINGS SECTION
        TextView(this).apply {
            text = "⚙️ SERVER SETTINGS"
            textSize = 18f
            setPadding(24, 16, 24, 8)
            setTextColor(Color.BLACK)
            layout.addView(this)
        }

        serverHostInput = EditText(this).apply {
            hint = "Server Host (Tailscale IP)"
            textSize = 14f
            setPadding(16, 16, 16, 16)
            layout.addView(this)
        }

        wsTokenInput = EditText(this).apply {
            hint = "WebSocket Token"
            textSize = 14f
            setPadding(16, 16, 16, 16)
            layout.addView(this)
        }

        Button(this).apply {
            text = "SAVE SETTINGS"
            setOnClickListener {
                lifecycleScope.launch {
                    val host = serverHostInput.text.toString()
                    val token = wsTokenInput.text.toString()
                    android.util.Log.i("MainActivity", "Saving settings: host=$host, token=${if(token.isNotEmpty()) "***" else "empty"}")
                    settingsManager.updateServerConfig(host, token)

                    // Update CameraManager with new settings
                    val newSettings = settingsManager.settingsFlow.first()
                    cameraManager.updateSettings(newSettings)
                    android.util.Log.i("MainActivity", "Settings applied to CameraManager")

                    Toast.makeText(this@MainActivity, "Settings saved and applied!", Toast.LENGTH_SHORT).show()
                }
            }
            layout.addView(this)
        }

        // Load and display current settings
        lifecycleScope.launch {
            settingsManager.settingsFlow.collect { settings ->
                serverHostInput.setText(settings.serverHost)
                wsTokenInput.setText(settings.wsToken)
            }
        }

        // BIG OBVIOUS STATUS BOX
        statusText = TextView(this).apply {
            text = "Initializing..."
            textSize = 20f
            setPadding(24, 24, 24, 24)
            setBackgroundColor(Color.LTGRAY)
            setTextColor(Color.BLACK)
            minHeight = 200
            layout.addView(this)
        }

        // BUTTONS
        Button(this).apply {
            text = "REGISTER WITH GLASSES"
            textSize = 16f
            setOnClickListener {
                manager.startRegistration(this@MainActivity)
                Toast.makeText(this@MainActivity, "Opening Meta AI app...", Toast.LENGTH_SHORT).show()
            }
            layout.addView(this)
        }

        Button(this).apply {
            text = "UPDATE GLASSES FIRMWARE"
            textSize = 16f
            setBackgroundColor(Color.CYAN)
            setOnClickListener {
                val success = manager.openFirmwareUpdate(this@MainActivity)
                if (success) {
                    Toast.makeText(this@MainActivity, "Opening firmware update...", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(this@MainActivity, "No update available", Toast.LENGTH_SHORT).show()
                }
            }
            layout.addView(this)
        }

        Button(this).apply {
            text = "CHECK DEVICES"
            textSize = 16f
            setOnClickListener {
                val info = manager.getDeviceList()
                statusText.text = info
                statusText.setBackgroundColor(Color.YELLOW)
            }
            layout.addView(this)
        }

        Button(this).apply {
            text = "REQUEST CAMERA ACCESS"
            textSize = 16f
            setOnClickListener {
                lifecycleScope.launch {
                    statusText.text = "Requesting camera permission..."
                    statusText.setBackgroundColor(Color.LTGRAY)
                    val status = requestWearablesPermission(Permission.CAMERA)
                    statusText.text = "Camera permission: $status"
                    statusText.setBackgroundColor(
                        if (status == PermissionStatus.Granted) Color.GREEN else Color.RED
                    )
                }
            }
            layout.addView(this)
        }

        // Video preview surface
        val surfaceView = SurfaceView(this).apply {
            // Video is 504x896 (portrait), display at native resolution
            layoutParams = LinearLayout.LayoutParams(
                504, // Width matches video
                896  // Height matches video
            ).apply {
                gravity = android.view.Gravity.CENTER_HORIZONTAL
            }
            // No background color - let video show through
            // setZOrderOnTop makes this surface render above its window
            setZOrderOnTop(true)
            layout.addView(this)

            holder.addCallback(object : SurfaceHolder.Callback {
                override fun surfaceCreated(holder: SurfaceHolder) {
                    // Set surface size to match video dimensions
                    holder.setFixedSize(504, 896)
                    cameraManager.setSurface(holder.surface)
                }

                override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
                    // Surface size changed
                }

                override fun surfaceDestroyed(holder: SurfaceHolder) {
                    cameraManager.setSurface(null)
                }
            })
        }

        // Camera streaming section
        cameraStatusText = TextView(this).apply {
            text = "Camera: Ready"
            textSize = 18f
            setPadding(24, 24, 24, 24)
            setBackgroundColor(Color.LTGRAY)
            setTextColor(Color.BLACK)
            layout.addView(this)
        }

        frameCountText = TextView(this).apply {
            text = "Frames: 0"
            textSize = 16f
            setPadding(24, 12, 24, 12)
            layout.addView(this)
        }

        imuCountText = TextView(this).apply {
            text = "IMU samples: 0"
            textSize = 16f
            setPadding(24, 12, 24, 12)
            layout.addView(this)
        }

        Button(this).apply {
            text = "START SESSION"
            textSize = 16f
            setOnClickListener {
                cameraManager.startSession()
            }
            layout.addView(this)
        }

        Button(this).apply {
            text = "START STREAMING"
            textSize = 16f
            setBackgroundColor(Color.GREEN)
            setOnClickListener {
                cameraManager.startStreaming()
            }
            layout.addView(this)
        }

        Button(this).apply {
            text = "STOP STREAMING"
            textSize = 16f
            setBackgroundColor(Color.RED)
            setTextColor(Color.WHITE)
            setOnClickListener {
                cameraManager.stopStreaming()
            }
            layout.addView(this)
        }

        setContentView(ScrollView(this).apply {
            addView(layout)
        })

        // Observe status updates from manager
        lifecycleScope.launch {
            manager.status.collect { status ->
                statusText.text = status
                // Color based on content
                statusText.setBackgroundColor(when {
                    status.contains("✅") -> Color.GREEN
                    status.contains("⚠️") -> Color.YELLOW
                    status.contains("❌") -> Color.RED
                    else -> Color.LTGRAY
                })
            }
        }

        // Observe camera status updates
        lifecycleScope.launch {
            cameraManager.status.collect { status ->
                cameraStatusText.text = "Camera: $status"
                cameraStatusText.setBackgroundColor(when {
                    status.contains("✅") -> Color.GREEN
                    status.contains("🎥") -> Color.CYAN
                    status.contains("❌") -> Color.RED
                    else -> Color.LTGRAY
                })
            }
        }

        // Observe frame count updates
        lifecycleScope.launch {
            cameraManager.frameCount.collect { count ->
                frameCountText.text = "Frames received: $count"
            }
        }

        // Observe IMU sample count updates
        lifecycleScope.launch {
            cameraManager.imuCount.collect { count ->
                imuCountText.text = "IMU samples: $count"
            }
        }

        checkAndRequestPermissions()
    }

    private fun checkAndRequestPermissions() {
        val missing = REQUIRED_PERMISSIONS.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            statusText.text = "Requesting permissions..."
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), 1001)
        } else {
            initializeSDK()
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 1001) {
            initializeSDK()
        }
    }

    private fun initializeSDK() {
        statusText.text = "Initializing SDK..."
        manager.initialize()
        android.os.Handler(mainLooper).postDelayed({
            statusText.text = "✅ SDK Ready!\n\nTap CHECK DEVICES button to test."
            statusText.setBackgroundColor(Color.GREEN)
        }, 500)
    }
}
