"""Thread-compatible node state and health tracking."""
from __future__ import annotations

import time
from collections import deque


class Node:
    def __init__(self, node_id, cfg):
        self.node_id = str(node_id)
        self.cfg = cfg
        self.csi_buffer = deque(maxlen=cfg.CSI_BUFFER_MAX)
        self.processed_buffer = deque(maxlen=cfg.CSI_BUFFER_MAX)
        self.last_seen = 0.0
        self.connected = False
        self.position = {"x": 0.0, "y": 0.0, "z": 0.0, "configured": False}
        self.frames_received = 0
        self.last_rssi = None
        self.last_rate = None
        self.last_timestamp = None
        self.parse_errors = 0

    def add_frame(self, frame):
        self.csi_buffer.append(frame)
        self.last_seen = time.time()
        self.connected = True
        self.frames_received += 1
        self.last_rssi = frame.get("rssi")
        self.last_rate = frame.get("rate")
        self.last_timestamp = frame.get("timestamp")

    def set_position(self, x, y, z=0.0, configured=True):
        self.position = {
            "x": float(x),
            "y": float(y),
            "z": float(z),
            "configured": bool(configured),
        }

    def is_alive(self):
        if not self.last_seen:
            return False
        return (time.time() - self.last_seen) < float(self.cfg.NODE_TIMEOUT_SEC)

    def to_dict(self):
        alive = self.is_alive()
        if not alive:
            self.connected = False
        return {
            "node_id": self.node_id,
            "connected": bool(self.connected and alive),
            "last_seen": self.last_seen,
            "age_sec": round(max(0.0, time.time() - self.last_seen), 3) if self.last_seen else None,
            "buffer_size": len(self.csi_buffer),
            "processed_buffer_size": len(self.processed_buffer),
            "frames_received": self.frames_received,
            "rssi": self.last_rssi,
            "rate": self.last_rate,
            "position": dict(self.position),
        }


class NodeManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.nodes = {}

    def get_or_create(self, node_id):
        node_id = str(node_id).strip()
        if not node_id:
            raise ValueError("node_id cannot be empty")
        if node_id not in self.nodes:
            self.nodes[node_id] = Node(node_id, self.cfg)
        return self.nodes[node_id]

    def get_all(self):
        return list(self.nodes.values())

    def get_all_dicts(self):
        return [n.to_dict() for n in sorted(self.nodes.values(), key=lambda x: x.node_id)]

    def cleanup(self):
        for node in self.nodes.values():
            if not node.is_alive():
                node.connected = False

    def connected_count(self):
        return sum(1 for node in self.nodes.values() if node.is_alive())
