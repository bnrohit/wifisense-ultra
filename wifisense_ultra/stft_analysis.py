"""Bounded STFT analysis for visualization and non-medical micro-motion cues."""
from __future__ import annotations

from collections import deque
import numpy as np
from scipy import signal as scipy_signal


class STFTAnalyzer:
    def __init__(self, sample_rate=20.0, nperseg=64, noverlap=48, nfft=256, max_history=500):
        self.fs = float(sample_rate)
        self.nperseg = max(16, int(nperseg))
        self.noverlap = min(max(0, int(noverlap)), self.nperseg - 1)
        self.nfft = max(self.nperseg, int(nfft))
        self.history = deque(maxlen=max(self.nperseg, int(max_history)))

    def add_frame(self, amplitude):
        arr = np.asarray(amplitude, dtype=np.float64).reshape(-1)
        if arr.size:
            self.history.append(arr)

    def compute_spectrogram(self, subcarrier_idx=None, max_bins=96, max_times=80):
        if len(self.history) < self.nperseg:
            return None
        hist = list(self.history)
        min_len = min(len(row) for row in hist)
        if min_len < 2:
            return None
        matrix = np.asarray([row[:min_len] for row in hist])
        if subcarrier_idx is not None:
            idx = int(np.clip(subcarrier_idx, 0, min_len - 1))
            sig = matrix[:, idx]
        else:
            # Median across subcarriers is less dominated by a single bad bin.
            sig = np.median(matrix, axis=1)
        sig = scipy_signal.detrend(sig, type="linear")
        f, t, zxx = scipy_signal.stft(
            sig,
            fs=self.fs,
            nperseg=self.nperseg,
            noverlap=self.noverlap,
            nfft=self.nfft,
            boundary=None,
            padded=False,
        )
        magnitude = np.abs(zxx)
        # Bound JSON payload size for the live dashboard.
        f = f[:max_bins]
        magnitude = magnitude[:max_bins, -max_times:]
        t = t[-max_times:]
        return {
            "frequencies": np.round(f, 4).tolist(),
            "times": np.round(t, 4).tolist(),
            "spectrogram": np.round(magnitude, 6).tolist(),
            "subcarrier": subcarrier_idx,
        }

    def detect_micro_motion(self):
        """Return broad frequency-band energy only; not a heart-rate estimator."""
        spec = self.compute_spectrogram(max_bins=128, max_times=80)
        if spec is None:
            return []
        S = np.square(np.asarray(spec["spectrogram"], dtype=np.float64))
        freqs = np.asarray(spec["frequencies"], dtype=np.float64)
        if S.size == 0:
            return []
        mean_power = np.mean(S, axis=1)
        total = float(np.sum(mean_power)) + 1e-12
        bands = [
            ("slow_micro_motion", 0.10, 0.50),
            ("body_motion", 0.50, 2.00),
            ("fast_motion", 2.00, min(8.0, self.fs / 2.0)),
        ]
        out = []
        for name, lo, hi in bands:
            mask = (freqs >= lo) & (freqs <= hi)
            if not np.any(mask):
                continue
            power = mean_power[mask]
            out.append({
                "band": name,
                "energy_ratio": float(np.sum(power) / total),
                "peak_hz": float(freqs[mask][int(np.argmax(power))]),
            })
        return out

    # Backward-compatible API, with the medically suggestive name removed from
    # the returned band labels.
    detect_micro_doppler = detect_micro_motion
