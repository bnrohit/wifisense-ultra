#!/usr/bin/env python3
"""Deterministic regression tests for WiFiSense Ultra."""
from __future__ import annotations

import math
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wifisense_ultra import config as base_config
from wifisense_ultra.phase_sanitize import PhaseSanitizer
from wifisense_ultra.protocol import encode_csi_packet, parse_csi_packet, validate_node_id
from wifisense_ultra.rf.triangulation import RFTriangulation
from wifisense_ultra.signal_processing import SignalProcessor
from wifisense_ultra.storage import EventStore
from wifisense_ultra.ultra_engine import UltraEngine


def cfg_for(db_path, calibration=60):
    values = {name: getattr(base_config, name) for name in dir(base_config) if name.isupper()}
    values.update({
        "DB_PATH": db_path,
        "ANOMALY_MODEL_DIR": os.path.join(tempfile.gettempdir(), "wifisense_test_models"),
        "CALIBRATION_FRAMES": calibration,
        "ANOMALY_MIN_BASELINE": min(40, calibration),
        "MQTT_ENABLED": False,
        "CSI_SNAPSHOT_EVERY_N": 0,
        "EVENT_RETENTION_DAYS": 0,
        "CSI_RETENTION_DAYS": 0,
        "FALL_STILLNESS_SEC": 0.8,
        "FALL_CONFIRM_WINDOW_SEC": 4.0,
    })
    return SimpleNamespace(**values)


def make_frame(cfg, rng, frame_no, mode="quiet"):
    amp = np.full(cfg.SUBCARRIERS, 30.0) + rng.normal(0, 0.35, cfg.SUBCARRIERS)
    phase = np.linspace(-np.pi, np.pi, cfg.SUBCARRIERS) + rng.normal(0, 0.02, cfg.SUBCARRIERS)
    if mode in {"motion", "spike"}:
        center = 20 + frame_no % 20
        magnitude = 28.0 if mode == "spike" else 10.0 + 2.0 * math.sin(frame_no * .2)
        for idx in range(max(0, center - 8), min(cfg.SUBCARRIERS, center + 8)):
            factor = math.exp(-abs(idx - center) / 3.0) * magnitude
            amp[idx] += factor
            phase[idx] += factor * 0.03
    return {
        "timestamp": frame_no / cfg.SAMPLE_RATE,
        "rssi": -50,
        "rate": 54,
        "csi_amplitude": amp.tolist(),
        "csi_phase": phase.tolist(),
    }


class ProtocolTests(unittest.TestCase):
    def test_imag_real_round_trip_and_resample(self):
        amp = np.linspace(10, 40, 56)
        phase = np.linspace(-2, 2, 56)
        frame = {"timestamp": 123, "rssi": -44, "rate": 54, "csi_amplitude": amp.tolist(), "csi_phase": phase.tolist()}
        line = encode_csi_packet(frame)
        parsed = parse_csi_packet(line, 64)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["source_subcarriers"], 56)
        self.assertEqual(len(parsed["csi_amplitude"]), 64)
        self.assertAlmostEqual(parsed["rssi"], -44)

    def test_node_id_validation(self):
        self.assertEqual(validate_node_id("room-1.node_2"), "room-1.node_2")
        with self.assertRaises(ValueError):
            validate_node_id("bad node id")


class SignalTests(unittest.TestCase):
    def test_filter_never_uses_nyquist(self):
        db = os.path.join(tempfile.gettempdir(), "ultra_filter_test.db")
        cfg = cfg_for(db)
        cfg.BUTTER_HIGH = cfg.SAMPLE_RATE / 2.0
        proc = SignalProcessor(cfg)
        self.assertIsNotNone(proc.b_butter)

    def test_phase_variable_length(self):
        sanitizer = PhaseSanitizer(64)
        out = sanitizer.sanitize(np.linspace(-4, 5, 57))
        self.assertEqual(len(out["cleaned"]), 57)
        self.assertTrue(np.isfinite(out["slope"]))


class PositioningTests(unittest.TestCase):
    def test_metric_2d_multilateration_with_calibrated_distances(self):
        tri = RFTriangulation({"x": (0, 10), "y": (0, 10), "z": (0, 3)})
        anchors = {"a": (0, 0, 1), "b": (10, 0, 1), "c": (0, 10, 1), "d": (10, 10, 1)}
        for nid, pos in anchors.items():
            tri.register_node(nid, *pos)
        target = np.array([3.0, 4.0])
        obs = []
        for nid, pos in anchors.items():
            d = float(np.linalg.norm(np.asarray(pos[:2]) - target))
            obs.append({"node_id": nid, "distance_m": d, "confidence": 1.0})
        result = tri.locate(obs)
        self.assertTrue(result["metric"])
        self.assertAlmostEqual(result["x"], 3.0, places=2)
        self.assertAlmostEqual(result["y"], 4.0, places=2)

    def test_default_fusion_does_not_claim_metric(self):
        tri = RFTriangulation({"x": (0, 10), "y": (0, 5), "z": (0, 3)})
        tri.register_node("a", 0, 2.5, 1)
        tri.register_node("b", 10, 2.5, 1)
        result = tri.locate([
            {"node_id": "a", "max_z": 8, "confidence": .8},
            {"node_id": "b", "max_z": 4, "confidence": .8},
        ])
        self.assertFalse(result["metric"])
        self.assertEqual(result["method"], "csi_weighted_centroid")
        self.assertIsNone(result["z"])


class StorageTests(unittest.TestCase):
    def test_node_position_persists(self):
        path = os.path.join(tempfile.gettempdir(), "ultra_storage_test.db")
        for suffix in ("", "-wal", "-shm"):
            try: os.remove(path + suffix)
            except FileNotFoundError: pass
        store = EventStore(path)
        store.save_node_position("n1", 1.0, 2.0, 1.2)
        loaded = store.load_node_positions()
        self.assertEqual(loaded[0]["node_id"], "n1")
        store.log_event("x", details={"ok": True})
        self.assertEqual(store.get_recent_events(1)[0]["details"]["ok"], True)

    def test_engine_restores_persisted_node_position(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = cfg_for(os.path.join(td, "persist.db"))
            first = UltraEngine(cfg)
            first.set_node_position("persisted_node", 2.2, 1.7, 1.1)
            second = UltraEngine(cfg)
            node = second.ensure_node("persisted_node")
            self.assertTrue(node.position["configured"])
            self.assertAlmostEqual(node.position["x"], 2.2)
            self.assertAlmostEqual(node.position["y"], 1.7)
            self.assertAlmostEqual(node.position["z"], 1.1)


class EngineTests(unittest.TestCase):
    def test_full_pipeline_and_fall(self):
        path = os.path.join(tempfile.gettempdir(), "ultra_engine_test.db")
        for suffix in ("", "-wal", "-shm"):
            try: os.remove(path + suffix)
            except FileNotFoundError: pass
        cfg = cfg_for(path, calibration=60)
        engine = UltraEngine(cfg)
        node = "test_node"
        engine.set_node_position(node, 1, 2, 1)
        rng = np.random.default_rng(77)
        t = 100.0
        dt = 1.0 / cfg.SAMPLE_RATE
        frame_no = 0
        for _ in range(cfg.CALIBRATION_FRAMES + 5):
            engine.ingest(node, make_frame(cfg, rng, frame_no, "quiet")); engine.tick(now=t)
            t += dt; frame_no += 1
        self.assertTrue(engine.calibrated)
        self.assertTrue(engine.get_anomaly(node).ready)
        state = None
        for _ in range(20):
            engine.ingest(node, make_frame(cfg, rng, frame_no, "motion")); state = engine.tick(now=t)
            t += dt; frame_no += 1
        self.assertTrue(state["presence"])
        self.assertTrue(state["tracks"])

        # Motion ending normally must not be a fall.
        false_fall = None
        for _ in range(int((cfg.FALL_STILLNESS_SEC + .5) * cfg.SAMPLE_RATE)):
            engine.ingest(node, make_frame(cfg, rng, frame_no, "quiet")); state = engine.tick(now=t)
            false_fall = state.get("fall") or false_fall
            t += dt; frame_no += 1
        self.assertIsNone(false_fall)

        for _ in range(12):
            engine.ingest(node, make_frame(cfg, rng, frame_no, "motion")); engine.tick(now=t)
            t += dt; frame_no += 1
        engine.ingest(node, make_frame(cfg, rng, frame_no, "spike")); armed = engine.tick(now=t)
        self.assertIn(armed["fall_states"][node]["phase"], {"post_spike", "stillness"})
        t += dt; frame_no += 1
        fall = None
        for _ in range(int((cfg.FALL_STILLNESS_SEC + 1.0) * cfg.SAMPLE_RATE)):
            engine.ingest(node, make_frame(cfg, rng, frame_no, "quiet")); state = engine.tick(now=t)
            if state.get("fall"):
                fall = state["fall"]; break
            t += dt; frame_no += 1
        self.assertIsNotNone(fall)
        self.assertTrue(engine.store.get_recent_events(10, "fall_detected"))
        self.assertFalse(engine.activity_lstm.trained)
        self.assertNotEqual(state["activity"].get("source"), "trained_lstm+hmm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
