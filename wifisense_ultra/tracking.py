"""Multi-target Kalman tracker with gating and track management."""
import time
from collections import deque

import numpy as np


class KalmanTrack:
    _id_counter = 0

    def __init__(self, x, y, confidence, cfg, node_id=None):
        KalmanTrack._id_counter += 1
        self.id = KalmanTrack._id_counter
        self.cfg = cfg
        self.node_id = node_id

        self.state = np.array([x, y, 0.0, 0.0], dtype=np.float64)
        self.P = np.eye(4) * 1.0
        self.Q = np.eye(4) * cfg.KALMAN_Q
        self.Q[2, 2] *= 4
        self.Q[3, 3] *= 4
        self.R = np.eye(2) * cfg.KALMAN_R
        self.F = np.array([
            [1, 0, 1, 0],
            [0, 1, 0, 1],
            [0, 0, 1, 0],
            [0, 0, 0, 1],
        ], dtype=np.float64)
        self.H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0],
        ], dtype=np.float64)

        self.confidence = confidence
        self.age = 0
        self.hits = 1
        self.last_update = time.time()
        self.history = deque(maxlen=50)
        self.history.append((x, y, self.last_update))

    def predict(self):
        self.state = self.F @ self.state
        self.P = self.F @ self.P @ self.F.T + self.Q
        self.age += 1
        return self.state[:2]

    def update(self, z, confidence, node_id=None):
        z = np.array(z, dtype=np.float64)
        residual = z - self.H @ self.state
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.state = self.state + K @ residual
        self.P = (np.eye(4) - K @ self.H) @ self.P

        self.confidence = 0.7 * self.confidence + 0.3 * confidence
        if node_id is not None:
            self.node_id = node_id
        self.hits += 1
        self.age = 0
        self.last_update = time.time()
        self.history.append((float(self.state[0]), float(self.state[1]), self.last_update))

    def is_confirmed(self):
        return self.hits >= 3

    def is_stale(self):
        return (time.time() - self.last_update) > self.cfg.TRACK_MAX_AGE

    def to_dict(self):
        return {
            "id": self.id,
            "node_id": self.node_id,
            "x": float(self.state[0]),
            "y": float(self.state[1]),
            "vx": float(self.state[2]),
            "vy": float(self.state[3]),
            "confidence": float(self.confidence),
            "hits": self.hits,
            "age": self.age,
            "confirmed": self.is_confirmed(),
            "history": list(self.history),
        }


class MultiTargetTracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tracks = []

    def predict_all(self):
        for track in self.tracks:
            track.predict()

    def update(self, detections):
        """Update tracks from {x,y,confidence,node_id?} detections."""
        self.predict_all()

        if len(detections) == 0:
            self.tracks = [t for t in self.tracks if not t.is_stale()]
            return [t.to_dict() for t in self.tracks if t.is_confirmed()]

        if len(self.tracks) == 0:
            for d in detections:
                self.tracks.append(KalmanTrack(
                    d["x"], d["y"], d["confidence"], self.cfg, d.get("node_id")
                ))
            return [t.to_dict() for t in self.tracks if t.is_confirmed()]

        cost = np.zeros((len(detections), len(self.tracks)))
        for i, detection in enumerate(detections):
            for j, track in enumerate(self.tracks):
                pred = track.state[:2]
                dist = np.linalg.norm(np.array([detection["x"], detection["y"]]) - pred)
                if dist > self.cfg.GATING_SIGMA * 2.0:
                    cost[i, j] = 1e6
                else:
                    # Mildly prefer same-node associations when multiple nodes
                    # observe nearby targets; geometry remains the primary cost.
                    node_penalty = 0.0
                    if track.node_id and detection.get("node_id") and track.node_id != detection["node_id"]:
                        node_penalty = 0.25
                    cost[i, j] = dist + node_penalty

        assigned_det = set()
        assigned_track = set()
        flat_idx = np.argsort(cost.ravel())
        for idx in flat_idx:
            if cost.ravel()[idx] >= 1e5:
                break
            i = idx // len(self.tracks)
            j = idx % len(self.tracks)
            if i in assigned_det or j in assigned_track:
                continue
            d = detections[i]
            self.tracks[j].update([d["x"], d["y"]], d["confidence"], d.get("node_id"))
            assigned_det.add(i)
            assigned_track.add(j)

        for i, d in enumerate(detections):
            if i not in assigned_det:
                self.tracks.append(KalmanTrack(
                    d["x"], d["y"], d["confidence"], self.cfg, d.get("node_id")
                ))

        for j, track in enumerate(self.tracks):
            if j not in assigned_track and track.is_stale():
                track.confidence *= 0.5

        self.tracks = [t for t in self.tracks if t.confidence > 0.05]
        return [t.to_dict() for t in self.tracks if t.is_confirmed()]
