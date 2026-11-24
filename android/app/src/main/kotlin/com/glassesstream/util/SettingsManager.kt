package com.glassesstream.util

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.*
import androidx.datastore.preferences.preferencesDataStore
import com.glassesstream.model.Settings
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/**
 * Manages app settings persistence using DataStore
 */
class SettingsManager(private val context: Context) {

    private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "settings")

    companion object {
        private val SERVER_HOST = stringPreferencesKey("server_host")
        private val SRT_PASSPHRASE = stringPreferencesKey("srt_passphrase")
        private val WS_TOKEN = stringPreferencesKey("ws_token")

        private val VIDEO_QUALITY = stringPreferencesKey("video_quality")
        private val FRAME_RATE = intPreferencesKey("frame_rate")

        private val AUDIO_ENABLED = booleanPreferencesKey("audio_enabled")
        private val AUDIO_SAMPLE_RATE = intPreferencesKey("audio_sample_rate")
        private val AUDIO_CHANNELS = intPreferencesKey("audio_channels")

        private val IMU_ENABLED = booleanPreferencesKey("imu_enabled")
        private val IMU_RATE = intPreferencesKey("imu_rate")

        private val ENABLE_RAW_PCM = booleanPreferencesKey("enable_raw_pcm")
        private val SESSION_LABEL = stringPreferencesKey("session_label")
    }

    /**
     * Flow of current settings
     */
    val settingsFlow: Flow<Settings> = context.dataStore.data.map { prefs ->
        Settings(
            serverHost = prefs[SERVER_HOST] ?: "",
            srtPassphrase = prefs[SRT_PASSPHRASE] ?: "",
            wsToken = prefs[WS_TOKEN] ?: "",
            videoQuality = prefs[VIDEO_QUALITY] ?: "MEDIUM",
            frameRate = prefs[FRAME_RATE] ?: 24,
            audioEnabled = prefs[AUDIO_ENABLED] ?: true,
            audioSampleRate = prefs[AUDIO_SAMPLE_RATE] ?: 48000,
            audioChannels = prefs[AUDIO_CHANNELS] ?: 1,
            imuEnabled = prefs[IMU_ENABLED] ?: true,
            imuRate = prefs[IMU_RATE] ?: 60,
            enableRawPcm = prefs[ENABLE_RAW_PCM] ?: false,
            sessionLabel = prefs[SESSION_LABEL] ?: ""
        )
    }

    /**
     * Update settings
     */
    suspend fun updateSettings(settings: Settings) {
        context.dataStore.edit { prefs ->
            prefs[SERVER_HOST] = settings.serverHost
            prefs[SRT_PASSPHRASE] = settings.srtPassphrase
            prefs[WS_TOKEN] = settings.wsToken
            prefs[VIDEO_QUALITY] = settings.videoQuality
            prefs[FRAME_RATE] = settings.frameRate
            prefs[AUDIO_ENABLED] = settings.audioEnabled
            prefs[AUDIO_SAMPLE_RATE] = settings.audioSampleRate
            prefs[AUDIO_CHANNELS] = settings.audioChannels
            prefs[IMU_ENABLED] = settings.imuEnabled
            prefs[IMU_RATE] = settings.imuRate
            prefs[ENABLE_RAW_PCM] = settings.enableRawPcm
            prefs[SESSION_LABEL] = settings.sessionLabel
        }
    }

    /**
     * Update just the server configuration
     */
    suspend fun updateServerConfig(host: String, wsToken: String, srtPassphrase: String = "") {
        context.dataStore.edit { prefs ->
            prefs[SERVER_HOST] = host
            prefs[WS_TOKEN] = wsToken
            prefs[SRT_PASSPHRASE] = srtPassphrase
        }
    }
}
