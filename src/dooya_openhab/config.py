"""Strict TOML configuration with file-backed credentials."""
from dataclasses import dataclass
from pathlib import Path
import ipaddress
import re
import tomllib


@dataclass(frozen=True)
class Shade:
    id: str
    mac: str
    invert: bool = False
    percentage: bool = False


@dataclass(frozen=True)
class Config:
    bridge_host: str
    interface_ip: str
    key: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_tls: bool
    prefix: str
    allow_commands: bool
    poll_seconds: int
    stale_seconds: int
    shades: tuple[Shade, ...]


def secret(path: str, root: Path) -> str:
    if not isinstance(path, str):
        raise ValueError("Credential file path must be a string")
    value = (root / path).read_text().rstrip("\r\n")
    if not value:
        raise ValueError("Empty credential file")
    return value


def load(path: str) -> Config:
    file = Path(path).resolve()
    with file.open("rb") as stream:
        raw = tomllib.load(stream)
    bridge, mqtt = raw["bridge"], raw["mqtt"]
    interface = str(ipaddress.IPv4Address(bridge.get("interface_ip", "0.0.0.0")))
    key = secret(bridge["key_file"], file.parent)
    if len(key.encode()) != 16:
        raise ValueError("Bridge key must contain exactly 16 bytes")
    shades = tuple(Shade(**item) for item in raw.get("shades", []))
    if len({s.id for s in shades}) != len(shades) or len({s.mac for s in shades}) != len(shades):
        raise ValueError("Shade IDs and MACs must be unique")
    for s in shades:
        if not re.fullmatch(r"[a-z][a-z0-9_\-]{0,47}", s.id) or s.id in ("service", "bridge"):
            raise ValueError("Invalid shade ID")
        if not re.fullmatch(r"[0-9A-Fa-f]{12,32}", s.mac):
            raise ValueError("Invalid shade MAC/device identifier")
        if type(s.invert) is not bool or type(s.percentage) is not bool:
            raise ValueError("Shade capability flags must be booleans")
    prefix = mqtt.get("prefix", "earthship/shades/v1")
    if not isinstance(prefix, str) or not prefix or any(x in prefix for x in ("#", "+", "\x00")) or prefix.endswith("/"):
        raise ValueError("Invalid topic prefix")
    allow = raw.get("allow_commands", False)
    poll, stale = raw.get("poll_seconds", 600), raw.get("stale_seconds", 1800)
    port = mqtt.get("port", 1883)
    tls = mqtt.get("tls", False)
    if type(allow) is not bool or type(tls) is not bool:
        raise ValueError("Command/TLS flags must be booleans")
    if type(poll) is not int or type(stale) is not int or not 30 <= poll < stale:
        raise ValueError("Require 30 <= poll_seconds < stale_seconds")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid MQTT port")
    password = secret(mqtt["password_file"], file.parent) if mqtt.get("password_file") else None
    return Config(bridge["host"], interface, key, mqtt["host"], port,
                  mqtt.get("username"), password, tls, prefix, allow, poll, stale, shades)
