# WiFiSense Ultra 1.0.1 — Validation Report

**Release date:** 2026-08-14 (America/Denver)  
**Brand:** Rohith Bommalla Naresh

## Build environment

- Python 3.13.5
- NumPy 2.3.5
- SciPy 1.17.0
- scikit-learn 1.8.0
- PyTorch 2.10.0+cpu
- Node.js available for dashboard JavaScript syntax validation
- Flask / Waitress not preinstalled
- Outbound package network unavailable
- Docker CLI unavailable
- ESP-IDF toolchain / physical ESP32 unavailable

The project targets Python 3.10+ and the Docker image uses Python 3.12.

## Automated regression suite

Command:

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
```

Result: **9/9 PASS**.

Covered:

1. ESP-IDF `imag,real` protocol round-trip and variable CSI-length normalization.
2. Node-ID validation.
3. Butterworth high cutoff remains below Nyquist.
4. Phase sanitation accepts variable CSI widths.
5. Calibrated 2D multilateration recovers a known synthetic target.
6. Default multi-node CSI fusion explicitly remains non-metric and does not invent z-height.
7. SQLite node-position persistence and structured event details.
8. Persisted node coordinates are restored onto a node after engine restart/reconnect.
9. Full calibration → anomaly baseline → motion → persistent tracking → ordinary-stop negative case → fall spike/stillness positive case → event persistence.

`ResourceWarning` was promoted to an exception; the suite passed after deterministic SQLite connection-closing was added.

## Deterministic in-process system test

Command:

```bash
python tools/inprocess_simulator.py
```

Result: **PASS**.

Observed checkpoints:

```text
[PASS] calibration + anomaly baseline node=sim_direct_1 frames=150
[PASS] motion -> persistent track #1 (rules)
[PASS] ordinary motion -> quiet does not trigger fall
[PASS] fall candidate armed phase=post_spike
[PASS] fall confirmed still=3.05s confidence=0.99
[PASS] multi-node RF fusion method=csi_weighted_centroid contributors=['sim_direct_1', 'sim_direct_2']
[PASS] untrained LSTM correctly suppressed (no random activity output)
WiFiSense Ultra in-process pipeline: ALL CHECKS PASSED
```

## Real TCP ingest integration

A `RuntimeSupervisor` TCP listener was started locally and fed by `tools/simulator.py`, using the same `NODE|...` / `CSI|...` wire path as ESP32 firmware.

Result: **PASS**.

Observed:

- 80 empty-room frames received for calibration.
- Calibration completed.
- 60 additional walking frames received.
- 140 total node frames observed.
- Persistent track created.
- Presence became active.

## CSI ingest authentication

Server was configured with `WIFISENSE_INGEST_TOKEN`.

Result: **PASS**.

- Incorrect `NODE|id|token` handshake was rejected.
- Rejected node was not created.
- Correct token was accepted.
- Authenticated simulator delivered 40 frames normally.

## Optional PyTorch smoke tests

Result: **PASS**.

### Autoencoder

- fit normal-room statistical baseline
- train 2-epoch smoke model
- save model
- reload model
- run inference
- verify combined `autoencoder+statistical` mode

### Activity LSTM

- train a small synthetic 2-class smoke dataset
- save model
- reload model
- run inference
- verify prediction maps to a defined activity class

This is a software-path test only; it is **not** an accuracy claim for real activity recognition.

## MQTT / Home Assistant payload smoke test

A fake MQTT client was used without requiring a broker.

Result: **PASS**.

- Five Home Assistant discovery entities generated.
- Discovery configuration was retained.
- Availability/device metadata paths executed without affecting the core engine.

## Dashboard validation

The embedded JavaScript from `wifisense_ultra/dashboard/index.html` was extracted and checked with:

```bash
node --check
```

Result: **PASS**.

The dashboard is included in the built Python wheel as package data.

## Python package build

Commands:

```bash
python -m pip install -e . --no-deps --no-build-isolation
python -m pip wheel . --no-deps --no-build-isolation
```

Result: **PASS**.

A `wifisense_ultra-1.0.1-py3-none-any.whl` wheel was created and verified to contain `wifisense_ultra/dashboard/index.html`.

Normal isolated `pip` build dependency resolution could not be exercised because the sandbox cannot reach PyPI. This was an environment/network limitation, not a package metadata failure.

## Static compilation

Command:

```bash
python -m compileall -q wifisense_ultra wifisense_pro tools tests
```

Result: **PASS**.

## Platform-specific validation still required

### Flask / Waitress

The web layer was syntax-checked and package-built, but Flask/Waitress could not be installed in the sandbox because they were not preinstalled and outbound package networking was unavailable.

On a deployment host run:

```bash
pip install -r requirements.txt
python -m wifisense_ultra.main
curl http://127.0.0.1:5000/api/health
curl http://127.0.0.1:5000/api/capabilities
```

### Docker

Docker was not installed in the sandbox. On the deployment host run:

```bash
docker compose config
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:5000/api/health
```

### ESP32 firmware

The firmware source was corrected against ESP-IDF CSI byte layout and callback behavior, but no ESP-IDF compiler or physical ESP32 was available here.

On hardware run:

```bash
cd esp32_firmware
idf.py set-target esp32
idf.py menuconfig
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Then confirm node connectivity and frame counts in `/api/nodes`.

## Release conclusion

The software core, fall-state regression behavior, persistence, TCP protocol, authentication, positioning math, optional neural-model execution, package build, and dashboard JavaScript all passed the available release checks.

The release should be called **software-tested / hardware-validation-required**, not medical-grade, zero-downtime-certified, or universally accurate. Real CSI behavior changes with radio hardware, room geometry, AP/channel characteristics, people, furniture, and placement, so each deployment needs calibration and field validation.

## 1.0.1 deployment validation addendum

The 2026-08-15 patch release additionally validated:

- automatic project `.env` loading without an extra Python dependency
- explicit process/container environment variables override `.env` values
- WSL/Linux invocation guidance using an activated venv or `python3`
- a complete real-time deployment/commissioning guide
- the full 9-test regression suite after these changes
- the deterministic calibration → tracking → ordinary-stop rejection → fall-confirmation simulator after these changes

