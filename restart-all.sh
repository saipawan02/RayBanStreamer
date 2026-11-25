#!/bin/bash
# Restart all streaming services
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "Restarting all Ray-Ban streaming services..."
echo ""

# Stop all services
./stop-all.sh

# Wait a moment for clean shutdown
sleep 2

# Start all services
./start-all.sh
