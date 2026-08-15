# WiFiSense Ultra — Project Requirements for Real-Time Use

**Release 1.0.1**  
**Designed by Rohith Bommalla Naresh**

This page answers one question: **what does a user actually need to run WiFiSense Ultra with live Wi-Fi CSI instead of only the simulator?**

WiFiSense Ultra is a local research and automation platform for Wi-Fi Channel State Information (CSI). It can process live CSI for room motion/presence research, fall-pattern events, signal anomaly detection, activity-model experiments, time-frequency visualization, RF fusion, persistence, and optional Home Assistant/MQTT integration.

> **Safety:** WiFiSense Ultra is not a medical device, emergency-response service, certified fall alarm, or life-safety product. Do not use it as the only way to determine whether someone needs help.

---

## 1. Minimum real-time setup

A user needs all of the following:

| Component | Minimum |
|---|---|
| Server | Linux/WSL/Docker-capable computer running Python 3.10+ |
| CPU/RAM | 2 CPU cores, 2 GB RAM for core processing |
| CSI hardware | 1 ESP32-class CSI receiver supported by the supplied ESP-IDF firmware |
| Wi-Fi | Stable local Wi-Fi network; start with 2.4 GHz |
| Server address | Stable/reachable LAN IP for the WiFiSense server |
| CSI transport | ESP32 node can reach server TCP **8089** |
| Dashboard/API | Operator can reach TCP **5000** or a reverse-proxy port |
| Calibration | Empty/stable room during initial baseline collection |

With one physical CSI node, users can validate live CSI ingestion, calibration, motion/presence research, STFT visualization, statistical anomaly behavior, and room-specific fall-pattern experiments.

---

## 2. Recommended serious room setup

For a more useful real-room installation:

- always-on native Linux or Docker host
- 4+ CPU cores
- 4+ GB RAM when PyTorch is enabled
- 3–4 fixed ESP32 CSI nodes around the room
- reserved/static server IP
- dedicated or trusted sensing VLAN/SSID
- API and ingest authentication tokens
- fixed AP/router and sensor locations
- NTP/time synchronization
- persistent local storage

Place nodes at different positions rather than together. Using different heights improves spatial diversity.

Example layout:

```text
node_nw (high) ---------------- node_ne (high)
       |                             |
       |           room              |
       |                             |
node_sw (mid) ----------------- node_se (low/mid)
```

---

## 3. What one, three, and four nodes mean

### 1 node

Useful for:

- CSI ingest validation
- calibration
- motion/presence research
- STFT and signal visualization
- anomaly scoring
- fall-pattern research

Not enough for meaningful multi-node spatial fusion.

### 2–3 nodes

Useful for:

- relative multi-node CSI fusion
- better spatial diversity
- comparing activity strength across the room

The default multi-node position output is intentionally **non-metric relative fusion**. Node coordinates alone do not create true person-to-sensor range measurements.

### 4+ nodes

Recommended when building toward 3D experiments, but true metric 3D still requires calibrated person-to-anchor distance observations and useful elevation diversity.

WiFiSense Ultra does **not** claim that ordinary Wi-Fi RSSI automatically gives accurate human range.

---

## 4. Server software required

Clone and install:

```bash
git clone https://github.com/bnrohit/wifisense-ultra.git
cd wifisense-ultra

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Run the regression checks before connecting real hardware:

```bash
python -m unittest discover -s tests -v
python tools/inprocess_simulator.py
```

Expected:

```text
Ran 9 tests
OK

WiFiSense Ultra in-process pipeline: ALL CHECKS PASSED
```

Start the server:

```bash
python -m wifisense_ultra.main
```

Open the dashboard:

```text
http://localhost:5000
```

For a different computer on the LAN, use the server's reachable LAN IP instead of `localhost`.

---

## 5. Optional software

### PyTorch activity/anomaly models

Not required for the core engine.

```bash
pip install -r requirements-ml.txt
```

or:

```bash
pip install -r requirements-full.txt
```

Until a trained activity model exists, WiFiSense Ultra intentionally uses the safe rule fallback instead of random LSTM predictions.

### Home Assistant / MQTT

Optional:

```bash
pip install -r requirements-homeassistant.txt
```

Configure the MQTT values in `.env`.

---

## 6. ESP32 hardware requirements

The repository includes baseline ESP-IDF firmware under:

```text
esp32_firmware/
```

The current baseline firmware uses direct TCP transport and is configured through ESP-IDF.

Typical workflow:

```bash
cd esp32_firmware
idf.py set-target esp32
idf.py menuconfig
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Each physical CSI node must be configured with:

- Wi-Fi SSID
- Wi-Fi password
- WiFiSense server LAN IP
- TCP port `8089`
- unique node ID
- optional ingest token
- target CSI frame rate; start around 20 Hz

Example node IDs:

```text
bedroom_nw
bedroom_ne
bedroom_sw
bedroom_se
```

A physical ESP32 must **not** use `127.0.0.1` as the server address. It must use the WiFiSense server address that is reachable from the Wi-Fi network.

Hardware and ESP-IDF compatibility should be verified for the exact ESP32 target being deployed. The included firmware is the project's reference baseline, not a guarantee that every ESP32-family board and every ESP-IDF version behaves identically.

---

## 7. Network requirements

Default ports:

```text
TCP 8089  ESP32 CSI node -> WiFiSense server
TCP 5000  Dashboard / API
```

Recommended rules:

- keep TCP 8089 on a trusted local network
- do not expose TCP 8089 directly to the public Internet
- reserve the server IP in DHCP or use a static address
- use firewall rules so only approved sensing nodes can reach the ingest listener
- use a reverse proxy with TLS/authentication if remote dashboard access is needed

Recommended `.env` security settings:

```dotenv
WIFISENSE_API_TOKEN=replace-with-a-long-random-management-secret
WIFISENSE_INGEST_TOKEN=replace-with-a-long-random-node-secret
```

Do not commit the real `.env` file. It is intentionally ignored by Git.

---

## 8. Configure room dimensions

Edit `.env` with measured values in meters:

```dotenv
WIFISENSE_WEB_PORT=5000
WIFISENSE_TCP_PORT=8089
WIFISENSE_SAMPLE_RATE=20
WIFISENSE_ROOM_WIDTH_M=6.0
WIFISENSE_ROOM_DEPTH_M=4.0
WIFISENSE_ROOM_HEIGHT_M=2.7
```

Measure node coordinates from one fixed room origin and register them in the dashboard or API.

Example:

```bash
curl -X POST http://SERVER-IP:5000/api/nodes/bedroom_nw/position \
  -H 'Content-Type: application/json' \
  -d '{"x":0.0,"y":0.0,"z":2.2}'
```

If API authentication is enabled, also send:

```bash
-H 'Authorization: Bearer YOUR_API_TOKEN'
```

---

## 9. Real-room commissioning sequence

Use this order:

```text
Install server
    ↓
Run 9 regression tests
    ↓
Run in-process simulator
    ↓
Start server
    ↓
Test TCP simulator
    ↓
Flash first ESP32
    ↓
Verify live CSI frames
    ↓
Install remaining ESP32 nodes
    ↓
Save measured node coordinates
    ↓
Empty-room calibration
    ↓
Test normal daily motion
    ↓
Test false-positive scenarios
    ↓
Safe staged fall-pattern evaluation
    ↓
Collect real labeled CSI
    ↓
Optional ML training
    ↓
24/7 deployment
```

---

## 10. How to know the live system is working

Check health:

```bash
curl -s http://SERVER-IP:5000/api/health | python3 -m json.tool
```

A working installation should eventually show:

- `status: ok`
- `errors: 0`
- physical node(s) connected while they are streaming
- `frame_count` continuously increasing
- calibration completed
- recent ingest age remaining low while nodes are online

Also inspect:

```bash
curl -s http://SERVER-IP:5000/api/nodes | python3 -m json.tool
curl -s 'http://SERVER-IP:5000/api/events?limit=100' | python3 -m json.tool
curl -s http://SERVER-IP:5000/api/capabilities | python3 -m json.tool
```

The dashboard should update live while the physical node is actively streaming.

---

## 11. Real-room acceptance tests

Before trusting automations, test at least:

1. Empty room -> no persistent presence/track.
2. Person enters -> presence becomes active.
3. Normal walking -> CSI/STFT and motion state change.
4. Person stops normally -> no fall-pattern event.
5. Normal sitting/standing/lying -> no unsafe assumptions.
6. Door movement, curtains/fans, pets if applicable -> evaluate false positives.
7. Dropped objects -> evaluate false fall-pattern detections.
8. Multiple people if that is an intended scenario.
9. Safe staged fall-like movements only with appropriate precautions.
10. Repeat across different sessions/days before trusting room thresholds.

Real CSI is environment-sensitive. Recalibrate after meaningful AP/channel/node/furniture changes.

---

## 12. ML training requirements

Do not treat simulator-only training as production-quality activity recognition.

Collect labeled real CSI for activities such as:

- empty
- walking
- sitting
- standing
- lying down normally
- sleeping, if appropriate and consented
- controlled fall-like activity
- different people, orientations, distances, sessions, and days

Then train:

```bash
python tools/train_activity_model.py --epochs 40
```

Evaluate on held-out sessions/environments before enabling ML-driven automations.

---

## 13. 24/7 operation

For unattended operation, prefer native Linux or Docker rather than relying on a WSL user session.

Docker baseline:

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
docker compose logs -f
```

The project uses persistent data storage, an HTTP health check, and `restart: unless-stopped` for recovery.

A single container is **not** true zero downtime. Real high availability requires redundant application instances, coordinated CSI stream ownership, shared/persistent state, and an ingress/load-balancing design.

---

## 14. What is operational today vs experimental

### Operational software paths in v1.0.1

- CSI protocol parsing and normalization
- calibration pipeline
- motion/presence processing
- fall-pattern state machine
- Kalman tracking
- STFT processing
- phase sanitization
- statistical anomaly baseline
- persisted SQLite events/settings
- node-coordinate persistence
- TCP simulator
- dashboard/API
- optional MQTT/Home Assistant bridge
- optional trained PyTorch model paths
- Docker deployment
- automated regression suite and GitHub CI

### Requires real-environment validation

- threshold accuracy in a specific room
- fall-pattern false-positive/false-negative rates
- activity-model accuracy
- multi-person behavior
- cross-room generalization
- respiration trend usefulness
- true metric 2D/3D positioning unless calibrated target-range observations are available
- exact hardware/ESP-IDF behavior on every ESP32-family board

---

## 15. Fastest path for a new user

For someone starting from zero:

1. Clone the repository.
2. Run the 9 tests and in-process simulator.
3. Start the dashboard.
4. Run `tools/simulator.py` to learn the UI.
5. Obtain one compatible ESP32 CSI development board.
6. Install ESP-IDF and flash the included firmware.
7. Point the ESP32 to the server's LAN IP and TCP 8089.
8. Confirm frame count increases.
9. Calibrate an empty room.
10. Add more fixed nodes only after the first physical node is stable.
11. Collect real data before training ML.
12. Use Docker/native Linux for unattended deployment.

For the detailed commissioning guide, also read:

- [README.md](README.md)
- [REALTIME_DEPLOYMENT.md](REALTIME_DEPLOYMENT.md)
- [esp32_firmware/README.md](esp32_firmware/README.md)
- [SECURITY.md](SECURITY.md)
- [TEST_REPORT.md](TEST_REPORT.md)
