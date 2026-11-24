#!/bin/bash
# Check status of all streaming services

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=================================================="
echo "  Service Status Check"
echo "=================================================="
echo ""

# MediaMTX
echo -n "MediaMTX (Docker):       "
if docker ps | grep -q mediamtx; then
    echo -e "${GREEN}✓ Running${NC}"
else
    echo -e "${RED}✗ Not running${NC}"
fi

# Telemetry Server
echo -n "Telemetry Server:        "
if pgrep -f "uvicorn app.main:app.*8000" > /dev/null; then
    PID=$(pgrep -f "uvicorn app.main:app.*8000")
    echo -e "${GREEN}✓ Running (PID: $PID)${NC}"
    # Test health endpoint
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        HEALTH=$(curl -s http://localhost:8000/health)
        echo "  Health: $HEALTH"
    fi
else
    echo -e "${RED}✗ Not running${NC}"
fi

# Relay Service
echo -n "Relay Service:           "
if pgrep -f "uvicorn app:app.*8001" > /dev/null; then
    PID=$(pgrep -f "uvicorn app:app.*8001")
    echo -e "${GREEN}✓ Running (PID: $PID)${NC}"
    # Test health endpoint
    if curl -s http://localhost:8001/health > /dev/null 2>&1; then
        HEALTH=$(curl -s http://localhost:8001/health)
        echo "  Health: $HEALTH"
    fi
else
    echo -e "${RED}✗ Not running${NC}"
fi

echo ""
echo "=================================================="
echo "  Network Ports"
echo "=================================================="
echo ""

# Check ports
for port in 8000 8001 8554 8890; do
    echo -n "Port $port: "
    if ss -ln | grep -q ":$port "; then
        echo -e "${GREEN}✓ Listening${NC}"
    else
        echo -e "${RED}✗ Not listening${NC}"
    fi
done

echo ""
echo "=================================================="
echo "  Data Directories"
echo "=================================================="
echo ""

# Check data directories
DATA_DIR="/tmp/glasses/data"
REC_DIR="/tmp/glasses/recordings/glasses"

echo "Session data: $DATA_DIR"
if [ -d "$DATA_DIR" ]; then
    SESSION_COUNT=$(find "$DATA_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
    echo "  $SESSION_COUNT session(s) found"
    if [ $SESSION_COUNT -gt 0 ]; then
        echo "  Latest sessions:"
        ls -t "$DATA_DIR" | head -3 | while read session; do
            echo "    - $session"
        done
    fi
else
    echo -e "  ${YELLOW}Directory does not exist${NC}"
fi

echo ""
echo "Recordings: $REC_DIR"
if [ -d "$REC_DIR" ]; then
    REC_COUNT=$(find "$REC_DIR" -name "*.mp4" 2>/dev/null | wc -l)
    echo "  $REC_COUNT recording(s) found"
    if [ $REC_COUNT -gt 0 ]; then
        echo "  Latest recordings:"
        ls -t "$REC_DIR"/*.mp4 2>/dev/null | head -3 | while read file; do
            SIZE=$(du -h "$file" | cut -f1)
            echo "    - $(basename "$file") ($SIZE)"
        done
    fi
else
    echo -e "  ${YELLOW}Directory does not exist${NC}"
fi

echo ""
echo "=================================================="
echo "  Quick Actions"
echo "=================================================="
echo ""
echo "View stream:        vlc rtsp://localhost:8554/glasses"
echo "Test pattern:       ./tools/synthetic_publisher.sh localhost test-passphrase"
echo "Telemetry logs:     tail -f /tmp/telemetry.log"
echo "Relay logs:         tail -f /tmp/relay.log"
echo "MediaMTX logs:      docker logs -f mediamtx"
echo ""
