# TECH-03: Server Ingest (Tailscale and MediaMTX)

Companion to `SOW.md` section 7 and `TECH-01-architecture-and-contracts.md`. Evidence tags **[V] [R] [A]** are defined in TECH-01.

Goal: the GPU server accepts the phone's SRT stream over Tailscale, records video and audio to disk **without re-encoding**, and re-serves the live stream on localhost over RTSP for GPU readers.

## 1. Prerequisites on the server

- Linux with systemd, an NVIDIA driver and CUDA working (`nvidia-smi` prints the GPU), Docker with the Compose plugin.
- Disk: raw HEVC at 2 to 4 Mbps is about 0.9 to 1.8 GB per hour per stream (video bitrate × 3600 / 8). Size the recording volume for your retention.
- Time sync: `chrony` or `systemd-timesyncd` running (`timedatectl` shows synchronised). Recording file names and `recv_wall_ms` depend on it.
- Time zone set to UTC (`sudo timedatectl set-timezone UTC`): MediaMTX names recording files with the server's local time, and the readers parse those names as UTC (TECH-04 section 8, TECH-05 section 3).
- A data root, for example `/srv/glasses`, owned by the service user.

```bash
sudo mkdir -p /srv/glasses/recordings /srv/glasses/data /srv/glasses/derived
sudo chown -R "$USER": /srv/glasses
```

## 2. Tailscale

Tailscale gives the phone a stable private address to the server and works behind carrier-grade NAT, so no public IP is needed [A: standard Tailscale behaviour].

1. Install on the server and the phone from Tailscale's official instructions; sign in to the same tailnet.
2. Note the server's tailnet IPv4 (`tailscale ip -4`) and, if MagicDNS is on, its name. Use the **IP** in the app's settings first; switch to the name later.
3. In the Tailscale admin console, confirm the phone and server both show as connected, and consider an ACL that lets only the phone reach ports 8890 and 8000 on the server.
4. Keep Tailscale running on the phone in the background (Android battery settings: unrestricted for the Tailscale app).

Firewall (ufw example; adapt for nftables). Allow only on the tailnet interface and loopback:

```bash
sudo ufw default deny incoming
sudo ufw allow in on tailscale0 to any port 8890 proto udp   # SRT ingest
sudo ufw allow in on tailscale0 to any port 8000 proto tcp   # telemetry (TECH-04)
sudo ufw allow in on lo
sudo ufw enable
```

RTSP (8554) is read on localhost by GPU readers, so it needs no rule. Open it on `tailscale0` only if you want to play the live stream from another machine.

## 3. MediaMTX

### 3.1 Run it

```yaml
# server/mediamtx/docker-compose.yml
services:
  mediamtx:
    image: bluenviron/mediamtx:latest     # pin a specific version tag once working
    container_name: mediamtx
    restart: unless-stopped
    network_mode: host                    # simplest for UDP (SRT) and RTSP
    volumes:
      - ./mediamtx.yml:/mediamtx.yml:ro
      - /srv/glasses/recordings:/recordings
```

### 3.2 Configuration

MediaMTX's recording and path-default keys are documented at https://mediamtx.org/docs/usage/record [V]. In current versions, per-path defaults sit under `pathDefaults:` and specific paths under `paths:`. Older releases accepted these keys at the top level. **Check the MediaMTX version you run** and compare with its bundled `mediamtx.yml`; if a key is rejected at startup, MediaMTX says so in its log.

```yaml
# server/mediamtx/mediamtx.yml  (keys verified against the recording docs; SRT keys [A], confirm in the bundled mediamtx.yml)
logLevel: info

# turn off everything not needed
api: no
metrics: no
rtmp: no
hls: no
webrtc: no

rtsp: yes
rtspAddress: :8554

srt: yes
srtAddress: :8890

pathDefaults:
  record: yes
  recordPath: /recordings/%path/%Y-%m-%d_%H-%M-%S-%f      # extension is added automatically [V]
  recordFormat: fmp4                                       # fmp4 or mpegts [V]
  recordPartDuration: 1s                                   # also the recovery point objective [V]
  recordMaxPartSize: 50M                                   # [V]
  recordSegmentDuration: 15m                               # minimum segment length [V]
  recordDeleteAfter: 0s                                    # 0s disables automatic deletion [V]; set e.g. 168h to expire

paths:
  glasses:
    source: publisher
    srtPublishPassphrase: "<random 10-79 character secret>"   # [A] key name; confirm
```

Notes:

- fMP4 recording supports H265 video and AAC audio [V], which is what the app sends.
- `recordDeleteAfter: 0s` is set deliberately: the example in the docs uses a deletion period, so check that your running config does not delete data unexpectedly [V].
- `recordPartDuration: 1s` bounds loss on a server crash to about one second of video [V].
- `%path` becomes `glasses`, so segments land in `/recordings/glasses/`, matching TECH-01 section 6.1.
- If you need RTMP for quick tests, set `rtmp: yes` temporarily and open port 1935 on `tailscale0` only.
- RTSP over UDP also uses UDP 8000 and 8001 in MediaMTX defaults [A]; telemetry uses TCP 8000. Different protocols, no clash. If you prefer, set `rtspTransports: [tcp]` to avoid UDP RTP entirely.

### 3.3 Publisher URL (what the phone uses)

```text
srt://<server-tailscale-ip>:8890?streamid=publish:glasses&passphrase=<secret>&pkt_size=1316
```

MediaMTX reads the SRT stream id as `publish:<path>` (and `read:<path>` for readers); extra user and password fields exist if you enable authentication [A: confirm syntax in the MediaMTX docs].

## 4. Verification (do this before any phone code)

1. Start: `docker compose up -d` in `server/mediamtx/`; `docker logs -f mediamtx` shows SRT and RTSP listeners.
2. Publish a synthetic stream that matches the glasses format, from any tailnet machine (HEVC, AAC, portrait, MPEG-TS over SRT):

```bash
ffmpeg -re -f lavfi -i testsrc2=size=504x896:rate=24 -f lavfi -i sine=frequency=440:sample_rate=48000 \
  -c:v libx265 -b:v 2M -g 24 -c:a aac -b:a 64k -ac 1 -f mpegts \
  "srt://<server-tailscale-ip>:8890?streamid=publish:glasses&passphrase=<secret>&pkt_size=1316"
```

3. Live read on the server: `ffplay -rtsp_transport tcp rtsp://127.0.0.1:8554/glasses` plays picture and a tone.
4. Recording: after at least 15 minutes (or stop the publisher to close the segment), check `ls -l /srv/glasses/recordings/glasses/` shows an `.mp4`; then:

```bash
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,sample_rate,channels -of default=nw=1 <file>
```

Expect `hevc` 504x896 and `aac` 48000 mono.

5. Fault tests: stop the publisher mid-stream (segment closes and is playable); restart the container (stream resumes when the publisher reconnects); `kill -9` the container and confirm at most about 1 s is lost.

## 5. Operations

- **Retention.** Either set `recordDeleteAfter` (for example `168h`) or run a nightly job. Never delete a segment whose session is still referenced as open in `data/<session_id>/session.json`.

```bash
# example nightly job: remove recordings older than 14 days
find /srv/glasses/recordings -name '*.mp4' -mtime +14 -delete
```

- **Disk watch.** Alert when free space falls below 15%. Recording failures appear in MediaMTX logs.
- **Version pin.** After the first successful run, pin the image tag and record it in `DECISIONS.md`.
- **Backups.** Recordings are raw and not reproducible. Decide per session whether to copy `data/<session_id>/` and its segments elsewhere.
- **Logs.** `docker logs mediamtx --since 1h`; keep at `info` normally, `debug` when diagnosing SRT.

## 6. Troubleshooting

| Symptom | Likely cause | Check |
| --- | --- | --- |
| Phone cannot connect to SRT | Firewall, Tailscale down, wrong port | `tailscale status` on both; `sudo ufw status`; `ss -ulpn \| grep 8890`; try the ffmpeg publisher from another tailnet machine |
| Publisher connects then drops | Wrong passphrase or stream id | MediaMTX log shows the rejection reason |
| Recording file empty or not created | Path not writable by the container, `record` not applied to the path | `docker exec mediamtx ls /recordings`; confirm `pathDefaults.record: yes` |
| File plays without audio | Phone not sending audio, or audio codec unsupported by fMP4 | `ffprobe` on the live RTSP; AAC-LC and Opus are supported in fMP4 [V] |
| Stuttering live reads | Jitter or UDP loss on RTSP | Read with `-rtsp_transport tcp`; raise reader latency (TECH-05) |
| High end-to-end delay | SRT receiver latency or reader jitter buffer | Lower SRT latency gradually; measure with the clock test in TECH-05 |
| Segments split unexpectedly | Publisher reconnects start new segments | Expected; `load_session` joins segments by time |

## 7. Acceptance for this workstream

- Smoke test (section 4) records a playable file with HEVC and AAC; a second machine plays the live RTSP stream.
- Container survives a reboot and restarts by itself.
- `kill -9` loses no more than about one `recordPartDuration` of media.
- Firewall rules show no exposure on the public interface (`nmap` from outside finds neither 8890 nor 8000).

## 8. References

- MediaMTX recording documentation: https://mediamtx.org/docs/usage/record [V]
- MediaMTX repository (bundled `mediamtx.yml`, README): https://github.com/bluenviron/mediamtx
- SOW section 7 and TECH-01 sections 3, 6.
