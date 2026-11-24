#!/bin/bash
# Start the telemetry receiver service

cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Load environment variables
set -a
source .env
set +a

# Start uvicorn
echo "Starting telemetry receiver on $TELEMETRY_BIND:8000"
echo "WebSocket Token: $TELEMETRY_TOKEN"
echo "Data Root: $DATA_ROOT"
echo ""
echo "Press Ctrl+C to stop"
echo ""

uvicorn app.main:app \
    --host "$TELEMETRY_BIND" \
    --port 8000 \
    --ws-max-size 1048576 \
    --log-level "$LOG_LEVEL"
