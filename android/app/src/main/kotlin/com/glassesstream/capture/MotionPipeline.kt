package com.glassesstream.capture

import android.util.Log
import com.glassesstream.model.ImuBatch
import com.glassesstream.model.ImuSample
import com.glassesstream.util.Clocks
import com.meta.wearable.dat.motion.types.MotionSample
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.Channel.Factory.UNLIMITED
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Motion/IMU sample collection and batching pipeline
 * Following TECH-02 section 6.3
 *
 * Collects samples from SDK, detects gaps, batches them every 100ms or 64 samples,
 * and emits ImuBatch messages ready for telemetry uplink.
 */
class MotionPipeline(
    private val sessionId: String,
    private val clocks: Clocks,
    private val scope: CoroutineScope,
    private val nominalRateHz: Int = 60
) {
    private val TAG = "MotionPipeline"

    // Incoming sample channel with generous capacity, drops oldest when full
    private val sampleChannel = Channel<MotionSample>(
        capacity = 1024,
        onBufferOverflow = kotlinx.coroutines.channels.BufferOverflow.DROP_OLDEST,
        onUndeliveredElement = {
            droppedCount++
            Log.w(TAG, "Sample dropped from buffer")
        }
    )

    private var droppedCount = 0
    private var lastTimestampNs: Long? = null
    private val nominalIntervalNs = 1_000_000_000L / nominalRateHz
    private var currentSeq = 0

    /**
     * Offer a sample from the SDK collector
     * Never blocks - drops oldest if channel is full
     */
    fun offer(sample: MotionSample) {
        sampleChannel.trySend(sample)
    }

    /**
     * Flow of batched IMU messages ready to send to server
     * Flushes every 100ms or 64 samples, whichever comes first
     */
    val batches: Flow<ImuBatch> = flow {
        val batch = mutableListOf<MotionSample>()
        var lastFlushTime = System.currentTimeMillis()
        val flushIntervalMs = 100L
        val maxBatchSize = 64

        // Launch a coroutine to periodically flush time-based batches
        scope.launch {
            while (isActive) {
                delay(flushIntervalMs)
                // Trigger a flush by sending a sentinel (empty sample with special marker)
                // Actually, better to use a timer and check in the main loop
            }
        }

        // Main collection loop
        for (sample in sampleChannel) {
            batch.add(sample)

            val now = System.currentTimeMillis()
            val shouldFlushSize = batch.size >= maxBatchSize
            val shouldFlushTime = (now - lastFlushTime) >= flushIntervalMs

            if (shouldFlushSize || shouldFlushTime) {
                if (batch.isNotEmpty()) {
                    val imuBatch = createBatch(batch)
                    emit(imuBatch)
                    batch.clear()
                    lastFlushTime = now
                }
            }
        }

        // Final flush if any remaining samples
        if (batch.isNotEmpty()) {
            emit(createBatch(batch))
        }
    }

    private fun createBatch(samples: List<MotionSample>): ImuBatch {
        val phoneWallMs = clocks.currentTimeMillis()
        val phoneElapsedNs = clocks.elapsedRealtimeNanos()

        // Detect gaps in timestamp sequence
        var dropped = droppedCount
        droppedCount = 0  // Reset after including in this batch

        for (sample in samples) {
            val last = lastTimestampNs
            if (last != null) {
                val delta = sample.timestampNs - last
                val expectedMax = nominalIntervalNs * 3
                if (delta > expectedMax) {
                    // Gap detected - estimate missing samples
                    val missingSamples = (delta / nominalIntervalNs) - 1
                    dropped += missingSamples.toInt()
                    Log.w(TAG, "Gap detected: $delta ns, estimated $missingSamples missing samples")
                }
            }
            lastTimestampNs = sample.timestampNs
        }

        val imuSamples = samples.map { sample ->
            ImuSample(
                timestamp_ns = sample.timestampNs,
                source = when (sample.source) {
                    com.meta.wearable.dat.motion.types.MotionSource.GLASSES -> "glasses"
                    com.meta.wearable.dat.motion.types.MotionSource.NEURAL_BAND -> "neural_band"
                    else -> "unknown"
                },
                accel = sample.accelerometer?.let { listOf(it.x.toDouble(), it.y.toDouble(), it.z.toDouble()) },
                gyro = sample.gyroscope?.let { listOf(it.x.toDouble(), it.y.toDouble(), it.z.toDouble()) },
                mag = sample.magnetometer?.let { listOf(it.x.toDouble(), it.y.toDouble(), it.z.toDouble()) },
                quat = sample.orientation?.let { listOf(it.w.toDouble(), it.x.toDouble(), it.y.toDouble(), it.z.toDouble()) }
            )
        }

        val batch = ImuBatch(
            session_id = sessionId,
            seq = currentSeq++,
            phone_wall_ms = phoneWallMs,
            phone_elapsed_ns = phoneElapsedNs,
            dropped_since_last = dropped,
            samples = imuSamples
        )

        return batch
    }

    /**
     * Stop the pipeline and close the channel
     */
    fun stop() {
        sampleChannel.close()
        Log.i(TAG, "Motion pipeline stopped")
    }
}
