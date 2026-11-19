"""MQTT connection to the broker (IF-01), using the paho-mqtt client."""
from __future__ import annotations

import json
import logging
import ssl

import paho.mqtt.client as mqtt

from .config import Settings
from .db import SessionLocal
from .stock import handle_calib, handle_event, handle_heartbeat
from .sync import SyncWorker

log = logging.getLogger(__name__)


class MqttWorker:
    def __init__(self, settings: Settings, sync: SyncWorker):
        self.s = settings
        self.sync = sync
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="shelf-cloud")
        self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.tls_set(ca_certs=settings.mqtt_ca_file, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 30)

    def start(self) -> None:
        self.client.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def send_command(self, device_id: str, payload: dict) -> bool:
        info = self.client.publish(f"shelf/{device_id}/cmd", json.dumps(payload), qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected")
        client.subscribe([("shelf/+/event", 1), ("shelf/+/heartbeat", 0), ("shelf/+/calib", 1)])

    def _on_message(self, client, userdata, message):
        try:
            _, device_id, kind = message.topic.split("/", 2)
            payload = json.loads(message.payload)
        except ValueError:
            log.warning("ignored message on %s", message.topic)
            return
        with SessionLocal() as db:
            try:
                if kind == "event":
                    pos = handle_event(db, device_id, payload)
                    if pos is not None:
                        self.sync.request(pos.id)
                elif kind == "heartbeat":
                    handle_heartbeat(db, device_id, payload)
                elif kind == "calib":
                    handle_calib(db, device_id, payload)
            except Exception:
                db.rollback()
                log.exception("failed to handle %s", message.topic)
