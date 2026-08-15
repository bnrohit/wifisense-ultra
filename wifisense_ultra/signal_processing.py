"""Advanced CSI signal processing: Hampel, PCA, Butterworth, FFT."""
import numpy as np
from scipy import signal as scipy_signal
from scipy.ndimage import median_filter
from sklearn.decomposition import PCA
from collections import deque
import time

class SignalProcessor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.subcarriers = cfg.SUBCARRIERS
        self.bg_mean = None
        self.bg_std = None
        self.bg_var = None
        self.calibrated = False
        self.calib_buffer = deque(maxlen=cfg.CALIBRATION_FRAMES)

        # Butterworth band-pass
        nyq = 0.5 * cfg.SAMPLE_RATE
        # Keep normalized cutoffs strictly inside (0, 1). This makes the
        # processor robust even if an environment override lands on Nyquist.
        low_hz = max(1e-4, min(float(cfg.BUTTER_LOW), nyq * 0.95))
        high_hz = max(low_hz + 1e-4, min(float(cfg.BUTTER_HIGH), nyq * 0.99))
        low = low_hz / nyq
        high = high_hz / nyq
        self.b_butter, self.a_butter = scipy_signal.butter(
            cfg.BUTTER_ORDER, [low, high], btype='band'
        )

        # PCA
        self.pca = PCA(n_components=cfg.PCA_COMPONENTS)
        self.pca_fitted = False

        # History for filtering
        self.amp_history = deque(maxlen=cfg.CALIBRATION_FRAMES)
        self.phase_history = deque(maxlen=cfg.CALIBRATION_FRAMES)

    def hampel_filter(self, data, window=None, n_sigma=None):
        """Hampel outlier filter on 1D array."""
        if window is None:
            window = self.cfg.HAMPEL_WINDOW
        if n_sigma is None:
            n_sigma = self.cfg.HAMPEL_N_SIGMA

        data = np.asarray(data, dtype=np.float64)
        out = data.copy()
        pad = window // 2

        for i in range(len(data)):
            start = max(0, i - pad)
            end = min(len(data), i + pad + 1)
            window_data = data[start:end]
            med = np.median(window_data)
            mad = np.median(np.abs(window_data - med))
            threshold = n_sigma * 1.4826 * mad
            if np.abs(data[i] - med) > threshold:
                out[i] = med
        return out

    def apply_pca_denoise(self, amplitude_matrix):
        """Denoise using PCA without learning a degenerate empty-room basis."""
        if amplitude_matrix.shape[0] < self.cfg.PCA_COMPONENTS * 2:
            return amplitude_matrix

        # A perfectly/static calibration matrix has no useful PCA basis.
        # Fitting it can later project real motion back to the empty-room
        # signal and erase the event. Wait until measurable variance exists.
        if float(np.var(amplitude_matrix)) < 1e-8:
            return amplitude_matrix

        if not self.pca_fitted:
            self.pca.fit(amplitude_matrix)
            self.pca_fitted = True

        transformed = self.pca.transform(amplitude_matrix)
        reconstructed = self.pca.inverse_transform(transformed)
        return reconstructed

    def bandpass_filter(self, sig):
        """Apply Butterworth band-pass filter."""
        if len(sig) < self.cfg.BUTTER_ORDER * 3:
            return sig
        try:
            filtered = scipy_signal.filtfilt(self.b_butter, self.a_butter, sig)
            return filtered
        except Exception:
            return sig

    def detect_peak_frequency(self, sig, sample_rate=None):
        """Detect dominant frequency via FFT."""
        if sample_rate is None:
            sample_rate = self.cfg.SAMPLE_RATE
        if len(sig) < 32:
            return None

        fft_vals = np.fft.rfft(sig)
        freqs = np.fft.rfftfreq(len(sig), d=1.0/sample_rate)
        power = np.abs(fft_vals) ** 2

        # Focus on breathing range
        mask = (freqs >= self.cfg.BREATHING_MIN_HZ) & (freqs <= self.cfg.BREATHING_MAX_HZ)
        if not mask.any():
            return None

        idx = np.argmax(power[mask])
        peak_freq = freqs[mask][idx]
        peak_power = power[mask][idx]

        return {"freq_hz": float(peak_freq), "power": float(peak_power)}

    def calibrate(self, csi_frames):
        """Build background model from empty-room frames."""
        if len(csi_frames) < self.cfg.CALIBRATION_FRAMES:
            return False

        amps = np.array([f["csi_amplitude"] for f in csi_frames[-self.cfg.CALIBRATION_FRAMES:]])
        self.bg_mean = np.mean(amps, axis=0)
        self.bg_var = np.var(amps, axis=0)
        self.bg_std = np.sqrt(self.bg_var)
        self.bg_std[self.bg_std < 1e-6] = 1e-6
        self.calibrated = True
        return True

    def update_background(self, amplitude):
        """Adapt mean/variance using an EWMA during quiet periods only."""
        if self.bg_mean is None:
            return
        alpha = self.cfg.BG_UPDATE_ALPHA
        delta = amplitude - self.bg_mean
        self.bg_mean = self.bg_mean + alpha * delta
        if self.bg_var is None:
            self.bg_var = np.maximum(self.bg_std ** 2, 1e-12)
        # Stable exponentially weighted variance update.
        self.bg_var = (1 - alpha) * (self.bg_var + alpha * (delta ** 2))
        self.bg_std = np.sqrt(np.maximum(self.bg_var, 1e-12))
        self.bg_std[self.bg_std < 1e-6] = 1e-6

    def process_frame(self, csi_frame):
        """Process a single CSI frame. Returns features or None."""
        amp = np.array(csi_frame["csi_amplitude"], dtype=np.float64)
        phase = np.array(csi_frame["csi_phase"], dtype=np.float64)

        # Hampel filter on amplitude
        amp = self.hampel_filter(amp)

        # Store for PCA
        self.amp_history.append(amp)
        self.phase_history.append(phase)

        # Background calibration
        self.calib_buffer.append({"csi_amplitude": amp, "csi_phase": phase})
        if not self.calibrated:
            if self.calibrate(list(self.calib_buffer)):
                return {"status": "calibrated"}
            return {"status": "calibrating", "progress": len(self.calib_buffer)}

        # PCA denoise if enough history
        if len(self.amp_history) >= self.cfg.PCA_COMPONENTS * 2:
            mat = np.array(list(self.amp_history)[-self.cfg.CALIBRATION_FRAMES:])
            denoised = self.apply_pca_denoise(mat)
            amp = denoised[-1]

        # Z-score vs background
        z_scores = np.abs(amp - self.bg_mean) / self.bg_std
        max_z = float(np.max(z_scores))
        mean_z = float(np.mean(z_scores))
        active = np.where(z_scores > self.cfg.MOTION_Z_THRESH)[0].tolist()

        # Never learn active motion/fall spikes into the room baseline.
        if max_z <= self.cfg.BG_UPDATE_MAX_Z:
            self.update_background(amp)

        return {
            "status": "processed",
            "amplitude": amp.tolist(),
            "phase": phase.tolist(),
            "z_scores": z_scores.tolist(),
            "max_z": max_z,
            "mean_z": mean_z,
            "active_subcarriers": active,
            "rssi": csi_frame.get("rssi", -60),
            "timestamp": csi_frame.get("timestamp", time.time()),
        }

    def extract_features(self, recent_frames):
        """Extract features from recent processed frames for classification."""
        if len(recent_frames) < 5:
            return None

        z_vals = [f["max_z"] for f in recent_frames]
        mean_z_vals = [f.get("mean_z", 0.0) for f in recent_frames]
        active_counts = [len(f["active_subcarriers"]) for f in recent_frames]
        rssis = [f["rssi"] for f in recent_frames]

        # Temporal features. Keep both window statistics and latest-frame
        # values so event detectors do not confuse an old spike with a new one.
        z_diff = np.diff(z_vals)
        recent_diff = z_diff[-4:] if len(z_diff) else []

        features = {
            "z_max": float(np.max(z_vals)),
            "z_mean": float(np.mean(z_vals)),
            "z_std": float(np.std(z_vals)),
            "z_last": float(z_vals[-1]),
            "z_prev_mean": float(np.mean(z_vals[:-1])) if len(z_vals) > 1 else 0.0,
            "mean_z_last": float(mean_z_vals[-1]),
            "mean_z_mean": float(np.mean(mean_z_vals)),
            "active_mean": float(np.mean(active_counts)),
            "active_max": float(np.max(active_counts)),
            "active_last": int(active_counts[-1]),
            "active_spread": float(np.std(active_counts)),
            "rssi_mean": float(np.mean(rssis)),
            "rssi_std": float(np.std(rssis)),
            "z_velocity": float(np.mean(np.abs(z_diff))) if len(z_diff) > 0 else 0.0,
            "z_velocity_recent": float(np.mean(np.abs(recent_diff))) if len(recent_diff) > 0 else 0.0,
        }
        return features
