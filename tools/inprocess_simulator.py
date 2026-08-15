#!/usr/bin/env python3
"""Deterministic no-network end-to-end validation for WiFiSense Ultra."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from wifisense_ultra import config as base_config
from wifisense_ultra.ultra_engine import UltraEngine


def build_config(db_path, fast=False):
    values = {name: getattr(base_config, name) for name in dir(base_config) if name.isupper()}
    values["DB_PATH"] = db_path
    values["ANOMALY_MODEL_DIR"] = os.path.join(tempfile.gettempdir(), "wifisense_ultra_models")
    values["MQTT_ENABLED"] = False
    values["CSI_SNAPSHOT_EVERY_N"] = 0
    values["EVENT_RETENTION_DAYS"] = 0
    values["CSI_RETENTION_DAYS"] = 0
    if fast:
        values["CALIBRATION_FRAMES"] = 80
        values["FALL_STILLNESS_SEC"] = 1.5
        values["FALL_CONFIRM_WINDOW_SEC"] = 5.0
        values["ANOMALY_MIN_BASELINE"] = 50
    return SimpleNamespace(**values)


def make_frame(rng, cfg, frame_no, mode="quiet", node_bias=0.0):
    subs = cfg.SUBCARRIERS
    amp = np.ones(subs, dtype=float) * (30.0 + node_bias)
    phase = np.linspace(-np.pi, np.pi, subs)
    amp += rng.normal(0, 0.35, subs)
    phase += rng.normal(0, 0.02, subs)

    if mode in {"motion", "fall_spike", "ordinary_stop_setup"}:
        center = 18 + (frame_no % 24)
        magnitude = 9.0 + 3.0 * np.sin(frame_no * 0.25)
        if mode == "fall_spike":
            magnitude = 30.0
        for idx in range(max(0, center - 9), min(subs, center + 9)):
            factor = np.exp(-abs(idx - center) / 3.2) * magnitude
            amp[idx] += factor
            phase[idx] += factor * 0.04

    return {
        "timestamp": frame_no / cfg.SAMPLE_RATE,
        "rssi": -48 if mode != "quiet" else -58,
        "rate": 54,
        "csi_amplitude": amp.tolist(),
        "csi_phase": phase.tolist(),
    }


def feed(engine, node_id, frame, now):
    engine.ingest(node_id, frame)
    return engine.tick(now=now)


def run_demo(fast=False, verbose=False):
    rng = np.random.default_rng(20260814)
    db_path = os.path.join(tempfile.gettempdir(), "wifisense_ultra_inprocess.db")
    try:
        os.remove(db_path)
    except FileNotFoundError:
        pass
    cfg = build_config(db_path, fast=fast)
    engine = UltraEngine(cfg)
    node = "sim_direct_1"
    engine.set_node_position(node, 1.0, 2.5, 1.2)
    sim_time = 1000.0
    dt = 1.0 / cfg.SAMPLE_RATE
    frame_no = 0

    for _ in range(cfg.CALIBRATION_FRAMES + 8):
        state = feed(engine, node, make_frame(rng, cfg, frame_no, "quiet"), sim_time)
        sim_time += dt
        frame_no += 1
    assert engine.calibrated
    assert engine.get_anomaly(node).ready
    print(f"[PASS] calibration + anomaly baseline node={node} frames={cfg.CALIBRATION_FRAMES}")

    state = None
    for _ in range(28):
        state = feed(engine, node, make_frame(rng, cfg, frame_no, "motion"), sim_time)
        sim_time += dt
        frame_no += 1
    assert state["presence"] and state["tracks"]
    assert state["tracks"][0]["position_method"] == "single_node_signal_map"
    print(f"[PASS] motion -> persistent track #{state['tracks'][0]['id']} ({state['activity']['source']})")

    false_fall = None
    for _ in range(int((cfg.FALL_STILLNESS_SEC + 0.8) * cfg.SAMPLE_RATE)):
        state = feed(engine, node, make_frame(rng, cfg, frame_no, "quiet"), sim_time)
        false_fall = state.get("fall") or false_fall
        sim_time += dt
        frame_no += 1
    assert false_fall is None
    print("[PASS] ordinary motion -> quiet does not trigger fall")

    for _ in range(18):
        feed(engine, node, make_frame(rng, cfg, frame_no, "motion"), sim_time)
        sim_time += dt
        frame_no += 1

    spike_state = feed(engine, node, make_frame(rng, cfg, frame_no, "fall_spike"), sim_time)
    sim_time += dt
    frame_no += 1
    phase = spike_state["fall_states"].get(node, {}).get("phase")
    assert phase in {"post_spike", "stillness"}, phase
    print(f"[PASS] fall candidate armed phase={phase}")

    fall = None
    for _ in range(int((cfg.FALL_STILLNESS_SEC + 1.5) * cfg.SAMPLE_RATE)):
        state = feed(engine, node, make_frame(rng, cfg, frame_no, "quiet"), sim_time)
        if verbose:
            print(state["fall_states"].get(node))
        if state.get("fall"):
            fall = state["fall"]
            break
        sim_time += dt
        frame_no += 1
    assert fall is not None
    assert fall["node_id"] == node
    assert engine.store.get_recent_events(10, "fall_detected")
    print(f"[PASS] fall confirmed still={fall['stillness_sec']}s confidence={fall['confidence']:.2f}")

    # Multi-node RF fusion: default is honest weighted-centroid, not fake range.
    second = "sim_direct_2"
    engine.set_node_position(second, 9.0, 2.5, 1.2)
    for _ in range(cfg.CALIBRATION_FRAMES + 5):
        engine.ingest(second, make_frame(rng, cfg, frame_no, "quiet", node_bias=0.3))
        engine.tick(now=sim_time)
        sim_time += dt
        frame_no += 1
    for _ in range(8):
        engine.ingest(node, make_frame(rng, cfg, frame_no, "motion"))
        engine.ingest(second, make_frame(rng, cfg, frame_no + 1, "motion", node_bias=0.3))
        state = engine.tick(now=sim_time)
        sim_time += dt
        frame_no += 2
    assert state["position"] and state["position"]["method"] == "csi_weighted_centroid"
    print(f"[PASS] multi-node RF fusion method={state['position']['method']} contributors={state['position']['contributors']}")

    caps = engine.capabilities()
    assert caps["activity_model_trained"] is False
    print("[PASS] untrained LSTM correctly suppressed (no random activity output)")
    print("\nWiFiSense Ultra in-process pipeline: ALL CHECKS PASSED")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_demo(args.fast, args.verbose))


if __name__ == "__main__":
    main()
