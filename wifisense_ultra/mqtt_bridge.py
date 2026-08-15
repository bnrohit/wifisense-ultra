"""Optional MQTT/Home Assistant bridge with retained discovery and availability."""
from __future__ import annotations

import json
import logging
import re
import time

logger = logging.getLogger(__name__)


class MQTTBridge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = bool(cfg.MQTT_ENABLED)
        self.client = None
        self.connected = False
        self.last_error = None
        if self.enabled:
            self._connect()

    @staticmethod
    def _slug(value):
        return re.sub(r"[^a-z0-9_]+", "_", str(value).lower()).strip("_") or "room"

    def _connect(self):
        try:
            import paho.mqtt.client as mqtt
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="wifisense-ultra")
            except Exception:
                client = mqtt.Client(client_id="wifisense-ultra")
            if self.cfg.MQTT_USERNAME:
                client.username_pw_set(self.cfg.MQTT_USERNAME, self.cfg.MQTT_PASSWORD)
            availability = f"{self.cfg.MQTT_TOPIC_PREFIX}/availability"
            client.will_set(availability, "offline", qos=1, retain=True)

            def on_connect(client, userdata, flags, reason_code, properties=None):
                self.connected = int(reason_code) == 0
                if self.connected:
                    client.publish(availability, "online", qos=1, retain=True)
                    self.publish_discovery()
                    client.subscribe(f"{self.cfg.MQTT_DISCOVERY_PREFIX}/status")

            def on_disconnect(client, userdata, *args):
                self.connected = False

            def on_message(client, userdata, message):
                if message.topic == f"{self.cfg.MQTT_DISCOVERY_PREFIX}/status" and message.payload.decode(errors="ignore") == "online":
                    self.publish_discovery()

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.on_message = on_message
            client.reconnect_delay_set(min_delay=1, max_delay=30)
            client.connect_async(self.cfg.MQTT_BROKER, self.cfg.MQTT_PORT, 60)
            client.loop_start()
            self.client = client
        except Exception as exc:
            self.last_error = str(exc)
            self.client = None
            logger.warning("MQTT disabled after connection setup failure: %s", exc)

    def publish(self, subtopic, payload, retain=False, qos=0):
        if not (self.enabled and self.client and self.connected):
            return False
        try:
            self.client.publish(
                f"{self.cfg.MQTT_TOPIC_PREFIX}/{subtopic}",
                json.dumps(payload, separators=(",", ":"), default=str),
                qos=qos,
                retain=retain,
            )
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def publish_state(self, state):
        room = self._slug(self.cfg.MQTT_ROOM)
        self.publish(f"{room}/state", {
            "presence": bool(state.get("presence")),
            "activity": (state.get("activity") or {}).get("state", "unknown"),
            "breathing_trend": ((state.get("vitals") or {}).get("breaths_per_min")),
            "fall": bool(state.get("fall")),
            "track_count": len(state.get("tracks") or []),
            "timestamp": time.time(),
        })
        if state.get("fall"):
            self.publish(f"{room}/fall_event", state["fall"], retain=False, qos=1)

    def publish_discovery(self):
        if not (self.enabled and self.client and self.connected):
            return
        room = self._slug(self.cfg.MQTT_ROOM)
        base = f"{self.cfg.MQTT_TOPIC_PREFIX}/{room}/state"
        avail = f"{self.cfg.MQTT_TOPIC_PREFIX}/availability"
        device = {
            "identifiers": [f"wifisense_ultra_{room}"],
            "name": f"WiFiSense Ultra {self.cfg.MQTT_ROOM}",
            "manufacturer": self.cfg.BRAND_NAME,
            "model": "WiFiSense Ultra",
            "sw_version": self.cfg.VERSION,
        }
        origin = {"name": "WiFiSense Ultra", "sw_version": self.cfg.VERSION}
        items = {
            ("binary_sensor", "presence"): {
                "name": "Presence", "unique_id": f"wifisense_{room}_presence",
                "device_class": "occupancy", "state_topic": base,
                "value_template": "{{ 'ON' if value_json.presence else 'OFF' }}",
            },
            ("binary_sensor", "fall"): {
                "name": "Fall Event", "unique_id": f"wifisense_{room}_fall",
                "device_class": "safety", "state_topic": base,
                "value_template": "{{ 'ON' if value_json.fall else 'OFF' }}",
            },
            ("sensor", "activity"): {
                "name": "Activity", "unique_id": f"wifisense_{room}_activity",
                "state_topic": base, "value_template": "{{ value_json.activity }}",
            },
            ("sensor", "breathing_trend"): {
                "name": "Respiration Trend", "unique_id": f"wifisense_{room}_breathing",
                "state_topic": base, "value_template": "{{ value_json.breathing_trend }}",
                "unit_of_measurement": "breaths/min",
            },
            ("sensor", "track_count"): {
                "name": "Track Count", "unique_id": f"wifisense_{room}_track_count",
                "state_topic": base, "value_template": "{{ value_json.track_count }}",
            },
        }
        for (component, object_id), payload in items.items():
            payload = dict(payload)
            payload.update({
                "availability_topic": avail,
                "payload_available": "online",
                "payload_not_available": "offline",
                "device": device,
                "origin": origin,
            })
            topic = f"{self.cfg.MQTT_DISCOVERY_PREFIX}/{component}/wifisense_{room}/{object_id}/config"
            self.client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1, retain=True)

    def status(self):
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "broker": self.cfg.MQTT_BROKER if self.enabled else None,
            "last_error": self.last_error,
        }
