"""Single-worker bridge IO with MQTT callbacks restricted to queue operations."""
from __future__ import annotations

import json
import logging
import threading
import time

import paho.mqtt.client as mqtt

from .model import Commands, State, parse_command
from .protocol import Connector, ProtocolError, ReportSocket, UDPTransport

LOG = logging.getLogger(__name__)


class Service:
    def __init__(self, config, connector=None, client=None):
        self.config = config
        self.connector = connector or Connector(UDPTransport(config.bridge_host), config.key)
        self.shades = {s.id: s for s in config.shades}
        self.by_mac = {s.mac: s.id for s in config.shades}
        self.states = {s.id: State(s.invert) for s in config.shades}
        self.commands = Commands()
        self.connected = threading.Event()
        self.reset = threading.Event()
        self.stop = threading.Event()
        self.ready = False
        self.bridge_seen = None
        self.sent = {}
        self.client = client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, clean_session=True)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.will_set(self.topic("service/availability"), "offline", qos=1, retain=True)
        if config.mqtt_username:
            self.client.username_pw_set(config.mqtt_username, config.mqtt_password)
        if config.mqtt_tls:
            self.client.tls_set()
        self.client.reconnect_delay_set(1, 30)

    def topic(self, suffix):
        return f"{self.config.prefix}/{suffix}"

    def publish(self, suffix, value, retain=True):
        if not self.connected.is_set():
            return
        payload = json.dumps(value, sort_keys=True) if isinstance(value, dict) else str(value)
        # Publish state transitions only, except unretained command outcomes.
        if retain and self.sent.get(suffix) == payload:
            return
        info = self.client.publish(self.topic(suffix), payload, qos=0, retain=retain)
        if retain and info.rc == mqtt.MQTT_ERR_SUCCESS:
            self.sent[suffix] = payload

    def event(self, shade, status, reason):
        self.publish(f"{shade}/event", {"status": status, "reason": reason}, retain=False)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            LOG.warning("MQTT connection rejected")
            return
        self.ready = False
        self.commands.clear()
        self.reset.set()
        self.connected.set()
        client.subscribe(self.topic("+/command"), qos=0)

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.ready = False
        self.connected.clear()
        self.commands.clear()

    def on_message(self, client, userdata, message):
        stem = self.topic("")
        if not message.topic.startswith(stem):
            return
        parts = message.topic[len(stem):].split("/")
        if len(parts) != 2 or parts[1] != "command" or parts[0] not in self.shades:
            return
        shade = parts[0]
        if message.retain:
            self.event(shade, "rejected", "retained_command")
            return
        if not self.config.allow_commands or not self.ready or not self.connected.is_set():
            self.event(shade, "rejected", "movement_disabled_or_not_ready")
            return
        try:
            command = parse_command(message.payload)
        except ValueError:
            self.event(shade, "rejected", "invalid_command")
            return
        if not self.commands.put(shade, command):
            self.event(shade, "rejected", "queue_full_or_stop_pending")

    def handle_report(self, message, now):
        kind = message.get("msgType")
        if kind == "Heartbeat" and message.get("mac") == self.connector.bridge_mac:
            self.connector.observe(message)
            self.bridge_seen = now
        elif kind == "Report" and message.get("mac") in self.by_mac:
            self.states[self.by_mac[message["mac"]]].accept(message, now)
            self.bridge_seen = now

    def publish_states(self, now):
        bridge_ok = self.bridge_seen is not None and now - self.bridge_seen <= 180
        self.publish("bridge/availability", "online" if bridge_ok else "offline")
        if not bridge_ok:
            self.ready = False
        for sid, state in self.states.items():
            snap = state.snapshot(now, self.config.stale_seconds)
            available = bridge_ok and not snap["stale"]
            self.publish(f"{sid}/availability", "online" if available else "offline")
            self.publish(f"{sid}/position", snap["reported_position"] if available else "UNDEF")
            self.publish(f"{sid}/state", snap)

    def execute(self, sid, command):
        if not self.config.allow_commands or not self.ready or not self.connected.is_set():
            self.event(sid, "rejected", "movement_disabled_or_not_ready")
            return
        shade, state = self.shades[sid], self.states[sid]
        if type(command) is int:
            mode = state.report.get("wirelessMode", state.cache.get("wirelessMode"))
            if not shade.percentage or mode != 1:
                self.event(sid, "rejected", "percentage_not_qualified")
                return
        try:
            response = self.connector.command(shade.mac, command, shade.invert)
            self.bridge_seen = time.time()
            state.accept(response, self.bridge_seen)
            self.event(sid, "acknowledged", "await_motor_report")
        except (OSError, ProtocolError, ValueError):
            self.ready = False
            self.commands.clear()
            self.event(sid, "uncertain", "bridge_error_no_retry")
            LOG.warning("Command outcome uncertain for %s; not retrying", sid)

    def run(self):
        reports = ReportSocket(self.config.bridge_host, self.config.interface_ip)
        self.client.connect_async(self.config.mqtt_host, self.config.mqtt_port, keepalive=30)
        self.client.loop_start()
        next_discovery = next_poll = 0.0
        poll_ids = []
        poll_due = 0.0
        try:
            while not self.stop.is_set():
                now = time.time()
                if self.reset.is_set():
                    self.reset.clear()
                    self.sent.clear()
                    self.states = {s.id: State(s.invert) for s in self.config.shades}
                    self.bridge_seen = None
                    self.publish("service/availability", "online")
                    self.publish_states(now)
                    next_discovery = next_poll = 0
                # Bound draining to prevent malformed/flooded multicast starving commands.
                for _ in range(100):
                    message = reports.receive()
                    if message is None:
                        break
                    self.handle_report(message, now)
                if self.connected.is_set() and now >= next_discovery:
                    try:
                        self.connector.discover()
                        self.bridge_seen = time.time()
                        self.ready = True
                        next_discovery = now + 60
                    except (OSError, ProtocolError, ValueError):
                        self.ready = False
                        self.bridge_seen = None
                        self.commands.clear()
                        next_discovery = now + 15
                        LOG.warning("Bridge discovery failed; retry in 15 seconds")
                job = self.commands.pop()
                if job:
                    self.execute(*job)
                elif self.ready:
                    if now >= next_poll:
                        poll_ids = list(self.shades)
                        next_poll = now + self.config.poll_seconds
                    if poll_ids and now >= poll_due:
                        sid = poll_ids.pop(0)
                        try:
                            # operation 5 asks the motor to send a Report; Ack stays cached.
                            response = self.connector.device_request(self.shades[sid].mac, {"operation": 5})
                            self.states[sid].accept(response, time.time())
                            self.bridge_seen = time.time()
                        except (OSError, ProtocolError, ValueError):
                            LOG.warning("Status request failed for %s", sid)
                        poll_due = time.time() + 1
                self.publish_states(time.time())
                self.stop.wait(0.1)
        finally:
            self.ready = False
            self.commands.clear()
            for sid in self.shades:
                self.publish(f"{sid}/availability", "offline")
                self.publish(f"{sid}/position", "UNDEF")
            self.publish("bridge/availability", "offline")
            if self.connected.is_set():
                receipt = self.client.publish(self.topic("service/availability"), "offline", qos=1, retain=True)
                try:
                    receipt.wait_for_publish(timeout=2)
                except (RuntimeError, ValueError):
                    pass
            self.client.disconnect()
            self.client.loop_stop()
            reports.close()
