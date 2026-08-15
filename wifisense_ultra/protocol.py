"""Wire protocol helpers for ESP32 CSI text frames.

ESP-IDF CSI bytes are interleaved as imaginary, real pairs. Incoming CSI with
variable LTF/channel lengths is resampled to the engine's fixed feature width.
"""
from __future__ import annotations

import re
import numpy as np

NODE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def validate_node_id(node_id: str) -> str:
    value = str(node_id).strip()
    if not NODE_ID_RE.fullmatch(value):
        raise ValueError("node id must be 1-64 characters: letters, numbers, . _ : -")
    return value


def _resample_complex(values: np.ndarray, target: int) -> np.ndarray:
    if values.size == target:
        return values
    if values.size == 0:
        return np.zeros(target, dtype=np.complex128)
    if values.size == 1:
        return np.full(target, values[0], dtype=np.complex128)
    old_x = np.linspace(0.0, 1.0, values.size)
    new_x = np.linspace(0.0, 1.0, target)
    amplitude = np.abs(values)
    phase = np.unwrap(np.angle(values))
    amp_i = np.interp(new_x, old_x, amplitude)
    phase_i = np.interp(new_x, old_x, phase)
    return amp_i * np.exp(1j * phase_i)


def parse_csi_packet(line: str, target_subcarriers: int = 64):
    parts = line.strip().split("|", 5)
    if len(parts) != 6 or parts[0] != "CSI":
        return None
    try:
        timestamp = int(parts[1])
        rssi = int(parts[2])
        rate = int(parts[3])
        declared_len = int(parts[4])
    except ValueError:
        return None
    if declared_len <= 0 or declared_len > 4096:
        return None
    try:
        raw = np.asarray([int(x) for x in parts[5].split(",") if x.strip() != ""], dtype=np.float64)
    except ValueError:
        return None
    if raw.size < 4 or raw.size % 2 != 0:
        return None
    # Firmware declares number of transmitted CSI bytes. Reject severely
    # malformed lines but tolerate a tiny mismatch from older firmware.
    if abs(int(raw.size) - declared_len) > 4:
        return None

    # ESP-IDF order is imaginary then real for each CSI item.
    imag = raw[0::2]
    real = raw[1::2]
    complex_csi = real + 1j * imag
    complex_csi = _resample_complex(complex_csi, int(target_subcarriers))
    return {
        "timestamp": timestamp,
        "rssi": rssi,
        "rate": rate,
        "source_subcarriers": int(real.size),
        "csi_amplitude": np.abs(complex_csi).tolist(),
        "csi_phase": np.angle(complex_csi).tolist(),
    }


def encode_csi_packet(frame, source_subcarriers=None):
    """Encode a simulator/test frame using ESP-IDF imag,real ordering."""
    amplitude = np.asarray(frame["csi_amplitude"], dtype=float)
    phase = np.asarray(frame["csi_phase"], dtype=float)
    n = min(amplitude.size, phase.size)
    c = amplitude[:n] * np.exp(1j * phase[:n])
    raw = np.empty(n * 2, dtype=np.int16)
    raw[0::2] = np.clip(np.round(c.imag), -128, 127).astype(np.int16)
    raw[1::2] = np.clip(np.round(c.real), -128, 127).astype(np.int16)
    payload = ",".join(str(int(v)) for v in raw)
    return f"CSI|{int(frame.get('timestamp', 0))}|{int(frame.get('rssi', -60))}|{int(frame.get('rate', 0))}|{len(raw)}|{payload}\n"
