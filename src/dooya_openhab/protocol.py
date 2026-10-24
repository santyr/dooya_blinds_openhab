"""Small WLAN v1.03 subset. No commands that pair, calibrate or reset motors."""
from __future__ import annotations

import datetime as dt
from contextlib import nullcontext
import fcntl
import math
import os
from pathlib import Path
import re
import stat
import uuid
import json
import socket
import threading
import time

from Cryptodome.Cipher import AES

GROUP = "238.0.0.18"
REPORT_PORT = 32101


class ProtocolError(Exception):
    """Sanitized protocol error; never include credentials or raw response."""


# Local resource bounds, not claims about an undocumented bridge maximum.
MAX_PACKET_BYTES = 32768
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 2048
SINGLE_AXIS_TYPES = frozenset({"10000000"})
DIAGNOSTIC_ENUMS = {"wirelessMode": range(6), "currentState": range(5),
                    "voltageMode": (0, 1), "chargingState": (0, 1),
                    "operation": (0, 1, 2, 5)}


def canonical_mac(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{12,32}", value):
        raise ProtocolError("Invalid device identity")
    return value.upper()


def single_axis_profile(device_type, data) -> bool:
    """The implemented roller/honeycomb subset, never physical commissioning."""
    return (
        isinstance(device_type, str) and device_type in SINGLE_AXIS_TYPES and isinstance(data, dict)
        and type(data.get("type")) is int and data["type"] in (1, 4)
        and type(data.get("wirelessMode")) is int and data["wirelessMode"] == 1
        and all(isinstance(key, str) and not key.endswith(("_T", "_B")) for key in data)
    )


def access_token(key: str, token: str) -> str:
    try:
        k, t = key.encode("utf-8"), token.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise ProtocolError("KEY and token must each contain exactly 16 bytes") from None
    if len(k) != 16 or len(t) != 16:
        raise ProtocolError("KEY and token must each contain exactly 16 bytes")
    return AES.new(k, AES.MODE_ECB).encrypt(t).hex().upper()


class MessageIDs:
    """Calendar-safe UTC-shaped IDs; durable high-water is required across restarts.

    Without state_path uniqueness is process-only. session_id identifies that local
    correlation generation, and is never presented as a bridge authentication token.
    """
    def __init__(self, clock=None, state_path=None):
        self.last = None
        self.lock = threading.Lock()
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.state_path = Path(state_path) if state_path is not None else None
        self.session_id = uuid.uuid4().hex

    def _advance(self, previous=None):
        now = self.clock().astimezone(dt.timezone.utc)
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        candidates = [stamp for stamp in (self.last, previous) if stamp is not None]
        if candidates and now <= max(candidates):
            now = max(candidates) + dt.timedelta(milliseconds=1)
        self.last = now
        return now.strftime("%Y%m%d%H%M%S%f")[:17]

    def next(self) -> str:
        with self.lock:
            if self.state_path is None:
                return self._advance()
            try:
                descriptor = os.open(self.state_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "r+", encoding="ascii") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                        raise ValueError("Invalid high-water file")
                    text = stream.read(128)
                    if text and not re.fullmatch(r"[0-9]{17}\n?", text):
                        raise ValueError("Invalid high-water record")
                    previous = dt.datetime.strptime(text.strip(), "%Y%m%d%H%M%S%f").replace(tzinfo=dt.timezone.utc) if text else None
                    value = self._advance(previous)
                    stream.seek(0)
                    stream.write(value + "\n")
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                    directory = os.open(self.state_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                    return value
            except (OSError, ValueError, OverflowError, UnicodeError):
                raise ProtocolError("Persistent message identity unavailable") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def validate_json(value):
    """Bound recursive work and reject all non-finite numeric values."""
    stack = [(value, 0)]
    count = 0
    while stack:
        current, depth = stack.pop()
        count += 1
        if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ProtocolError("JSON packet exceeds resource bounds")
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                raise ProtocolError("Invalid JSON object")
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif type(current) is float:
            if not math.isfinite(current):
                raise ProtocolError("Invalid numeric field")
        elif current is not None and type(current) not in (str, int, bool):
            raise ProtocolError("Invalid JSON value")


def decode(raw: bytes) -> dict:
    if not isinstance(raw, bytes) or len(raw) > MAX_PACKET_BYTES:
        raise ProtocolError("Invalid packet size")
    try:
        message = json.loads(raw, object_pairs_hook=_unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("Invalid JSON packet") from None
    validate_json(message)
    if not isinstance(message, dict) or not isinstance(message.get("msgType"), str):
        raise ProtocolError("Invalid packet envelope")
    if "mac" in message:
        message["mac"] = canonical_mac(message["mac"])
    if "msgID" in message and (type(message["msgID"]) is not str or not message["msgID"] or len(message["msgID"]) > 64):
        raise ProtocolError("Invalid correlation identity")
    data = message.get("data")
    if isinstance(data, dict):
        for field in ("wirelessMode", "operation", "currentState", "voltageMode", "chargingState", "type"):
            if field in data and type(data[field]) is not int:
                raise ProtocolError("Invalid diagnostic field")
        for field, allowed in DIAGNOSTIC_ENUMS.items():
            if field in data and data[field] not in allowed:
                raise ProtocolError("Invalid diagnostic enum")
        for field in ("currentPosition", "targetPosition"):
            if field in data and (type(data[field]) is not int or not 0 <= data[field] <= 100):
                raise ProtocolError("Invalid position field")
        for field in ("RSSI", "batteryLevel"):
            if field in data and type(data[field]) not in (int, float):
                raise ProtocolError("Invalid diagnostic field")
    return message


DISCOVERY_CORRELATIONS = frozenset(("echoed_id", "pinned_discovery_without_id"))


def discovery_profile(profile, bridge_mac):
    if type(profile) is not str or profile not in DISCOVERY_CORRELATIONS:
        raise ValueError("Invalid discovery correlation profile")
    if profile == "pinned_discovery_without_id" and bridge_mac is None:
        raise ValueError("No-ID discovery requires a configured bridge identity")
    return profile


def discovery_snapshot(response, expected_bridge=None, *, require_bridge_type=False):
    """Validate the complete public discovery contract before any state changes."""
    validate_json(response)
    bridge_mac = canonical_mac(response.get("mac"))
    if expected_bridge is not None and bridge_mac != expected_bridge:
        raise ProtocolError("Bridge identity mismatch; commissioning required")
    if require_bridge_type and response.get("deviceType") != "02000001":
        raise ProtocolError("Unsupported discovery bridge type")
    data = response.get("data")
    if not isinstance(data, list) or len(data) > 256:
        raise ProtocolError("Invalid discovery response")
    token = response.get("token")
    try:
        if type(token) is not str or len(token.encode("utf-8")) != 16:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ProtocolError("Discovery did not supply a valid token") from None
    devices = {}; diagnostic = set()
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("deviceType"), str) or not re.fullmatch(r"[0-9A-Fa-f]{8}", item["deviceType"]):
            raise ProtocolError("Invalid discovery device")
        mac = canonical_mac(item.get("mac"))
        if mac in devices:
            raise ProtocolError("Duplicate discovery identity")
        devices[mac] = item["deviceType"].upper()
        if (("type" in item and (type(item["type"]) is not int or item["type"] not in (1, 4)))
            or ("wirelessMode" in item and (type(item["wirelessMode"]) is not int or item["wirelessMode"] != 1))
            or any(key.endswith(("_T", "_B")) for key in item)):
            diagnostic.add(mac)
    return bridge_mac, token, devices, diagnostic


class UDPTransport:
    """Strict unicast matching with one monotonic budget and a final send guard."""
    def __init__(self, host: str, port: int = 32100, timeout: float = 2, *, clock=None, socket_factory=None,
                 discovery_correlation="echoed_id", bridge_mac=None):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("Invalid UDP timeout")
        self.address = (socket.gethostbyname(host), port)
        self.timeout = timeout
        self.clock = clock or time.monotonic
        self.socket_factory = socket_factory or socket.socket
        self.expected_bridge_mac = canonical_mac(bridge_mac) if bridge_mac is not None else None
        self.discovery_correlation = discovery_profile(discovery_correlation,self.expected_bridge_mac)

    def request(self, message: dict, *, before_send=None, cancelled=None, send_guard=None) -> dict:
        expected = message["msgType"] + "Ack"
        validate_json(message)
        try:
            payload = json.dumps(message, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError):
            raise ProtocolError("Invalid request packet") from None
        if len(payload) > MAX_PACKET_BYTES:
            raise ProtocolError("Invalid request size")
        target = canonical_mac(message["mac"]) if "mac" in message else None
        deadline = self.clock() + self.timeout
        with self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # The owner lock spans authorization and actual send, not the reply wait.
            with send_guard() if send_guard is not None else nullcontext():
                if cancelled is not None and cancelled():
                    raise ProtocolError("Request cancelled before dispatch")
                if before_send is not None and before_send() is False:
                    raise ProtocolError("Request refused before dispatch")
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise TimeoutError("Bridge response timed out before dispatch")
                sock.settimeout(min(remaining, .05))
                sock.sendto(payload, self.address)
            while True:
                if cancelled is not None and cancelled():
                    raise ProtocolError("Request cancelled; dispatched outcome unknown")
                remaining = deadline - self.clock()
                if remaining <= 0:
                    raise TimeoutError("Bridge response timed out")
                sock.settimeout(min(remaining, .05))
                try:
                    raw, sender = sock.recvfrom(MAX_PACKET_BYTES + 1)
                except socket.timeout:
                    continue
                if sender != self.address:
                    continue
                try:
                    response = decode(raw)
                except ProtocolError:
                    continue
                if response.get("msgType") != expected:
                    continue
                no_id_discovery = (message["msgType"] == "GetDeviceList"
                    and "msgID" not in response
                    and self.discovery_correlation == "pinned_discovery_without_id")
                if not no_id_discovery and response.get("msgID") != message["msgID"]:
                    continue
                if message["msgType"] == "GetDeviceList" and self.discovery_correlation == "pinned_discovery_without_id":
                    try:
                        discovery_snapshot(response,self.expected_bridge_mac,require_bridge_type=True)
                    except ProtocolError:
                        continue
                if target is not None and response.get("mac") != target:
                    continue
                if "deviceType" in message and response.get("deviceType") != message["deviceType"]:
                    continue
                return response


class Connector:
    def __init__(self, transport, key: str, bridge_mac=None, ids=None, discovery_correlation="echoed_id"):
        self.transport = transport
        self.key = key
        self.token = None
        self.ids = ids or MessageIDs()
        self.devices: dict[str, str] = {}
        self.device_profiles: dict[str, dict] = {}
        self.diagnostic_devices: set[str] = set()
        self.bridge_mac = canonical_mac(bridge_mac) if bridge_mac is not None else None
        self.expected_bridge_mac = self.bridge_mac
        self.discovery_correlation = discovery_profile(discovery_correlation,self.expected_bridge_mac)
        if isinstance(transport,UDPTransport):
            transport.discovery_correlation = self.discovery_correlation
            transport.expected_bridge_mac = self.expected_bridge_mac

    def observe(self, message: dict):
        kind = message.get("msgType")
        if kind == "Heartbeat":
            if self.bridge_mac is None or self.token is None:
                return
            try:
                if canonical_mac(message.get("mac")) != self.bridge_mac:
                    return
                token = message.get("token")
                access_token(self.key, token)
                validate_json(message)
            except ProtocolError:
                return
            if token != self.token:
                self.device_profiles.clear()
            self.token = token
        elif kind in ("ReadDeviceAck", "WriteDeviceAck", "Report"):
            try:
                mac = canonical_mac(message.get("mac"))
            except ProtocolError:
                return
            if mac not in self.devices or mac in self.diagnostic_devices:
                return
            try:
                checked = decode(json.dumps(message, allow_nan=False).encode("utf-8"))
                data = checked.get("data")
                if not isinstance(data, dict) or checked.get("deviceType") != self.devices[mac]:
                    raise ProtocolError("Invalid device profile")
                merged = {**self.device_profiles.get(mac, {}), **data}
                if not single_axis_profile(self.devices[mac], merged):
                    raise ProtocolError("Unsupported device profile")
            except (ProtocolError, ValueError, UnicodeError):
                self.device_profiles.pop(mac, None)
                return
            self.device_profiles[mac] = {"type": merged["type"], "wirelessMode": merged["wirelessMode"]}

    def _request(self, kind: str, *, before_send=None, cancelled=None, send_guard=None, **fields) -> dict:
        packet = {"msgType": kind, "msgID": self.ids.next(), **fields}
        if kind != "GetDeviceList":
            if self.token is None:
                raise ProtocolError("Discovery required before authenticated requests")
            packet["AccessToken"] = access_token(self.key, self.token)
        options = {key: value for key, value in (("before_send", before_send), ("cancelled", cancelled), ("send_guard", send_guard)) if value is not None}
        response = self.transport.request(packet, **options)
        result = response.get("actionResult")
        if result not in (None, "success", "Success", "ok", "OK"):
            raise ProtocolError("Bridge rejected request; inspect firmware/key compatibility")
        self.observe(response)
        return response

    def discover(self, **options) -> list[dict]:
        response = self._request("GetDeviceList", **options)
        bridge_mac, token, devices, diagnostic = discovery_snapshot(response,self.bridge_mac,
            require_bridge_type=self.discovery_correlation == "pinned_discovery_without_id")
        access_token(self.key,token)
        # Commit all authentication/inventory fields only after complete validation.
        if self.token != token:
            self.device_profiles.clear()
        else:
            self.device_profiles = {mac: profile for mac, profile in self.device_profiles.items()
                                    if mac in devices and devices[mac] == self.devices.get(mac)}
        self.bridge_mac, self.token, self.devices = bridge_mac, token, devices
        self.diagnostic_devices = diagnostic
        self.device_profiles = {mac: profile for mac, profile in self.device_profiles.items() if mac not in diagnostic}
        return [{"mac": mac, "deviceType": typ} for mac, typ in devices.items()]

    def device_request(self, mac: str, data: dict | None = None, **options) -> dict:
        mac = canonical_mac(mac)
        if mac not in self.devices:
            raise ProtocolError("Device not discovered on this bridge")
        if data is not None:
            if not single_axis_profile(self.devices[mac], self.device_profiles.get(mac)):
                raise ProtocolError("Device profile is diagnostic only; read/commission supported single-axis mode first")
            if not isinstance(data, dict) or not (
                (set(data) == {"operation"} and type(data["operation"]) is int and data["operation"] in (0, 1, 2, 5)) or
                (set(data) == {"targetPosition"} and type(data["targetPosition"]) is int and 0 <= data["targetPosition"] <= 100)
            ):
                raise ProtocolError("Unsupported device operation")
        fields = {"mac": mac, "deviceType": self.devices[mac]}
        if data is not None:
            fields["data"] = data
        return self._request("ReadDevice" if data is None else "WriteDevice", **options, **fields)

    def command(self, mac: str, command: str | int, invert: bool = False, **options) -> dict:
        if type(command) is int and 0 <= command <= 100:
            data = {"targetPosition": 100 - command if invert else command}
        elif command in ("UP", "DOWN", "STOP"):
            op = {"UP": 1, "DOWN": 0, "STOP": 2}[command]
            data = {"operation": 1 - op if invert and op != 2 else op}
        else:
            raise ValueError("Unsupported command")
        return self.device_request(mac, data, **options)


class ReportSocket:
    """Nonblocking multicast reader. The service owns all protocol state."""
    def __init__(self, bridge_ip: str, interface_ip: str):
        self.bridge_ip = socket.gethostbyname(bridge_ip)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("", REPORT_PORT))
            membership = socket.inet_aton(GROUP) + socket.inet_aton(interface_ip)
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            self.sock.setblocking(False)
        except Exception:
            self.sock.close()
            raise

    def receive(self) -> dict | None:
        try:
            raw, address = self.sock.recvfrom(65535)
        except BlockingIOError:
            return None
        if address[0] != self.bridge_ip:
            return {}
        try:
            return decode(raw)
        except ProtocolError:
            return {}

    def close(self):
        self.sock.close()
