#!/bin/bash
# Stop all streaming services
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Stopping all services..."
echo ""

# Stop MediaMTX
echo "Stopping MediaMTX..."
cd "$SCRIPT_DIR/server/mediamtx"
docker compose down
echo "✓ MediaMTX stopped"
echo ""

# Stop Telemetry Server
echo "Stopping Telemetry Server..."
if [ -f /tmp/telemetry.pid ]; then
    PID=$(cat /tmp/telemetry.pid)
    if ps -p $PID > /dev/null 2>&1; then
        kill $PID
        echo "✓ Telemetry server stopped (PID: $PID)"
    fi
    rm /tmp/telemetry.pid
else
    # Fallback: kill by name
    pkill -f "uvicorn app.main:app.*8000" && echo "✓ Telemetry server stopped" || echo "  (not running)"
fi
echo ""

# Stop Relay Service
echo "Stopping Relay Service..."
if [ -f /tmp/relay.pid ]; then
    PID=$(cat /tmp/relay.pid)
    if ps -p $PID > /dev/null 2>&1; then
        kill $PID
        echo "✓ Relay service stopped (PID: $PID)"
    fi
    rm /tmp/relay.pid
else
    # Fallback: kill by name
    pkill -f "uvicorn app:app.*8001" && echo "✓ Relay service stopped" || echo "  (not running)"
fi
echo ""

# Stop VLC
echo "Stopping VLC Player..."
pkill -f "vlc.*rtsp://localhost:8554/glasses" 2>/dev/null && echo "✓ VLC stopped" || echo "  (not running)"
echo ""

# Stop any synthetic publishers
pkill -f "synthetic_publisher" 2>/dev/null && echo "✓ Synthetic publisher stopped" || true
pkill -f "ffmpeg.*testsrc2" 2>/dev/null && echo "✓ Test streams stopped" || true

echo ""
echo "All services stopped."
