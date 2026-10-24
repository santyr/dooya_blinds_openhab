"""Strict TOML configuration with file-backed credentials."""
from dataclasses import dataclass, field
from pathlib import Path
import ipaddress
import re
import tomllib
import math
import os
import stat
import uuid

from .protocol import ProtocolError, canonical_mac, discovery_profile


@dataclass(frozen=True)
class Shade:
    id: str
    mac: str
    invert: bool = False
    percentage: bool = False
    zone: str | None = None
    slot: int | None = None
    commissioned_epoch: str | None = None
    model: str | None = None
    firmware: str | None = None
    commissioning_ref: str | None = None
    supported_operations: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    bridge_host: str
    interface_ip: str
    key: str = field(repr=False)
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None = field(repr=False)
    mqtt_tls: bool
    prefix: str
    allow_commands: bool
    poll_seconds: int
    stale_seconds: int
    shades: tuple[Shade, ...]
    bridge_mac: str | None = None
    message_id_state_file: str | None = None
    owner_key: bytes | None = field(default=None, repr=False)
    receipt_journal_file: str | None = None
    request_timeout_seconds: float = 2.0
    poll_spacing_seconds: float = 1.0
    discovery_correlation: str = "echoed_id"


def secret(path: str, root: Path) -> str:
    if not isinstance(path, str):
        raise ValueError("Credential file path must be a string")
    value = (root / path).read_text().rstrip("\r\n")
    if not value:
        raise ValueError("Empty credential file")
    return value


def owner_secret(path, root):
    if not isinstance(path, str) or not path:
        raise ValueError("Invalid owner key path")
    try:
        descriptor = os.open(root / path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
                raise ValueError()
            value = stream.read(4097)
        if not 32 <= len(value) <= 4096:
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise ValueError("Owner key must be a private 0600 file containing 32..4096 bytes") from None


def load(path: str) -> Config:
    file = Path(path).resolve()
    with file.open("rb") as stream:
        raw = tomllib.load(stream)
    bridge, mqtt = raw["bridge"], raw["mqtt"]
    for section in (bridge, mqtt):
        if not isinstance(section, dict) or not isinstance(section.get("host"), str) or not section["host"] or len(section["host"]) > 253:
            raise ValueError("Invalid connection host")
    identity_path = bridge.get("message_id_state_file")
    if identity_path is not None and (not isinstance(identity_path, str) or not identity_path or "\x00" in identity_path):
        raise ValueError("Invalid message identity state path")
    try:
        bridge_mac = canonical_mac(bridge["mac"]) if "mac" in bridge else None
    except ProtocolError:
        raise ValueError("Invalid bridge identity") from None
    correlation = discovery_profile(bridge.get("discovery_correlation","echoed_id"),bridge_mac)
    interface = str(ipaddress.IPv4Address(bridge.get("interface_ip", "0.0.0.0")))
    key = secret(bridge["key_file"], file.parent)
    if len(key.encode()) != 16:
        raise ValueError("Bridge key must contain exactly 16 bytes")
    try:
        shades = tuple(Shade(**{**item, "mac": canonical_mac(item.get("mac"))}) for item in raw.get("shades", []))
    except (ProtocolError, TypeError, AttributeError):
        raise ValueError("Invalid shade configuration") from None
    if len({s.id for s in shades}) != len(shades) or len({s.mac for s in shades}) != len(shades):
        raise ValueError("Shade IDs and MACs must be unique")
    slots = [s.slot for s in shades if s.slot is not None]
    if len(shades) > 26 or len(set(slots)) != len(slots):
        raise ValueError("Shade inventory exceeds capacity or repeats a logical slot")
    for s in shades:
        if not re.fullmatch(r"[a-z][a-z0-9_\-]{0,47}", s.id) or s.id in ("service", "bridge"):
            raise ValueError("Invalid shade ID")
        if not re.fullmatch(r"[0-9A-Fa-f]{12,32}", s.mac):
            raise ValueError("Invalid shade MAC/device identifier")
        if type(s.invert) is not bool or type(s.percentage) is not bool:
            raise ValueError("Shade capability flags must be booleans")
        if s.zone not in (None, "kitchen", "living", "bathroom", "bedroom"):
            raise ValueError("Invalid shade zone")
        if (s.slot is None) != (s.zone is None):
            raise ValueError("Logical shade slot and zone must be supplied together")
        if s.slot is not None:
            if type(s.slot) is not int or not 1 <= s.slot <= 26:
                raise ValueError("Invalid logical shade slot")
            expected = "kitchen" if s.slot <= 8 else "living" if s.slot <= 17 else "bathroom" if s.slot <= 21 else "bedroom"
            if s.zone != expected:
                raise ValueError("Shade zone conflicts with logical slot")
        if not isinstance(s.supported_operations, (list, tuple)) or any(type(op) is not str or op not in ("UP", "DOWN", "STOP") for op in s.supported_operations) or len(set(s.supported_operations)) != len(s.supported_operations):
            raise ValueError("Invalid commissioned operations")
        if s.commissioned_epoch is not None:
            try:
                if type(s.commissioned_epoch) is not str or str(uuid.UUID(s.commissioned_epoch)) != s.commissioned_epoch:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ValueError("Invalid commissioning epoch") from None
            if s.zone is None or any(type(value) is not str or not value or len(value) > 256 for value in (s.model, s.firmware, s.commissioning_ref)):
                raise ValueError("Physical commissioning requires model, firmware, reference and logical mapping")
    shades = tuple(Shade(**{**s.__dict__, "supported_operations": tuple(s.supported_operations)}) for s in shades)
    prefix = mqtt.get("prefix", "earthship/shades/v2")
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
    timeout, spacing = raw.get("request_timeout_seconds", 2.0), raw.get("poll_spacing_seconds", 1.0)
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (timeout, spacing)) or not .05 <= timeout <= 10 or not .1 <= spacing <= 10:
        raise ValueError("Invalid bridge timeout or pacing")
    if shades and stale <= len(shades) * (timeout + spacing) + timeout:
        raise ValueError("Freshness interval cannot cover the paced inventory timeout budget")
    owner_key = owner_secret(raw["owner_key_file"], file.parent) if "owner_key_file" in raw else None
    journal = raw.get("receipt_journal_file")
    if journal is not None and (type(journal) is not str or not journal or "\x00" in journal):
        raise ValueError("Invalid receipt journal path")
    password = secret(mqtt["password_file"], file.parent) if mqtt.get("password_file") else None
    return Config(bridge["host"], interface, key, mqtt["host"], port,
                  mqtt.get("username"), password, tls, prefix, allow, poll, stale, shades,
                  bridge_mac, str(file.parent / identity_path) if identity_path is not None else None,
                  owner_key, str(file.parent / journal) if journal is not None else None,
                  float(timeout), float(spacing), correlation)
