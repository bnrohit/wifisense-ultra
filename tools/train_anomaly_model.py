#!/usr/bin/env python3
"""Train a per-node optional CSI autoencoder from persisted normal-room snapshots."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from wifisense_ultra import config
from wifisense_ultra.dl.autoencoder import AnomalyDetector, TORCH_AVAILABLE


def load_snapshots(db_path: str, node_id: str, limit: int):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT amplitude FROM csi_snapshots WHERE node_id=? ORDER BY timestamp DESC LIMIT ?",
            (node_id, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    samples = []
    for (payload,) in reversed(rows):
        vector = json.loads(payload)
        if isinstance(vector, list) and len(vector) == config.SUBCARRIERS:
            samples.append(vector)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description="Train an optional per-node anomaly autoencoder")
    parser.add_argument("node_id")
    parser.add_argument("--db", default=config.DB_PATH)
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--force", action="store_true", help="acknowledge that the selected snapshots are known normal-room data")
    args = parser.parse_args()

    if not args.force:
        print("Refusing to train without --force. Only use verified NORMAL-room snapshots; motion/falls will contaminate the model.")
        return 2
    if not TORCH_AVAILABLE:
        print("PyTorch is not installed. Install requirements-ml.txt first.")
        return 2
    if not Path(args.db).exists():
        print(f"Database not found: {args.db}")
        return 2
    samples = load_snapshots(args.db, args.node_id, args.limit)
    if len(samples) < max(50, config.ANOMALY_MIN_BASELINE):
        print(f"Need at least {max(50, config.ANOMALY_MIN_BASELINE)} normal snapshots; found {len(samples)}")
        return 2

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.node_id)
    model_path = str(Path(config.ANOMALY_MODEL_DIR) / f"{safe}_autoencoder.pt")
    detector = AnomalyDetector(config.SUBCARRIERS, model_path, config.ANOMALY_PERCENTILE)
    result = detector.train(samples, epochs=args.epochs)
    print(json.dumps({"node_id": args.node_id, "model": model_path, "samples": len(samples), **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
