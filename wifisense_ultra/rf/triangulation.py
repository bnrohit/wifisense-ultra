"""Conservative multi-node RF positioning.

Important distinction:
- RSSI reported by an ESP32 receiving the Wi-Fi packet describes the radio
  link used to acquire CSI. It is not, by itself, a metric range from a person
  to that ESP32.
- Therefore the default localization method is a CSI-strength weighted
  centroid across known sensor anchors.
- True metric least-squares multilateration is used only when observations
  include an explicit calibrated ``distance_m`` estimate for the target.
"""
from __future__ import annotations

import math
import numpy as np
from scipy.optimize import least_squares


class RFTriangulation:
    def __init__(self, room_bounds=None, max_distance=30.0):
        self.nodes = {}
        self.max_distance = float(max_distance)
        self.room_bounds = room_bounds or {"x": (0.0, 10.0), "y": (0.0, 5.0), "z": (0.0, 3.0)}

    def register_node(self, node_id, x, y, z=0.0):
        pos = np.asarray([float(x), float(y), float(z)], dtype=np.float64)
        if not np.all(np.isfinite(pos)):
            raise ValueError("Node position must contain finite numbers")
        self.nodes[str(node_id)] = pos

    def unregister_node(self, node_id):
        self.nodes.pop(str(node_id), None)

    def configured_nodes(self):
        return {nid: pos.tolist() for nid, pos in self.nodes.items()}

    def _clip(self, pos):
        x = float(np.clip(pos[0], *self.room_bounds["x"]))
        y = float(np.clip(pos[1], *self.room_bounds["y"]))
        z = None if len(pos) < 3 or pos[2] is None else float(np.clip(pos[2], *self.room_bounds["z"]))
        return x, y, z

    def weighted_centroid(self, observations):
        anchors, weights, contributors = [], [], []
        for obs in observations:
            nid = str(obs.get("node_id", ""))
            if nid not in self.nodes:
                continue
            strength = max(0.0, float(obs.get("motion_strength", obs.get("max_z", 0.0))))
            confidence = float(np.clip(obs.get("confidence", 0.5), 0.05, 1.0))
            # sqrt compresses extreme z-scores so one noisy node cannot dominate.
            weight = confidence * math.sqrt(max(strength, 0.05))
            anchors.append(self.nodes[nid])
            weights.append(weight)
            contributors.append(nid)
        if len(anchors) < 2 or sum(weights) <= 0:
            return None
        anchors = np.asarray(anchors)
        weights = np.asarray(weights)
        pos = np.average(anchors, axis=0, weights=weights)
        x, y, _ = self._clip(pos)
        spread = float(np.average(np.linalg.norm(anchors[:, :2] - np.asarray([x, y]), axis=1), weights=weights))
        confidence = float(np.clip(1.0 / (1.0 + spread / 3.0), 0.15, 0.85))
        return {
            "x": x,
            "y": y,
            "z": None,
            "confidence": confidence,
            "residual": None,
            "gdop": None,
            "method": "csi_weighted_centroid",
            "contributors": contributors,
            "metric": False,
        }

    def multilaterate(self, observations):
        anchors, distances, weights, contributors = [], [], [], []
        for obs in observations:
            nid = str(obs.get("node_id", ""))
            if nid not in self.nodes or obs.get("distance_m") is None:
                continue
            distance = float(obs["distance_m"])
            if not np.isfinite(distance) or distance <= 0 or distance > self.max_distance:
                continue
            anchors.append(self.nodes[nid])
            distances.append(distance)
            weights.append(float(np.clip(obs.get("confidence", 0.5), 0.05, 1.0)))
            contributors.append(nid)

        if len(anchors) < 3:
            return None
        anchors = np.asarray(anchors, dtype=np.float64)
        distances = np.asarray(distances, dtype=np.float64)
        weights = np.sqrt(np.asarray(weights, dtype=np.float64))

        # 3D only if at least four anchors have genuine elevation diversity.
        z_span = float(np.ptp(anchors[:, 2]))
        use_3d = len(anchors) >= 4 and z_span >= 0.25
        dims = 3 if use_3d else 2
        A = anchors[:, :dims]
        init = np.average(A, axis=0, weights=np.square(weights))

        def residuals(p):
            predicted = np.linalg.norm(A - p, axis=1)
            return weights * (predicted - distances)

        lo = np.asarray([self.room_bounds["x"][0], self.room_bounds["y"][0]] + ([self.room_bounds["z"][0]] if use_3d else []))
        hi = np.asarray([self.room_bounds["x"][1], self.room_bounds["y"][1]] + ([self.room_bounds["z"][1]] if use_3d else []))
        result = least_squares(residuals, init, bounds=(lo, hi), loss="soft_l1", max_nfev=200)
        if not result.success:
            return None
        residual = float(np.sqrt(np.mean(np.square(result.fun))))
        gdop = self._gdop(A, result.x)
        confidence = float(np.clip(math.exp(-residual / 2.0) / (1.0 + gdop / 5.0), 0.05, 0.99))
        z = float(result.x[2]) if use_3d else None
        return {
            "x": float(result.x[0]),
            "y": float(result.x[1]),
            "z": z,
            "confidence": confidence,
            "residual": residual,
            "gdop": gdop,
            "method": "calibrated_multilateration_3d" if use_3d else "calibrated_multilateration_2d",
            "contributors": contributors,
            "metric": True,
        }

    @staticmethod
    def _gdop(anchors, pos):
        if len(anchors) <= anchors.shape[1]:
            return 99.0
        rows = []
        for anchor in anchors:
            delta = pos - anchor
            d = np.linalg.norm(delta)
            if d < 1e-6:
                continue
            rows.append(delta / d)
        if len(rows) <= anchors.shape[1]:
            return 99.0
        H = np.asarray(rows)
        try:
            cov = np.linalg.inv(H.T @ H)
            return float(np.sqrt(np.trace(cov)))
        except np.linalg.LinAlgError:
            return 99.0

    def locate(self, observations):
        metric = self.multilaterate(observations)
        if metric is not None:
            return metric
        return self.weighted_centroid(observations)

    # Backward-compatible name.
    triangulate = locate
