#!/usr/bin/env python3
"""Train the optional object RandomForest from JSONL feature labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from wifisense_ultra import config
from wifisense_ultra.classifier import ObjectClassifier


def load_labels(path: Path):
    X, y = [], []
    with path.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            raw = raw.strip()
            if not raw:
                continue
            row = json.loads(raw)
            if not isinstance(row.get("features"), dict) or not row.get("label"):
                raise ValueError(f"line {number}: expected label and features object")
            X.append(row["features"])
            y.append(str(row["label"]))
    return X, y


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", nargs="?", default=str(Path(config.DATA_DIR) / "object_labels.jsonl"))
    parser.add_argument("--output", default=config.CLASSIFIER_ML_PATH)
    args = parser.parse_args()
    label_path = Path(args.labels)
    if not label_path.exists():
        print(f"No object-label dataset found: {label_path}")
        print('JSONL format: {"label":"human","features":{"z_max":5.1,...}}')
        return 2
    X, y = load_labels(label_path)
    result = ObjectClassifier(config).train(X, y, save_path=args.output)
    print(json.dumps({"model": args.output, "samples": len(X), **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
