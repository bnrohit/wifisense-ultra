"""Object classification: rule-based fallback + optional ML."""
import os
import pickle

import numpy as np


FEATURE_KEYS = [
    "z_max",
    "z_mean",
    "z_std",
    "z_last",
    "z_prev_mean",
    "mean_z_last",
    "mean_z_mean",
    "active_mean",
    "active_max",
    "active_last",
    "active_spread",
    "rssi_mean",
    "rssi_std",
    "z_velocity",
    "z_velocity_recent",
]


class ObjectClassifier:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ml_model = None
        self.ml_scaler = None
        self.ml_feature_keys = FEATURE_KEYS
        self._load_ml()

    def _load_ml(self):
        path = os.path.abspath(self.cfg.CLASSIFIER_ML_PATH)
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
                self.ml_model = data.get("model")
                self.ml_scaler = data.get("scaler")
                self.ml_feature_keys = data.get("feature_keys", FEATURE_KEYS)
            except Exception:
                self.ml_model = None
                self.ml_scaler = None

    def vectorize(self, features, feature_keys=None):
        keys = feature_keys or self.ml_feature_keys or FEATURE_KEYS
        return [float(features.get(k, 0.0)) for k in keys]

    def classify_rule_based(self, features, active_subcarriers):
        """Heuristic classification based on signal features."""
        spread = features.get("active_spread", 0)
        count = features.get("active_max", 0)
        z_vel = features.get("z_velocity", 0)

        if count > 45 and spread > 18:
            return "large_object", 0.6
        if count > 22 and spread > 10 and z_vel > 0.3:
            return "human_moving", 0.75
        if count > 18 and spread > 8:
            return "human", 0.65
        if count > 10 and spread > 5:
            return "small_object", 0.5
        return "unknown", 0.3

    def classify(self, features, active_subcarriers):
        """Classify object type from features."""
        if features is None:
            return "unknown", 0.0

        if self.ml_model is not None and self.ml_scaler is not None:
            try:
                vec = np.array([self.vectorize(features)], dtype=float)
                vec = self.ml_scaler.transform(vec)
                pred = self.ml_model.predict(vec)[0]
                proba = np.max(self.ml_model.predict_proba(vec))
                return str(pred), float(proba)
            except Exception:
                pass

        if self.cfg.CLASSIFIER_RULES:
            return self.classify_rule_based(features, active_subcarriers)
        return "unknown", 0.0

    def train(self, X, y, save_path=None):
        """Train a RandomForest classifier using a stable feature schema.

        X may be a list of feature dictionaries or a numeric matrix. Dictionary
        input is preferred because it preserves the exact production schema.
        """
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler

        if len(X) != len(y) or len(X) < 2:
            raise ValueError("Need at least two labeled samples with matching X/y lengths")
        if len(set(y)) < 2:
            raise ValueError("Need labels from at least two classes")

        feature_keys = FEATURE_KEYS
        if isinstance(X[0], dict):
            X_arr = np.array([self.vectorize(row, feature_keys) for row in X], dtype=float)
        else:
            X_arr = np.asarray(X, dtype=float)
            feature_keys = [f"f{i}" for i in range(X_arr.shape[1])]
        y_arr = np.asarray(y)

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_arr)

        # Tiny datasets are common during first deployment. Train on all data
        # until a meaningful holdout is possible rather than failing randomly.
        if len(X_scaled) >= 10 and min(np.unique(y_arr, return_counts=True)[1]) >= 2:
            X_train, X_test, y_train, y_test = train_test_split(
                X_scaled, y_arr, test_size=0.2, random_state=42, stratify=y_arr
            )
            model = RandomForestClassifier(n_estimators=200, max_depth=12, random_state=42)
            model.fit(X_train, y_train)
            acc = float(model.score(X_test, y_test))
            eval_mode = "holdout"
        else:
            model = RandomForestClassifier(n_estimators=200, max_depth=12, random_state=42)
            model.fit(X_scaled, y_arr)
            acc = float(model.score(X_scaled, y_arr))
            eval_mode = "training_only"

        path = save_path or self.cfg.CLASSIFIER_ML_PATH
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "model": model,
                "scaler": scaler,
                "accuracy": acc,
                "feature_keys": feature_keys,
                "eval_mode": eval_mode,
            }, f)

        self.ml_model = model
        self.ml_scaler = scaler
        self.ml_feature_keys = feature_keys
        return {
            "accuracy": acc,
            "classes": list(model.classes_),
            "evaluation": eval_mode,
            "feature_keys": feature_keys,
        }
