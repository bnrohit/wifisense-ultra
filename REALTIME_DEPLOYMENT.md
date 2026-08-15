# WiFiSense Ultra — Real-Time Deployment Guide

**Release 1.0.1 · Designed by Rohith Bommalla Naresh**

This guide is for people moving from the built-in simulator to a continuously running, real-room CSI sensing installation.

> WiFiSense Ultra is a research/automation platform. It is not a medical device, certified fall alarm, emergency service, or life-safety system. Always provide another way to obtain help.

## 1. What a user needs

### Server

Minimum:

- x86-64 or ARM64 Linux computer capable of Python 3.10+
- 2 CPU cores
- 2 GB RAM
- local persistent storage
- network interface reachable by the CSI nodes

Recommended:

- always-on Linux mini PC, server, or VM
- 4+ CPU cores and 4+ GB RAM when using PyTorch
- Docker/Compose for repeatable startup and restart policy
- reserved/static LAN IP for the WiFiSense server
- NTP/time synchronization

WSL2 works well for development and testing, but a native always-on Linux/Docker host is preferable for unattended operation because a WSL user session, Windows sleep/reboot, or networking changes can stop/readdress the service.

### CSI nodes

- 1 supported ESP32 CSI receiver is enough to validate real CSI, presence/motion, signal processing, and room-specific fall-pattern experiments.
- 2+ fixed nodes allow conservative relative multi-node fusion.
- 3-4 nodes are recommended for a useful room deployment and better spatial diversity.
- Metric 2D requires at least 3 calibrated person-to-anchor distance observations.
- Metric 3D requires at least 4 calibrated person-to-anchor distance observations plus useful elevation diversity.

Configured anchor coordinates alone do **not** create true ranging. Without a calibrated target-distance source, the product intentionally labels its position as non-metric relative CSI fusion.

### Wi-Fi/network

- stable local Wi-Fi for ESP32 nodes
- nodes must be able to reach the server on TCP **8089**
- operators must be able to reach the dashboard/API on TCP **5000** (or the reverse-proxy port)
- do not expose TCP 8089 directly to the Internet
- use a trusted sensing VLAN/SSID or firewall policy
- reserve the server address so firmware does not have to chase a changing IP

## 2. Install the server

```bash
git clone https://github.com/bnrohit/wifisense-ultra.git
cd wifisense-ultra
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Optional ML + Home Assistant dependencies:

```bash
pip install -r requirements-full.txt
```

The application now automatically reads the project's `.env` file. Values explicitly set in the shell/container still take precedence.

## 3. Configure the room

Edit `.env`:

```dotenv
WIFISENSE_WEB_PORT=5000
WIFISENSE_TCP_PORT=8089
WIFISENSE_SAMPLE_RATE=20
WIFISENSE_ROOM_WIDTH_M=6.0
WIFISENSE_ROOM_DEPTH_M=4.0
WIFISENSE_ROOM_HEIGHT_M=2.7

# Strongly recommended on a shared/trusted LAN
WIFISENSE_API_TOKEN=replace-with-a-long-random-management-secret
WIFISENSE_INGEST_TOKEN=replace-with-a-long-random-node-secret
```

Do not commit `.env`; it is intentionally ignored by Git.

## 4. Validate software before hardware

```bash
python tools/inprocess_simulator.py
python -m unittest discover -s tests -v
```

Expected regression result for this release:

```text
Ran 9 tests
OK
```

Start the server:

```bash
python -m wifisense_ultra.main
```

In another terminal:

```bash
curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool
curl -s http://127.0.0.1:5000/api/capabilities | python3 -m json.tool
```

A fresh healthy server can correctly show `calibrated: false`, `connected_nodes: 0`, and `frame_count: 0` until a node streams CSI.

## 5. Run the real TCP simulator

Before flashing hardware, keep the server running and test the exact TCP path:

```bash
python tools/simulator.py empty --duration 10
python tools/simulator.py walking --duration 10
python tools/simulator.py fall --duration 20
```

For three continuously visible simulated nodes:

```bash
python tools/simulator.py walking --nodes 3 --duration 300
```

If ingest authentication is enabled:

```bash
python tools/simulator.py walking --nodes 3 --duration 300 --token YOUR_NODE_SECRET
```

Open `http://localhost:5000` while the simulator is still running. After the command exits, seeing the node as **OFFLINE** is expected; the simulator disconnects when finished.

## 6. Flash and commission ESP32 hardware

Use the firmware under `esp32_firmware/` with an ESP-IDF version supported by the selected ESP32 target.

```bash
cd esp32_firmware
idf.py set-target esp32
idf.py menuconfig
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Configure each node with:

- Wi-Fi SSID/password
- WiFiSense server LAN IP
- TCP port 8089
- unique node ID (`bedroom_nw`, `bedroom_ne`, etc.)
- the same ingest token as the server when enabled
- target CSI rate (start at 20 Hz)

Do not point a physical ESP32 at `127.0.0.1`; use the WiFiSense server's LAN-reachable address.

## 7. Recommended physical layout

For a rectangular room, distribute nodes instead of placing all receivers together. Example:

```text
node_nw (high) ---------------- node_ne (high)
       |                             |
       |          room               |
       |                             |
node_sw (mid) ----------------- node_se (low/mid)
```

Measure coordinates from one fixed room origin in meters and save them through the dashboard or API.

Example:

```bash
curl -X POST http://SERVER:5000/api/nodes/bedroom_nw/position \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer YOUR_API_TOKEN' \
  -d '{"x":0.0,"y":0.0,"z":2.2}'
```

Repeat for every node.

## 8. Calibrate the real room

Keep the room empty and stable during initial calibration. Avoid people walking through, moving the AP/nodes, large moving doors, or intentionally changing furniture during the baseline.

Check:

```bash
curl -s http://SERVER:5000/api/health | python3 -m json.tool
curl -s http://SERVER:5000/api/nodes | python3 -m json.tool
```

You want:

- `status: ok`
- `errors: 0`
- connected physical node(s)
- frame count steadily increasing
- node calibration complete

Recalibrate after meaningful AP/channel/node/furniture changes because CSI is environment-sensitive.

## 9. Real-room acceptance tests

Do these before enabling automations:

1. Empty room → no persistent presence/track.
2. Person enters → presence becomes active.
3. Normal walking → track/motion appears and CSI/STFT changes.
4. Person stops normally → **no fall-pattern event**.
5. Sitting/standing/lying normally → no unsafe alert assumptions.
6. Safe staged fall-like test onto appropriate padding with spotters → evaluate the event detector; never intentionally endanger a participant.
7. Open/close doors and move curtains/fans → measure false positives.
8. Drop ordinary objects → make sure impacts do not create unacceptable alerts.
9. Test multiple people if that is an intended use case.
10. Repeat on different days before trusting room thresholds.

Inspect events:

```bash
curl -s 'http://SERVER:5000/api/events?limit=100' | python3 -m json.tool
```

## 10. Activity ML

The safe fallback is intentionally active until a model has been trained. Do **not** train a production activity model only on simulator output.

Collect labeled real CSI across:

- empty
- walking
- sitting
- standing
- lying down normally
- sleeping (if appropriate and consented)
- controlled fall-like activity
- multiple people, orientations, distances, sessions, and days

Then train and evaluate on held-out sessions:

```bash
python tools/train_activity_model.py --epochs 40
```

Only enable ML-driven automations after measuring false-positive/false-negative behavior in the actual deployment environments.

## 11. 24/7 operation

Recommended Docker deployment:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f
```

The compose file persists data and uses `restart: unless-stopped` plus an HTTP health check.

Monitor:

```bash
curl -fsS http://127.0.0.1:5000/api/health
curl -fsS http://127.0.0.1:5000/api/ready
```

A single process/container is restart-resilient, not truly zero-downtime. High availability would require redundant application instances and a deliberate ownership strategy for long-lived CSI TCP streams and shared state.

## 12. Security/privacy checklist

- Keep port 8089 private to the trusted sensing network.
- Enable `WIFISENSE_INGEST_TOKEN`.
- Enable `WIFISENSE_API_TOKEN`.
- Put remote dashboard access behind HTTPS/VPN/reverse proxy.
- Use an authenticated MQTT broker if Home Assistant is enabled.
- Never commit `.env`, passwords, tokens, Wi-Fi credentials, or generated databases.
- Obtain appropriate consent: CSI does not record video, but occupancy/activity inference is still sensitive.
- Do not use the system for covert monitoring.

## 13. Operational definition of “working”

A real room is not operational merely because the web page loads. A healthy deployment should satisfy all of these:

```text
server health = ok
runtime errors = 0
physical CSI nodes = continuously online
frame count = continuously increasing
calibration = complete
empty room = no persistent false presence
normal movement = detected
normal stop = no false fall event
node coordinates = stored/restored after restart
alerts/events = persisted
restart test = service and nodes recover
room-specific false-positive testing = completed
```

Only after those checks should the deployment be treated as an operational research/automation installation.
