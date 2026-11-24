package com.glassesstream.model

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Message schemas for phone-to-server communication
 * Following TECH-01 section 5 contracts
 */

const val SCHEMA_VERSION = 1

/**
 * Base interface for all messages sent to server
 */
@Serializable
sealed interface TelemetryMessage {
    val schema_version: Int
    val type: String
    val session_id: String
    val seq: Int
    val phone_wall_ms: Long
    val phone_elapsed_ns: Long
}

/**
 * IMU batch message (TECH-01 section 5.1)
 */
@Serializable
data class ImuBatch(
    override val schema_version: Int = SCHEMA_VERSION,
    override val type: String = "imu_batch",
    override val session_id: String,
    override val seq: Int,
    override val phone_wall_ms: Long,
    override val phone_elapsed_ns: Long,
    val dropped_since_last: Int = 0,
    val samples: List<ImuSample>
) : TelemetryMessage

/**
 * Individual IMU sample within a batch
 */
@Serializable
data class ImuSample(
    val timestamp_ns: Long,
    val source: String,  // "glasses", "neural_band", "unknown"
    val accel: List<Double>?,  // [x, y, z] in m/s²
    val gyro: List<Double>?,   // [x, y, z] in rad/s
    val mag: List<Double>?,    // [x, y, z] in µT
    val quat: List<Double>?    // [w, x, y, z]
)

/**
 * Event message (TECH-01 section 5.2)
 */
@Serializable
data class Event(
    override val schema_version: Int = SCHEMA_VERSION,
    override val type: String = "event",
    override val session_id: String,
    override val seq: Int,
    override val phone_wall_ms: Long,
    override val phone_elapsed_ns: Long,
    val event: String,
    val detail: JsonObject? = null
) : TelemetryMessage

/**
 * Event names as defined in TECH-01
 */
object EventNames {
    const val SESSION_STARTED = "session_started"
    const val SETTINGS_APPLIED = "settings_applied"
    const val VIDEO_STARTED = "video_started"
    const val AUDIO_STARTED = "audio_started"
    const val IMU_STARTED = "imu_started"
    const val CLOCK_ANCHOR = "clock_anchor"
    const val STREAM_STATE = "stream_state"
    const val MOTION_STATE = "motion_state"
    const val SESSION_PAUSED = "session_paused"
    const val SESSION_RESUMED = "session_resumed"
    const val UPLINK_CONNECTED = "uplink_connected"
    const val UPLINK_RECONNECT = "uplink_reconnect"
    const val IMU_ERROR = "imu_error"
    const val WARNING = "warning"
    const val ERROR = "error"
    const val SESSION_STOPPED = "session_stopped"
}

/**
 * WebSocket hello message from server
 */
@Serializable
data class ServerHello(
    val type: String,  // "hello"
    val schema_version: Int,
    val next_seq: Int
)

/**
 * WebSocket ack message from server
 */
@Serializable
data class ServerAck(
    val type: String,  // "ack"
    val seq: Int
)
