"""CSI phase cleaning with variable-length safety.

Phase is unwrapped and its best-fit linear slope is removed. This compensates
for common packet timing/frequency offsets without pretending to recover an
absolute physical phase reference.
"""
from __future__ import annotations

import numpy as np


class PhaseSanitizer:
    def __init__(self, subcarriers=64):
        self.subcarriers = int(subcarriers)

    @staticmethod
    def unwrap(phase):
        arr = np.asarray(phase, dtype=np.float64)
        if arr.ndim != 1:
            arr = arr.reshape(-1)
        return np.unwrap(arr)

    def remove_linear_trend(self, phase):
        phase = np.asarray(phase, dtype=np.float64).reshape(-1)
        if phase.size < 2:
            return phase.tolist(), 0.0, float(phase[0]) if phase.size else 0.0
        k = np.arange(phase.size, dtype=np.float64)
        A = np.vstack([k, np.ones(phase.size)]).T
        slope, intercept = np.linalg.lstsq(A, phase, rcond=None)[0]
        cleaned = phase - (slope * k + intercept)
        return cleaned.tolist(), float(slope), float(intercept)

    def sanitize(self, phase):
        unwrapped = self.unwrap(phase)
        cleaned, slope, intercept = self.remove_linear_trend(unwrapped)
        return {
            "unwrapped": unwrapped.tolist(),
            "cleaned": cleaned,
            "slope": slope,
            "intercept": intercept,
        }

    def compute_phase_difference(self, phase1, phase2):
        p1 = self.unwrap(phase1)
        p2 = self.unwrap(phase2)
        n = min(p1.size, p2.size)
        if n == 0:
            return []
        return np.angle(np.exp(1j * (p1[:n] - p2[:n]))).tolist()

    def detect_phase_anomaly(self, phase_history):
        if len(phase_history) < 10:
            return 0.0
        rows = []
        target = min(len(np.asarray(p).reshape(-1)) for p in phase_history[-20:])
        if target < 2:
            return 0.0
        for phase in phase_history[-20:]:
            rows.append(self.unwrap(phase)[:target])
        return float(np.mean(np.var(np.asarray(rows), axis=0)))
