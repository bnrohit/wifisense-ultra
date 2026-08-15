# WiFiSense Ultra — Hardware Checklist

Use this checklist when moving from the software simulator to real Wi-Fi CSI sensing.

> The current reference firmware targets the classic ESP32 through ESP-IDF (`idf.py set-target esp32`). Verify CSI support and ESP-IDF compatibility before substituting a different ESP32-family chip.

## Minimum one-node hardware test

You need:

- **1 compatible ESP32 development board** supported by the project's ESP-IDF CSI firmware
- **1 USB data cable** for flashing and serial monitoring
- **1 stable 2.4 GHz Wi-Fi access point/router**
- **1 server computer** running WiFiSense Ultra
- server and ESP32 on a network where the ESP32 can reach **TCP 8089**
- browser access to the server dashboard on **TCP 5000**
- a stable power source for the ESP32

This is enough to validate:

- real CSI frames reaching the server
- node online/offline state
- calibration
- motion/presence research
- STFT visualization
- anomaly scoring
- room-specific fall-pattern experiments

## Recommended room deployment

For a more useful fixed-room installation, use:

- **3–4 compatible ESP32 CSI nodes**
- one USB data cable for flashing each board, or reuse one during commissioning
- reliable 5 V power for every fixed node
- stable 2.4 GHz AP/router
- always-on Linux/Docker server
- Ethernet for the server when practical
- reserved/static server LAN address
- fixed wall/shelf mounts or enclosures for the ESP32 nodes
- measuring tape or laser measure for room and node coordinates
- trusted sensing VLAN/SSID or equivalent firewall isolation
- optional UPS for the server/AP if continuous operation matters

Keep AP and node positions fixed during calibration and testing.

## Suggested placement

Distribute nodes around the room instead of placing them together.

```text
node_nw (high) ---------------- node_ne (high)
       |                             |
       |           room              |
       |                             |
node_sw (mid) ----------------- node_se (low/mid)
```

Use one room corner as `(0,0,0)` and measure each node position in meters.

## Server requirements

Minimum core processing:

- Python 3.10+
- 2 CPU cores
- 2 GB RAM
- persistent local storage

Recommended when using PyTorch or keeping the service online continuously:

- native Linux or Docker-capable host
- 4+ CPU cores
- 4+ GB RAM
- reserved/static LAN IP
- NTP/time synchronization
- persistent storage

WSL2 is appropriate for development and testing. Native Linux/Docker is preferable for unattended 24/7 operation.

## Network requirements

Default ports:

```text
ESP32 -> WiFiSense server: TCP 8089
Operator -> dashboard/API: TCP 5000
```

Do not expose TCP 8089 directly to the public Internet.

Recommended security configuration:

```dotenv
WIFISENSE_API_TOKEN=long-random-management-secret
WIFISENSE_INGEST_TOKEN=long-random-node-secret
```

The ingest token authenticates the node handshake but does not encrypt raw TCP. Keep the sensing network trusted or place it behind an appropriate protected tunnel/network boundary.

## What each ESP32 node needs configured

In `idf.py menuconfig` configure:

- Wi-Fi SSID
- Wi-Fi password
- WiFiSense server LAN IP
- server port `8089`
- unique node ID
- optional ingest token
- target CSI frame rate; start near 20 Hz

A real ESP32 must use the WiFiSense server's LAN-reachable address, not `127.0.0.1`.

## Before buying many boards

Start with **one** compatible ESP32 and complete this sequence first:

1. Build and flash the reference firmware.
2. Confirm the ESP32 joins Wi-Fi.
3. Confirm it connects to server TCP 8089.
4. Confirm `frame_count` increases continuously.
5. Confirm the dashboard shows the physical node online.
6. Complete empty-room calibration.
7. Walk through the room and verify CSI/STFT changes.
8. Reboot the ESP32 and server and verify recovery.

Only after the one-node path is stable should you expand to 3–4 fixed nodes.

## What is not required

The core WiFiSense Ultra system does **not** require:

- cameras
- wearable sensors
- PyTorch
- MQTT/Home Assistant
- a cloud service

PyTorch and MQTT are optional features.

## Important positioning limitation

More ESP32 nodes improve spatial diversity and relative RF fusion, but node coordinates alone do not provide true person-to-anchor distance.

True metric 2D/3D localization requires calibrated target-range observations. The project intentionally does not present ordinary AP-to-receiver RSSI as accurate human range.

For complete software and commissioning instructions, read:

- [PROJECT_REQUIREMENTS.md](PROJECT_REQUIREMENTS.md)
- [REALTIME_DEPLOYMENT.md](REALTIME_DEPLOYMENT.md)
- [esp32_firmware/README.md](esp32_firmware/README.md)
