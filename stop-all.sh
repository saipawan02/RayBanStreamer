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
if [ -f /tmp/task8_relay.pid ]; then
    PID=$(cat /tmp/task8_relay.pid)
    if ps -p $PID > /dev/null 2>&1; then
        kill $PID
        echo "✓ Relay service stopped (PID: $PID)"
    fi
    rm /tmp/task8_relay.pid
else
    # Fallback: kill by name (both old and new versions)
    pkill -f "python.*app_task8.py" && echo "✓ Relay service (app_task8.py) stopped" || true
    pkill -f "uvicorn app:app.*8001" && echo "✓ Relay service (app.py) stopped" || echo "  (not running)"
fi
echo ""

# Stop FFplay
echo "Stopping FFplay viewer..."
if [ -f /tmp/ffplay.pid ]; then
    PID=$(cat /tmp/ffplay.pid)
    if ps -p $PID > /dev/null 2>&1; then
        kill $PID
        echo "✓ FFplay stopped (PID: $PID)"
    fi
    rm /tmp/ffplay.pid
else
    pkill -f "ffplay.*rtsp://localhost:8554/glasses" 2>/dev/null && echo "✓ FFplay stopped" || echo "  (not running)"
fi

# Also stop VLC if someone started it manually
pkill -f "vlc.*rtsp://localhost:8554/glasses" 2>/dev/null && echo "✓ VLC stopped" || true
echo ""

# Stop any synthetic publishers
pkill -f "synthetic_publisher" 2>/dev/null && echo "✓ Synthetic publisher stopped" || true
pkill -f "ffmpeg.*testsrc2" 2>/dev/null && echo "✓ Test streams stopped" || true

echo ""
echo "All services stopped."
