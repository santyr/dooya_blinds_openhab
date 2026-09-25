"""Small WLAN v1.03 subset. No commands that pair, calibrate or reset motors."""
from __future__ import annotations

import datetime as dt
import json
import socket
import threading
import time

from Cryptodome.Cipher import AES

GROUP = "238.0.0.18"
REPORT_PORT = 32101


class ProtocolError(Exception):
    """Sanitized protocol error; never include credentials or raw response."""


def access_token(key: str, token: str) -> str:
    k, t = key.encode("utf-8"), token.encode("utf-8")
    if len(k) != 16 or len(t) != 16:
        raise ProtocolError("KEY and token must each contain exactly 16 bytes")
    return AES.new(k, AES.MODE_ECB).encrypt(t).hex().upper()


class MessageIDs:
    def __init__(self):
        self.last = 0
        self.lock = threading.Lock()

    def next(self) -> str:
        with self.lock:
            now = int(dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S%f")[:17])
            self.last = max(now, self.last + 1)
            return str(self.last)


def decode(raw: bytes) -> dict:
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("Invalid JSON packet") from exc
    if not isinstance(message, dict) or not isinstance(message.get("msgType"), str):
        raise ProtocolError("Invalid packet envelope")
    return message


class UDPTransport:
    """One unicast operation at a time, replies matched by source and message ID."""
    def __init__(self, host: str, port: int = 32100, timeout: float = 2):
        self.address = (socket.gethostbyname(host), port)
        self.timeout = timeout

    def request(self, message: dict) -> dict:
        expected = message["msgType"] + "Ack"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(self.timeout)
            sock.sendto(json.dumps(message).encode(), self.address)
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Bridge response timed out")
                sock.settimeout(remaining)
                raw, sender = sock.recvfrom(65535)
                if sender != self.address:
                    continue
                try:
                    response = decode(raw)
                except ProtocolError:
                    continue
                if response.get("msgID") != message["msgID"]:
                    continue
                if response.get("msgType") != expected:
                    continue
                if "mac" in message and response.get("mac") != message["mac"]:
                    continue
                return response


class Connector:
    def __init__(self, transport, key: str):
        self.transport = transport
        self.key = key
        self.token = None
        self.ids = MessageIDs()
        self.devices: dict[str, str] = {}
        self.bridge_mac = None

    def observe(self, message: dict):
        # Only bridge-level discovery/heartbeats may rotate authentication state.
        if message.get("msgType") in ("GetDeviceListAck", "Heartbeat"):
            if self.bridge_mac and message.get("mac") != self.bridge_mac:
                return
            token = message.get("token")
            if isinstance(token, str) and len(token.encode()) == 16:
                self.token = token

    def _request(self, kind: str, **fields) -> dict:
        packet = {"msgType": kind, "msgID": self.ids.next(), **fields}
        if kind != "GetDeviceList":
            if self.token is None:
                raise ProtocolError("Discovery required before authenticated requests")
            packet["AccessToken"] = access_token(self.key, self.token)
        response = self.transport.request(packet)
        result = response.get("actionResult")
        if result not in (None, "success", "Success", "ok", "OK"):
            # Never echo an arbitrary remote error, which may contain secrets.
            raise ProtocolError("Bridge rejected request; inspect firmware/key compatibility")
        self.observe(response)
        return response

    def discover(self) -> list[dict]:
        response = self._request("GetDeviceList")
        data = response.get("data")
        if not isinstance(data, list) or not isinstance(response.get("mac"), str):
            raise ProtocolError("Invalid discovery response")
        devices = {}
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("mac"), str) or not isinstance(item.get("deviceType"), str):
                raise ProtocolError("Invalid discovery device")
            if item["deviceType"].startswith("1000"):
                devices[item["mac"]] = item["deviceType"]
        self.bridge_mac = response["mac"]
        self.devices = devices
        if self.token is None:
            raise ProtocolError("Discovery did not supply a valid token")
        return [{"mac": mac, "deviceType": typ} for mac, typ in devices.items()]

    def device_request(self, mac: str, data: dict | None = None) -> dict:
        if mac not in self.devices:
            raise ProtocolError("Device not discovered on this bridge")
        fields = {"mac": mac, "deviceType": self.devices[mac]}
        if data is not None:
            fields["data"] = data
        return self._request("ReadDevice" if data is None else "WriteDevice", **fields)

    def command(self, mac: str, command: str | int, invert: bool = False) -> dict:
        if type(command) is int and 0 <= command <= 100:
            data = {"targetPosition": 100 - command if invert else command}
        elif command in ("UP", "DOWN", "STOP"):
            op = {"UP": 1, "DOWN": 0, "STOP": 2}[command]
            data = {"operation": 1 - op if invert and op != 2 else op}
        else:
            raise ValueError("Unsupported command")
        return self.device_request(mac, data)


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
