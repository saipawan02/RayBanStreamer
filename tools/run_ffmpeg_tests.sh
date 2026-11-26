#!/bin/bash
#
# Phase 2 Task 6: Run all FFmpeg configuration tests
#
# This script runs each test mode in sequence and collects results.
#

set -e

PROJECT_ROOT="/home/sai-pawan/Documents/RayBanStreamer"
RELAY_DIR="$PROJECT_ROOT/server/relay"
RESULTS_DIR="/tmp/task6_results"

# Create results directory
mkdir -p "$RESULTS_DIR"

echo "======================================================================"
echo "PHASE 2 TASK 6: Dual-Input FFmpeg Test Matrix"
echo "======================================================================"
echo ""
echo "This will run 4 different FFmpeg configurations:"
echo "  1. VIDEO_ONLY_NULL         - Baseline (known working)"
echo "  2. DUAL_NULL               - Dual-input, null output"
echo "  3. ENHANCED_PROBE_DUAL_FLV - Enhanced probe + FLV output"
echo "  4. DUAL_FLV_ORIGINAL       - Original failing command"
echo ""
echo "Results will be saved to: $RESULTS_DIR"
echo "======================================================================"
echo ""

# Test configurations
TESTS=(
    "VIDEO_ONLY_NULL"
    "DUAL_NULL"
    "ENHANCED_PROBE_DUAL_FLV"
    "DUAL_FLV_ORIGINAL"
)

run_test() {
    local test_mode=$1
    local test_num=$2

    echo ""
    echo "======================================================================"
    echo "TEST $test_num: $test_mode"
    echo "======================================================================"

    # Update test mode in script
    sed -i "s/^TEST_MODE = .*/TEST_MODE = \"$test_mode\"/" "$RELAY_DIR/app_test_matrix.py"

    # Kill any existing relay process
    pkill -f "app_test_matrix.py" || true
    sleep 2

    # Start relay service
    echo "Starting relay service with test mode: $test_mode"
    cd "$RELAY_DIR"
    source venv/bin/activate
    nohup python app_test_matrix.py > "$RESULTS_DIR/${test_mode}_relay.log" 2>&1 &
    RELAY_PID=$!

    # Wait for startup
    sleep 3

    # Check if relay started
    if ! curl -s http://localhost:8001/health > /dev/null; then
        echo "ERROR: Relay service failed to start"
        cat "$RESULTS_DIR/${test_mode}_relay.log"
        exit 1
    fi

    echo "Relay service started (PID $RELAY_PID)"
    echo ""
    echo "========================================"
    echo "READY FOR STREAMING"
    echo "========================================"
    echo "1. On your phone: START SESSION"
    echo "2. On your phone: START STREAMING"
    echo "3. Stream for ~20 seconds"
    echo "4. On your phone: STOP STREAMING"
    echo ""
    echo "Press ENTER when streaming is complete..."
    read

    # Stop relay
    echo "Stopping relay service..."
    kill $RELAY_PID || true
    sleep 2

    # Find the FFmpeg log
    FFMPEG_LOG=$(ls -t /tmp/ffmpeg_*_${test_mode}.log 2>/dev/null | head -1)

    if [ -n "$FFMPEG_LOG" ]; then
        echo "Analyzing FFmpeg output: $FFMPEG_LOG"

        # Check for key indicators
        echo "" > "$RESULTS_DIR/${test_mode}_analysis.txt"
        echo "TEST: $test_mode" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        echo "======================================" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        echo "" >> "$RESULTS_DIR/${test_mode}_analysis.txt"

        # Check for stream detection
        if grep -q "Input #0" "$FFMPEG_LOG"; then
            echo "✅ FFmpeg opened input" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
            grep "Input #0" "$FFMPEG_LOG" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        else
            echo "❌ FFmpeg did not open input" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        fi
        echo "" >> "$RESULTS_DIR/${test_mode}_analysis.txt"

        # Check for stream parameters
        if grep -q "Stream #0:0.*Video.*hevc" "$FFMPEG_LOG"; then
            echo "✅ Video stream detected" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
            grep "Stream #0:0.*Video" "$FFMPEG_LOG" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        else
            echo "❌ Video stream not detected" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        fi
        echo "" >> "$RESULTS_DIR/${test_mode}_analysis.txt"

        # Check for resolution
        if grep -q "504x896" "$FFMPEG_LOG"; then
            echo "✅ Resolution detected: 504x896" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        elif grep -q "unspecified size" "$FFMPEG_LOG"; then
            echo "❌ Unspecified size error" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        else
            echo "⚠️  Resolution status unclear" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        fi
        echo "" >> "$RESULTS_DIR/${test_mode}_analysis.txt"

        # Check for errors
        if grep -qi "error\|fail\|could not" "$FFMPEG_LOG"; then
            echo "⚠️  Errors found:" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
            grep -i "error\|fail\|could not" "$FFMPEG_LOG" | head -10 >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        else
            echo "✅ No errors found" >> "$RESULTS_DIR/${test_mode}_analysis.txt"
        fi

        # Copy full log
        cp "$FFMPEG_LOG" "$RESULTS_DIR/${test_mode}_ffmpeg_full.log"

        # Show analysis
        cat "$RESULTS_DIR/${test_mode}_analysis.txt"
    else
        echo "WARNING: FFmpeg log not found for $test_mode"
    fi

    echo ""
    echo "Test $test_num complete: $test_mode"
    echo "======================================================================"
    sleep 2
}

# Run all tests
for i in "${!TESTS[@]}"; do
    run_test "${TESTS[$i]}" "$((i+1))"
done

# Generate comparison report
echo ""
echo "======================================================================"
echo "GENERATING COMPARISON REPORT"
echo "======================================================================"

cat > "$RESULTS_DIR/COMPARISON_REPORT.txt" << 'EOF'
======================================================================
PHASE 2 TASK 6: FFmpeg Configuration Test Results
======================================================================

EOF

for test in "${TESTS[@]}"; do
    if [ -f "$RESULTS_DIR/${test}_analysis.txt" ]; then
        cat "$RESULTS_DIR/${test}_analysis.txt" >> "$RESULTS_DIR/COMPARISON_REPORT.txt"
        echo "" >> "$RESULTS_DIR/COMPARISON_REPORT.txt"
        echo "----------------------------------------------------------------------" >> "$RESULTS_DIR/COMPARISON_REPORT.txt"
        echo "" >> "$RESULTS_DIR/COMPARISON_REPORT.txt"
    fi
done

echo "======================================================================" >> "$RESULTS_DIR/COMPARISON_REPORT.txt"
echo "END OF REPORT" >> "$RESULTS_DIR/COMPARISON_REPORT.txt"
echo "======================================================================" >> "$RESULTS_DIR/COMPARISON_REPORT.txt"

echo ""
echo "======================================================================"
echo "ALL TESTS COMPLETE"
echo "======================================================================"
echo ""
echo "Results saved to: $RESULTS_DIR"
echo ""
echo "Files generated:"
ls -lh "$RESULTS_DIR"
echo ""
echo "View comparison report:"
echo "  cat $RESULTS_DIR/COMPARISON_REPORT.txt"
echo ""
echo "======================================================================"
