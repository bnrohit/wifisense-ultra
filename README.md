# WiFiSense Ultra

**Privacy-first Wi-Fi CSI sensing research and automation platform**  
**Designed by Rohith Bommalla Naresh** · Release **1.0.1**

WiFiSense Ultra turns supported ESP32 Channel State Information (CSI) streams into a resilient local sensing pipeline for **room motion/presence research, fall-pattern events, signal anomaly detection, activity-model experiments, time-frequency visualization, and Home Assistant automation**—without requiring cameras or wearables.

> **Important:** WiFiSense Ultra is a research/automation system, not a medical device, emergency-response system, or certified life-safety product. Fall-pattern and respiration outputs can be wrong. Never use them as the only way to determine whether someone needs help.

## Why this release is different

The original Ultra prototype contained impressive-looking modules that did not all agree with each other at runtime. Release 1.0.0 was rebuilt around one rule: **advanced options may add capability, but they must never make the core CSI engine unusable.**

| Capability | Release behavior |
|---|---|
| CSI ingest | ESP-IDF `imag,real` byte ordering, variable CSI length accepted and normalized |
| Calibration | Per-node empty-room baseline, guarded adaptive background |
| Motion | Standardized CSI change with persistent Kalman tracks |
| Fall pattern | Per-node impact → settling → stillness state machine; independent of track lifetime |
| Positioning | Conservative relative RF cue by default; calibrated metric multilateration only with real target-distance observations |
| 3D | Enabled only when calibrated distances and enough elevation-diverse anchors exist |
| STFT | Bounded live time-frequency analysis and broad micro-motion bands |
| Phase | Unwrap + linear trend removal; variable-length safe |
| Anomaly | Statistical baseline always available; optional PyTorch autoencoder |
| Activity | Safe rule fallback; optional trained LSTM + HMM. **No random untrained predictions** |
| MQTT / Home Assistant | Optional, retained discovery + availability, isolated from core engine |
| Persistence | SQLite WAL, bounded retention, persisted node coordinates |
| Runtime | TCP ingest and processing workers recover independently; SSE with polling fallback |
| Deployment | Waitress production WSGI, Docker health check + restart policy |
| Firmware | Honest direct-TCP ESP32 baseline; mesh is a separate architecture reference, not a fake checkbox |

## Architecture

```text
ESP32 CSI node(s)
       │
       │ TCP 8089  NODE|... + CSI|...
       ▼
┌──────────────────────── WiFiSense Ultra ────────────────────────┐
│ Protocol validation / complex CSI normalization                  │
│            │                                                     │
│            ├── phase sanitization                                │
│            ├── Hampel / PCA / adaptive background                │
│            ├── z-score motion features                           │
│            ├── node-keyed fall state machine                     │
│            ├── Kalman tracking + object rules/optional RF model  │
│            ├── STFT + broad micro-motion spectrum                │
│            ├── statistical / optional neural anomaly detection   │
│            ├── optional trained LSTM + HMM                       │
│            └── conservative RF fusion / calibrated ranging       │
│                                                                  │
│ SQLite WAL        Flask/Waitress + SSE        Optional MQTT/HA    │
└──────────────┬───────────────┬──────────────────────┬─────────────┘
               ▼               ▼                      ▼
            events          dashboard             automations
```

## Fastest no-hardware verification

Use this first. It does not need sockets, ESP32 hardware, Flask, Docker, or MQTT.

```bash
python tools/inprocess_simulator.py
```

Expected final line:

```text
WiFiSense Ultra in-process pipeline: ALL CHECKS PASSED
```

Run the automated regression suite too:

```bash
python -m unittest discover -s tests -v
```

The suite covers protocol byte order/resampling, node validation, filter safety, phase handling, calibrated multilateration, conservative non-metric fusion, SQLite persistence, and the full calibration → motion → no-false-fall → fall-confirmation path.

## Install

### Core installation

Python **3.10+** is required. Python 3.12 is the recommended deployment target.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Start the application:

```bash
python -m wifisense_ultra.main
```

Open:

```text
http://localhost:5000
```

The CSI listener is:

```text
TCP 8089
```

### Full ML + Home Assistant installation

```bash
pip install -r requirements-full.txt
```

Or install only what you use:

```bash
pip install -r requirements-ml.txt
pip install -r requirements-homeassistant.txt
```

The core engine deliberately does **not** require PyTorch or MQTT.

## Docker deployment

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
docker compose logs -f
```

For PyTorch + MQTT in the image:

```bash
WIFISENSE_INSTALL_EXTRAS=full docker compose build --no-cache
docker compose up -d
```

The compose service uses `restart: unless-stopped`, persists `./data`, and has an HTTP health check. This reduces downtime after process/host restarts, but **a single container is not mathematically zero-downtime**. True high availability needs multiple coordinated application instances, an ingress/load balancer, and a deliberate strategy for CSI stream ownership and shared state.


## Real-time deployment requirements

For a complete continuously running installation, read **[REALTIME_DEPLOYMENT.md](REALTIME_DEPLOYMENT.md)**. The short version is:

| Need | Minimum | Recommended for a serious room deployment |
|---|---|---|
| Server | Python 3.10+ host | Always-on Linux host or Docker-capable mini PC/server |
| CSI hardware | 1 supported ESP32 node | 3-4 supported ESP32 nodes with fixed placement |
| Wi-Fi | Stable local 2.4 GHz WLAN | Dedicated/trusted sensing VLAN or SSID |
| Network | Nodes can reach TCP 8089 | Static/reserved server IP, no public exposure of 8089 |
| Dashboard | TCP 5000 reachable by operator | TLS reverse proxy for remote access |
| Calibration | Empty/stable room at startup | Recalibrate after AP/channel/furniture/sensor-placement changes |
| Positioning | 1 node = sensing only | 2+ = relative fusion; true metric 2D/3D requires calibrated target ranges |
| ML | Not required | Train only from real labeled CSI collected in the target environments |
| Security | Trusted LAN | `WIFISENSE_INGEST_TOKEN`, `WIFISENSE_API_TOKEN`, authenticated MQTT |

**WSL users:** WSL is excellent for development and testing. In a new shell either activate the project venv first, or use `python3`:

```bash
cd ~/wifisense-ultra-build/wifisense-ultra
source .venv/bin/activate
python -m wifisense_ultra.main
```

Without the venv active, use `python3`, for example:

```bash
curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool
```

For unattended 24/7 use, prefer a native Linux/Docker host with a reserved LAN IP over a user-session WSL process.

## Simulator over the real TCP protocol

Start the application in terminal 1:

```bash
python -m wifisense_ultra.main
```

Then terminal 2:

```bash
python tools/simulator.py empty --duration 10
python tools/simulator.py walking --duration 10
python tools/simulator.py fall --duration 15
```

Multiple simulated nodes:

```bash
python tools/simulator.py walking --nodes 3 --duration 15

# If WIFISENSE_INGEST_TOKEN is set on the server:
python tools/simulator.py walking --token YOUR_NODE_SECRET --duration 15
```

The TCP simulator emits the same signed-byte ordering expected from ESP-IDF (`imaginary, real`).

## Real ESP32 node

The release firmware is under `esp32_firmware/`.

```bash
cd esp32_firmware
idf.py set-target esp32
idf.py menuconfig
# WiFiSense Ultra -> SSID, password, server IP, node ID, target rate
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Recommended first deployment:

- one supported ESP32 receiver
- stable 2.4 GHz Wi-Fi link
- server and ESP32 on a trusted local network
- 20 CSI frames/second to begin
- fixed sensor/router locations during calibration and evaluation
- empty, stable room during calibration

The firmware:

- handles ESP-IDF `first_word_invalid`
- preserves ESP-IDF `imag,real` CSI byte order
- keeps the Wi-Fi CSI callback short and queues work outside it
- throttles CSI to the configured target rate
- uses TCP keepalive and reconnect backoff
- reports dropped frames rather than blocking the Wi-Fi callback

See `esp32_firmware/README.md` for details.

### About mesh

The default firmware is **direct TCP**, because that is the simplest transport to validate and recover. `esp32_firmware/mesh_reference/` documents the architecture needed for ESP-WIFI-MESH, but mesh is not advertised as production-ready until it has been built and stress-tested on the exact chip and ESP-IDF release in use.

## First-room commissioning

1. Fix the ESP32 and router/AP positions. Do not carry them around during a session.
2. Measure the room width, depth, and height and put them in `.env`.
3. Start the server and connect each CSI node with a unique node ID.
4. Enter measured node coordinates in the dashboard or API.
5. Keep the room empty while the first calibration frames are collected.
6. Test ordinary daily activities before testing fall-like movements.
7. Recalibrate after major furniture, AP, channel, or sensor-placement changes.
8. Collect data in each intended room before trusting thresholds or a trained model.

Example room configuration:

```dotenv
WIFISENSE_ROOM_WIDTH_M=6.4
WIFISENSE_ROOM_DEPTH_M=4.8
WIFISENSE_ROOM_HEIGHT_M=2.7
```

Register a node:

```bash
curl -X POST http://localhost:5000/api/nodes/bedroom_1/position \
  -H "Content-Type: application/json" \
  -d '{"x":0.3,"y":0.4,"z":1.2}'
```

If you set `WIFISENSE_API_TOKEN`, include:

```bash
-H "Authorization: Bearer YOUR_TOKEN"
```

## Positioning: what the product can and cannot claim

A receiver's Wi-Fi RSSI primarily describes the **radio link that delivered the packet**. It is not automatically the person's range from that ESP32. Treating AP↔receiver RSSI as person↔sensor distance creates convincing-looking but physically invalid coordinates.

WiFiSense Ultra therefore has two positioning modes:

### 1. Relative CSI fusion — default

With two or more configured nodes, motion strength is fused into a **CSI weighted centroid**. It is useful as a stable room-relative cue, but the API reports:

```json
{"method":"csi_weighted_centroid","metric":false,"z":null}
```

It does not pretend to be precise ranging.

### 2. Calibrated multilateration — advanced

If a future ranging adapter/model supplies a calibrated `target_distance_m` per sensor observation:

- 2D least-squares needs at least 3 usable anchors.
- 3D needs at least 4 usable anchors **plus real elevation diversity**.
- robust least squares, residual error, GDOP, and confidence are returned.

This separation makes the system more scientifically defensible and gives a clean path to true ranging hardware/models later.

## Fall-pattern detector

The fall detector is deliberately **node-keyed rather than track-keyed**.

That matters because after a person falls and becomes still, motion detections can disappear and the Kalman track may expire. The per-node state machine continues through:

```text
sudden standardized spike
        ↓
post-impact settling window
        ↓
low-motion / low-z stillness
        ↓
minimum stillness duration
        ↓
fall-pattern event + cooldown
```

It also rejects the regression case tested in the suite: **ordinary movement followed by simply stopping must not create a fall event**.

Starter thresholds are in `.env.example`. They are not universal; validate them with your own environment and safe staged movements.

## STFT and phase processing

`phase_sanitize.py`:

- unwraps phase
- removes a fitted linear phase trend
- accepts variable CSI lengths
- can compute antenna phase differences when real MIMO inputs are available

`stft_analysis.py`:

- keeps bounded history
- detrends the time series
- computes a bounded STFT payload for the dashboard
- reports broad `slow_micro_motion`, `body_motion`, and `fast_motion` energy bands

WiFiSense Ultra deliberately does **not** call an arbitrary STFT peak a reliable heartbeat measurement.

## Anomaly detection

Every calibrated node receives an always-available **statistical normal-room baseline**. PyTorch is optional.

The engine remains functional when PyTorch is absent. If PyTorch is present, you can train a per-node autoencoder from known-normal persisted snapshots:

```bash
python tools/train_anomaly_model.py bedroom_1 --force
```

Only do this after verifying that the selected stored data represents the normal state. Training on motion/fall periods makes the anomaly model learn the wrong behavior.

The saved model is loaded automatically on future runs.

## LSTM + HMM activity recognition

Classes:

```text
empty, walking, sitting, standing, falling, sleeping
```

Until a trained model exists, the application exposes a transparent **rule fallback** instead of random LSTM output.

Collect a labeled sequence while a known activity is happening:

```bash
curl -X POST http://localhost:5000/api/label/activity \
  -H "Content-Type: application/json" \
  -d '{"node_id":"bedroom_1","label":"walking"}'
```

Repeat many times for several classes, rooms, people, orientations, and days. Then:

```bash
python tools/train_activity_model.py --epochs 40
```

A model trained on a tiny single-room dataset can memorize the room rather than the activity. Evaluate on held-out sessions/rooms before enabling it for meaningful automation.

## Object classifier

A conservative rules classifier is enabled by default. An optional RandomForest model can be trained from JSONL feature samples:

```bash
python tools/train_classifier.py data/object_labels.jsonl
```

Example line:

```json
{"label":"human","features":{"z_max":5.2,"z_mean":1.8,"active_max":24}}
```

Production feature vectorization uses a fixed schema; it no longer depends on arbitrary dictionary key order.

## Home Assistant / MQTT

Enable in `.env`:

```dotenv
WIFISENSE_MQTT_ENABLED=1
MQTT_BROKER=192.168.1.20
MQTT_PORT=1883
MQTT_ROOM=bedroom
MQTT_USERNAME=wifisense
MQTT_PASSWORD=change-this
```

Install the integration dependency:

```bash
pip install -r requirements-homeassistant.txt
```

The bridge uses Home Assistant MQTT discovery and publishes availability. It exposes room-level presence, fall-pattern state, activity, track count, and an **experimental respiration trend**.

MQTT failures are isolated from CSI processing; an unavailable broker must not take down the sensing engine.

## API

Read-only:

```text
GET /api/status
GET /api/health
GET /api/ready
GET /api/capabilities
GET /api/config
GET /api/nodes
GET /api/events?limit=100
GET /api/spectrogram
GET /stream                  # Server-Sent Events
```

Mutating:

```text
POST /api/calibrate
POST /api/nodes/{node_id}/position
POST /api/label/activity
```

Set `WIFISENSE_API_TOKEN` to require Bearer authentication for mutating endpoints. Set `WIFISENSE_INGEST_TOKEN` to require an authenticated node handshake before TCP CSI frames are accepted.

## Dashboard

The dashboard is self-contained and has no CDN dependency. It includes:

- **RBN** monogram branding and “Rohith Bommalla Naresh” identity
- live connection status
- calibration/presence/fall state
- track list and node health
- live 3D room projection using configured dimensions; z-height is rendered only when calibrated metric z exists
- relative vs metric positioning labels
- STFT waterfall
- micro-motion bands
- anomaly score
- activity model source/status
- persisted event history
- node-coordinate setup
- automatic SSE reconnection + HTTP polling fallback

## Reliability design

The release is built to **degrade gracefully**:

- malformed CSI lines are rejected instead of crashing the process
- per-client TCP failures stay in that client thread
- TCP listener retries with exponential backoff
- processing worker catches and records per-tick failures
- optional PyTorch and MQTT imports are capability-gated
- SSE clients use bounded queues so slow browsers cannot build unbounded memory
- STFT history and event history are bounded
- SQLite uses WAL + busy timeout
- SQLite connections are closed deterministically
- old event/CSI data is pruned according to retention settings
- GitHub Actions tests Python 3.10, 3.12, and 3.13 on pushes/pull requests
- Docker health check catches a stalled HTTP process
- Docker restart policy recovers after process/host restarts

For environments that truly require uninterrupted service, deploy external HA rather than claiming a single Python process can guarantee zero downtime.

## Security and privacy

- Keep TCP 8089 on a trusted network/VLAN; the CSI TCP transport is intentionally simple and not encrypted by itself.
- Set `WIFISENSE_INGEST_TOKEN` to prevent unauthenticated node handshakes; use a VPN/tunnel if transport confidentiality is required.
- Do not expose TCP 8089 directly to the public Internet.
- Put the dashboard behind TLS/reverse proxy when remote access is required.
- Set `WIFISENSE_API_TOKEN` before allowing other users onto the management network.
- Use an authenticated MQTT broker.
- CSI sensing can still reveal occupancy/activity patterns even though it does not capture video. Obtain consent and follow applicable law/policy.
- Do not use this release for covert monitoring.

See `SECURITY.md`.

## Configuration

All operational settings can be overridden using environment variables. Start from:

```bash
cp .env.example .env
```

Useful controls include:

```text
WIFISENSE_SAMPLE_RATE
WIFISENSE_CALIBRATION_FRAMES
WIFISENSE_MOTION_Z_THRESH
WIFISENSE_ROOM_WIDTH_M / DEPTH_M / HEIGHT_M
WIFISENSE_FALL_*
WIFISENSE_CSI_RETENTION_DAYS
WIFISENSE_EVENT_RETENTION_DAYS
WIFISENSE_API_TOKEN
WIFISENSE_INGEST_TOKEN
WIFISENSE_MQTT_ENABLED
```

## Project structure

```text
wifisense-ultra/
├── wifisense_ultra/
│   ├── main.py                    HTTP/SSE + resilient TCP runtime
│   ├── ultra_engine.py            integrated capability-gated engine
│   ├── protocol.py                ESP-IDF CSI parser / normalization
│   ├── signal_processing.py       calibration, Hampel, PCA, z-score
│   ├── phase_sanitize.py          phase unwrap / trend removal
│   ├── tracking.py                Kalman multi-target tracker
│   ├── vitals.py                  node-keyed fall + experimental respiration
│   ├── classifier.py              rules + optional RandomForest
│   ├── stft_analysis.py           bounded STFT / micro-motion bands
│   ├── storage.py                 SQLite WAL persistence
│   ├── node_manager.py            node state / health / coordinates
│   ├── mqtt_bridge.py             optional Home Assistant MQTT
│   ├── rf/triangulation.py        relative fusion + calibrated multilateration
│   ├── dl/autoencoder.py          optional PyTorch anomaly model
│   ├── dl/activity_lstm.py        optional trained LSTM
│   └── activity/hmm.py            temporal smoothing
│   ├── dashboard/index.html       branded live dashboard
├── tools/
│   ├── inprocess_simulator.py     deterministic regression simulator
│   ├── simulator.py               actual TCP protocol simulator
│   ├── train_activity_model.py
│   ├── train_anomaly_model.py
│   └── train_classifier.py
├── esp32_firmware/                direct-TCP CSI node + mesh reference
├── tests/test_pipeline.py
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── pyproject.toml
```

## Troubleshooting

### Server starts but stays “Calibrating”

Check that CSI nodes are actually sending frames and that the configured sample rate roughly matches the stream. Use:

```bash
curl http://localhost:5000/api/nodes
curl http://localhost:5000/api/health
```

### Tracks appear during an empty room

Recalibrate with no motion and inspect node placement, Wi-Fi channel stability, fans/doors, moving curtains, pets, and other RF-changing sources. Increase thresholds only after identifying the source.

### LSTM says “untrained”

That is intentional. Collect activity labels and train a model. The product never presents random untrained neural output as a prediction.

### Position says “relative”

That is intentional unless you have a calibrated target-distance estimator. Configured node coordinates alone do not magically create person-to-anchor ranges.

### Home Assistant shows nothing

Check `/api/capabilities`, MQTT credentials, broker reachability, the HA discovery prefix, and whether the Home Assistant MQTT integration is enabled.

## Push to GitHub

From this directory:

```bash
git init
git add .
git commit -m "WiFiSense Ultra v1.0.1"
git branch -M main

gh auth status
gh repo create bnrohit/wifisense-ultra --private --source=. --remote=origin --push
```

If the repository already exists:

```bash
git remote add origin https://github.com/bnrohit/wifisense-ultra.git
git push -u origin main
```

For subsequent work, use feature branches instead of committing directly to `main`.

## Validation status for this release

See `TEST_REPORT.md` for the exact checks performed. In short:

- Python syntax/compile: tested
- deterministic full pipeline: tested
- fall positive + ordinary-stop negative path: tested
- SQLite leak regression: tested with `ResourceWarning` promoted to an error
- real local TCP ingest with simulator: tested
- dashboard JavaScript syntax: tested with Node
- PyTorch “untrained suppression”: tested
- calibrated positioning math: tested
- Flask/Waitress runtime: code-complete, but the release-build sandbox had no Flask package and no outbound package network
- Docker runtime: deployment files provided; Docker CLI was unavailable in the release-build sandbox
- ESP32 firmware: source corrected against ESP-IDF CSI format; ESP-IDF toolchain/hardware was unavailable in the release-build sandbox

Run the final three platform-specific validations on the target machine/hardware before calling a deployment production-ready.

## License

MIT. See `LICENSE`.
