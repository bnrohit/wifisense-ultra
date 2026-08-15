"""WiFiSense Ultra core engine.

The core detection path has no dependency on Flask, MQTT, or PyTorch. Optional
capabilities are isolated so a failed integration cannot stop CSI ingestion,
motion detection, tracking, fall state, or persistence.
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque

import numpy as np

from . import config
from .activity.hmm import SimpleHMM
from .classifier import ObjectClassifier
from .dl.activity_lstm import ActivityClassifier
from .dl.autoencoder import AnomalyDetector
from .mqtt_bridge import MQTTBridge
from .node_manager import NodeManager
from .phase_sanitize import PhaseSanitizer
from .rf.triangulation import RFTriangulation
from .signal_processing import SignalProcessor
from .storage import EventStore
from .stft_analysis import STFTAnalyzer
from .tracking import MultiTargetTracker
from .vitals import VitalsMonitor


class UltraEngine:
    def __init__(self, cfg=None):
        self.cfg = cfg or config
        self.lock = threading.RLock()
        self.last_error = None
        self.error_count = 0
        self.nodes = NodeManager(self.cfg)
        self.tracker = MultiTargetTracker(self.cfg)
        self.classifier = ObjectClassifier(self.cfg)
        self.vitals = VitalsMonitor(self.cfg)
        self.store = EventStore(self.cfg.DB_PATH)
        self.phase_sanitizer = PhaseSanitizer(self.cfg.SUBCARRIERS)

        bounds = {
            "x": (0.0, float(self.cfg.ROOM_WIDTH_M)),
            "y": (0.0, float(self.cfg.ROOM_DEPTH_M)),
            "z": (0.0, float(self.cfg.ROOM_HEIGHT_M)),
        }
        self.triangulation = RFTriangulation(bounds, self.cfg.POSITIONING_MAX_DISTANCE_M)
        self.saved_node_positions = {}
        self._load_node_positions()

        self.activity_lstm = ActivityClassifier(
            model_path=self.cfg.ACTIVITY_MODEL_PATH,
            seq_len=self.cfg.ACTIVITY_SEQ_LEN,
            input_dim=self.cfg.SUBCARRIERS,
        )
        self.mqtt = MQTTBridge(self.cfg)

        self.processors = {}
        self.stft_by_node = {}
        self.anomaly_by_node = {}
        self.hmm_by_node = {}
        self.last_anomaly_by_node = {}
        self.last_track_by_node = {}

        self.global_tracks = []
        self.events = deque(maxlen=500)
        self.presence = False
        self.calibrated = False
        self.frame_count = 0
        self.started_at = time.time()
        self.last_ingest_at = None
        self.last_tick_at = None
        self.tick_count = 0
        self.last_spectrogram = None
        self.last_micro_motion = []
        self.last_vitals = None
        self.last_activity = {"state": "empty", "confidence": 1.0, "source": "rules"}
        self.last_position = None
        self._last_mqtt_publish = 0.0
        self._last_prune = 0.0
        self._last_state = None

    def _load_node_positions(self):
        try:
            for row in self.store.load_node_positions():
                position = {"x": float(row["x"]), "y": float(row["y"]), "z": float(row["z"]), "configured": True}
                self.saved_node_positions[row["node_id"]] = position
                self.triangulation.register_node(row["node_id"], row["x"], row["y"], row["z"])
        except Exception as exc:
            self._record_error(exc)

    def _record_error(self, exc):
        self.error_count += 1
        self.last_error = f"{type(exc).__name__}: {exc}"

    def ensure_node(self, node_id):
        """Create/return a node and restore persisted coordinates atomically."""
        with self.lock:
            node = self.nodes.get_or_create(node_id)
            saved = self.saved_node_positions.get(str(node_id))
            if saved and not node.position.get("configured"):
                node.set_position(saved["x"], saved["y"], saved.get("z", 0.0), configured=True)
            return node

    def get_processor(self, node_id):
        if node_id not in self.processors:
            self.processors[node_id] = SignalProcessor(self.cfg)
        return self.processors[node_id]

    def get_stft(self, node_id):
        if node_id not in self.stft_by_node:
            self.stft_by_node[node_id] = STFTAnalyzer(
                sample_rate=self.cfg.SAMPLE_RATE,
                nperseg=self.cfg.STFT_NPERSEG,
                noverlap=self.cfg.STFT_NOVERLAP,
                nfft=self.cfg.STFT_NFFT,
                max_history=self.cfg.STFT_HISTORY,
            )
        return self.stft_by_node[node_id]

    def get_anomaly(self, node_id):
        if node_id not in self.anomaly_by_node:
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", node_id)
            model_path = os.path.join(self.cfg.ANOMALY_MODEL_DIR, f"{safe}_autoencoder.pt")
            self.anomaly_by_node[node_id] = AnomalyDetector(
                input_dim=self.cfg.SUBCARRIERS,
                model_path=model_path,
                percentile=self.cfg.ANOMALY_PERCENTILE,
            )
        return self.anomaly_by_node[node_id]

    def get_hmm(self, node_id):
        if node_id not in self.hmm_by_node:
            self.hmm_by_node[node_id] = SimpleHMM()
        return self.hmm_by_node[node_id]

    def set_node_position(self, node_id, x, y, z=0.0):
        x, y, z = float(x), float(y), float(z)
        if not (0 <= x <= self.cfg.ROOM_WIDTH_M and 0 <= y <= self.cfg.ROOM_DEPTH_M and 0 <= z <= self.cfg.ROOM_HEIGHT_M):
            raise ValueError("node position is outside configured room bounds")
        with self.lock:
            node = self.ensure_node(node_id)
            node.set_position(x, y, z, configured=True)
            self.saved_node_positions[str(node_id)] = dict(node.position)
            self.triangulation.register_node(node_id, x, y, z)
            self.store.save_node_position(node_id, x, y, z)
        return dict(node.position)

    def recalibrate(self):
        with self.lock:
            self.calibrated = False
            self.processors.clear()
            self.stft_by_node.clear()
            self.anomaly_by_node.clear()
            self.hmm_by_node.clear()
            self.last_anomaly_by_node.clear()
            self.tracker = MultiTargetTracker(self.cfg)
            self.global_tracks = []
            self.last_track_by_node.clear()
            self.vitals.reset()
            self.presence = False
            self.last_activity = {"state": "empty", "confidence": 1.0, "source": "rules"}
            self.last_position = None
            for node in self.nodes.get_all():
                node.processed_buffer.clear()
                node.csi_buffer.clear()
            self.store.log_event("recalibration_started")

    def _fit_node_baseline(self, node_id, proc):
        if not self.cfg.ANOMALY_ENABLED:
            return
        normal = [np.asarray(row["csi_amplitude"], dtype=float).tolist() for row in list(proc.calib_buffer)]
        if len(normal) >= self.cfg.ANOMALY_MIN_BASELINE:
            try:
                self.get_anomaly(node_id).fit_baseline(normal)
            except Exception as exc:
                self._record_error(exc)

    def ingest(self, node_id, csi_frame):
        """Ingest one normalized CSI frame. Returns processing status."""
        with self.lock:
            try:
                node = self.ensure_node(node_id)
                frame = dict(csi_frame)
                phase = frame.get("csi_phase")
                if phase is not None:
                    sanitized = self.phase_sanitizer.sanitize(phase)
                    frame["csi_phase_raw"] = list(phase)
                    frame["csi_phase_cleaned"] = sanitized["cleaned"]
                    frame["csi_phase"] = sanitized["cleaned"]
                    frame["phase_slope"] = sanitized["slope"]
                node.add_frame(frame)
                self.last_ingest_at = time.time()

                proc = self.get_processor(node_id)
                result = proc.process_frame(frame)
                if result is None:
                    return None

                if result.get("status") == "calibrated":
                    self._fit_node_baseline(node_id, proc)
                    self.calibrated = bool(self.processors) and all(p.calibrated for p in self.processors.values())
                    self.store.log_event("calibrated", node_id=node_id)
                    return {"status": "calibrated", "node_id": node_id}

                if result.get("status") == "calibrating":
                    self.calibrated = bool(self.processors) and all(p.calibrated for p in self.processors.values())
                    result["node_id"] = node_id
                    result["target"] = self.cfg.CALIBRATION_FRAMES
                    return result

                # Preserve future calibrated-ranging output if a node/adapter supplies it.
                if frame.get("target_distance_m") is not None:
                    result["target_distance_m"] = float(frame["target_distance_m"])
                node.processed_buffer.append(result)
                self.frame_count += 1

                if self.cfg.STFT_ENABLED:
                    self.get_stft(node_id).add_frame(result["amplitude"])
                if self.cfg.ANOMALY_ENABLED:
                    self.last_anomaly_by_node[node_id] = self.get_anomaly(node_id).detect(result["amplitude"])

                every = int(self.cfg.CSI_SNAPSHOT_EVERY_N)
                if every > 0 and self.frame_count % every == 0:
                    self.store.log_csi(node_id, result["amplitude"], result.get("phase"), result.get("rssi"))
                return result
            except Exception as exc:
                self._record_error(exc)
                raise

    def _track_context_for_node(self, node_id, now):
        context = self.last_track_by_node.get(node_id)
        if not context:
            return None
        max_age = self.cfg.FALL_CONFIRM_WINDOW_SEC + self.cfg.TRACK_MAX_AGE
        if now - context.get("seen_at", 0.0) > max_age:
            self.last_track_by_node.pop(node_id, None)
            return None
        return context.get("track")

    def _heuristic_single_node_position(self, node, latest):
        active = latest.get("active_subcarriers") or []
        if not active:
            return None
        avg_idx = float(np.mean(active))
        spread = float(np.std(active))
        # This is deliberately labeled non-metric; it is only a stable display cue.
        x = (avg_idx / max(1, self.cfg.SUBCARRIERS - 1)) * self.cfg.ROOM_WIDTH_M
        y = (1.0 - min(1.0, spread / max(1, self.cfg.SUBCARRIERS / 3.0))) * self.cfg.ROOM_DEPTH_M
        if node.position.get("configured"):
            x = 0.65 * x + 0.35 * float(node.position["x"])
            y = 0.65 * y + 0.35 * float(node.position["y"])
        return {
            "x": float(np.clip(x, 0, self.cfg.ROOM_WIDTH_M)),
            "y": float(np.clip(y, 0, self.cfg.ROOM_DEPTH_M)),
            "z": None,
            "confidence": min(float(latest.get("max_z", 0)) / 12.0, 0.55),
            "method": "single_node_signal_map",
            "metric": False,
            "contributors": [node.node_id],
            "gdop": None,
            "residual": None,
        }

    def _activity_for_node(self, node_id, moving):
        node = self.nodes.nodes.get(node_id)
        learned = None
        if node and self.activity_lstm.trained and len(node.processed_buffer) >= self.cfg.ACTIVITY_SEQ_LEN:
            sequence = [row["amplitude"] for row in list(node.processed_buffer)[-self.cfg.ACTIVITY_SEQ_LEN:]]
            learned = self.activity_lstm.classify(sequence)
        if learned:
            smoothed = self.get_hmm(node_id).update(learned["probabilities"])
            return {
                "state": smoothed["state"],
                "confidence": smoothed["confidence"],
                "source": "trained_lstm+hmm",
                "raw": learned,
            }
        return {
            "state": "motion" if moving else ("still" if self.presence else "empty"),
            "confidence": 0.75 if moving else 0.6,
            "source": "rules",
            "model_status": "trained" if self.activity_lstm.trained else "untrained",
        }

    def tick(self, node_id=None, now=None):
        """Advance detection state once; safe to call at 10-20 Hz."""
        with self.lock:
            now = float(time.time() if now is None else now)
            self.last_tick_at = time.time()
            self.tick_count += 1
            target_nodes = [self.nodes.nodes.get(node_id)] if node_id else self.nodes.get_all()
            features_by_node = {}
            latest_by_node = {}
            observations = []

            for node in target_nodes:
                if node is None or len(node.processed_buffer) < 5:
                    continue
                recent = list(node.processed_buffer)[-20:]
                latest = recent[-1]
                feats = self.get_processor(node.node_id).extract_features(recent)
                if feats:
                    features_by_node[node.node_id] = feats
                    latest_by_node[node.node_id] = latest

                active = latest.get("active_subcarriers") or []
                if latest.get("max_z", 0.0) < self.cfg.MOTION_Z_THRESH or len(active) < 3:
                    continue
                observations.append({
                    "node_id": node.node_id,
                    "motion_strength": float(latest.get("max_z", 0.0)),
                    "max_z": float(latest.get("max_z", 0.0)),
                    "rssi": float(latest.get("rssi", -100)),
                    "confidence": float(np.clip(latest.get("max_z", 0.0) / 10.0, 0.1, 1.0)),
                    "distance_m": latest.get("target_distance_m"),
                })

            position = None
            configured_obs = [o for o in observations if o["node_id"] in self.triangulation.nodes]
            if self.cfg.POSITIONING_ENABLED and len(configured_obs) >= self.cfg.POSITIONING_MIN_NODES:
                position = self.triangulation.locate(configured_obs)
            if position is None and observations:
                strongest = max(observations, key=lambda o: o["motion_strength"])
                node = self.nodes.nodes[strongest["node_id"]]
                position = self._heuristic_single_node_position(node, latest_by_node[strongest["node_id"]])
            self.last_position = position

            detections = []
            if position:
                primary = max(observations, key=lambda o: o["motion_strength"])["node_id"] if observations else None
                detections.append({
                    "x": position["x"],
                    "y": position["y"],
                    "confidence": position["confidence"],
                    "node_id": primary,
                })
            self.global_tracks = self.tracker.update(detections)

            contributors = (position or {}).get("contributors", [])
            for track in self.global_tracks:
                if position:
                    track["z"] = position.get("z")
                    track["position_method"] = position.get("method")
                    track["position_metric"] = position.get("metric", False)
                    track["gdop"] = position.get("gdop")
                    track["contributors"] = contributors
                nid = track.get("node_id")
                feats = features_by_node.get(nid)
                active = latest_by_node.get(nid, {}).get("active_subcarriers", []) if nid else []
                label, conf = self.classifier.classify(feats, active)
                track["label"] = label
                track["label_confidence"] = conf
                track["features"] = feats or {}
                # A fused track is useful context for every contributing node's fall state.
                link_nodes = contributors or ([nid] if nid else [])
                for linked_nid in link_nodes:
                    self.last_track_by_node[linked_nid] = {"track": dict(track), "seen_at": now}

            moving = bool(observations)
            self.presence = bool(self.global_tracks or moving)
            primary_node = None
            if observations:
                primary_node = max(observations, key=lambda o: o["motion_strength"])["node_id"]
            elif self.global_tracks:
                primary_node = self.global_tracks[0].get("node_id")

            self.last_activity = self._activity_for_node(primary_node, moving) if primary_node else {
                "state": "empty", "confidence": 1.0, "source": "rules",
                "model_status": "trained" if self.activity_lstm.trained else "untrained",
            }

            fall_events = []
            for nid, feats in features_by_node.items():
                fall = self.vitals.detect_fall(
                    nid,
                    feats,
                    now=now,
                    track_context=self._track_context_for_node(nid, now),
                )
                if fall:
                    if position and fall.get("position") is None:
                        fall["position"] = {"x": position["x"], "y": position["y"], "z": position.get("z")}
                    fall["position_method"] = (position or {}).get("method")
                    fall_events.append(fall)
                    self.events.append(fall)
                    self.store.log_event(
                        "fall_detected",
                        node_id=nid,
                        track_id=fall.get("track_id"),
                        details=fall,
                        timestamp=fall.get("timestamp"),
                    )
            fall_event = fall_events[0] if fall_events else None
            if fall_event:
                self.last_activity = {"state": "fall_detected", "confidence": fall_event["confidence"], "source": "fall_detector"}

            # Experimental respiration trend: only emit when a person is present
            # and the selected node has enough history.
            self.last_vitals = None
            vital_node = self.nodes.nodes.get(primary_node) if primary_node else None
            if self.presence and vital_node and len(vital_node.processed_buffer) >= self.cfg.BREATHING_MIN_SAMPLES:
                recent = list(vital_node.processed_buffer)[-self.cfg.BREATHING_MIN_SAMPLES:]
                mean_amp = [float(np.median(row["amplitude"])) for row in recent]
                self.last_vitals = self.vitals.extract_breathing_rate(mean_amp)
                if self.last_vitals:
                    self.last_vitals.update({"node_id": primary_node, "experimental": True})

            if self.cfg.STFT_ENABLED and primary_node and primary_node in self.stft_by_node:
                if self.tick_count % max(1, self.cfg.STFT_COMPUTE_EVERY_N) == 0:
                    analyzer = self.stft_by_node[primary_node]
                    self.last_spectrogram = analyzer.compute_spectrogram()
                    self.last_micro_motion = analyzer.detect_micro_motion()

            self.nodes.cleanup()
            self.calibrated = bool(self.processors) and all(p.calibrated for p in self.processors.values())

            anomaly_list = [
                {"node_id": nid, **result}
                for nid, result in sorted(self.last_anomaly_by_node.items())
            ]
            state = {
                "timestamp": now,
                "presence": self.presence,
                "calibrated": self.calibrated,
                "tracks": self.global_tracks,
                "position": self.last_position,
                "vitals": self.last_vitals,
                "fall": fall_event,
                "falls": fall_events,
                "fall_states": self.vitals.get_fall_states(now),
                "activity": self.last_activity,
                "anomaly": anomaly_list,
                "micro_motion": self.last_micro_motion,
                "spectrogram": self.last_spectrogram,
                "nodes": self.nodes.get_all_dicts(),
                "frame_count": self.frame_count,
            }
            self._last_state = state

            if self.mqtt.enabled and time.time() - self._last_mqtt_publish >= self.cfg.MQTT_PUBLISH_INTERVAL_SEC:
                try:
                    self.mqtt.publish_state(state)
                except Exception as exc:
                    self._record_error(exc)
                self._last_mqtt_publish = time.time()

            if time.time() - self._last_prune > 3600:
                try:
                    self.store.prune(self.cfg.EVENT_RETENTION_DAYS, self.cfg.CSI_RETENTION_DAYS)
                except Exception as exc:
                    self._record_error(exc)
                self._last_prune = time.time()
            return state

    def capabilities(self):
        return {
            "core_detection": True,
            "fall_detection": True,
            "phase_sanitization": True,
            "stft": bool(self.cfg.STFT_ENABLED),
            "statistical_anomaly": bool(self.cfg.ANOMALY_ENABLED),
            "pytorch_available": bool(self.activity_lstm.torch_available),
            "activity_model_trained": bool(self.activity_lstm.trained),
            "mqtt": self.mqtt.status(),
            "positioning": {
                "enabled": bool(self.cfg.POSITIONING_ENABLED),
                "configured_nodes": len(self.triangulation.nodes),
                "metric_multilateration_requires_calibrated_distance": True,
            },
        }

    def health(self):
        wall_now = time.time()
        ingest_age = wall_now - self.last_ingest_at if self.last_ingest_at else None
        tick_age = wall_now - self.last_tick_at if self.last_tick_at else None
        runtime_ok = tick_age is None or tick_age < 3.0
        return {
            "status": "ok" if runtime_ok else "degraded",
            "version": self.cfg.VERSION,
            "uptime_sec": round(wall_now - self.started_at, 1),
            "calibrated": self.calibrated,
            "nodes": len(self.nodes.get_all()),
            "connected_nodes": self.nodes.connected_count(),
            "tracks": len(self.global_tracks),
            "frame_count": self.frame_count,
            "last_ingest_age_sec": round(ingest_age, 2) if ingest_age is not None else None,
            "last_tick_age_sec": round(tick_age, 2) if tick_age is not None else None,
            "errors": self.error_count,
            "last_error": self.last_error,
        }

    def get_state(self):
        with self.lock:
            if self._last_state is not None:
                state = dict(self._last_state)
                state["timestamp"] = time.time()
                state["nodes"] = self.nodes.get_all_dicts()
                return state
            return {
                "timestamp": time.time(),
                "presence": self.presence,
                "calibrated": self.calibrated,
                "tracks": self.global_tracks,
                "position": self.last_position,
                "vitals": self.last_vitals,
                "fall": None,
                "falls": [],
                "fall_states": self.vitals.get_fall_states(),
                "activity": self.last_activity,
                "anomaly": [],
                "micro_motion": self.last_micro_motion,
                "spectrogram": self.last_spectrogram,
                "nodes": self.nodes.get_all_dicts(),
                "frame_count": self.frame_count,
            }
