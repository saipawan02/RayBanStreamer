#!/usr/bin/env bash
# TECH-03 section 4 step 2: publish a glasses-shaped test stream (HEVC 504x896@24 + AAC mono 48k, MPEG-TS over SRT).
# Usage: tools/synthetic_publisher.sh <server-tailscale-ip> <passphrase>
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: $0 <server-tailscale-ip> <passphrase>" >&2; exit 2; }
exec ffmpeg -re -f lavfi -i testsrc2=size=504x896:rate=24 -f lavfi -i sine=frequency=440:sample_rate=48000 \
  -c:v libx265 -b:v 2M -g 24 -c:a aac -b:a 64k -ac 1 -f mpegts \
  "srt://$1:8890?streamid=publish:glasses&passphrase=$2&pkt_size=1316"
