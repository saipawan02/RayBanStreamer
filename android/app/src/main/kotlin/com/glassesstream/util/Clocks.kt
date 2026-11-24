package com.glassesstream.util

import android.os.SystemClock

/**
 * Centralized clock access for consistent timestamp handling
 * Following TECH-01 section 4.1 time model
 *
 * All clock reads go through this interface so tests can fake them
 */
interface Clocks {
    /**
     * Phone elapsed time in nanoseconds (monotonic, persists across sleep)
     * Maps to phone_elapsed_ns in TECH-01
     */
    fun elapsedRealtimeNanos(): Long

    /**
     * Phone wall clock in milliseconds since epoch (UTC)
     * Maps to phone_wall_ms in TECH-01
     */
    fun currentTimeMillis(): Long
}

/**
 * Production implementation using Android system clocks
 */
class SystemClocks : Clocks {
    override fun elapsedRealtimeNanos(): Long = SystemClock.elapsedRealtimeNanos()
    override fun currentTimeMillis(): Long = System.currentTimeMillis()
}

/**
 * Fake implementation for testing
 */
class FakeClocks(
    private var fakeElapsedNanos: Long = 0L,
    private var fakeWallMillis: Long = 0L
) : Clocks {
    override fun elapsedRealtimeNanos(): Long = fakeElapsedNanos
    override fun currentTimeMillis(): Long = fakeWallMillis

    fun advance(elapsedNanos: Long = 0L, wallMillis: Long = 0L) {
        fakeElapsedNanos += elapsedNanos
        fakeWallMillis += wallMillis
    }

    fun set(elapsedNanos: Long, wallMillis: Long) {
        fakeElapsedNanos = elapsedNanos
        fakeWallMillis = wallMillis
    }
}
