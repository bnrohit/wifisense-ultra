# Changelog

## 1.0.1 — 2026-08-15

- Added automatic project `.env` loading while preserving explicit shell/container environment precedence.
- Added a complete real-time deployment/commissioning guide for end users.
- Added WSL/Linux command guidance to avoid `python` vs `python3` confusion.
- Clarified minimum hardware, multi-node positioning limits, 24/7 hosting, security, calibration, and acceptance tests.

## 1.0.0 — 2026-08-14

- Rebuilt CSI protocol around ESP-IDF imaginary/real ordering and variable-length normalization.
- Fixed Butterworth Nyquist crash.
- Added robust phase sanitization.
- Added quiet-only adaptive background learning.
- Reworked fall detection into a per-node spike → stillness state machine.
- Prevented ordinary movement → stop regression from triggering a fall.
- Added conservative multi-node RF fusion and calibrated-only metric multilateration/3D.
- Added bounded STFT and non-medical micro-motion bands.
- Added statistical anomaly baseline with optional PyTorch autoencoder.
- Disabled random untrained LSTM predictions; added training workflow and HMM smoothing.
- Added optional MQTT/Home Assistant discovery with availability.
- Added SQLite WAL, retention, node-coordinate persistence, and deterministic connection closing.
- Added resilient TCP/engine worker supervision, health/readiness APIs, SSE/poll fallback.
- Rebuilt branded dashboard for Rohith Bommalla Naresh.
- Corrected Docker entrypoint and production WSGI path.
- Replaced misleading mesh firmware claim with reliable direct-TCP firmware plus a mesh reference architecture.
- Added deterministic and TCP simulators plus regression tests.
