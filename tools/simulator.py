#!/usr/bin/env python3
"""TCP CSI simulator for WiFiSense Ultra.

Uses the same ESP-IDF wire byte order as the firmware (imaginary, real pairs).
For deterministic no-network verification use tools/inprocess_simulator.py.
"""
from __future__ import annotations

import argparse
import math
import socket
import threading
import time
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Scenario:
    motion: bool
    fall_at_sec: float | None = None
    post_fall_still: bool = False


SCENARIOS = {
    "empty": Scenario(False),
    "walking": Scenario(True),
    "standing": Scenario(True),
    "multiple": Scenario(True),
    "fall": Scenario(True, fall_at_sec=8.0, post_fall_still=True),
}


def _encode_signed_bytes(values: np.ndarray) -> str:
    return ",".join(str(int(v)) for v in values.astype(np.int8))


def generate_packet(scenario: str, frame_num: int, sample_rate: float, seed: int, node_index: int = 0) -> str:
    sc = SCENARIOS[scenario]
    rng = np.random.default_rng(seed + frame_num * 7919 + node_index * 104729)
    subs = 64
    t = frame_num / sample_rate
    idx = np.arange(subs, dtype=float)

    amplitude = 30.0 + rng.normal(0.0, 0.45, subs)
    phase = np.linspace(-math.pi, math.pi, subs) + rng.normal(0.0, 0.02, subs)

    if sc.motion:
        center = 18.0 + ((t * 5.0 + node_index * 9.0) % 30.0)
        envelope = np.exp(-np.abs(idx - center) / 5.0)
        movement = 10.0 * math.sin(2.0 * math.pi * 0.8 * t + node_index * 0.6)

        if sc.fall_at_sec is not None:
            dt = t - sc.fall_at_sec
            if -0.05 <= dt <= 0.10:
                movement = 78.0
            elif dt > 0.10 and sc.post_fall_still:
                movement = 0.15 * math.sin(2.0 * math.pi * 0.18 * t)

        amplitude += envelope * movement
        phase += envelope * movement * 0.045

        if scenario == "multiple":
            center2 = 48.0 - ((t * 2.0) % 16.0)
            envelope2 = np.exp(-np.abs(idx - center2) / 4.0)
            amplitude += envelope2 * 5.0 * math.sin(2.0 * math.pi * 0.45 * t)

    real = np.clip(np.rint(amplitude * np.cos(phase)), -128, 127).astype(np.int8)
    imag = np.clip(np.rint(amplitude * np.sin(phase)), -128, 127).astype(np.int8)

    # ESP-IDF CSI buffers are signed int8 pairs in imaginary, real order.
    interleaved = np.empty(subs * 2, dtype=np.int8)
    interleaved[0::2] = imag
    interleaved[1::2] = real

    timestamp_us = int(time.time() * 1_000_000)
    rssi = -48 if sc.motion else -63
    return f"CSI|{timestamp_us}|{rssi}|54|{len(interleaved)}|{_encode_signed_bytes(interleaved)}\n"


def simulate_node(host: str, port: int, node_id: str, scenario: str, duration: float, sample_rate: float, seed: int, node_index: int, token: str = ""):
    interval = 1.0 / sample_rate
    frames = max(1, int(duration * sample_rate))
    with socket.create_connection((host, port), timeout=5.0) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        hello = f"NODE|{node_id}|{token}\n" if token else f"NODE|{node_id}\n"
        sock.sendall(hello.encode("utf-8"))
        next_send = time.monotonic()
        for frame in range(frames):
            sock.sendall(generate_packet(scenario, frame, sample_rate, seed, node_index).encode("utf-8"))
            next_send += interval
            delay = next_send - time.monotonic()
            if delay > 0:
                time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(description="Feed simulated CSI frames to a running WiFiSense Ultra TCP listener")
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS), default="walking")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--nodes", type=int, default=1)
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--sample-rate", type=float, default=20.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--token", default="", help="optional WIFISENSE_INGEST_TOKEN")
    args = parser.parse_args()

    if args.nodes < 1 or args.nodes > 16:
        parser.error("--nodes must be between 1 and 16")
    if args.duration <= 0 or args.sample_rate <= 0:
        parser.error("--duration and --sample-rate must be positive")

    print(f"WiFiSense Ultra TCP simulator: {args.scenario}, {args.nodes} node(s), {args.duration:g}s")
    threads = []
    failures: list[str] = []

    def run_one(index: int):
        node_id = f"sim_node_{index + 1}"
        try:
            simulate_node(args.host, args.port, node_id, args.scenario, args.duration, args.sample_rate, args.seed, index, args.token)
            print(f"[OK] {node_id}")
        except Exception as exc:  # CLI must report individual node failures cleanly.
            failures.append(f"{node_id}: {type(exc).__name__}: {exc}")

    for i in range(args.nodes):
        thread = threading.Thread(target=run_one, args=(i,), daemon=False)
        thread.start()
        threads.append(thread)
        time.sleep(0.05)
    for thread in threads:
        thread.join()

    if failures:
        for failure in failures:
            print(f"[ERROR] {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
