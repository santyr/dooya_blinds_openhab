"""Trusted owner contracts and bounded dispatch queue, without network IO.

HMAC proves possession of the configured owner key, not correctness of household
policy or physical commissioning. Service must enforce commissioned capabilities
and share ``queue.lock`` across final validation and actual UDP send. A queued or
acknowledged request never proves movement. No work is restored after restart.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import re
import threading
import uuid

COMMAND_SCHEMA = 'dooya-owner-command/v1'
POLICY_SCHEMA = 'dooya-owner-policy/v1'
RESULT_SCHEMA = 'dooya-command-result/v1'
MAX_TTL_MS = 30000
MAX_SIGNED_BYTES = 16384
ZONES = frozenset(('kitchen', 'living', 'bathroom', 'bedroom'))
_TRUSTED = object()
_WAITING = frozenset(('queued', 'selected'))
_IN_FLIGHT = frozenset(('dispatched', 'acknowledged'))
_OUTCOMES = frozenset(('queued', 'selected', 'rejected', 'dispatched', 'acknowledged',
                       'target_observed', 'stopped', 'interrupted', 'uncertain',
                       'superseded', 'expired'))


class CommandError(ValueError):
    """Sanitized contract error; never include a key, signature, or payload."""


def _canonical(value):
    try:
        result = json.dumps(value, sort_keys=True, separators=(',', ':'),
                            ensure_ascii=True, allow_nan=False).encode('ascii')
        if len(result) > MAX_SIGNED_BYTES:
            raise ValueError()
        return result
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise CommandError('Invalid owner contract') from None


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _object(value):
    if type(value) in (bytes, str):
        if len(value) > MAX_SIGNED_BYTES:
            raise CommandError('Invalid owner contract')
        try:
            value = json.loads(value, object_pairs_hook=_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise CommandError('Invalid owner contract') from None
    if type(value) is not dict:
        raise CommandError('Invalid owner contract')
    # Copy through bounded canonical serialization; callers cannot mutate trusted
    # values afterwards, and nested non-finite/unsupported objects are rejected.
    return json.loads(_canonical(value))


def _key(value):
    if type(value) is not bytes or not 32 <= len(value) <= 4096:
        raise CommandError('Owner authentication unavailable')
    return value


def _sign(envelope, key, domain):
    value = _object(envelope)
    signature = hmac.new(_key(key), domain.encode('ascii') + b'\0' + _canonical(value),
                         hashlib.sha256).hexdigest()
    return {'envelope': value, 'signature': signature}


def signed_command(envelope, key):
    return _sign(envelope, key, COMMAND_SCHEMA)


def signed_policy(envelope, key):
    return _sign(envelope, key, POLICY_SCHEMA)


def _uuid(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _text(value, maximum=128):
    return type(value) is str and 0 < len(value) <= maximum and all(32 <= ord(c) < 127 for c in value)


def _time_range(created, expires):
    return (type(created) is int and type(expires) is int
            and 0 <= created < expires < 2**53 and expires - created <= MAX_TTL_MS)


def _context(value):
    if type(value) is not dict or len(value) > 16 or len(_canonical(value)) > 4096:
        return False
    stack = [(value, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 128 or depth > 4:
            return False
        if type(node) is dict:
            if any(not _text(k, 64) for k in node):
                return False
            stack.extend((v, depth + 1) for v in node.values())
        elif type(node) is list:
            stack.extend((v, depth + 1) for v in node)
        elif type(node) is str:
            if len(node) > 512:
                return False
        elif type(node) is int:
            if not -(2**53) < node < 2**53:
                return False
        elif node is not None and type(node) is not bool:
            return False
    return True


def _validate_command(value):
    required = {'schema', 'request_id', 'shade_id', 'command', 'origin',
                'created_at_ms', 'expires_at_ms', 'adapter_session', 'owner_session',
                'policy_generation', 'source_context'}
    command = value.get('command')
    if (set(value) != required or value['schema'] != COMMAND_SCHEMA
            or not _uuid(value['request_id']) or not _uuid(value['owner_session'])
            or not _text(value['adapter_session'])
            or not _text(value['shade_id'], 64)
            or re.fullmatch(r'[A-Za-z0-9_-]+', value['shade_id']) is None
            or not ((type(command) is int and 0 <= command <= 100)
                    or (type(command) is str and command in ('UP', 'DOWN', 'STOP')))
            or value['origin'] not in ('manual', 'automatic')
            or not _time_range(value['created_at_ms'], value['expires_at_ms'])
            or not (_uuid(value['policy_generation'])
                    or ((command == 'STOP' or value['origin'] == 'manual')
                        and value['policy_generation'] is None))
            or not _context(value['source_context'])
            or (value['origin'] == 'automatic' and not value['source_context'])):
        raise CommandError('Invalid owner command')


def _validate_policy(value):
    required = {'schema', 'generation', 'owner_session', 'adapter_session',
                'issued_at_ms', 'expires_at_ms', 'zones', 'override', 'global_released'}
    if (set(value) != required or value['schema'] != POLICY_SCHEMA
            or not _uuid(value['generation']) or not _uuid(value['owner_session'])
            or not _text(value['adapter_session'])
            or not _time_range(value['issued_at_ms'], value['expires_at_ms'])
            or type(value['global_released']) is not bool
            or type(value['zones']) is not dict or set(value['zones']) != ZONES
            or type(value['override']) is not dict
            or set(value['override']) != {'state', 'authoritative'}
            or value['override']['state'] not in ('ON', 'OFF', None)
            or type(value['override']['authoritative']) is not bool):
        raise CommandError('Invalid owner policy')
    for zone in value['zones'].values():
        if (type(zone) is not dict or set(zone) != {'enabled', 'authoritative'}
                or (zone['enabled'] is not None and type(zone['enabled']) is not bool)
                or type(zone['authoritative']) is not bool):
            raise CommandError('Invalid owner policy')


@dataclass(frozen=True)
class _Verified:
    _canonical: bytes = field(repr=False)
    _seal: object = field(repr=False)

    def to_dict(self):
        return json.loads(self._canonical)

    def __getattr__(self, name):
        value = self.to_dict()
        if name not in value:
            raise AttributeError(name)
        # Every nested value is a copy: an accessor never exposes trusted state.
        return value[name]


class Command(_Verified):
    pass


class Policy(_Verified):
    pass


def _verify(wrapper, key, domain, validator, cls):
    wrapped = _object(wrapper)
    if (set(wrapped) != {'envelope', 'signature'}
            or type(wrapped['envelope']) is not dict
            or type(wrapped['signature']) is not str
            or re.fullmatch(r'[0-9a-f]{64}', wrapped['signature']) is None):
        raise CommandError('Owner authentication failed')
    encoded = _canonical(wrapped['envelope'])
    expected = hmac.new(_key(key), domain.encode('ascii') + b'\0' + encoded,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, wrapped['signature']):
        raise CommandError('Owner authentication failed')
    validator(wrapped['envelope'])
    return cls(encoded, _TRUSTED)


def verify_command(wrapper, key):
    return _verify(wrapper, key, COMMAND_SCHEMA, _validate_command, Command)


def verify_policy(wrapper, key):
    return _verify(wrapper, key, POLICY_SCHEMA, _validate_policy, Policy)


@dataclass(frozen=True)
class Job:
    envelope: Command
    connection_generation: int
    deadline_mono: float


class CommandQueue:
    def __init__(self, shade_zones, *, adapter_session, limit=64, ledger_limit=512,
                 automatic_released=False):
        if (type(shade_zones) is not dict or len(shade_zones) > 64
                or any(not _text(k, 64) or type(v) is not str or v not in ZONES for k, v in shade_zones.items())
                or not _text(adapter_session) or type(limit) is not int or not 1 <= limit <= 64
                or type(ledger_limit) is not int or not limit <= ledger_limit <= 512
                or type(automatic_released) is not bool):
            raise CommandError('Invalid command queue configuration')
        self.shade_zones = dict(shade_zones)
        self.adapter_session = adapter_session
        self.limit = limit
        self.ledger_limit = ledger_limit
        self.automatic_released = automatic_released
        self.lock = threading.RLock()
        self.pending = OrderedDict()
        self.connection_generation = 0
        self.policy = None
        self._policy_deadline = None
        self._policy_generations = OrderedDict()
        self._retired_owners = set()
        self._ledger = OrderedDict()
        self._jobs = {}
        self._latest_dispatched = {}
        self._utc_highwater = 0

    def _time(self, now_ms, now_mono):
        if (type(now_ms) is not int or not 0 <= now_ms < 2**63
                or type(now_mono) not in (int, float) or not math.isfinite(now_mono)):
            raise CommandError('Invalid dispatch clock')
        self._utc_highwater = max(self._utc_highwater, now_ms)
        return self._utc_highwater

    @staticmethod
    def _trusted(value, cls):
        return type(value) is cls and value._seal is _TRUSTED

    @staticmethod
    def _fingerprint(envelope):
        return hashlib.sha256(envelope._canonical).hexdigest()

    def _receipt(self, envelope, status, reason, now_ms):
        return {'schema': RESULT_SCHEMA, 'request_id': envelope.request_id,
                'shade_id': envelope.shade_id, 'origin': envelope.origin,
                'command': envelope.command, 'adapter_session': envelope.adapter_session,
                'owner_session': envelope.owner_session,
                'policy_generation': envelope.policy_generation,
                'source_context': envelope.source_context, 'status': status, 'reason': reason,
                'updated_at_ms': now_ms, 'envelope': envelope.to_dict(),
                'fingerprint': self._fingerprint(envelope)}

    def _set(self, request_id, status, reason, now_ms=None):
        record = self._ledger[request_id]['receipt']
        record['status'], record['reason'] = status, reason
        if now_ms is not None:
            record['updated_at_ms'] = now_ms
        if status not in _WAITING:
            self.pending.pop(request_id, None)
        return json.loads(_canonical(record))

    def receipt(self, request_id):
        with self.lock:
            item = self._ledger.get(request_id)
            return json.loads(_canonical(item['receipt'])) if item else None

    @staticmethod
    def _manual_stop(envelope):
        return envelope.origin == 'manual' and envelope.command == 'STOP'

    def _prune(self, now_ms, now_mono):
        # Expiration withdraws pending authority, but history is retained until
        # capacity pressure requires reclaiming an expired terminal identity.
        for request_id, item in list(self._ledger.items()):
            record = item['receipt']
            deadline = item['deadline_mono']
            if record['status'] in _WAITING and (record['envelope']['expires_at_ms'] <= now_ms
                    or (deadline is not None and now_mono >= deadline)):
                self._set(request_id, 'expired', 'request_expired', now_ms)

    def _evict_expired(self, now_ms, now_mono, reserve_shade=None):
        for request_id, item in list(self._ledger.items()):
            record = item['receipt']
            reserved = record.get('capacity_class', 'normal') == 'manual_stop_reserve'
            if reserved != (reserve_shade is not None):
                continue
            if reserved and record['shade_id'] != reserve_shade:
                continue
            deadline = item['deadline_mono']
            if (record['status'] not in _WAITING | _IN_FLIGHT
                    and record['envelope']['expires_at_ms'] <= now_ms
                    and (deadline is None or now_mono >= deadline)):
                del self._ledger[request_id]
                self._jobs.pop(request_id, None)
                return True
        return False

    def _normal_ledger_size(self):
        return sum(item['receipt'].get('capacity_class', 'normal') == 'normal'
                   for item in self._ledger.values())

    def _reserve_occupied(self, shade_id):
        return any(item['receipt'].get('capacity_class') == 'manual_stop_reserve'
                   and item['receipt']['envelope']['shade_id'] == shade_id for item in self._ledger.values())

    def _admission_reason(self, envelope, now_ms, now_mono):
        if envelope.adapter_session != self.adapter_session:
            return 'adapter_session_mismatch'
        if envelope.shade_id not in self.shade_zones:
            return 'unknown_shade'
        if not envelope.created_at_ms <= now_ms < envelope.expires_at_ms:
            return 'request_expired_or_future'
        if self.policy is not None and envelope.owner_session != self.policy.owner_session:
            return 'owner_session_mismatch'
        if envelope.command == 'STOP':
            if envelope.origin == 'automatic':
                owned_id = envelope.source_context.get('owned_request_id')
                owned = self._jobs.get(owned_id) if type(owned_id) is str else None
                record = self._ledger.get(owned_id, {}).get('receipt', {}) if owned else {}
                if (owned is None or owned.envelope.origin != 'automatic'
                        or owned.envelope.shade_id != envelope.shade_id
                        or owned.envelope.owner_session != envelope.owner_session
                        or owned.connection_generation != self.connection_generation
                        or self._latest_dispatched.get(envelope.shade_id) not in (owned_id, envelope.request_id)
                        or record.get('status') not in _IN_FLIGHT | {'uncertain'}):
                    return 'automatic_stop_not_owned'
            return None
        # A trusted, fresh manual request does not depend on permission to run
        # recommendations, a zone switch, or availability of the policy owner.
        if envelope.origin == 'manual':
            return None
        if (self.policy is None or self._policy_deadline is None
                or now_mono >= self._policy_deadline
                or not self.policy.issued_at_ms <= now_ms < self.policy.expires_at_ms):
            return 'policy_not_current'
        if envelope.policy_generation != self.policy.generation:
            return 'policy_generation_mismatch'
        if not self.automatic_released or not self.policy.global_released:
            return 'automatic_not_released'
        zone_name = self.shade_zones[envelope.shade_id]
        zone = self.policy.zones[zone_name]
        if not zone['authoritative'] or zone['enabled'] is not True:
            return 'zone_automation_inhibited'
        if zone_name in ('bathroom', 'bedroom'):
            override = self.policy.override
            if not override['authoritative'] or override['state'] != 'OFF':
                return 'nightly_override_inhibited'
        return None

    def submit(self, envelope, now_ms, now_mono):
        with self.lock:
            now = self._time(now_ms, now_mono)
            if not self._trusted(envelope, Command):
                return {'schema': RESULT_SCHEMA, 'status': 'rejected', 'reason': 'untrusted_command'}
            old = self._ledger.get(envelope.request_id)
            if old is not None:
                if old['receipt']['fingerprint'] != self._fingerprint(envelope):
                    return self._receipt(envelope, 'rejected', 'request_identity_conflict', now_ms)
                return self.receipt(envelope.request_id)
            self._prune(now, now_mono)
            reason = self._admission_reason(envelope, now, now_mono)
            capacity_class = 'normal'
            if self._normal_ledger_size() >= self.ledger_limit:
                eligible_stop = reason is None and self._manual_stop(envelope)
                if eligible_stop:
                    self._evict_expired(now, now_mono, reserve_shade=envelope.shade_id)
                    if not self._reserve_occupied(envelope.shade_id):
                        capacity_class = 'manual_stop_reserve'
                    elif not self._evict_expired(now, now_mono):
                        return self._receipt(envelope, 'rejected', 'manual_stop_reserve_full', now_ms)
                elif not self._evict_expired(now, now_mono):
                    return self._receipt(envelope, 'rejected', 'identity_ledger_full', now_ms)
            if reason is None and envelope.command != 'STOP' and self.has_stop(envelope.shade_id):
                reason = 'stop_pending'
            if reason is None:
                for request_id, job in list(self._jobs.items()):
                    status = self._ledger[request_id]['receipt']['status']
                    other = job.envelope
                    if (status in _WAITING and other.shade_id == envelope.shade_id
                            and (envelope.command == 'STOP'
                                 or (other.command != 'STOP' and other.origin == envelope.origin))):
                        self._set(request_id, 'superseded', 'newer_stop' if envelope.command == 'STOP'
                                  else 'newer_compatible_target', now_ms)
                ordinary_pending = sum(not self._manual_stop(job.envelope)
                                       for job in self.pending.values())
                # Every known shade has one operator STOP waiting slot, separate
                # from ordinary work; coalescing above keeps that bound exact.
                if not self._manual_stop(envelope) and ordinary_pending >= self.limit:
                    reason = 'queue_full'
            deadline = now_mono + max(0, envelope.expires_at_ms - now) / 1000
            receipt = self._receipt(envelope, 'rejected' if reason else 'queued', reason or 'admitted', now_ms)
            receipt['capacity_class'] = capacity_class
            self._ledger[envelope.request_id] = {'receipt': receipt, 'deadline_mono': deadline}
            if reason is None:
                job = Job(envelope, self.connection_generation, deadline)
                self._jobs[envelope.request_id] = job
                self.pending[envelope.request_id] = job
                if envelope.command == 'STOP':
                    self.pending.move_to_end(envelope.request_id, last=False)
            return self.receipt(envelope.request_id)

    def pop(self):
        with self.lock:
            if not self.pending:
                return None
            request_id, job = self.pending.popitem(last=False)
            self._set(request_id, 'selected', 'await_final_dispatch')
            return job

    def owns_job(self, job):
        with self.lock:
            return (type(job) is Job and self._trusted(job.envelope, Command)
                    and self._jobs.get(job.envelope.request_id) is job)

    def dispatched_still_valid(self, job, now_ms, now_mono):
        """Recheck after durable dispatch recording, immediately before UDP send.

        Does not change the outcome or renew any deadline. Service holds lock
        across this check and send, then records uncertainty if sending fails.
        """
        with self.lock:
            now = self._time(now_ms, now_mono)
            return bool(self.owns_job(job)
                        and self._ledger[job.envelope.request_id]['receipt']['status'] == 'dispatched'
                        and job.connection_generation == self.connection_generation
                        and now_mono < job.deadline_mono
                        and now < job.envelope.expires_at_ms
                        and (job.envelope.command == 'STOP' or not self.has_stop())
                        and self._admission_reason(job.envelope, now, now_mono) is None)

    def validate_for_send(self, job, now_ms, now_mono):
        with self.lock:
            now = self._time(now_ms, now_mono)
            if (not self.owns_job(job)
                    or self._ledger[job.envelope.request_id]['receipt']['status'] not in _WAITING):
                return False
            request_id = job.envelope.request_id
            if job.connection_generation != self.connection_generation:
                self._set(request_id, 'rejected', 'connection_generation_changed', now_ms)
                return False
            if now_mono >= job.deadline_mono or now >= job.envelope.expires_at_ms:
                self._set(request_id, 'expired', 'request_expired', now_ms)
                return False
            if job.envelope.command != 'STOP' and self.has_stop():
                self._set(request_id, 'rejected', 'stop_priority', now_ms)
                return False
            reason = self._admission_reason(job.envelope, now, now_mono)
            if reason:
                self._set(request_id, 'rejected', reason, now_ms)
                return False
            return True

    def mark_dispatched(self, job, now_ms, now_mono):
        with self.lock:
            if not self.validate_for_send(job, now_ms, now_mono):
                return False
            self._set(job.envelope.request_id, 'dispatched', 'udp_dispatch_attempted', now_ms)
            self._latest_dispatched[job.envelope.shade_id] = job.envelope.request_id
            return True

    def record_result(self, job, status, reason, now_ms, now_mono=None):
        with self.lock:
            if (not self.owns_job(job)
                    or status not in _OUTCOMES or status in _WAITING
                    or not _text(reason, 160) or type(now_ms) is not int):
                raise CommandError('Invalid command outcome')
            if now_mono is not None:
                self._time(now_ms, now_mono)
            current = self._ledger[job.envelope.request_id]['receipt']['status']
            if status == 'dispatched' or (current in _WAITING and status not in {'rejected', 'expired', 'superseded'}):
                raise CommandError('Invalid command outcome transition')
            if current not in _WAITING | _IN_FLIGHT | {'uncertain'}:
                return self.receipt(job.envelope.request_id)
            return self._set(job.envelope.request_id, status, reason, now_ms)

    def has_stop(self, shade_id=None):
        with self.lock:
            return any(self._ledger[rid]['receipt']['status'] in _WAITING
                       and job.envelope.command == 'STOP'
                       and (shade_id is None or job.envelope.shade_id == shade_id)
                       for rid, job in self._jobs.items())

    def invalidate(self, reason, automatic_only=False, preserve_stops=False):
        with self.lock:
            if not _text(reason, 160):
                raise CommandError('Invalid invalidation reason')
            if not automatic_only and not preserve_stops:
                self.connection_generation += 1
            for request_id, job in list(self._jobs.items()):
                if automatic_only and job.envelope.origin != 'automatic':
                    continue
                if preserve_stops and job.envelope.command == 'STOP':
                    continue
                status = self._ledger[request_id]['receipt']['status']
                if status in _WAITING:
                    self._set(request_id, 'rejected', reason)
                elif status in _IN_FLIGHT:
                    self._set(request_id, 'uncertain', reason)

    @staticmethod
    def _permission_fingerprint(policy):
        data = policy.to_dict()
        for key in ('issued_at_ms', 'expires_at_ms', 'generation'):
            data.pop(key)
        return hashlib.sha256(_canonical(data)).hexdigest()

    def update_policy(self, policy, now_ms, now_mono):
        with self.lock:
            now = self._time(now_ms, now_mono)
            if (not self._trusted(policy, Policy) or policy.adapter_session != self.adapter_session
                    or not policy.issued_at_ms <= now < policy.expires_at_ms
                    or policy.owner_session in self._retired_owners):
                return False
            if self.policy is not None:
                if policy.issued_at_ms < self.policy.issued_at_ms:
                    return False
                if policy.generation == self.policy.generation:
                    if self._permission_fingerprint(policy) != self._permission_fingerprint(self.policy):
                        return False
                    if policy.issued_at_ms == self.policy.issued_at_ms:
                        # An exact retained/repeated snapshot cannot restamp its
                        # original monotonic validity after wall-clock rollback.
                        return (policy._canonical == self.policy._canonical
                                and now_mono < self._policy_deadline)
                elif policy.generation in self._policy_generations:
                    return False
            for generation, interval in list(self._policy_generations.items()):
                if (self.policy is not None and generation != self.policy.generation
                        and now >= interval[0] and now_mono >= interval[1]):
                    del self._policy_generations[generation]
            if policy.generation not in self._policy_generations and len(self._policy_generations) >= 512:
                return False  # Explicit owner/adapter reconciliation, never silent replay-window eviction.
            changed = self.policy is not None and policy.generation != self.policy.generation
            if (changed and self.policy.owner_session != policy.owner_session
                    and len(self._retired_owners) >= 512):
                return False
            if changed:
                for request_id, job in list(self._jobs.items()):
                    status = self._ledger[request_id]['receipt']['status']
                    if job.envelope.origin == 'automatic' and status in _WAITING:
                        self._set(request_id, 'superseded', 'policy_generation_changed', now_ms)
                    elif job.envelope.origin == 'automatic' and status in _IN_FLIGHT:
                        self._set(request_id, 'uncertain', 'automatic_admission_withdrawn', now_ms)
                if self.policy.owner_session != policy.owner_session:
                    self._retired_owners.add(self.policy.owner_session)
            self.policy = policy
            self._policy_deadline = now_mono + (policy.expires_at_ms - now) / 1000
            # Keep superseded identities through the maximum lifetime of every
            # older signed snapshot, then reclaim space without a replay window.
            until_ms = policy.issued_at_ms + MAX_TTL_MS
            self._policy_generations[policy.generation] = (
                until_ms, now_mono + (until_ms - now) / 1000)
            return True

    def export_ledger(self):
        with self.lock:
            return [json.loads(_canonical(item['receipt'])) for item in self._ledger.values()]

    def import_ledger(self, records):
        if type(records) is not list or len(records) > self.ledger_limit + len(self.shade_zones):
            raise CommandError('Invalid command history')
        validated = []
        reserved_shades = set()
        normal_count = 0
        try:
            for item in records:
                value = _object(item)
                if value.get('schema') != RESULT_SCHEMA or value.get('status') not in _OUTCOMES:
                    raise CommandError('Invalid command history')
                envelope = value.get('envelope')
                if type(envelope) is not dict:
                    raise CommandError('Invalid command history')
                _validate_command(envelope)
                command = Command(_canonical(envelope), _TRUSTED)
                if (value.get('request_id') != command.request_id
                        or value.get('fingerprint') != self._fingerprint(command)
                        or type(value.get('updated_at_ms')) is not int
                        or any(value.get(key) != command.to_dict()[key] for key in
                               ('shade_id', 'command', 'origin', 'adapter_session', 'owner_session',
                                'policy_generation', 'source_context'))):
                    raise CommandError('Invalid command history')
                capacity_class = value.get('capacity_class', 'normal')
                if capacity_class == 'manual_stop_reserve':
                    if (not self._manual_stop(command) or command.shade_id not in self.shade_zones
                            or command.shade_id in reserved_shades):
                        raise CommandError('Invalid command history')
                    reserved_shades.add(command.shade_id)
                elif capacity_class == 'normal':
                    normal_count += 1
                    if normal_count > self.ledger_limit:
                        raise CommandError('Invalid command history')
                else:
                    raise CommandError('Invalid command history')
                if value['status'] in _WAITING:
                    value.update(status='expired', reason='restart_not_dispatched')
                elif value['status'] in _IN_FLIGHT:
                    value.update(status='uncertain', reason='restart_outcome_unknown_no_retry')
                validated.append((command.request_id, value))
        except (KeyError, TypeError, ValueError, RecursionError):
            raise CommandError('Invalid command history') from None
        if len({rid for rid, _ in validated}) != len(validated):
            raise CommandError('Invalid command history')
        with self.lock:
            if self._ledger or self.pending:
                raise CommandError('Command history must load before admission')
            for request_id, value in validated:
                self._ledger[request_id] = {'receipt': value, 'deadline_mono': None}
                self._utc_highwater = max(self._utc_highwater, value['updated_at_ms'])
