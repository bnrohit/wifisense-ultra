"""Breathing-rate extraction and node-keyed fall detection."""
import time
from collections import deque

import numpy as np


class VitalsMonitor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.breath_history = deque(maxlen=int(cfg.SAMPLE_RATE * 30))  # 30 sec
        self._fall_states = {}
        self._fall_last_time = {}

    def reset(self):
        """Reset transient vital/fall state, e.g. after room recalibration."""
        self.breath_history.clear()
        self._fall_states.clear()
        self._fall_last_time.clear()

    def extract_breathing_rate(self, recent_amps):
        """Extract breathing rate from amplitude variations."""
        if len(recent_amps) < 64:
            return None

        sig = np.array(recent_amps, dtype=np.float64)
        sig = sig - np.mean(sig)

        fft_vals = np.fft.rfft(sig)
        freqs = np.fft.rfftfreq(len(sig), d=1.0 / self.cfg.SAMPLE_RATE)
        power = np.abs(fft_vals) ** 2

        mask = (freqs >= self.cfg.BREATHING_MIN_HZ) & (freqs <= self.cfg.BREATHING_MAX_HZ)
        if not mask.any():
            return None

        idx = np.argmax(power[mask])
        peak_freq = freqs[mask][idx]
        band_power = power[mask]
        confidence = float(band_power[idx] / (np.sum(band_power) + 1e-6))

        return {
            "breaths_per_min": round(float(peak_freq * 60), 1),
            "freq_hz": round(float(peak_freq), 3),
            "confidence": round(confidence, 3),
        }

    def _new_fall_state(self):
        return {
            "phase": "idle",
            "spike_time": None,
            "spike_z": 0.0,
            "still_since": None,
            "last_z": 0.0,
            "last_mean_z": 0.0,
        }

    def _reset_candidate(self, state):
        state.update(self._new_fall_state())

    def get_fall_states(self, now=None):
        """Return safe, serializable detector state for API/diagnostics."""
        now = time.time() if now is None else now
        out = {}
        for node_id, state in self._fall_states.items():
            still_for = 0.0
            if state.get("still_since") is not None:
                still_for = max(0.0, now - state["still_since"])
            out[node_id] = {
                "phase": state.get("phase", "idle"),
                "spike_z": round(float(state.get("spike_z", 0.0)), 2),
                "still_for_sec": round(float(still_for), 2),
                "last_z": round(float(state.get("last_z", 0.0)), 2),
                "last_mean_z": round(float(state.get("last_mean_z", 0.0)), 2),
            }
        return out

    def detect_fall(self, node_id, node_features, now=None, track_context=None):
        """Detect a fall using a per-node spike -> stillness state machine.

        The detector deliberately does not depend on an active track. A person
        can fall and become still, causing motion detections (and eventually the
        Kalman track) to disappear before the stillness window completes.

        Args:
            node_id: Stable sensor/node identifier.
            node_features: SignalProcessor.extract_features() output.
            now: Optional timestamp for deterministic/in-process testing.
            track_context: Optional last known track for position attribution.
        """
        if not node_id or not node_features:
            return None

        now = time.time() if now is None else float(now)
        state = self._fall_states.setdefault(node_id, self._new_fall_state())
        last_event = self._fall_last_time.get(node_id, 0.0)

        z_last = float(node_features.get("z_last", node_features.get("z_max", 0.0)))
        z_prev_mean = float(node_features.get("z_prev_mean", node_features.get("z_mean", 0.0)))
        mean_z_last = float(node_features.get("mean_z_last", node_features.get("z_mean", 0.0)))
        z_velocity = float(node_features.get("z_velocity_recent", node_features.get("z_velocity", 0.0)))
        active_last = int(node_features.get("active_last", node_features.get("active_max", 0)))

        state["last_z"] = z_last
        state["last_mean_z"] = mean_z_last

        # During cooldown, keep diagnostics current but do not start candidates.
        if now - last_event < self.cfg.FALL_COOLDOWN_SEC:
            state["phase"] = "cooldown"
            return None
        elif state.get("phase") == "cooldown":
            self._reset_candidate(state)

        # Arm only on a sudden scale-independent spike, not merely sustained
        # high motion. Once armed, a later high frame can increase spike_z but
        # does not restart the confirmation clock indefinitely.
        ratio_base = max(z_prev_mean, 1e-6)
        spike_ratio = z_last / ratio_base
        if state.get("spike_time") is None:
            if (
                z_last >= self.cfg.FALL_AMP_SPIKE_Z
                and spike_ratio >= self.cfg.FALL_SPIKE_RATIO
                and z_velocity >= self.cfg.FALL_SPIKE_VELOCITY_Z
            ):
                state["phase"] = "post_spike"
                state["spike_time"] = now
                state["spike_z"] = z_last
                state["still_since"] = None
            else:
                state["phase"] = "idle"
            return None

        if z_last >= self.cfg.FALL_AMP_SPIKE_Z:
            state["spike_z"] = max(float(state.get("spike_z", 0.0)), z_last)

        # Candidate expired without sufficient stillness.
        if now - state["spike_time"] > self.cfg.FALL_CONFIRM_WINDOW_SEC:
            self._reset_candidate(state)
            return None

        is_still = (
            mean_z_last <= self.cfg.FALL_STILLNESS_Z
            and z_velocity <= self.cfg.FALL_STILLNESS_VELOCITY_Z
            and active_last <= self.cfg.FALL_MAX_ACTIVE_SUBCARRIERS
        )

        if not is_still:
            state["phase"] = "post_spike"
            state["still_since"] = None
            # A real fall can have a brief settling period. Sustained motion
            # after the spike is much more likely to be ordinary activity.
            if now - state["spike_time"] > self.cfg.FALL_POST_SPIKE_MOTION_GRACE_SEC:
                self._reset_candidate(state)
            return None

        if state.get("still_since") is None:
            state["still_since"] = now
            state["phase"] = "stillness"
            return None

        still_for = now - state["still_since"]
        state["phase"] = "stillness"
        if still_for < self.cfg.FALL_STILLNESS_SEC:
            return None

        # Confirm once per node, then enter cooldown.
        spike_z = float(state.get("spike_z", 0.0))
        excess = max(0.0, spike_z - self.cfg.FALL_AMP_SPIKE_Z)
        confidence = min(0.99, 0.65 + 0.04 * excess + 0.05 * min(still_for, 5.0))

        event = {
            "type": "fall_detected",
            "node_id": node_id,
            "track_id": None,
            "position": None,
            "timestamp": now,
            "z_spike": round(spike_z, 2),
            "stillness_sec": round(float(still_for), 2),
            "confidence": round(float(confidence), 3),
        }

        if track_context:
            event["track_id"] = track_context.get("id")
            if "x" in track_context and "y" in track_context:
                event["position"] = {
                    "x": round(float(track_context["x"]), 3),
                    "y": round(float(track_context["y"]), 3),
                    "z": (round(float(track_context["z"]), 3) if track_context.get("z") is not None else None),
                }

        self._fall_last_time[node_id] = now
        self._reset_candidate(state)
        state["phase"] = "cooldown"
        return event
