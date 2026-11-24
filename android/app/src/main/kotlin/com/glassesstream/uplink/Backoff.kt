package com.glassesstream.uplink

import kotlin.math.min

/**
 * Exponential backoff for reconnection attempts
 * Following TECH-02 section 5.2: 1, 2, 4, 8, 15 seconds
 */
class Backoff(
    private val initialDelayMs: Long = 1000,
    private val maxDelayMs: Long = 15000
) {
    private var attempt = 0

    /**
     * Get the delay for the current attempt and increment
     */
    fun nextDelay(): Long {
        val delay = if (attempt == 0) {
            initialDelayMs
        } else {
            min(initialDelayMs * (1 shl (attempt - 1)), maxDelayMs)
        }
        attempt++
        return delay
    }

    /**
     * Reset backoff after successful connection
     */
    fun reset() {
        attempt = 0
    }

    /**
     * Get current attempt number
     */
    fun getAttempt(): Int = attempt
}
