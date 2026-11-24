#!/bin/bash
# Start the media relay service
cd "$(dirname "$0")"

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "Starting media relay on port 8001"
echo "Receives video over WebSocket and relays to MediaMTX via FFmpeg"
echo ""

python3 -m uvicorn app:app --host 0.0.0.0 --port 8001 --log-level info
