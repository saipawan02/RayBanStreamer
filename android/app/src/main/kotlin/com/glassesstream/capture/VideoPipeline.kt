package com.glassesstream.capture

import android.util.Log
import com.meta.wearable.dat.camera.Stream
import com.meta.wearable.dat.camera.types.VideoFrame
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.launch
import java.nio.ByteBuffer
import java.security.MessageDigest

/**
 * Captures video frames from the SDK and prepares them for upload
 *
 * TECH-02 section 6.1: Copy ByteBuffer immediately, never hold SDK buffer past callback
 */
class VideoPipeline(
    private val context: android.content.Context,
    private val sessionId: String,
    private val scope: CoroutineScope
) {
    private val TAG = "VideoPipeline"

    /**
     * HEVC NAL Unit representation
     * Phase 2 Task 1: Proper Annex-B parsing
     */
    data class NALUnit(
        val type: Int,           // NAL unit type (0-63)
        val typeName: String,    // Human-readable name
        val offset: Int,         // Start position in buffer
        val size: Int,           // Total NAL unit size including start code
        val headerSize: Int,     // Start code size (3 or 4 bytes)
        val payloadSize: Int,    // Actual NAL data size
        val isValid: Boolean,    // Has complete 2-byte HEVC header
        val isTruncated: Boolean // Appears incomplete
    ) {
        companion object {
            fun getTypeName(type: Int): String = when (type) {
                0 -> "TRAIL_N"
                1 -> "TRAIL_R"
                2 -> "TSA_N"
                3 -> "TSA_R"
                16 -> "BLA_W_LP"
                17 -> "BLA_W_RADL"
                18 -> "BLA_N_LP"
                19 -> "IDR_W_RADL"
                20 -> "IDR_N_LP"
                21 -> "CRA_NUT"
                32 -> "VPS_NUT"
                33 -> "SPS_NUT"
                34 -> "PPS_NUT"
                35 -> "AUD_NUT"
                39 -> "PREFIX_SEI_NUT"
                40 -> "SUFFIX_SEI_NUT"
                else -> "NAL_$type"
            }
        }
    }

    data class VideoPacket(
        val data: ByteArray,
        val presentationTimeUs: Long,
        val isKeyframe: Boolean,
        val isCodecConfig: Boolean,  // VPS/SPS/PPS parameter sets
        val width: Int,
        val height: Int,
        val isCompressed: Boolean,
        val sequence: Int,           // Phase 2 Task 4: Frame sequence number
        val sha256: String           // Phase 2 Task 4: Payload checksum
    )

    private val frameChannel = Channel<VideoPacket>(
        capacity = 48, // ~2 seconds at 24fps
        onBufferOverflow = BufferOverflow.DROP_OLDEST
    )

    private var droppedFrames = 0
    private var frameCounter = 0
    private var codecConfigFrames = 0
    private var keyframeCount = 0

    // Phase 2 Task 3: Raw HEVC bitstream capture
    private var captureOutputStream: java.io.FileOutputStream? = null
    private var captureFile: java.io.File? = null
    private var captureFramesWritten = 0
    private var captureBytesWritten = 0L
    private var captureCodecConfigCount = 0
    private var captureKeyframeCount = 0

    // Phase 2 Task 4: Data integrity validation
    private var manifestWriter: java.io.BufferedWriter? = null
    private var sequenceNumber = 0

    /**
     * Flow of video packets ready for upload
     */
    val packets: Flow<VideoPacket> = flow {
        for (packet in frameChannel) {
            emit(packet)
        }
    }

    /**
     * Phase 2 Task 1: Parse Annex-B HEVC bitstream
     *
     * Finds all NAL units in the buffer, supporting both 3-byte and 4-byte start codes.
     * Returns list of NAL units with complete metadata.
     */
    private fun parseAnnexBNALUnits(data: ByteArray): List<NALUnit> {
        val nalUnits = mutableListOf<NALUnit>()
        var pos = 0

        while (pos < data.size - 4) {
            // Check for start code: 00 00 00 01 or 00 00 01
            val startCodeSize = when {
                pos + 3 < data.size &&
                data[pos] == 0.toByte() &&
                data[pos + 1] == 0.toByte() &&
                data[pos + 2] == 0.toByte() &&
                data[pos + 3] == 1.toByte() -> 4

                pos + 2 < data.size &&
                data[pos] == 0.toByte() &&
                data[pos + 1] == 0.toByte() &&
                data[pos + 2] == 1.toByte() -> 3

                else -> {
                    pos++
                    continue
                }
            }

            val nalStart = pos + startCodeSize

            // Validate HEVC header (need at least 2 bytes)
            if (nalStart + 2 > data.size) {
                // Truncated NAL unit at end of buffer
                val nalUnit = NALUnit(
                    type = -1,
                    typeName = "TRUNCATED",
                    offset = pos,
                    size = data.size - pos,
                    headerSize = startCodeSize,
                    payloadSize = data.size - nalStart,
                    isValid = false,
                    isTruncated = true
                )
                nalUnits.add(nalUnit)
                break
            }

            // Extract NAL unit type from first header byte
            // HEVC NAL header structure:
            //   Bit 0: forbidden_zero_bit
            //   Bits 1-6: nal_unit_type
            //   Bits 7-8: nuh_layer_id (part 1)
            val firstHeaderByte = data[nalStart].toInt() and 0xFF
            val nalType = (firstHeaderByte ushr 1) and 0x3F

            // Find end of this NAL unit (next start code or end of buffer)
            var nalEnd = findNextStartCode(data, nalStart)
            if (nalEnd == -1) {
                nalEnd = data.size
            }

            val nalSize = nalEnd - pos
            val payloadSize = nalEnd - nalStart

            val nalUnit = NALUnit(
                type = nalType,
                typeName = NALUnit.getTypeName(nalType),
                offset = pos,
                size = nalSize,
                headerSize = startCodeSize,
                payloadSize = payloadSize,
                isValid = true,
                isTruncated = false
            )
            nalUnits.add(nalUnit)

            pos = nalEnd
        }

        return nalUnits
    }

    /**
     * Phase 2 Task 4: Calculate SHA-256 checksum of payload
     */
    private fun sha256(data: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256")
        return digest.digest(data).joinToString("") { "%02x".format(it) }
    }

    /**
     * Find the next Annex-B start code starting from the given position
     * Returns -1 if no start code is found
     */
    private fun findNextStartCode(data: ByteArray, startPos: Int): Int {
        var pos = startPos

        while (pos < data.size - 3) {
            if (data[pos] == 0.toByte() && data[pos + 1] == 0.toByte()) {
                // Check for 4-byte start code
                if (pos + 3 < data.size &&
                    data[pos + 2] == 0.toByte() &&
                    data[pos + 3] == 1.toByte()) {
                    return pos
                }
                // Check for 3-byte start code
                if (data[pos + 2] == 1.toByte()) {
                    return pos
                }
            }
            pos++
        }

        return -1
    }

    /**
     * Start collecting frames from the SDK stream
     */
    fun start(stream: Stream) {
        // Phase 2 Task 3: Open raw HEVC capture file
        // Use app's cache directory (no permissions needed)
        captureFile = java.io.File(context.cacheDir, "video_capture_${sessionId.take(8)}.h265")

        try {
            captureOutputStream = java.io.FileOutputStream(captureFile!!)
            Log.i(TAG, "")
            Log.i(TAG, "=== PHASE 2 TASK 3: RAW HEVC CAPTURE ===")
            Log.i(TAG, "Capturing to: ${captureFile!!.absolutePath}")
            Log.i(TAG, "")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open capture file", e)
            captureOutputStream = null
        }

        // Phase 2 Task 4: Open manifest file
        val manifestFile = java.io.File(context.cacheDir, "android_video_manifest.jsonl")
        try {
            manifestWriter = java.io.BufferedWriter(java.io.FileWriter(manifestFile))
            Log.i(TAG, "=== PHASE 2 TASK 4: INTEGRITY VALIDATION ===")
            Log.i(TAG, "Android manifest: ${manifestFile.absolutePath}")
            Log.i(TAG, "")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to open manifest file", e)
            manifestWriter = null
        }

        scope.launch {
            stream.videoStream.collect { frame ->
                try {
                    processFrame(frame)
                } catch (e: Exception) {
                    Log.e(TAG, "Error processing frame", e)
                }
            }
        }
        Log.i(TAG, "Video pipeline started for session $sessionId")
    }

    private fun processFrame(frame: VideoFrame) {
        frameCounter++

        // Copy buffer immediately - SDK may reuse it after callback
        val buffer = frame.buffer.duplicate()
        buffer.position(0)

        val size = buffer.limit()

        if (size == 0) {
            Log.w(TAG, "Frame #$frameCounter: Received frame with 0 bytes")
            return
        }

        val data = ByteArray(size)
        buffer.get(data)

        // Check if compressed HEVC or raw YUV
        if (!frame.isCompressed) {
            Log.w(TAG, "Frame #$frameCounter: Received UNCOMPRESSED frame - compression should be enabled!")
            return
        }

        // PHASE 2 TASK 3: Write exact payload bytes to .h265 file
        captureOutputStream?.let { out ->
            try {
                out.write(data)
                captureFramesWritten++
                captureBytesWritten += data.size
            } catch (e: Exception) {
                Log.e(TAG, "Error writing to capture file", e)
            }
        }

        // PHASE 2 TASK 1: Parse and validate NAL units for first 10 frames
        val nalUnits = if (frameCounter <= 10) {
            parseAndLogNALUnits(data, frameCounter, frame)
        } else {
            // For frames > 10, just detect keyframes efficiently
            parseAnnexBNALUnits(data)
        }

        // Determine if this is a keyframe based on NAL analysis
        val isKeyframe = nalUnits.any { nal ->
            nal.type in 16..21  // IDR, BLA, CRA frames
        }

        // Check if this contains codec configuration
        val hasCodecConfig = nalUnits.any { nal ->
            nal.type in 32..34  // VPS, SPS, PPS
        }

        if (hasCodecConfig) {
            codecConfigFrames++
            captureCodecConfigCount++
        }

        if (isKeyframe) {
            keyframeCount++
            captureKeyframeCount++
        }

        // Phase 2 Task 4: Calculate checksum and increment sequence
        sequenceNumber++
        val checksum = sha256(data)

        // Write manifest entry
        manifestWriter?.let { writer ->
            try {
                val entry = """{"session":"$sessionId","sequence":$sequenceNumber,"size":${data.size},"sha256":"$checksum","timestamp":${frame.presentationTimeUs},"isCodecConfig":${frame.isCodecConfig},"isKeyframe":$isKeyframe}"""
                writer.write(entry)
                writer.newLine()
                writer.flush()
            } catch (e: Exception) {
                Log.e(TAG, "Error writing manifest entry", e)
            }
        }

        val packet = VideoPacket(
            data = data,
            presentationTimeUs = frame.presentationTimeUs,
            isKeyframe = isKeyframe,
            isCodecConfig = frame.isCodecConfig,
            width = frame.width,
            height = frame.height,
            isCompressed = frame.isCompressed,
            sequence = sequenceNumber,
            sha256 = checksum
        )

        val success = frameChannel.trySend(packet).isSuccess
        if (!success) {
            droppedFrames++
            if (droppedFrames % 10 == 0) {
                Log.w(TAG, "Dropped $droppedFrames frames (queue full)")
            }
        }
    }

    /**
     * Phase 2 Task 2: SPS Parser
     *
     * Bit-level reader for HEVC RBSP data
     */
    private class BitReader(private val data: ByteArray) {
        private var bytePos = 0
        private var bitPos = 0

        fun readBits(n: Int): Int {
            var result = 0
            repeat(n) {
                if (bytePos >= data.size) return result

                val bit = (data[bytePos].toInt() shr (7 - bitPos)) and 1
                result = (result shl 1) or bit

                bitPos++
                if (bitPos == 8) {
                    bitPos = 0
                    bytePos++
                }
            }
            return result
        }

        fun readUE(): Int {
            // Read exponential-Golomb coded unsigned integer
            var leadingZeros = 0
            while (readBits(1) == 0 && leadingZeros < 32) {
                leadingZeros++
            }
            if (leadingZeros == 0) return 0
            if (leadingZeros >= 32) return 0

            val value = readBits(leadingZeros)
            return (1 shl leadingZeros) - 1 + value
        }

        fun readSE(): Int {
            // Read exponential-Golomb coded signed integer
            val ue = readUE()
            return if (ue and 1 == 1) (ue + 1) / 2 else -(ue / 2)
        }

        fun skipBits(n: Int) {
            readBits(n)
        }
    }

    /**
     * Parse HEVC SPS NAL unit to extract resolution
     * Returns Pair(width, height) or null if parsing fails
     */
    private fun parseSPS(spsData: ByteArray): Pair<Int, Int>? {
        try {
            // Remove emulation prevention bytes (00 00 03 -> 00 00)
            val rbsp = removeEmulationPrevention(spsData)

            // Skip NAL header (2 bytes)
            if (rbsp.size < 2) return null
            val reader = BitReader(rbsp.sliceArray(2 until rbsp.size))

            // sps_video_parameter_set_id (4 bits)
            reader.skipBits(4)

            // sps_max_sub_layers_minus1 (3 bits)
            val maxSubLayers = reader.readBits(3) + 1

            // sps_temporal_id_nesting_flag (1 bit)
            reader.skipBits(1)

            // profile_tier_level()
            parseProfileTierLevel(reader, maxSubLayers)

            // sps_seq_parameter_set_id
            reader.readUE()

            // chroma_format_idc
            val chromaFormat = reader.readUE()
            if (chromaFormat == 3) {
                reader.skipBits(1) // separate_colour_plane_flag
            }

            // pic_width_in_luma_samples
            val width = reader.readUE()

            // pic_height_in_luma_samples
            val height = reader.readUE()

            // conformance_window_flag
            val conformanceFlag = reader.readBits(1)

            var finalWidth = width
            var finalHeight = height

            if (conformanceFlag == 1) {
                val leftOffset = reader.readUE()
                val rightOffset = reader.readUE()
                val topOffset = reader.readUE()
                val bottomOffset = reader.readUE()

                // Apply conformance window cropping
                // SubWidthC and SubHeightC depend on chroma format
                val subWidthC = if (chromaFormat == 1 || chromaFormat == 2) 2 else 1
                val subHeightC = if (chromaFormat == 1) 2 else 1

                finalWidth = width - (leftOffset + rightOffset) * subWidthC
                finalHeight = height - (topOffset + bottomOffset) * subHeightC

                Log.i(TAG, "    SPS conformance window: left=$leftOffset, right=$rightOffset, top=$topOffset, bottom=$bottomOffset")
            }

            return Pair(finalWidth, finalHeight)

        } catch (e: Exception) {
            Log.e(TAG, "    SPS parsing failed: ${e.message}")
            return null
        }
    }

    /**
     * Remove HEVC emulation prevention bytes
     * 00 00 03 -> 00 00
     */
    private fun removeEmulationPrevention(data: ByteArray): ByteArray {
        val result = mutableListOf<Byte>()
        var i = 0
        while (i < data.size) {
            if (i + 2 < data.size &&
                data[i] == 0.toByte() &&
                data[i + 1] == 0.toByte() &&
                data[i + 2] == 3.toByte()) {
                result.add(0)
                result.add(0)
                i += 3  // Skip the 0x03
            } else {
                result.add(data[i])
                i++
            }
        }
        return result.toByteArray()
    }

    /**
     * Parse profile_tier_level structure
     */
    private fun parseProfileTierLevel(reader: BitReader, maxSubLayers: Int) {
        // general_profile_space (2 bits)
        reader.skipBits(2)
        // general_tier_flag (1 bit)
        reader.skipBits(1)
        // general_profile_idc (5 bits)
        reader.skipBits(5)
        // general_profile_compatibility_flag (32 bits)
        reader.skipBits(32)
        // general_progressive_source_flag (1 bit)
        reader.skipBits(1)
        // general_interlaced_source_flag (1 bit)
        reader.skipBits(1)
        // general_non_packed_constraint_flag (1 bit)
        reader.skipBits(1)
        // general_frame_only_constraint_flag (1 bit)
        reader.skipBits(1)
        // reserved 44 bits
        reader.skipBits(44)
        // general_level_idc (8 bits)
        reader.skipBits(8)

        // sub_layer flags and levels
        val subLayerProfilePresent = BooleanArray(maxSubLayers)
        val subLayerLevelPresent = BooleanArray(maxSubLayers)

        for (i in 0 until maxSubLayers - 1) {
            subLayerProfilePresent[i] = reader.readBits(1) == 1
            subLayerLevelPresent[i] = reader.readBits(1) == 1
        }

        if (maxSubLayers > 1) {
            for (i in maxSubLayers - 1 until 8) {
                reader.skipBits(2) // reserved_zero_2bits
            }
        }

        for (i in 0 until maxSubLayers - 1) {
            if (subLayerProfilePresent[i]) {
                reader.skipBits(88) // Profile info
            }
            if (subLayerLevelPresent[i]) {
                reader.skipBits(8) // sub_layer_level_idc
            }
        }
    }

    /**
     * Phase 2 Task 1: Parse NAL units and log detailed information
     * Called only for first 10 frames for validation
     */
    private fun parseAndLogNALUnits(data: ByteArray, frameNum: Int, frame: VideoFrame): List<NALUnit> {
        Log.i(TAG, "")
        Log.i(TAG, "=== PHASE 2 TASK 1: NAL UNIT ANALYSIS ===")
        Log.i(TAG, "Frame #$frameNum")
        Log.i(TAG, "  Total size: ${data.size} bytes")
        Log.i(TAG, "  SDK isCompressed: ${frame.isCompressed}")
        Log.i(TAG, "  SDK isCodecConfig: ${frame.isCodecConfig}")
        Log.i(TAG, "  SDK width: ${frame.width}")
        Log.i(TAG, "  SDK height: ${frame.height}")
        Log.i(TAG, "  SDK presentationTimeUs: ${frame.presentationTimeUs}")

        // Parse all NAL units
        val nalUnits = parseAnnexBNALUnits(data)

        Log.i(TAG, "  NAL units found: ${nalUnits.size}")
        Log.i(TAG, "")

        // Log each NAL unit
        nalUnits.forEachIndexed { index, nal ->
            Log.i(TAG, "  NAL #${index + 1}:")
            Log.i(TAG, "    Type: ${nal.type} (${nal.typeName})")
            Log.i(TAG, "    Offset: ${nal.offset}")
            Log.i(TAG, "    Size: ${nal.size} bytes (header=${nal.headerSize}, payload=${nal.payloadSize})")
            Log.i(TAG, "    Valid: ${nal.isValid}")
            Log.i(TAG, "    Truncated: ${nal.isTruncated}")

            // Hexdump first 32 bytes of NAL payload
            if (nal.isValid && !nal.isTruncated) {
                val payloadStart = nal.offset + nal.headerSize
                val dumpSize = minOf(32, nal.payloadSize)
                val hexBytes = data.sliceArray(payloadStart until payloadStart + dumpSize)
                    .joinToString(" ") { "%02x".format(it) }
                Log.i(TAG, "    First $dumpSize bytes: $hexBytes")
            }

            // Validate HEVC header structure
            if (nal.isValid) {
                val payloadStart = nal.offset + nal.headerSize
                if (payloadStart + 2 <= data.size) {
                    val byte1 = data[payloadStart].toInt() and 0xFF
                    val byte2 = data[payloadStart + 1].toInt() and 0xFF

                    val forbiddenZero = (byte1 shr 7) and 0x01
                    val nalTypeCheck = (byte1 ushr 1) and 0x3F
                    val layerId = ((byte1 and 0x01) shl 5) or ((byte2 shr 3) and 0x1F)
                    val temporalId = byte2 and 0x07

                    if (forbiddenZero != 0) {
                        Log.w(TAG, "    WARNING: forbidden_zero_bit is not 0!")
                    }
                    if (nalTypeCheck != nal.type) {
                        Log.w(TAG, "    WARNING: NAL type mismatch! Parsed=${nal.type}, Header=$nalTypeCheck")
                    }

                    Log.i(TAG, "    HEVC header: layerId=$layerId, temporalId=$temporalId")
                }
            }
            Log.i(TAG, "")
        }

        // Validation summary
        val vpsCount = nalUnits.count { it.type == 32 }
        val spsCount = nalUnits.count { it.type == 33 }
        val ppsCount = nalUnits.count { it.type == 34 }
        val idrCount = nalUnits.count { it.type in 19..20 }
        val craCount = nalUnits.count { it.type == 21 }
        val malformedCount = nalUnits.count { !it.isValid }

        Log.i(TAG, "  === NAL UNIT SUMMARY ===")
        Log.i(TAG, "  VPS (32): $vpsCount")
        Log.i(TAG, "  SPS (33): $spsCount")
        Log.i(TAG, "  PPS (34): $ppsCount")
        Log.i(TAG, "  IDR (19-20): $idrCount")
        Log.i(TAG, "  CRA (21): $craCount")
        Log.i(TAG, "  Malformed/Truncated: $malformedCount")

        if (vpsCount > 0 || spsCount > 0 || ppsCount > 0) {
            Log.i(TAG, "  ✅ CODEC CONFIG PRESENT")
        } else {
            Log.i(TAG, "  ❌ NO CODEC CONFIG (VPS/SPS/PPS missing)")
        }

        // PHASE 2 TASK 2: Parse SPS to extract resolution
        nalUnits.firstOrNull { it.type == 33 }?.let { sps ->
            Log.i(TAG, "")
            Log.i(TAG, "  === PHASE 2 TASK 2: SPS RESOLUTION PARSING ===")

            val spsStart = sps.offset + sps.headerSize
            val spsEnd = sps.offset + sps.size
            val spsPayload = data.sliceArray(spsStart until spsEnd)

            // Add back NAL header for parser
            val spsWithHeader = ByteArray(2 + spsPayload.size)
            val headerPos = sps.offset + sps.headerSize - 2
            spsWithHeader[0] = data[headerPos]
            spsWithHeader[1] = data[headerPos + 1]
            System.arraycopy(spsPayload, 0, spsWithHeader, 2, spsPayload.size)

            val resolution = parseSPS(spsWithHeader)
            if (resolution != null) {
                val (width, height) = resolution
                Log.i(TAG, "  ✅ SPS PARSED SUCCESSFULLY")
                Log.i(TAG, "  Extracted resolution: ${width}x${height}")
                Log.i(TAG, "  SDK reported resolution: ${frame.width}x${frame.height}")

                if (width == frame.width && height == frame.height) {
                    Log.i(TAG, "  ✅ RESOLUTION MATCH!")
                } else {
                    Log.w(TAG, "  ⚠️  RESOLUTION MISMATCH!")
                    Log.w(TAG, "     SPS: ${width}x${height}")
                    Log.w(TAG, "     SDK: ${frame.width}x${frame.height}")
                }

                // Verify expected resolution for MEDIUM quality
                if (width == 504 && height == 896) {
                    Log.i(TAG, "  ✅ MEDIUM QUALITY CONFIRMED (504x896)")
                } else if (width == 720 && height == 1280) {
                    Log.i(TAG, "  ✅ HIGH QUALITY DETECTED (720x1280)")
                } else {
                    Log.w(TAG, "  ⚠️  UNEXPECTED RESOLUTION: ${width}x${height}")
                }
            } else {
                Log.e(TAG, "  ❌ SPS PARSING FAILED")
                Log.e(TAG, "  Could not extract resolution from SPS")
            }
            Log.i(TAG, "  === END SPS PARSING ===")
        }

        Log.i(TAG, "=== END NAL ANALYSIS ===")
        Log.i(TAG, "")

        return nalUnits
    }

    fun stop() {
        // Phase 2 Task 3: Close capture file and log statistics
        captureOutputStream?.let { out ->
            try {
                out.flush()
                out.close()

                Log.i(TAG, "")
                Log.i(TAG, "=== PHASE 2 TASK 3: CAPTURE COMPLETE ===")
                Log.i(TAG, "File: ${captureFile?.absolutePath}")
                Log.i(TAG, "Frames written: $captureFramesWritten")
                val mb = captureBytesWritten / (1024.0 * 1024.0)
                Log.i(TAG, "Bytes written: $captureBytesWritten (${String.format("%.2f", mb)} MB)")
                Log.i(TAG, "Codec config frames: $captureCodecConfigCount")
                Log.i(TAG, "Keyframes: $captureKeyframeCount")
                Log.i(TAG, "")
                Log.i(TAG, "Verification commands:")
                Log.i(TAG, "  adb pull ${captureFile?.absolutePath} .")
                Log.i(TAG, "  ffprobe -v error -show_streams video_capture.h265")
                Log.i(TAG, "  ffmpeg -i video_capture.h265 -f null -")
                Log.i(TAG, "=== END CAPTURE ===")
                Log.i(TAG, "")
            } catch (e: Exception) {
                Log.e(TAG, "Error closing capture file", e)
            }
            captureOutputStream = null
        }

        // Phase 2 Task 4: Close manifest writer
        manifestWriter?.let { writer ->
            try {
                writer.close()
                Log.i(TAG, "")
                Log.i(TAG, "=== PHASE 2 TASK 4: ANDROID MANIFEST COMPLETE ===")
                Log.i(TAG, "Total frames logged: $sequenceNumber")
                Log.i(TAG, "Manifest closed successfully")
                Log.i(TAG, "")
            } catch (e: Exception) {
                Log.e(TAG, "Error closing manifest", e)
            }
            manifestWriter = null
        }

        frameChannel.close()
        Log.i(TAG, "=== VIDEO PIPELINE SUMMARY ===")
        Log.i(TAG, "Total frames processed: $frameCounter")
        Log.i(TAG, "Codec config frames: $codecConfigFrames")
        Log.i(TAG, "Keyframes detected: $keyframeCount")
        Log.i(TAG, "Frames dropped: $droppedFrames")
        Log.i(TAG, "Video pipeline stopped")
    }
}
