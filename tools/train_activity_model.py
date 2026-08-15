#!/usr/bin/env python3
"""Train the optional LSTM activity classifier from dashboard-captured JSONL sequences."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from wifisense_ultra import config
from wifisense_ultra.dl.activity_lstm import ActivityClassifier, CLASSES, TORCH_AVAILABLE


def load_dataset(path: Path):
    sequences, labels = [], []
    class_to_id = {name: i for i, name in enumerate(CLASSES)}
    with path.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            raw = raw.strip()
            if not raw:
                continue
            row = json.loads(raw)
            label = str(row.get("label", "")).lower()
            if label not in class_to_id:
                raise ValueError(f"line {number}: invalid activity label {label!r}")
            seq = np.asarray(row.get("sequence"), dtype=np.float32)
            expected = (config.ACTIVITY_SEQ_LEN, config.SUBCARRIERS)
            if seq.shape != expected:
                raise ValueError(f"line {number}: sequence shape {seq.shape}, expected {expected}")
            sequences.append(seq)
            labels.append(class_to_id[label])
    return np.asarray(sequences, dtype=np.float32), np.asarray(labels, dtype=int)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", nargs="?", default=config.ACTIVITY_LABEL_PATH)
    parser.add_argument("--output", default=config.ACTIVITY_MODEL_PATH)
    parser.add_argument("--epochs", type=int, default=40)
    args = parser.parse_args()
    if not TORCH_AVAILABLE:
        print("PyTorch is not installed. Install requirements-ml.txt first.")
        return 2
    path = Path(args.labels)
    if not path.exists():
        print(f"No activity-label dataset found: {path}")
        print("Collect sequences with POST /api/label/activity while a known activity is occurring.")
        return 2
    X, y = load_dataset(path)
    counts = Counter(CLASSES[int(v)] for v in y)
    if len(X) < 12 or len(counts) < 2:
        print(f"Need >=12 sequences across >=2 classes; found {len(X)} {dict(counts)}")
        return 2
    model = ActivityClassifier(model_path=args.output, seq_len=config.ACTIVITY_SEQ_LEN, input_dim=config.SUBCARRIERS)
    result = model.train(X, y, epochs=args.epochs)
    print(json.dumps({"model": args.output, "class_counts": dict(counts), **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
