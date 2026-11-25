package com.glassesstream

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.*
import android.view.SurfaceHolder
import android.view.SurfaceView
import androidx.appcompat.widget.SwitchCompat
import androidx.appcompat.app.AppCompatActivity
import androidx.cardview.widget.CardView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.glassesstream.model.Settings
import com.glassesstream.util.SettingsManager
import com.google.android.material.button.MaterialButton
import com.google.android.material.tabs.TabLayout
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout
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

    private lateinit var serverHostInput: TextInputEditText
    private lateinit var wsTokenInput: TextInputEditText

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
        setTheme(R.style.AppTheme)

        settingsManager = SettingsManager(this)
        manager = WearablesManager(this, lifecycleScope)
        cameraManager = CameraManager(this, lifecycleScope, Settings.DEFAULT)

        lifecycleScope.launch {
            val loadedSettings = settingsManager.settingsFlow.first()
            if (loadedSettings.serverHost.isNotEmpty()) {
                cameraManager.updateSettings(loadedSettings)
                android.util.Log.i("MainActivity", "Settings loaded and applied: host=${loadedSettings.serverHost.take(20)}")
            } else {
                android.util.Log.i("MainActivity", "No saved settings found, using defaults")
            }
        }

        val mainLayout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(ContextCompat.getColor(context, R.color.background))
        }

        // Create TabLayout
        val tabLayout = TabLayout(this).apply {
            setBackgroundColor(ContextCompat.getColor(context, R.color.primary))
            setTabTextColors(
                ContextCompat.getColor(context, R.color.primary_light),
                Color.WHITE
            )
            tabMode = TabLayout.MODE_FIXED
            addTab(newTab().setText("Settings").setIcon(android.R.drawable.ic_menu_manage))
            addTab(newTab().setText("Devices").setIcon(android.R.drawable.ic_menu_info_details))
            addTab(newTab().setText("Stream").setIcon(android.R.drawable.ic_menu_camera))
            addTab(newTab().setText("Status").setIcon(android.R.drawable.ic_menu_view))
            mainLayout.addView(this)
        }

        // Create content container for tabs
        val tabContentContainer = FrameLayout(this).apply {
            id = android.R.id.content
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.MATCH_PARENT
            )
            mainLayout.addView(this)
        }

        // Create tab contents
        val settingsTab = createSettingsTab()
        val devicesTab = createDevicesTab()
        val streamTab = createStreamTab()
        val statusTab = createStatusTab()

        // Show settings tab by default
        tabContentContainer.removeAllViews()
        tabContentContainer.addView(settingsTab)

        // Handle tab selection
        tabLayout.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                tabContentContainer.removeAllViews()
                when (tab.position) {
                    0 -> tabContentContainer.addView(settingsTab)
                    1 -> tabContentContainer.addView(devicesTab)
                    2 -> tabContentContainer.addView(streamTab)
                    3 -> tabContentContainer.addView(statusTab)
                }
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })

        setContentView(mainLayout)

        // Observe status updates from manager
        lifecycleScope.launch {
            manager.status.collect { status ->
                statusText.text = status
                statusText.setBackgroundColor(when {
                    status.contains("✅") -> ContextCompat.getColor(this@MainActivity, R.color.success_light)
                    status.contains("⚠️") -> ContextCompat.getColor(this@MainActivity, R.color.warning_light)
                    status.contains("❌") -> ContextCompat.getColor(this@MainActivity, R.color.error_light)
                    else -> ContextCompat.getColor(this@MainActivity, R.color.surface_variant)
                })
            }
        }

        // Observe camera status updates
        lifecycleScope.launch {
            cameraManager.status.collect { status ->
                cameraStatusText.text = "Camera: $status"
                cameraStatusText.setBackgroundColor(when {
                    status.contains("✅") -> ContextCompat.getColor(this@MainActivity, R.color.success_light)
                    status.contains("🎥") -> ContextCompat.getColor(this@MainActivity, R.color.info_light)
                    status.contains("❌") -> ContextCompat.getColor(this@MainActivity, R.color.error_light)
                    else -> ContextCompat.getColor(this@MainActivity, R.color.surface_variant)
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

    private fun createSettingsTab(): ScrollView {
        return ScrollView(this).apply {
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dpToPx(16), dpToPx(16), dpToPx(16), dpToPx(16))

                addView(createCard("Server Configuration") {
                    addView(TextView(context).apply {
                        text = "Configure the server connection details"
                        textSize = 14f
                        setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
                        setPadding(0, 0, 0, dpToPx(16))
                    })

                    addView(createTextInputLayout("Server Host", "192.168.1.100").also {
                        serverHostInput = it.editText as TextInputEditText
                    })
                    addView(createTextInputLayout("WebSocket Token", "your-token-here").also {
                        wsTokenInput = it.editText as TextInputEditText
                    })
                    addView(createMaterialButton("Save Settings", R.color.primary) {
                        lifecycleScope.launch {
                            val host = serverHostInput.text.toString()
                            val token = wsTokenInput.text.toString()
                            android.util.Log.i("MainActivity", "Saving settings: host=$host, token=${if(token.isNotEmpty()) "***" else "empty"}")
                            settingsManager.updateServerConfig(host, token)
                            val newSettings = settingsManager.settingsFlow.first()
                            cameraManager.updateSettings(newSettings)
                            android.util.Log.i("MainActivity", "Settings applied to CameraManager")
                            Toast.makeText(this@MainActivity, "Settings saved successfully!", Toast.LENGTH_SHORT).show()
                        }
                    })
                })

                lifecycleScope.launch {
                    settingsManager.settingsFlow.collect { settings ->
                        serverHostInput.setText(settings.serverHost)
                        wsTokenInput.setText(settings.wsToken)
                    }
                }
            })
        }
    }

    private fun createDevicesTab(): ScrollView {
        return ScrollView(this).apply {
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dpToPx(16), dpToPx(16), dpToPx(16), dpToPx(16))

                addView(createCard("Ray-Ban Meta Glasses") {
                    addView(TextView(context).apply {
                        text = "Manage your Ray-Ban Meta glasses connection"
                        textSize = 14f
                        setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
                        setPadding(0, 0, 0, dpToPx(16))
                    })

                    addView(createMaterialButton("Register with Glasses", R.color.primary) {
                        manager.startRegistration(this@MainActivity)
                        Toast.makeText(this@MainActivity, "Opening Meta AI app...", Toast.LENGTH_SHORT).show()
                    })
                    addView(createMaterialButton("Check Devices", R.color.button_neutral) {
                        val info = manager.getDeviceList()
                        statusText.text = info
                        statusText.setBackgroundColor(ContextCompat.getColor(context, R.color.warning_light))
                    })
                    addView(createMaterialButton("Request Camera Access", R.color.accent) {
                        lifecycleScope.launch {
                            statusText.text = "Requesting camera permission..."
                            statusText.setBackgroundColor(ContextCompat.getColor(context, R.color.surface_variant))
                            val status = requestWearablesPermission(Permission.CAMERA)
                            statusText.text = "Camera permission: $status"
                            statusText.setBackgroundColor(
                                if (status == PermissionStatus.Granted)
                                    ContextCompat.getColor(context, R.color.success_light)
                                else
                                    ContextCompat.getColor(context, R.color.error_light)
                            )
                        }
                    })
                    addView(createMaterialButton("Request Microphone Access", R.color.accent) {
                        lifecycleScope.launch {
                            statusText.text = "Requesting microphone permission..."
                            statusText.setBackgroundColor(ContextCompat.getColor(context, R.color.surface_variant))
                            val status = requestWearablesPermission(Permission.MICROPHONE)
                            statusText.text = "Microphone permission: $status"
                            statusText.setBackgroundColor(
                                if (status == PermissionStatus.Granted)
                                    ContextCompat.getColor(context, R.color.success_light)
                                else
                                    ContextCompat.getColor(context, R.color.error_light)
                            )
                        }
                    })
                    addView(createMaterialButton("Update Firmware", R.color.info) {
                        val success = manager.openFirmwareUpdate(this@MainActivity)
                        if (success) {
                            Toast.makeText(this@MainActivity, "Opening firmware update...", Toast.LENGTH_SHORT).show()
                        } else {
                            Toast.makeText(this@MainActivity, "No update available", Toast.LENGTH_SHORT).show()
                        }
                    })
                })
            })
        }
    }

    private fun createStreamTab(): ScrollView {
        return ScrollView(this).apply {
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dpToPx(16), dpToPx(16), dpToPx(16), dpToPx(16))

                addView(createCard("Live Preview") {
                    val surfaceView = SurfaceView(this@MainActivity).apply {
                        layoutParams = LinearLayout.LayoutParams(
                            dpToPx(252),
                            dpToPx(448)
                        ).apply {
                            gravity = Gravity.CENTER_HORIZONTAL
                            setMargins(0, dpToPx(8), 0, dpToPx(8))
                        }
                        setZOrderOnTop(true)

                        holder.addCallback(object : SurfaceHolder.Callback {
                            override fun surfaceCreated(holder: SurfaceHolder) {
                                holder.setFixedSize(504, 896)
                                cameraManager.setSurface(holder.surface)
                            }
                            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {}
                            override fun surfaceDestroyed(holder: SurfaceHolder) {
                                cameraManager.setSurface(null)
                            }
                        })
                    }
                    addView(surfaceView)
                })

                addView(createCard("Camera Status") {
                    cameraStatusText = TextView(this@MainActivity).apply {
                        text = "Camera: Ready"
                        textSize = 16f
                        setPadding(dpToPx(16), dpToPx(12), dpToPx(16), dpToPx(12))
                        setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                        setBackgroundColor(ContextCompat.getColor(context, R.color.surface_variant))
                    }
                    addView(cameraStatusText)
                })

                addView(createCard("Server Upload Configuration") {
                    addView(TextView(context).apply {
                        text = "Control what data is sent to server\n(All streams active on phone)"
                        textSize = 13f
                        setTextColor(ContextCompat.getColor(context, R.color.text_secondary))
                        setPadding(0, 0, 0, dpToPx(12))
                    })

                    // Video Toggle
                    val videoSwitch = SwitchCompat(context).apply {
                        text = "📹 Upload Video to Server"
                        textSize = 16f
                        isChecked = true
                        setPadding(dpToPx(16), dpToPx(8), dpToPx(16), dpToPx(8))
                        setOnCheckedChangeListener { _, isChecked ->
                            cameraManager.setVideoUploadEnabled(isChecked)
                            if (isChecked) {
                                Toast.makeText(context, "Video upload ON", Toast.LENGTH_SHORT).show()
                            } else {
                                Toast.makeText(context, "Video upload OFF (saves bandwidth)", Toast.LENGTH_SHORT).show()
                            }
                        }
                    }
                    addView(videoSwitch)

                    // Audio Toggle
                    val audioSwitch = SwitchCompat(context).apply {
                        text = "🎤 Upload Audio to Server"
                        textSize = 16f
                        isChecked = true
                        setPadding(dpToPx(16), dpToPx(8), dpToPx(16), dpToPx(8))
                        setOnCheckedChangeListener { _, isChecked ->
                            cameraManager.setAudioUploadEnabled(isChecked)
                            if (isChecked) {
                                Toast.makeText(context, "Audio upload ON", Toast.LENGTH_SHORT).show()
                            } else {
                                Toast.makeText(context, "Audio upload OFF", Toast.LENGTH_SHORT).show()
                            }
                        }
                    }
                    addView(audioSwitch)

                    // IMU Toggle
                    val imuSwitch = SwitchCompat(context).apply {
                        text = "📊 Upload IMU to Server"
                        textSize = 16f
                        isChecked = true
                        setPadding(dpToPx(16), dpToPx(8), dpToPx(16), dpToPx(8))
                        setOnCheckedChangeListener { _, isChecked ->
                            cameraManager.setImuUploadEnabled(isChecked)
                            if (isChecked) {
                                Toast.makeText(context, "IMU upload ON", Toast.LENGTH_SHORT).show()
                            } else {
                                Toast.makeText(context, "IMU upload OFF", Toast.LENGTH_SHORT).show()
                            }
                        }
                    }
                    addView(imuSwitch)
                })

                addView(createCard("Streaming Controls") {
                    addView(createMaterialButton("Start Session", R.color.primary) {
                        cameraManager.startSession()
                    })

                    addView(createMaterialButton("▶ Start Streaming", R.color.button_start) {
                        cameraManager.startStreaming()
                    })

                    addView(createMaterialButton("⏹ Stop Streaming", R.color.button_stop) {
                        cameraManager.stopStreaming()
                    })
                })
            })
        }
    }

    private fun createStatusTab(): ScrollView {
        return ScrollView(this).apply {
            addView(LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dpToPx(16), dpToPx(16), dpToPx(16), dpToPx(16))

                addView(createCard("System Status") {
                    statusText = TextView(this@MainActivity).apply {
                        text = "Initializing..."
                        textSize = 16f
                        setPadding(dpToPx(16), dpToPx(16), dpToPx(16), dpToPx(16))
                        setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                        setBackgroundColor(ContextCompat.getColor(context, R.color.surface_variant))
                        minHeight = dpToPx(120)
                    }
                    addView(statusText)
                })

                addView(createCard("Statistics") {
                    frameCountText = TextView(this@MainActivity).apply {
                        text = "Frames: 0"
                        textSize = 16f
                        setPadding(dpToPx(16), dpToPx(12), dpToPx(16), dpToPx(12))
                        setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                    }
                    addView(frameCountText)

                    imuCountText = TextView(this@MainActivity).apply {
                        text = "IMU samples: 0"
                        textSize = 16f
                        setPadding(dpToPx(16), dpToPx(12), dpToPx(16), dpToPx(12))
                        setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                    }
                    addView(imuCountText)
                })
            })
        }
    }

    private fun dpToPx(dp: Int): Int {
        return (dp * resources.displayMetrics.density).toInt()
    }

    private fun createCard(title: String, content: LinearLayout.() -> Unit): CardView {
        return CardView(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                setMargins(0, 0, 0, dpToPx(16))
            }
            cardElevation = dpToPx(4).toFloat()
            radius = dpToPx(8).toFloat()
            setCardBackgroundColor(ContextCompat.getColor(context, R.color.surface))

            val cardLayout = LinearLayout(context).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dpToPx(16), dpToPx(16), dpToPx(16), dpToPx(16))

                // Add title
                addView(TextView(context).apply {
                    text = title
                    textSize = 18f
                    setTypeface(null, android.graphics.Typeface.BOLD)
                    setTextColor(ContextCompat.getColor(context, R.color.text_primary))
                    setPadding(0, 0, 0, dpToPx(12))
                })

                content()
            }

            addView(cardLayout)
        }
    }

    private fun createTextInputLayout(label: String, hint: String): TextInputLayout {
        return TextInputLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                setMargins(0, dpToPx(8), 0, dpToPx(8))
            }
            this.hint = label
            boxBackgroundMode = TextInputLayout.BOX_BACKGROUND_OUTLINE

            val editText = TextInputEditText(this.context).apply {
                this.hint = hint
                textSize = 14f
            }
            addView(editText)
        }
    }

    private fun createMaterialButton(text: String, colorRes: Int, onClick: () -> Unit): MaterialButton {
        return MaterialButton(this).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply {
                setMargins(0, dpToPx(8), 0, dpToPx(8))
            }
            this.text = text
            textSize = 14f
            setBackgroundColor(ContextCompat.getColor(context, colorRes))
            setTextColor(Color.WHITE)
            cornerRadius = dpToPx(8)
            setOnClickListener { onClick() }
        }
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
            statusText.setBackgroundColor(ContextCompat.getColor(this, R.color.success_light))
        }, 500)
    }
}
