"""Production-oriented HTTP + TCP runtime for WiFiSense Ultra."""
from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import signal
import socket
import threading
import time
from pathlib import Path

from . import config
from .dl.activity_lstm import CLASSES
from .protocol import parse_csi_packet, validate_node_id
from .ultra_engine import UltraEngine

logging.basicConfig(
    level=os.getenv("WIFISENSE_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wifisense.runtime")


class RuntimeSupervisor:
    """Keeps the TCP ingest and engine worker isolated from the web server."""

    def __init__(self, engine):
        self.engine = engine
        self.stop_event = threading.Event()
        self.threads = []
        self.client_threads = set()
        self.client_lock = threading.Lock()
        self.sse_clients = []
        self.sse_lock = threading.Lock()
        self.tcp_socket = None
        self.started = False
        self.last_runtime_error = None

    def start(self):
        if self.started:
            return
        self.started = True
        for target, name in [(self._tcp_loop, "csi-tcp"), (self._engine_loop, "engine-worker")]:
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self.threads.append(thread)
        logger.info("Runtime started: TCP %s, engine %.1f Hz", self.engine.cfg.TCP_PORT, self.engine.cfg.SAMPLE_RATE)

    def stop(self):
        self.stop_event.set()
        if self.tcp_socket:
            try:
                self.tcp_socket.close()
            except OSError:
                pass
        logger.info("Runtime stop requested")

    def broadcast(self, state):
        encoded = json.dumps(state, separators=(",", ":"), default=str)
        with self.sse_lock:
            for q in list(self.sse_clients):
                try:
                    if q.full():
                        q.get_nowait()
                    q.put_nowait(encoded)
                except Exception:
                    try:
                        self.sse_clients.remove(q)
                    except ValueError:
                        pass

    def sse_stream(self):
        q = queue.Queue(maxsize=max(1, self.engine.cfg.SSE_QUEUE_MAX))
        with self.sse_lock:
            self.sse_clients.append(q)
        try:
            yield f"retry: {self.engine.cfg.SSE_RETRY_MS}\n\n"
            while not self.stop_event.is_set():
                try:
                    payload = q.get(timeout=15.0)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    # SSE comments keep proxies/browsers from treating an idle room as a dead stream.
                    yield f": keepalive {int(time.time())}\n\n"
        finally:
            with self.sse_lock:
                if q in self.sse_clients:
                    self.sse_clients.remove(q)

    def _engine_loop(self):
        period = 1.0 / max(1.0, float(self.engine.cfg.SAMPLE_RATE))
        next_tick = time.monotonic()
        while not self.stop_event.is_set():
            try:
                state = self.engine.tick()
                self.broadcast(state)
            except Exception as exc:
                self.last_runtime_error = f"engine: {type(exc).__name__}: {exc}"
                logger.exception("Engine worker recovered from error")
                time.sleep(min(1.0, period * 4))
            next_tick += period
            delay = next_tick - time.monotonic()
            if delay > 0:
                self.stop_event.wait(delay)
            elif delay < -1.0:
                next_tick = time.monotonic()

    def _tcp_loop(self):
        backoff = 1.0
        while not self.stop_event.is_set():
            server = None
            try:
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.settimeout(1.0)
                server.bind((self.engine.cfg.HOST, self.engine.cfg.TCP_PORT))
                server.listen(32)
                self.tcp_socket = server
                logger.info("CSI TCP listener active on %s:%s", self.engine.cfg.HOST, self.engine.cfg.TCP_PORT)
                backoff = 1.0
                while not self.stop_event.is_set():
                    try:
                        client, addr = server.accept()
                    except socket.timeout:
                        continue
                    thread = threading.Thread(target=self._handle_client, args=(client, addr), daemon=True)
                    with self.client_lock:
                        self.client_threads.add(thread)
                    thread.start()
            except OSError as exc:
                if self.stop_event.is_set():
                    break
                self.last_runtime_error = f"tcp: {type(exc).__name__}: {exc}"
                logger.error("TCP listener failed; retrying: %s", exc)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2.0, 30.0)
            finally:
                if server:
                    try:
                        server.close()
                    except OSError:
                        pass
                if self.tcp_socket is server:
                    self.tcp_socket = None

    def _handle_client(self, sock, addr):
        node_id = f"node_{addr[0].replace(':', '_')}_{addr[1]}"
        authenticated = not bool(self.engine.cfg.INGEST_TOKEN)
        buffer = b""
        sock.settimeout(30.0)
        logger.info("CSI client connected: %s", addr)
        try:
            while not self.stop_event.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > self.engine.cfg.TCP_MAX_LINE_BYTES * 2:
                    raise ValueError("client receive buffer exceeded safety limit")
                while b"\n" in buffer:
                    raw_line, buffer = buffer.split(b"\n", 1)
                    if len(raw_line) > self.engine.cfg.TCP_MAX_LINE_BYTES:
                        logger.warning("Dropping oversized CSI line from %s", addr)
                        continue
                    line = raw_line.decode("utf-8", errors="strict").strip()
                    if not line:
                        continue
                    if line.startswith("NODE|"):
                        hello = line.split("|", 2)
                        node_id = validate_node_id(hello[1] if len(hello) > 1 else "")
                        if self.engine.cfg.INGEST_TOKEN:
                            supplied = hello[2] if len(hello) == 3 else ""
                            if not hmac.compare_digest(supplied, self.engine.cfg.INGEST_TOKEN):
                                raise ValueError("invalid CSI ingest token")
                        authenticated = True
                        self.engine.ensure_node(node_id)
                        continue
                    if line.startswith("CSI|"):
                        if not authenticated:
                            raise ValueError("NODE authentication required before CSI frames")
                        frame = parse_csi_packet(line, self.engine.cfg.SUBCARRIERS)
                        if frame is not None:
                            self.engine.ingest(node_id, frame)
                        else:
                            logger.debug("Malformed CSI frame from %s", node_id)
        except (UnicodeDecodeError, ValueError) as exc:
            logger.warning("CSI client %s closed: %s", addr, exc)
        except OSError as exc:
            logger.info("CSI client %s disconnected: %s", addr, exc)
        except Exception:
            logger.exception("CSI client handler recovered from unexpected failure")
        finally:
            try:
                sock.close()
            except OSError:
                pass
            with self.client_lock:
                self.client_threads.discard(threading.current_thread())


def _require_mutation_auth(cfg, request):
    if not cfg.API_TOKEN:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {cfg.API_TOKEN}"


def create_app(engine=None, runtime=None, cfg=None):
    cfg = cfg or config
    try:
        from flask import Flask, Response, jsonify, render_template, request
    except ImportError as exc:  # helpful for minimal/core-only imports
        raise RuntimeError("Flask is required for the web UI. Install requirements.txt") from exc

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "dashboard"),
        static_folder=str(Path(__file__).resolve().parent / "dashboard" / "static"),
    )
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
    eng = engine or UltraEngine(cfg)
    rt = runtime or RuntimeSupervisor(eng)
    app.extensions["wifisense_engine"] = eng
    app.extensions["wifisense_runtime"] = rt

    if cfg.CORS_ORIGINS:
        try:
            from flask_cors import CORS
            origins = [x.strip() for x in cfg.CORS_ORIGINS.split(",") if x.strip()]
            CORS(app, origins=origins)
        except ImportError:
            logger.warning("CORS origins configured but flask-cors is not installed")

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
        )
        if request.path.startswith("/api/") or request.path == "/stream":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/")
    def index():
        return render_template("index.html", brand=cfg.BRAND_NAME, version=cfg.VERSION)

    @app.get("/stream")
    def stream():
        response = Response(rt.sse_stream(), mimetype="text/event-stream")
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.get("/api/status")
    def api_status():
        return jsonify(eng.get_state())

    @app.get("/api/health")
    def api_health():
        health = eng.health()
        if rt.last_runtime_error:
            health["runtime_last_error"] = rt.last_runtime_error
        return jsonify(health), (200 if health["status"] == "ok" else 503)

    @app.get("/api/ready")
    def api_ready():
        health = eng.health()
        ready = health["status"] == "ok" and rt.started
        return jsonify({"ready": ready, "runtime_started": rt.started, "calibrated": eng.calibrated}), (200 if ready else 503)

    @app.get("/api/capabilities")
    def api_capabilities():
        return jsonify(eng.capabilities())

    @app.get("/api/config")
    def api_config():
        return jsonify({
            "app": cfg.APP_NAME,
            "brand": cfg.BRAND_NAME,
            "version": cfg.VERSION,
            "sample_rate": cfg.SAMPLE_RATE,
            "subcarriers": cfg.SUBCARRIERS,
            "calibration_frames": cfg.CALIBRATION_FRAMES,
            "motion_z_thresh": cfg.MOTION_Z_THRESH,
            "fall_amp_spike_z": cfg.FALL_AMP_SPIKE_Z,
            "fall_stillness_sec": cfg.FALL_STILLNESS_SEC,
            "room": {"width_m": cfg.ROOM_WIDTH_M, "depth_m": cfg.ROOM_DEPTH_M, "height_m": cfg.ROOM_HEIGHT_M},
        })

    @app.get("/api/nodes")
    def api_nodes():
        return jsonify(eng.nodes.get_all_dicts())

    @app.post("/api/nodes/<node_id>/position")
    def api_node_position(node_id):
        if not _require_mutation_auth(cfg, request):
            return jsonify({"error": "unauthorized"}), 401
        try:
            node_id = validate_node_id(node_id)
            data = request.get_json(silent=True) or {}
            if "x" not in data or "y" not in data:
                return jsonify({"error": "x and y are required"}), 400
            position = eng.set_node_position(node_id, data["x"], data["y"], data.get("z", 0.0))
            return jsonify({"status": "ok", "node_id": node_id, "position": position})
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/calibrate")
    def api_calibrate():
        if not _require_mutation_auth(cfg, request):
            return jsonify({"error": "unauthorized"}), 401
        eng.recalibrate()
        return jsonify({"status": "calibrating", "target_frames": cfg.CALIBRATION_FRAMES})

    @app.get("/api/events")
    def api_events():
        limit = request.args.get("limit", 100, type=int)
        event_type = request.args.get("type")
        return jsonify(eng.store.get_recent_events(limit=limit, event_type=event_type))

    @app.get("/api/spectrogram")
    def api_spectrogram():
        return jsonify(eng.last_spectrogram or {"status": "warming_up"})

    @app.post("/api/label/activity")
    def api_label_activity():
        if not _require_mutation_auth(cfg, request):
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        label = str(data.get("label", "")).lower().strip()
        node_id = str(data.get("node_id", "")).strip()
        if label not in CLASSES:
            return jsonify({"error": f"label must be one of {CLASSES}"}), 400
        try:
            node_id = validate_node_id(node_id)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        with eng.lock:
            node = eng.nodes.nodes.get(node_id)
            if not node or len(node.processed_buffer) < cfg.ACTIVITY_SEQ_LEN:
                return jsonify({"error": f"need {cfg.ACTIVITY_SEQ_LEN} processed frames for {node_id}"}), 409
            sequence = [row["amplitude"] for row in list(node.processed_buffer)[-cfg.ACTIVITY_SEQ_LEN:]]
        Path(cfg.ACTIVITY_LABEL_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(cfg.ACTIVITY_LABEL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"node_id": node_id, "label": label, "sequence": sequence}, separators=(",", ":")) + "\n")
        return jsonify({"status": "saved", "node_id": node_id, "label": label, "frames": len(sequence)})

    return app, rt


def main():
    engine = UltraEngine(config)
    app, runtime = create_app(engine=engine, cfg=config)
    runtime.start()

    def handle_signal(signum, frame):
        # Break the blocking WSGI serve loop so Docker/systemd stops cleanly.
        runtime.stop()
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handle_signal)
        except Exception:
            pass

    logger.info("%s %s by %s", config.APP_NAME, config.VERSION, config.BRAND_NAME)
    try:
        from waitress import serve
        logger.info("Web UI listening on http://%s:%s using Waitress", config.HOST, config.WEB_PORT)
        serve(app, host=config.HOST, port=config.WEB_PORT, threads=max(4, config.WEB_THREADS), channel_timeout=60)
    except ImportError:
        logger.warning("Waitress not installed; using Flask development server. Install requirements.txt for production use.")
        app.run(host=config.HOST, port=config.WEB_PORT, threaded=True, debug=False, use_reloader=False)
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
