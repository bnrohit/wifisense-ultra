"""Optional PyTorch autoencoder plus an always-available statistical anomaly baseline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import numpy as np

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except Exception:  # pragma: no cover - exercised on minimal installs
    torch = None
    nn = None
    TORCH_AVAILABLE = False


if TORCH_AVAILABLE:
    class CSIAutoencoder(nn.Module):
        def __init__(self, input_dim=64, latent_dim=8):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Linear(input_dim, 48), nn.ReLU(),
                nn.Linear(48, 32), nn.ReLU(),
                nn.Linear(32, latent_dim),
            )
            self.decoder = nn.Sequential(
                nn.Linear(latent_dim, 32), nn.ReLU(),
                nn.Linear(32, 48), nn.ReLU(),
                nn.Linear(48, input_dim),
            )

        def forward(self, x):
            return self.decoder(self.encoder(x))
else:
    class CSIAutoencoder:  # pragma: no cover
        pass


class AnomalyDetector:
    def __init__(self, input_dim=64, model_path=None, percentile=99.0):
        self.input_dim = int(input_dim)
        self.model_path = str(model_path) if model_path else None
        self.percentile = float(percentile)
        self.baseline_mean = None
        self.baseline_std = None
        self.stat_threshold = None
        self.torch_available = TORCH_AVAILABLE
        self.device = None
        self.model = None
        self.neural_trained = False
        self.neural_threshold = None

        if TORCH_AVAILABLE:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.model = CSIAutoencoder(self.input_dim).to(self.device)
            self._load_model()

    @property
    def ready(self):
        return self.stat_threshold is not None

    def _metadata_path(self):
        return f"{self.model_path}.json" if self.model_path else None

    def fit_baseline(self, normal_data):
        X = np.asarray(normal_data, dtype=np.float64)
        if X.ndim != 2 or X.shape[0] < 5:
            raise ValueError("Need multiple normal CSI frames to fit anomaly baseline")
        if X.shape[1] != self.input_dim:
            raise ValueError(f"Expected {self.input_dim} subcarriers, got {X.shape[1]}")
        self.baseline_mean = np.mean(X, axis=0)
        self.baseline_std = np.std(X, axis=0)
        self.baseline_std[self.baseline_std < 1e-3] = 1e-3
        scores = np.mean(np.square((X - self.baseline_mean) / self.baseline_std), axis=1)
        self.stat_threshold = float(max(1.0, np.percentile(scores, self.percentile) * 1.2))
        return {
            "mode": "statistical",
            "threshold": self.stat_threshold,
            "baseline_samples": int(X.shape[0]),
        }

    def train(self, normal_data, epochs=40, lr=1e-3):
        """Train the optional neural model. Statistical detection stays active regardless."""
        baseline = self.fit_baseline(normal_data)
        if not TORCH_AVAILABLE:
            return {**baseline, "neural": "unavailable", "reason": "PyTorch not installed"}
        X_np = np.asarray(normal_data, dtype=np.float32)
        X = torch.tensor(X_np, dtype=torch.float32, device=self.device)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=float(lr))
        criterion = nn.MSELoss()
        self.model.train()
        for _ in range(max(1, int(epochs))):
            optimizer.zero_grad(set_to_none=True)
            reconstruction = self.model(X)
            loss = criterion(reconstruction, X)
            loss.backward()
            optimizer.step()
        self.model.eval()
        with torch.no_grad():
            errors = torch.mean((self.model(X) - X) ** 2, dim=1).cpu().numpy()
        self.neural_threshold = float(np.percentile(errors, self.percentile) * 1.2)
        self.neural_trained = True
        self._save_model()
        return {
            **baseline,
            "neural": "trained",
            "neural_threshold": self.neural_threshold,
            "mean_reconstruction_error": float(np.mean(errors)),
        }

    def _save_model(self):
        if not (self.model_path and TORCH_AVAILABLE and self.neural_trained):
            return
        Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"state_dict": self.model.state_dict(), "input_dim": self.input_dim}, self.model_path)
        meta = {"neural_threshold": self.neural_threshold}
        with open(self._metadata_path(), "w", encoding="utf-8") as f:
            json.dump(meta, f)

    def _load_model(self):
        if not (self.model_path and TORCH_AVAILABLE and os.path.exists(self.model_path)):
            return
        try:
            payload = torch.load(self.model_path, map_location=self.device, weights_only=False)
            state = payload.get("state_dict", payload) if isinstance(payload, dict) else payload
            self.model.load_state_dict(state)
            self.model.eval()
            meta_path = self._metadata_path()
            if meta_path and os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    self.neural_threshold = float(json.load(f).get("neural_threshold"))
            self.neural_trained = self.neural_threshold is not None
        except Exception:
            self.neural_trained = False

    def detect(self, amplitude_vector):
        x = np.asarray(amplitude_vector, dtype=np.float64).reshape(-1)
        if x.size != self.input_dim:
            return {"score": 0.0, "is_anomaly": False, "threshold": None, "mode": "unavailable"}

        stat_score = None
        stat_flag = False
        if self.ready:
            z = (x - self.baseline_mean) / self.baseline_std
            stat_score = float(np.mean(np.square(z)))
            stat_flag = stat_score > self.stat_threshold

        neural_score = None
        neural_flag = False
        if TORCH_AVAILABLE and self.neural_trained:
            tx = torch.from_numpy(x.astype(np.float32, copy=False)).unsqueeze(0).to(self.device)
            self.model.eval()
            with torch.no_grad():
                neural_score = float(torch.mean((self.model(tx) - tx) ** 2).cpu().item())
            neural_flag = neural_score > self.neural_threshold

        if neural_score is not None:
            return {
                "score": neural_score,
                "is_anomaly": bool(neural_flag or stat_flag),
                "threshold": self.neural_threshold,
                "mode": "autoencoder+statistical",
                "statistical_score": stat_score,
                "statistical_threshold": self.stat_threshold,
            }
        return {
            "score": stat_score if stat_score is not None else 0.0,
            "is_anomaly": bool(stat_flag),
            "threshold": self.stat_threshold,
            "mode": "statistical" if self.ready else "calibrating",
        }
