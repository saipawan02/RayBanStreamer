#!/bin/bash
# Start all streaming services for Ray-Ban Meta glasses
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "  Ray-Ban Meta Glasses Streaming - Start All"
echo "=================================================="
echo ""

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker not found${NC}"
    echo "  Install: sudo apt install docker.io docker-compose"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python3 not found${NC}"
    exit 1
fi

if ! command -v ffmpeg &> /dev/null; then
    echo -e "${YELLOW}⚠ FFmpeg not found (needed for video relay)${NC}"
    echo "  Install: sudo apt install ffmpeg"
fi

echo -e "${GREEN}✓ Prerequisites OK${NC}"
echo ""

# 1. Start MediaMTX
echo "=================================================="
echo "Starting MediaMTX (streaming server)..."
echo "=================================================="
cd "$SCRIPT_DIR/server/mediamtx"

if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠ Creating .env from .env.example${NC}"
    cp .env.example .env
fi

docker compose up -d
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ MediaMTX started${NC}"
    echo "  RTSP: rtsp://localhost:8554/glasses"
    echo "  SRT:  srt://localhost:8890"
else
    echo -e "${RED}✗ Failed to start MediaMTX${NC}"
    exit 1
fi
echo ""

# Wait for MediaMTX to initialize
sleep 2

# 2. Start Telemetry Server
echo "=================================================="
echo "Starting Telemetry Server (IMU data)..."
echo "=================================================="
cd "$SCRIPT_DIR/server/telemetry"

if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -q -e '.[dev]'
else
    source venv/bin/activate
fi

if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠ Creating .env${NC}"
    cat > .env <<EOF
DATA_ROOT=/tmp/glasses
TELEMETRY_TOKEN=test-token-change-me-in-production
TELEMETRY_BIND=0.0.0.0
PART_SECONDS=5
PART_ROWS=1000
LOG_LEVEL=info
EOF
fi

# Load environment variables
set -a
source .env
set +a

# Check if already running
if pgrep -f "uvicorn app.main:app.*8000" > /dev/null; then
    echo -e "${YELLOW}⚠ Telemetry server already running${NC}"
else
    # Start in background with proper log redirection
    nohup uvicorn app.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --ws-max-size 1048576 \
        --log-level info \
        > /tmp/telemetry.log 2>&1 &

    TELEMETRY_PID=$!
    echo $TELEMETRY_PID > /tmp/telemetry.pid

    sleep 2

    # Verify it started
    if ps -p $TELEMETRY_PID > /dev/null; then
        echo -e "${GREEN}✓ Telemetry server started (PID: $TELEMETRY_PID)${NC}"
        echo "  WebSocket: ws://localhost:8000/ingest/<session_id>"
        echo "  Logs: /tmp/telemetry.log"
    else
        echo -e "${RED}✗ Failed to start telemetry server${NC}"
        cat /tmp/telemetry.log
        exit 1
    fi
fi
echo ""

# 3. Start Relay Service
echo "=================================================="
echo "Starting Relay Service (video/audio)..."
echo "=================================================="
cd "$SCRIPT_DIR/server/relay"

if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -q -r requirements.txt
else
    source venv/bin/activate
fi

# Check if already running
if pgrep -f "uvicorn app:app.*8001" > /dev/null; then
    echo -e "${YELLOW}⚠ Relay service already running${NC}"
else
    nohup python3 -m uvicorn app:app \
        --host 0.0.0.0 \
        --port 8001 \
        --log-level info \
        > /tmp/relay.log 2>&1 &

    RELAY_PID=$!
    echo $RELAY_PID > /tmp/relay.pid

    sleep 2

    # Verify it started
    if ps -p $RELAY_PID > /dev/null; then
        echo -e "${GREEN}✓ Relay service started (PID: $RELAY_PID)${NC}"
        echo "  WebSocket: ws://localhost:8001/media/<session_id>"
        echo "  Logs: /tmp/relay.log"
    else
        echo -e "${RED}✗ Failed to start relay service${NC}"
        cat /tmp/relay.log
        exit 1
    fi
fi
echo ""

# 4. Start VLC to view the stream
echo "=================================================="
echo "Starting VLC Player (stream viewer)..."
echo "=================================================="

if ! command -v vlc &> /dev/null; then
    echo -e "${YELLOW}⚠ VLC not found - install with: sudo apt install vlc${NC}"
else
    # Check if VLC is already running with this stream
    if pgrep -f "vlc.*rtsp://localhost:8554/glasses" > /dev/null; then
        echo -e "${YELLOW}⚠ VLC already viewing stream${NC}"
    else
        # Start VLC in background to view the RTSP stream
        vlc rtsp://localhost:8554/glasses > /dev/null 2>&1 &
        VLC_PID=$!
        sleep 1

        if ps -p $VLC_PID > /dev/null; then
            echo -e "${GREEN}✓ VLC started (PID: $VLC_PID)${NC}"
            echo "  Viewing: rtsp://localhost:8554/glasses"
        else
            echo -e "${YELLOW}⚠ VLC may not have started (this is OK if no stream yet)${NC}"
        fi
    fi
fi
echo ""

# Get local IP address
LOCAL_IP=$(hostname -I | awk '{print $1}')

# Summary
echo "=================================================="
echo "  All Services Started Successfully!"
echo "=================================================="
echo ""
echo "Services:"
echo -e "  ${GREEN}✓${NC} MediaMTX       - Port 8554 (RTSP), 8890 (SRT)"
echo -e "  ${GREEN}✓${NC} Telemetry      - Port 8000 (WebSocket)"
echo -e "  ${GREEN}✓${NC} Relay          - Port 8001 (WebSocket)"
echo -e "  ${GREEN}✓${NC} VLC Player     - Viewing RTSP stream"
echo ""
echo "=================================================="
echo "  📱 ANDROID APP CONFIGURATION"
echo "=================================================="
echo ""
echo -e "${YELLOW}Configure these settings in your phone app:${NC}"
echo ""
echo "  Server Host:      ${GREEN}${LOCAL_IP}${NC}"
echo "  WebSocket Token:  ${GREEN}test-token-change-me-in-production${NC}"
echo ""
echo "The app will connect to:"
echo "  • Telemetry (IMU): ws://${LOCAL_IP}:8000"
echo "  • Media (Video):   ws://${LOCAL_IP}:8001"
echo ""
echo "=================================================="
echo ""
echo "Data directories:"
echo "  Sessions:    $SCRIPT_DIR/data/sessions/"
echo "  Recordings:  $SCRIPT_DIR/data/recordings/glasses/"
echo ""
echo "View stream on this machine:"
echo "  vlc rtsp://localhost:8554/glasses"
echo "  ffplay rtsp://localhost:8554/glasses"
echo ""
echo "Check status:"
echo "  ./check-services.sh"
echo ""
echo "Stop all services:"
echo "  ./stop-all.sh"
echo ""
echo "Next steps:"
echo "  1. Install Android app: cd android && ./gradlew installDebug"
echo "  2. Configure app with IP shown above: ${GREEN}${LOCAL_IP}${NC}"
echo "  3. Start streaming from glasses"
echo ""
