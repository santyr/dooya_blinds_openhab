"""State provenance and bounded command queue, independent of IO."""
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from copy import deepcopy
import hashlib
import json
import math
import re
import threading
import uuid

from .protocol import DIAGNOSTIC_ENUMS, ProtocolError, single_axis_profile, validate_json

FIELDS = {"currentPosition", "operation", "currentState", "batteryLevel", "wirelessMode",
          "voltageMode", "RSSI", "chargingState", "type"}


def _valid_field(name, value):
    # Booleans are never protocol numbers. Preserve raw battery/RSSI units;
    # finite floats are supported for these diagnostics, without estimating %.
    if name in {"batteryLevel", "RSSI"}:
        return ((type(value) is int and -(2**63) <= value < 2**63)
                or (type(value) is float and math.isfinite(value)))
    if type(value) is not int or not -(2**63) <= value < 2**63:
        return False
    if name in DIAGNOSTIC_ENUMS and value not in DIAGNOSTIC_ENUMS[name]:
        return False
    return name != "currentPosition" or 0 <= value <= 100


def safe_data(message):
    data = message.get("data", {}) if isinstance(message, dict) else {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in FIELDS and _valid_field(k, v)}


def _message_time(value):
    """Parse only the working WLAN v1.03 timestamp-shaped ID contract.

    This establishes ordering, never motor measurement time or trusted UTC.
    Opaque IDs and clock rollback require withholding current usability.
    """
    if type(value) is not str or re.fullmatch(r"[0-9]{17}", value) is None:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S%f")
    except ValueError:
        return None


def _finite_time(value):
    return ((type(value) is int and -(2**63) <= value < 2**63)
            or (type(value) is float and math.isfinite(value)))


def _valid_commissioned_epoch(value):
    if value is None:
        return True
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def source_identity_sha256(bridge_id, device_id, commissioned_epoch=None):
    """Public identity digest, using exact reconciled strings and canonical ASCII.

    This binds an observation to its original configured commissioning identity;
    it authenticates neither a packet nor a physical motor/bridge boot session.
    """
    if (any(type(value) is not str or not 0 < len(value) <= 128
            or not value.isascii() or any(ord(c) < 32 for c in value)
            for value in (bridge_id, device_id))
            or not _valid_commissioned_epoch(commissioned_epoch)):
        raise ValueError("Invalid source identity")
    value = {"bridge_id": bridge_id, "device_id": device_id,
             "commissioned_epoch": commissioned_epoch}
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def parse_command(payload: bytes) -> str | int:
    try:
        text = payload.decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid command encoding") from exc
    if text in ("UP", "DOWN", "STOP"):
        return text
    if re.fullmatch(r"(?:0|[1-9][0-9]?|100)", text):
        return int(text)
    raise ValueError("Expected UP/DOWN/STOP or integer 0..100")


@dataclass
class State:
    """Last-known history plus conservative, local Report usability.

    The caller validates the sender against bridge inventory and supplies the
    ingress session/generation. An adapter session is not a hardware boot ID.
    No receipt timestamp certifies when the bridge actually measured a motor.
    """
    invert: bool = False
    report: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    position: int | None = None
    position_at: float | None = None
    _session: dict | None = field(default=None, init=False, repr=False)
    _observation: dict | None = field(default=None, init=False, repr=False)
    _motor_contact: dict | None = field(default=None, init=False, repr=False)
    _profile: dict = field(default_factory=dict, init=False, repr=False)
    _watermark: str | None = field(default=None, init=False, repr=False)
    _watermark_identity: dict | None = field(default=None, init=False, repr=False)
    _current: bool = field(default=False, init=False, repr=False)
    _reason: str = field(default="source_session_unknown", init=False, repr=False)
    _movement: str = field(default="unknown", init=False, repr=False)
    _target: int | str | None = field(default=None, init=False, repr=False)
    _request_id: str | None = field(default=None, init=False, repr=False)

    def begin_session(self, *, session_id, source_generation, bridge_id,
                      device_id, device_type, now, monotonic_now, motor_profile=None,
                      commissioned_epoch=None):
        if (any(type(v) is not str or not 0 < len(v) <= 128
                for v in (session_id, bridge_id, device_id, device_type))
                or type(source_generation) is not int or source_generation < 0
                or not _finite_time(now) or not _finite_time(monotonic_now)
                or not _valid_commissioned_epoch(commissioned_epoch)):
            raise ValueError("Invalid observation session")
        # Validate digest inputs before mutating the existing session.
        source_identity_sha256(bridge_id, device_id, commissioned_epoch)
        if motor_profile is not None and not single_axis_profile(device_type, motor_profile):
            raise ValueError("Invalid reconciled motor profile")
        self._profile = ({k: motor_profile[k] for k in ("wirelessMode", "type")}
                         if motor_profile is not None else {})
        identity = {"bridge_id": bridge_id, "device_id": device_id, "device_type": device_type}
        if self._watermark_identity is not None and identity != self._watermark_identity:
            self._watermark = None
        self._watermark_identity = identity
        self._session = {"adapter_session_id": session_id,
                         "source_generation": source_generation,
                         "bridge_id": bridge_id, "device_id": device_id,
                         "device_type": device_type, "commissioned_epoch": commissioned_epoch,
                         "started_at": now, "started_monotonic": monotonic_now}
        self.invalidate("await_current_session_report")

    def export_watermark(self):
        if self._watermark_identity is None or self._watermark is None:
            return None
        return {**self._watermark_identity, "msg_id": self._watermark}

    def import_watermark(self, record):
        if (self._session is None or not isinstance(record, dict)
                or any(record.get(k) != self._session[k] for k in
                       ("bridge_id", "device_id", "device_type"))
                or _message_time(record.get("msg_id")) is None):
            return False
        if (self._watermark is None
                or _message_time(record["msg_id"]) > _message_time(self._watermark)):
            self._watermark = record["msg_id"]
        return True

    def export_history(self):
        """Private bounded history; original times survive, current authority does not."""
        return {"schema": "dooya-state-history/v1", "invert": self.invert,
                "reported_position": self.position, "report_received_at": self.position_at,
                "report": dict(self.report), "cache": dict(self.cache),
                "observation": deepcopy(self._observation),
                "motor_contact": deepcopy(self._motor_contact),
                "watermark": self.export_watermark()}

    @staticmethod
    def _valid_history_source(value):
        if not isinstance(value, dict):
            return False
        for key in ("adapter_session_id", "bridge_id", "device_id", "device_type"):
            if value.get(key) is not None and (type(value[key]) is not str
                                             or not 0 < len(value[key]) <= 128):
                return False
        generation = value.get("source_generation")
        if generation is not None and (type(generation) is not int or not 0 <= generation < 2**63):
            return False
        provenance = {"commissioned_epoch", "source_identity_sha256"}
        if not provenance.intersection(value):
            return True  # Older private journals retain unknown original provenance.
        if not provenance.issubset(value) or not _valid_commissioned_epoch(value["commissioned_epoch"]):
            return False
        digest = value["source_identity_sha256"]
        if digest is None:
            return value["commissioned_epoch"] is None
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return False
        try:
            return digest == source_identity_sha256(value["bridge_id"], value["device_id"],
                                                    value["commissioned_epoch"])
        except ValueError:
            return False

    def import_history(self, record):
        """Restore before session reconciliation; never import target or usability."""
        required = {"schema", "invert", "reported_position", "report_received_at",
                    "report", "cache", "observation", "motor_contact", "watermark"}
        if (self._session is not None or self.position_at is not None
                or type(record) is not dict or set(record) != required
                or record["schema"] != "dooya-state-history/v1"
                or type(record["invert"]) is not bool or record["invert"] != self.invert):
            return False
        position, received = record["reported_position"], record["report_received_at"]
        if position is None:
            if received is not None or record["observation"] is not None:
                return False
        elif (type(position) is not int or not 0 <= position <= 100
              or not _finite_time(received) or record["observation"] is None):
            return False
        for key in ("report", "cache"):
            data = record[key]
            if (type(data) is not dict or set(data) - FIELDS
                    or any(not _valid_field(k, v) for k, v in data.items())):
                return False
        observation = record["observation"]
        if observation is not None:
            expected = {"id", "received_at", "validated_at", "received_monotonic",
                        "upstream_message_id", "measurement_time_known", "hardware_boot_known",
                        "ordering_contract", "freshness_basis", "delayed_delivery_excluded",
                        "origin", "adapter_session_id", "source_generation", "bridge_id",
                        "device_id", "device_type"}
            if (type(observation) is not dict
                    or (set(observation) != expected and set(observation) != expected |
                        {"commissioned_epoch", "source_identity_sha256"})
                    or not self._valid_history_source(observation)
                    or not _finite_time(observation["received_at"])
                    or observation["received_at"] != received
                    or not _finite_time(observation["validated_at"])
                    or (observation["received_monotonic"] is not None
                        and not _finite_time(observation["received_monotonic"]))
                    or observation["measurement_time_known"] is not False
                    or observation["hardware_boot_known"] is not False
                    or observation["delayed_delivery_excluded"] is not False
                    or observation["origin"] != "unknown"
                    or observation["ordering_contract"] not in (None, "working_calendar_msg_id")
                    or observation["freshness_basis"] not in (None, "validated_unique_report_receipt")
                    or (observation["upstream_message_id"] is not None
                        and _message_time(observation["upstream_message_id"]) is None)):
                return False
            expected_id = (f"{observation['adapter_session_id']}:{observation['source_generation']}:"
                           f"{observation['upstream_message_id']}"
                           if observation["adapter_session_id"] is not None else None)
            if observation["id"] != expected_id:
                return False
        contact = record["motor_contact"]
        if contact is not None:
            contact_fields = {"received_at", "received_monotonic", "upstream_message_id",
                              "adapter_session_id", "source_generation", "bridge_id",
                              "device_id", "device_type"}
            if (type(contact) is not dict
                    or (set(contact) != contact_fields and set(contact) != contact_fields |
                        {"commissioned_epoch", "source_identity_sha256"})
                    or not self._valid_history_source(contact)
                    or not _finite_time(contact["received_at"])
                    or not _finite_time(contact["received_monotonic"])
                    or _message_time(contact["upstream_message_id"]) is None):
                return False
        watermark = record["watermark"]
        if watermark is not None:
            if (type(watermark) is not dict
                    or set(watermark) != {"bridge_id", "device_id", "device_type", "msg_id"}
                    or any(type(watermark[k]) is not str or not 0 < len(watermark[k]) <= 128
                           for k in ("bridge_id", "device_id", "device_type"))
                    or _message_time(watermark["msg_id"]) is None):
                return False
        self.position, self.position_at = position, received
        self.report, self.cache = dict(record["report"]), dict(record["cache"])
        self._observation, self._motor_contact = deepcopy(observation), deepcopy(contact)
        for original in (self._observation, self._motor_contact):
            if original is not None:
                # Absence in an old journal means unknown, never today's epoch.
                original.setdefault("commissioned_epoch", None)
                original.setdefault("source_identity_sha256", None)
        self._watermark = watermark["msg_id"] if watermark is not None else None
        self._watermark_identity = ({k: watermark[k] for k in ("bridge_id", "device_id", "device_type")}
                                    if watermark is not None else None)
        self.invalidate("previous_session_history")
        return True

    def invalidate(self, reason="source_unavailable"):
        self._current = False
        self._reason = reason
        self._movement = "uncertain" if self.position is not None else "unknown"

    def motion_admitted(self, command, now, *, monotonic_now=None, request_id=None):
        if (not (type(command) is int and 0 <= command <= 100)
                and command not in ("UP", "DOWN", "STOP")):
            raise ValueError("Invalid motion operation")
        if not _finite_time(now) or (monotonic_now is not None
                                    and not _finite_time(monotonic_now)):
            raise ValueError("Invalid motion time")
        if request_id is not None and (type(request_id) is not str or len(request_id) > 128):
            raise ValueError("Invalid motion identity")
        self._target = command
        self._request_id = request_id
        self.invalidate("await_post_motion_report")
        # Admission/dispatch is not proof that physical motion has started.
        self._movement = "uncertain"

    def accept(self, message, now: float, *, session_id=None,
               source_generation=None, monotonic_now=None):
        if not isinstance(message, dict) or not _finite_time(now):
            return False
        data = safe_data(message)
        if message.get("msgType") != "Report":
            self.cache = data
            return False
        raw = message.get("data")
        malformed = (not isinstance(raw, dict)
                     or any(k in FIELDS and k not in data for k in raw))
        session = self._session
        if session is not None:
            # The service binds the UDP source to this bridge before calling.
            if (session_id != session["adapter_session_id"]
                    or type(source_generation) is not int
                    or source_generation != session["source_generation"]
                    or message.get("mac") != session["device_id"]
                    or message.get("deviceType") != session["device_type"]):
                return False
            if (not _finite_time(monotonic_now)
                    or monotonic_now < session["started_monotonic"]):
                self.invalidate("receipt_clock_unknown")
                return False
            stamp = message.get("msgID")
            ordered = _message_time(stamp)
            if ordered is None:
                self.invalidate("report_order_unknown")
                return False
            if self._watermark is not None and ordered <= _message_time(self._watermark):
                # Repeated/older datagrams never update observation or receipt.
                return False
            self._watermark = stamp
        try:
            validate_json(message)
        except ProtocolError:
            malformed = True
        self.report = data
        if malformed:
            self.invalidate("malformed_report")
            return False
        if session is not None:
            # Inspect raw fields: discarding unknown *_T/_B diagnostics must not
            # hide a contradictory dual-axis report and qualify its position.
            if (not single_axis_profile(session["device_type"], {**self._profile, **raw})
                    or any(k in data and data[k] != v for k, v in self._profile.items())):
                self.invalidate("device_type_not_qualified")
                return False
            source_digest = source_identity_sha256(session["bridge_id"], session["device_id"],
                                                   session["commissioned_epoch"])
            self._motor_contact = {
                "received_at": now, "received_monotonic": monotonic_now,
                "commissioned_epoch": session["commissioned_epoch"],
                "source_identity_sha256": source_digest,
                "upstream_message_id": message["msgID"],
                **{k: session[k] for k in ("adapter_session_id", "source_generation",
                                          "bridge_id", "device_id", "device_type")}}
        p = data.get("currentPosition")
        mode = data.get("wirelessMode", self._profile.get("wirelessMode"))
        if mode != 1 or type(p) is not int:
            self.invalidate("position_not_supported_or_missing")
            return False
        self.position = 100 - p if self.invert else p
        self.position_at = now
        self._observation = {
            "id": (f"{session_id}:{source_generation}:{message['msgID']}"
                   if session else None),
            "received_at": now, "validated_at": now,
            "received_monotonic": monotonic_now if session else None,
            "upstream_message_id": message.get("msgID") if _message_time(message.get("msgID")) else None,
            "measurement_time_known": False, "hardware_boot_known": False,
            "ordering_contract": "working_calendar_msg_id" if session else None,
            "freshness_basis": "validated_unique_report_receipt" if session else None,
            "delayed_delivery_excluded": False,
            "commissioned_epoch": session["commissioned_epoch"] if session else None,
            "source_identity_sha256": source_digest if session else None,
            "origin": "unknown",
            **({k: session[k] for k in ("adapter_session_id", "source_generation",
                                       "bridge_id", "device_id", "device_type")}
               if session else {"adapter_session_id": None, "source_generation": None,
                                "bridge_id": None, "device_id": None, "device_type": None})}
        self._current = session is not None
        self._reason = None if self._current else "source_session_unknown"
        self._movement = "stationary_reported" if self._current else "unknown"
        return self._current

    def snapshot(self, now, stale_seconds, *, monotonic_now=None):
        if not _finite_time(now) or not _finite_time(stale_seconds) or stale_seconds < 0:
            raise ValueError("Invalid observation deadline")
        received = self._observation.get("received_monotonic") if self._observation else None
        clock_ok = (_finite_time(monotonic_now) and received is not None
                    and monotonic_now >= received)
        fresh = bool(clock_ok and monotonic_now - received <= stale_seconds)
        usable = bool(self._current and fresh)
        reason = self._reason
        if self._current and not clock_ok:
            reason = "receipt_clock_unknown"
        elif self._current and not fresh:
            reason = "report_expired"
        return {"schema_version": 2, "reported_position": self.position,
                "report_received_at": self.position_at, "stale": not usable,
                "source": "motor_report" if self.position_at is not None else "unknown",
                "report": dict(self.report), "cache": dict(self.cache),
                "current_position": self.position if usable else None,
                "usable": usable, "action_qualified": False,
                "movement_state": self._movement if usable else
                    ("uncertain" if self.position is not None else "unknown"),
                "uncertainty_reason": reason,
                "requested_target": self._target, "request_id": self._request_id,
                "observation": dict(self._observation) if self._observation else None,
                "motor_contact": dict(self._motor_contact) if self._motor_contact else None,
                "adapter_session_id": self._session["adapter_session_id"] if self._session else None,
                "source_generation": self._session["source_generation"] if self._session else None,
                "usable_interval": {"clock": "adapter_monotonic", "start": received,
                                    "end": received + stale_seconds} if received is not None else None}


class Commands:
    def __init__(self, limit=64):
        self.limit = limit
        self.pending = OrderedDict()
        self.lock = threading.Lock()

    def put(self, shade, command):
        with self.lock:
            # A queued STOP must execute before any later target for this shade.
            if self.pending.get(shade) == "STOP" and command != "STOP":
                return False
            if shade not in self.pending and len(self.pending) >= self.limit:
                return False
            self.pending[shade] = command
            if command == "STOP":
                self.pending.move_to_end(shade, last=False)
            return True

    def pop(self):
        with self.lock:
            return self.pending.popitem(last=False) if self.pending else None

    def clear(self):
        with self.lock:
            self.pending.clear()
