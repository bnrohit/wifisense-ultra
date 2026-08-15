"""Runtime configuration for WiFiSense Ultra.

Every operational setting can be overridden with an environment variable so a
single build can be used on laptops, Raspberry Pi-class hosts, and servers
without source edits.
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def _load_env_file(path: Path) -> None:
    """Load a simple KEY=VALUE .env file without overriding real environment variables.

    This keeps direct ``python -m wifisense_ultra.main`` and Docker behavior
    consistent without adding another runtime dependency. Shell/container
    environment variables always take precedence over values from .env.
    """
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or not key.replace("_", "").isalnum():
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
    except OSError:
        # A missing/unreadable optional .env must never prevent the core engine
        # from starting; explicit environment variables/defaults still apply.
        pass


_load_env_file(PROJECT_DIR / ".env")
DATA_DIR = Path(os.getenv("WIFISENSE_DATA_DIR", str(PROJECT_DIR / "data"))).resolve()

APP_NAME = "WiFiSense Ultra"
BRAND_NAME = "Rohith Bommalla Naresh"
VERSION = "1.0.1"


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


# CSI input / normalization
SUBCARRIERS = _env_int("WIFISENSE_SUBCARRIERS", 64)
CSI_BUFFER_MAX = _env_int("WIFISENSE_CSI_BUFFER_MAX", 2000)
SAMPLE_RATE = _env_float("WIFISENSE_SAMPLE_RATE", 20.0)
NODE_TIMEOUT_SEC = _env_float("WIFISENSE_NODE_TIMEOUT_SEC", 10.0)
TCP_MAX_LINE_BYTES = _env_int("WIFISENSE_TCP_MAX_LINE_BYTES", 16384)

# Signal processing
HAMPEL_WINDOW = _env_int("WIFISENSE_HAMPEL_WINDOW", 7)
HAMPEL_N_SIGMA = _env_float("WIFISENSE_HAMPEL_N_SIGMA", 3.0)
PCA_COMPONENTS = _env_int("WIFISENSE_PCA_COMPONENTS", 8)
BUTTER_LOW = _env_float("WIFISENSE_BUTTER_LOW", 0.1)
BUTTER_HIGH = _env_float("WIFISENSE_BUTTER_HIGH", 8.0)
BUTTER_ORDER = _env_int("WIFISENSE_BUTTER_ORDER", 4)
CALIBRATION_FRAMES = _env_int("WIFISENSE_CALIBRATION_FRAMES", 150)
BG_UPDATE_ALPHA = _env_float("WIFISENSE_BG_UPDATE_ALPHA", 0.01)
BG_UPDATE_MAX_Z = _env_float("WIFISENSE_BG_UPDATE_MAX_Z", 1.5)

# Motion / tracking
MOTION_Z_THRESH = _env_float("WIFISENSE_MOTION_Z_THRESH", 2.5)
MOTION_MIN_DURATION = _env_float("WIFISENSE_MOTION_MIN_DURATION", 0.3)
TRACK_MAX_AGE = _env_float("WIFISENSE_TRACK_MAX_AGE", 2.0)
TRACK_INIT_CONF = _env_float("WIFISENSE_TRACK_INIT_CONF", 0.4)
KALMAN_Q = _env_float("WIFISENSE_KALMAN_Q", 0.01)
KALMAN_R = _env_float("WIFISENSE_KALMAN_R", 0.5)
GATING_SIGMA = _env_float("WIFISENSE_GATING_SIGMA", 3.0)
ROOM_WIDTH_M = _env_float("WIFISENSE_ROOM_WIDTH_M", 10.0)
ROOM_DEPTH_M = _env_float("WIFISENSE_ROOM_DEPTH_M", 5.0)
ROOM_HEIGHT_M = _env_float("WIFISENSE_ROOM_HEIGHT_M", 3.0)

# Classification
CLASSIFIER_RULES = _env_bool("WIFISENSE_CLASSIFIER_RULES", True)
CLASSIFIER_ML_PATH = os.getenv("WIFISENSE_CLASSIFIER_ML_PATH", str(DATA_DIR / "classifier.pkl"))
ACTIVITY_MODEL_PATH = os.getenv("WIFISENSE_ACTIVITY_MODEL_PATH", str(DATA_DIR / "activity_lstm.pt"))
ACTIVITY_SEQ_LEN = _env_int("WIFISENSE_ACTIVITY_SEQ_LEN", 50)

# Experimental respiration trend. This is intentionally not presented as a
# medical measurement in the UI.
BREATHING_MIN_HZ = _env_float("WIFISENSE_BREATHING_MIN_HZ", 0.15)
BREATHING_MAX_HZ = _env_float("WIFISENSE_BREATHING_MAX_HZ", 0.5)
BREATHING_MIN_SAMPLES = _env_int("WIFISENSE_BREATHING_MIN_SAMPLES", 128)

# Node-keyed fall detection: sudden standardized spike -> settling -> stillness.
FALL_AMP_SPIKE_Z = _env_float("WIFISENSE_FALL_AMP_SPIKE_Z", 6.0)
FALL_SPIKE_RATIO = _env_float("WIFISENSE_FALL_SPIKE_RATIO", 2.0)
FALL_SPIKE_VELOCITY_Z = _env_float("WIFISENSE_FALL_SPIKE_VELOCITY_Z", 2.5)
FALL_POST_SPIKE_MOTION_GRACE_SEC = _env_float("WIFISENSE_FALL_POST_SPIKE_MOTION_GRACE_SEC", 0.6)
FALL_STILLNESS_Z = _env_float("WIFISENSE_FALL_STILLNESS_Z", 1.6)
FALL_STILLNESS_VELOCITY_Z = _env_float("WIFISENSE_FALL_STILLNESS_VELOCITY_Z", 1.5)
FALL_MAX_ACTIVE_SUBCARRIERS = _env_int("WIFISENSE_FALL_MAX_ACTIVE_SUBCARRIERS", 10)
FALL_STILLNESS_SEC = _env_float("WIFISENSE_FALL_STILLNESS_SEC", 3.0)
FALL_CONFIRM_WINDOW_SEC = _env_float("WIFISENSE_FALL_CONFIRM_WINDOW_SEC", 8.0)
FALL_COOLDOWN_SEC = _env_float("WIFISENSE_FALL_COOLDOWN_SEC", 10.0)

# STFT / micro-motion analysis
STFT_ENABLED = _env_bool("WIFISENSE_STFT_ENABLED", True)
STFT_NPERSEG = _env_int("WIFISENSE_STFT_NPERSEG", 64)
STFT_NOVERLAP = _env_int("WIFISENSE_STFT_NOVERLAP", 48)
STFT_NFFT = _env_int("WIFISENSE_STFT_NFFT", 256)
STFT_HISTORY = _env_int("WIFISENSE_STFT_HISTORY", 500)

# Anomaly detection. Statistical mode always works; PyTorch is optional.
ANOMALY_ENABLED = _env_bool("WIFISENSE_ANOMALY_ENABLED", True)
ANOMALY_MODEL_DIR = os.getenv("WIFISENSE_ANOMALY_MODEL_DIR", str(DATA_DIR / "models"))
ANOMALY_PERCENTILE = _env_float("WIFISENSE_ANOMALY_PERCENTILE", 99.0)
ANOMALY_MIN_BASELINE = _env_int("WIFISENSE_ANOMALY_MIN_BASELINE", 50)

# Positioning. Without a calibrated per-node target-distance estimator, the
# engine uses conservative CSI weighted-centroid localization. Metric 2D/3D
# multilateration is only used when explicit calibrated distances are present.
POSITIONING_ENABLED = _env_bool("WIFISENSE_POSITIONING_ENABLED", True)
POSITIONING_MIN_NODES = _env_int("WIFISENSE_POSITIONING_MIN_NODES", 2)
POSITIONING_MAX_DISTANCE_M = _env_float("WIFISENSE_POSITIONING_MAX_DISTANCE_M", 30.0)

# Persistence
DB_PATH = os.getenv("WIFISENSE_DB_PATH", str(DATA_DIR / "wifisense.db"))
CSI_SNAPSHOT_EVERY_N = _env_int("WIFISENSE_CSI_SNAPSHOT_EVERY_N", 100)
CSI_RETENTION_DAYS = _env_int("WIFISENSE_CSI_RETENTION_DAYS", 7)
EVENT_RETENTION_DAYS = _env_int("WIFISENSE_EVENT_RETENTION_DAYS", 90)

# HTTP / TCP runtime
HOST = os.getenv("WIFISENSE_HOST", "0.0.0.0")
TCP_PORT = _env_int("WIFISENSE_TCP_PORT", 8089)
WEB_PORT = _env_int("WIFISENSE_WEB_PORT", 5000)
SSE_RETRY_MS = _env_int("WIFISENSE_SSE_RETRY_MS", 2000)
SSE_QUEUE_MAX = _env_int("WIFISENSE_SSE_QUEUE_MAX", 8)
WEB_THREADS = _env_int("WIFISENSE_WEB_THREADS", 8)
CORS_ORIGINS = os.getenv("WIFISENSE_CORS_ORIGINS", "").strip()

# MQTT / Home Assistant is opt-in so the application starts cleanly even when
# no broker exists.
MQTT_ENABLED = _env_bool("WIFISENSE_MQTT_ENABLED", False)
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = _env_int("MQTT_PORT", 1883)
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "wifisense")
MQTT_DISCOVERY_PREFIX = os.getenv("MQTT_DISCOVERY_PREFIX", "homeassistant")
MQTT_ROOM = os.getenv("MQTT_ROOM", "living_room")
STFT_COMPUTE_EVERY_N = _env_int("WIFISENSE_STFT_COMPUTE_EVERY_N", 4)
MQTT_PUBLISH_INTERVAL_SEC = _env_float("WIFISENSE_MQTT_PUBLISH_INTERVAL_SEC", 0.5)
API_TOKEN = os.getenv("WIFISENSE_API_TOKEN", "").strip()
INGEST_TOKEN = os.getenv("WIFISENSE_INGEST_TOKEN", "").strip()
ACTIVITY_LABEL_PATH = os.getenv("WIFISENSE_ACTIVITY_LABEL_PATH", str(DATA_DIR / "activity_labels.jsonl"))
