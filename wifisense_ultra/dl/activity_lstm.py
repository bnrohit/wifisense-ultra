"""Optional trained LSTM activity classifier.

No random/untrained predictions are ever exposed to the application.
"""
from __future__ import annotations

import os
from pathlib import Path
import numpy as np

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except Exception:  # pragma: no cover
    torch = None
    nn = None
    TORCH_AVAILABLE = False

CLASSES = ["empty", "walking", "sitting", "standing", "falling", "sleeping"]

if TORCH_AVAILABLE:
    class ActivityLSTM(nn.Module):
        def __init__(self, input_dim=64, hidden_dim=48, num_layers=2, num_classes=len(CLASSES)):
            super().__init__()
            self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True, dropout=0.2)
            self.classifier = nn.Sequential(nn.Linear(hidden_dim, 24), nn.ReLU(), nn.Linear(24, num_classes))

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.classifier(out[:, -1, :])
else:
    class ActivityLSTM:  # pragma: no cover
        pass


class ActivityClassifier:
    def __init__(self, model_path=None, seq_len=50, input_dim=64):
        self.model_path = str(model_path) if model_path else None
        self.seq_len = int(seq_len)
        self.input_dim = int(input_dim)
        self.torch_available = TORCH_AVAILABLE
        self.trained = False
        self.device = None
        self.model = None
        if TORCH_AVAILABLE:
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.model = ActivityLSTM(input_dim=self.input_dim).to(self.device)
            self._load()

    def _normalize(self, sequences):
        X = np.asarray(sequences, dtype=np.float32)
        # Per-sequence normalization prevents absolute CSI scale from dominating.
        mean = X.mean(axis=(1, 2), keepdims=True)
        std = X.std(axis=(1, 2), keepdims=True)
        std[std < 1e-3] = 1.0
        return (X - mean) / std

    def _load(self):
        if not (TORCH_AVAILABLE and self.model_path and os.path.exists(self.model_path)):
            return
        try:
            payload = torch.load(self.model_path, map_location=self.device, weights_only=False)
            state = payload.get("state_dict", payload) if isinstance(payload, dict) else payload
            self.model.load_state_dict(state)
            self.model.eval()
            self.trained = True
        except Exception:
            self.trained = False

    def train(self, X_sequences, y_labels, epochs=30, lr=1e-3):
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch is not installed; install the ml extra first")
        X_np = np.asarray(X_sequences, dtype=np.float32)
        y_np = np.asarray(y_labels)
        if X_np.ndim != 3 or X_np.shape[1:] != (self.seq_len, self.input_dim):
            raise ValueError(f"Expected shape (N,{self.seq_len},{self.input_dim})")
        if len(X_np) != len(y_np) or len(X_np) < 12:
            raise ValueError("Need at least 12 labeled sequences")
        if len(set(map(int, y_np.tolist()))) < 2:
            raise ValueError("Need at least two activity classes")
        X = torch.tensor(self._normalize(X_np), dtype=torch.float32, device=self.device)
        y = torch.tensor(y_np.astype(int), dtype=torch.long, device=self.device)
        optimizer = torch.optim.Adam(self.model.parameters(), lr=float(lr))
        criterion = nn.CrossEntropyLoss()
        self.model.train()
        for _ in range(max(1, int(epochs))):
            optimizer.zero_grad(set_to_none=True)
            logits = self.model(X)
            loss = criterion(logits, y)
            loss.backward()
            optimizer.step()
        self.model.eval()
        with torch.no_grad():
            logits = self.model(X)
            acc = float((logits.argmax(dim=1) == y).float().mean().item())
        self.trained = True
        if self.model_path:
            Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
            torch.save({"state_dict": self.model.state_dict(), "classes": CLASSES, "seq_len": self.seq_len}, self.model_path)
        return {"status": "trained", "training_accuracy": acc, "samples": len(X_np)}

    def classify(self, sequence):
        if not (TORCH_AVAILABLE and self.trained):
            return None
        seq = np.asarray(sequence, dtype=np.float32)
        if seq.ndim != 2 or seq.shape[1] != self.input_dim or len(seq) < self.seq_len:
            return None
        seq = seq[-self.seq_len:]
        normalized = self._normalize(seq[None, ...])
        x = torch.tensor(normalized, dtype=torch.float32, device=self.device)
        self.model.eval()
        with torch.no_grad():
            probs = torch.softmax(self.model(x), dim=1).cpu().numpy()[0]
        pred = int(np.argmax(probs))
        return {
            "activity": CLASSES[pred],
            "confidence": float(probs[pred]),
            "probabilities": {name: float(value) for name, value in zip(CLASSES, probs)},
        }
