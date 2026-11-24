# CLAUDE.md: Ray-Ban Meta to GPU Server Streaming

Personal, non-production project. An **Android** app streams camera video, in-stream microphone audio and IMU motion from Ray-Ban Meta glasses (Meta Wearables Device Access Toolkit, Developer Mode) to a GPU server over Tailscale. The server records raw media, stores IMU data as Parquet, and exposes GPU-decoded frames. Analytics are out of scope; this repo stops at clean, aligned data.

## Current state

Docs live in `refdocs/` (move `DECISIONS.md` and `TESTLOG.md` to the repo root if you prefer; update the layout below if so). Built so far: `server/mediamtx/` (config only, **not run**), `server/telemetry/` (35 passing tests), `gpu/glasses_reader/` (26 passing tests on CPU with PyAV), and `tools/` (`synthetic_imu_client.py`, `synthetic_publisher.sh`, `fake_session.py`, `estimate_offset.py`, `check_session.py`). Written but **never run**: the `ffmpeg` backend, `iter_live` (GStreamer) and any NVDEC/CUDA path. Not built: `/pcm` endpoint, PyNvVideoCodec backend, the alignment plot, `android/`. Spikes S1 to S6 are open. The authoring machine cannot run the phone, glasses, MediaMTX or GPU parts: write code there, and give the user the commands to run on the real machines.

## Read these documents (on demand; do not load them all at once)

| File | Read it when you are |
| --- | --- |
| `refdocs/SOW.md` | Starting any work: scope, constraints, acceptance criteria, build order, risks, privacy rules |
| `refdocs/TECH-01-architecture-and-contracts.md` | Touching any schema, port, event name, folder path, clock, or config. **Always read first** |
| `refdocs/TECH-02-android-app.md` | Working in `android/` |
| `refdocs/TECH-03-server-ingest.md` | Working on Tailscale, MediaMTX, firewall, recording, retention |
| `refdocs/TECH-04-telemetry-receiver.md` | Working in `server/telemetry/` |
| `refdocs/TECH-05-gpu-readers-and-sync.md` | Working in `gpu/` or `tools/estimate_offset.py`, or anything about timestamps and alignment |
| `refdocs/TECH-06-testing-and-operations.md` | Writing tests, running the soak test, writing `TESTLOG.md`, debugging end to end |

## Precedence and evidence

1. User instructions in the current conversation.
2. This file.
3. `TECH-*.md` (they win over `SOW.md` on technical detail: schemas, ports, file layout, protocol).
4. `SOW.md` (scope, acceptance, privacy).
5. The pinned Meta SDK source and sample app **win over every document on SDK API names and behaviour**.

Every technical claim in the TECH files is tagged **[V]** verified in vendor docs, **[R]** reported by staff or community, **[A]** assumption. Never build on an **[A]** without running the spike or test that settles it (TECH-01 section 11). Record every departure or finding in `DECISIONS.md` as a dated line with the evidence.

## Hard rules

- **Never invent Meta SDK class, method or enum names.** Read them from the pinned SDK source, the CameraAccess sample, and the repo's `AGENTS.md` (clone `github.com/facebook/meta-wearables-dat-android`). Do not run installer scripts from the internet without reading them first.
- Developer Mode only: application ID and client token `0`. No Developer Center registration, release channels or store packaging.
- No face, voice or person identification anywhere. Never hide, disable or work around a recording indicator (the glasses' LED, the app's RECORDING banner).
- Never modify or transcode raw recordings. Never rewrite sensor timestamps: store as received; alignment is computed later (TECH-05).
- No secrets in git (SRT passphrase, WebSocket token, Tailscale keys). Use `.env` files and phone settings. Add them to `.gitignore` before the first commit.
- No server port reachable from the public internet. Ports 8890/udp and 8000/tcp are for the tailnet only.
- Do not add analytics models, cloud services or new dependencies beyond those named in the TECH files without asking.

## How to work

1. **One workstream per session**, in the order of `SOW.md` section 13: server first (no phone needed), then GPU readers, then the Android app. Say which workstream and which document you are working from at the start.
2. **Spikes before design lock** for the Android app (TECH-01 section 11, S1 to S6). The compressed-video byte format, the compressed-video flag name, the audio and video timebase, the SRT and MPEG-TS library, delivered IMU rate and pause behaviour are all unknown until measured. Put results in `DECISIONS.md`.
3. **Build in the order given in each TECH document's build-order section**; stop at each "Exit" and show evidence (command output, test result, file played) before continuing.
4. **Test before saying done.** Server and reader code needs pytest tests (TECH-04 section 11, TECH-05 section 10). The Android app needs unit tests plus the mock-device run (TECH-02 section 10). State which acceptance rows (`SOW.md` section 11; TECH-06 section 3) you have actually run, and which you could not run and why.
5. **Ask before departing** from a document: schemas and ports are contracts between components. If a change is needed, propose it, update `TECH-01` and `DECISIONS.md` together, and change all affected components.
6. Prefer small, reviewable commits: one component or one spike per commit. Reference the document section in the message.
7. When you cannot verify something (needs the glasses, the GPU server, or a network you cannot reach), say so plainly and give the exact command or step the user should run.

## Repository layout

```text
CLAUDE.md
refdocs/            SOW.md, TECH-01 ... TECH-06, TESTLOG.md, DECISIONS.md
android/            Gradle project, module :app (Kotlin)            -> TECH-02
server/mediamtx/    docker-compose.yml, mediamtx.yml                 -> TECH-03
server/telemetry/   FastAPI service, tests, systemd unit            -> TECH-04
server/relay/       Plan B only (TECH-01 section 7); do not build unless spike S4 fails
gpu/                glasses_reader package                           -> TECH-05
tools/              synthetic publisher/IMU client, estimate_offset, check_session, fake_session  -> TECH-06
tests/              pytest for server and gpu
```

## Commands (fill in as they come to exist; keep this list true)

```bash
# server
docker compose -f server/mediamtx/docker-compose.yml up -d
uvicorn app.main:app --host "$TELEMETRY_BIND" --port 8000 --ws-max-size 1048576    # from server/telemetry
cd server/telemetry && pip install -e '.[dev]' && pytest -q                         # telemetry tests (~20 s, starts real uvicorn servers)
pytest -q tests/test_protocol.py::test_duplicates_ignored                            # one test, from server/telemetry
cd gpu && pip install -e '.[dev]' && pytest -q                                       # reader + tool tests (~10 s; needs PyAV; torch only for iter_frames)
python tools/fake_session.py --data-root /tmp/g [--segments 2]                       # synthetic session with known tap offsets
python tools/check_session.py <session_id> --data-root /tmp/g                       # health report; exit 1 on failed thresholds
python tools/estimate_offset.py <session_id> --data-root /tmp/g --write             # tap-test calibration into session.json
ruff check server gpu tools                                                          # lint
tools/synthetic_publisher.sh <server-tailscale-ip> <passphrase>
python tools/synthetic_imu_client.py --host <ip> --token <token> --rate 60 --seconds 300
python tools/check_session.py <session_id>
# android
cd android && ./gradlew assembleDebug testDebugUnitTest && ./gradlew installDebug
```

## Defaults worth remembering

- Video: MEDIUM (504x896) at 24 fps by default; HIGH (720x1280) at 30 fps selectable. Android video goes over Bluetooth only, so lower settings often look better.
- Audio: 16-bit PCM, 48 kHz mono in-stream; AAC-LC in the TS; optional raw PCM channel.
- IMU: 60 Hz target (a target, not a guarantee); batches every 100 ms; keep `timestamp_ns` exactly.
- Server runs in **UTC**; recordings under `/srv/glasses/recordings/glasses/`; per-session data under `/srv/glasses/data/<session_id>/`.
- Acceptance numbers (90% of requested fps and IMU rate, audio gaps under 200 ms, reconnect under 15 s, calibration spread under 50 ms, crash loss under 10 s) are **proposed targets**, not Meta figures.

## Definition of done for the whole project

A 30-minute walking session leaves a playable recording, an audio track and an IMU dataset on the server, aligned within the accuracy in `SOW.md` section 11, and `gpu/` can read frames from that session (`SOW.md` section 1).

## Style

- Kotlin: official style, coroutines with structured scopes (no `GlobalScope`), never block the SDK collectors; all clock reads through `Clocks`.
- Python 3.11 or later: type hints, `ruff`, `pytest`; Pydantic v2 models mirror TECH-01 section 5 exactly.
- Logs are structured (JSONL on the phone, JSON or key=value on the server); never log frame data, raw audio or secrets.
