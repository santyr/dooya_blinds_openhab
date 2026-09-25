"""State provenance and bounded command queue, independent of IO."""
from collections import OrderedDict
from dataclasses import dataclass, field
import re
import threading

FIELDS = {"currentPosition", "operation", "currentState", "batteryLevel", "wirelessMode",
          "voltageMode", "RSSI", "chargingState", "type"}


def safe_data(message):
    data = message.get("data", {})
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in FIELDS and type(v) in (int, float, bool)}


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
    invert: bool = False
    report: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    position: int | None = None
    position_at: float | None = None

    def accept(self, message, now: float):
        data = safe_data(message)
        if message.get("msgType") != "Report":
            self.cache = data
            return
        self.report = data
        p = data.get("currentPosition")
        if data.get("wirelessMode") == 1 and type(p) is int and 0 <= p <= 100:
            self.position = 100 - p if self.invert else p
            self.position_at = now

    def snapshot(self, now, stale_seconds):
        stale = self.position_at is None or now - self.position_at > stale_seconds
        return {"schema_version": 1, "reported_position": self.position,
                "report_received_at": self.position_at, "stale": stale,
                "source": "motor_report" if self.position_at is not None else "unknown",
                "report": self.report, "cache": self.cache}


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
