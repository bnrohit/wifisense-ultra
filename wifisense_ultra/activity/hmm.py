"""Small temporal probability smoother for trained activity predictions."""
from __future__ import annotations

from collections import deque
import numpy as np


class SimpleHMM:
    STATES = ["empty", "walking", "sitting", "standing", "falling", "sleeping"]

    def __init__(self):
        self.states = list(self.STATES)
        self.n_states = len(self.states)
        self.transitions = np.asarray([
            [0.75, 0.16, 0.03, 0.03, 0.005, 0.025],
            [0.05, 0.70, 0.07, 0.14, 0.025, 0.015],
            [0.04, 0.05, 0.72, 0.14, 0.010, 0.040],
            [0.04, 0.12, 0.15, 0.65, 0.020, 0.020],
            [0.03, 0.03, 0.18, 0.08, 0.60, 0.080],
            [0.03, 0.01, 0.04, 0.02, 0.005, 0.895],
        ], dtype=np.float64)
        self.reset()

    def reset(self):
        self.current_probs = np.asarray([0.85, 0.05, 0.025, 0.025, 0.005, 0.045], dtype=np.float64)
        self.history = deque(maxlen=20)
        self.state = "empty"

    def update(self, observation_probs):
        obs = np.asarray([max(1e-6, float(observation_probs.get(s, 0.0))) for s in self.states])
        obs /= np.sum(obs)
        pred = self.transitions.T @ self.current_probs
        posterior = obs * pred
        total = float(np.sum(posterior))
        self.current_probs = posterior / total if total > 0 else pred
        idx = int(np.argmax(self.current_probs))
        self.state = self.states[idx]
        self.history.append(self.state)
        return {
            "state": self.state,
            "confidence": float(self.current_probs[idx]),
            "distribution": {s: float(self.current_probs[i]) for i, s in enumerate(self.states)},
        }

    def stable_state(self, min_fraction=0.7, window=8):
        recent = list(self.history)[-max(1, int(window)):]
        if not recent:
            return self.state
        best = max(set(recent), key=recent.count)
        return best if recent.count(best) / len(recent) >= float(min_fraction) else self.state
