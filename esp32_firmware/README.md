# ESP32 CSI Node Firmware

The default firmware is a **direct TCP node**, because it is the simplest path to validate and operate reliably.
It uses ESP-IDF CSI, drops the invalid first four CSI bytes when `first_word_invalid` is set, throttles output to the configured rate, queues CSI outside the callback, reconnects with backoff, and sends the ESP-IDF byte order unchanged (`imag,real`).

## Build

```bash
cd esp32_firmware
idf.py set-target esp32
idf.py menuconfig
# WiFiSense Ultra -> set SSID, password, server IP, node ID, optional ingest token and target rate
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Use ESP-IDF 5.x for the classic ESP32 target. For other ESP32 families, verify CSI fields against that chip's current ESP-IDF documentation before flashing.

## Mesh

`mesh_reference/` explains the supported ESP-WIFI-MESH architecture and the additional validation needed before using it. The release does **not** falsely label the direct-TCP firmware as mesh. A mesh deployment must validate root forwarding, packet size/fragmentation, channel behavior, and recovery on the exact ESP-IDF/chip combination.

If the server sets `WIFISENSE_INGEST_TOKEN`, configure the same token in `idf.py menuconfig`. This authenticates the node handshake but does **not** encrypt TCP; keep CSI ingest on a trusted network or protected tunnel.
