package com.glassesstream.model

/**
 * App configuration settings
 * Stored using DataStore preferences
 */
data class Settings(
    // Server configuration
    val serverHost: String = "",  // Tailscale IP or MagicDNS name
    val srtPassphrase: String = "",  // SRT encryption passphrase
    val wsToken: String = "",  // WebSocket authentication token

    // Video settings
    val videoQuality: String = "MEDIUM",  // HIGH, MEDIUM, LOW
    val frameRate: Int = 24,  // 2, 7, 15, 24, 30

    // Audio settings
    val audioEnabled: Boolean = true,
    val audioSampleRate: Int = 48000,  // 16000, 44100, 48000
    val audioChannels: Int = 1,  // mono

    // IMU settings
    val imuEnabled: Boolean = true,
    val imuRate: Int = 60,  // 5, 10, 15, 24, 30, 60 Hz

    // Optional features
    val enableRawPcm: Boolean = false,  // Send raw PCM to separate WebSocket
    val sessionLabel: String = ""  // Optional label for this session
) {
    companion object {
        val DEFAULT = Settings()
    }
}
