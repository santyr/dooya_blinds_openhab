"""Single-worker protocol IO with signed owner admission and durable no-replay receipts."""
from __future__ import annotations

from collections import OrderedDict, deque
import json
import logging
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from .commands import CommandError, CommandQueue, verify_command, verify_policy
from .model import State
from .protocol import (Connector, MessageIDs, ProtocolError, ReportSocket,
                       SINGLE_AXIS_TYPES, UDPTransport, canonical_mac, decode,
                       single_axis_profile)

LOG = logging.getLogger(__name__)
_CONTEXT_FIELDS = frozenset(('commissioned_epoch', 'source_generation', 'bridge_id',
    'device_id', 'observation_id', 'effect_qualified', 'manual_hold_clear',
    'safety_clear', 'decision_id', 'batch_id', 'zone', 'source_schema', 'owned_request_id'))
# 576 bounded 16-KiB receipts plus device history and atomic-write headroom.
_MAX_JOURNAL = 16 * 1024 * 1024


class Journal:
    """Private bounded atomic snapshot; records evidence, never pending execution."""
    def __init__(self, path):
        self.path = Path(path) if path is not None else None

    def read(self):
        if self.path is None:
            return {}
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {}
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
                raise ValueError('Unsafe receipt journal')
            raw = stream.read(_MAX_JOURNAL + 1)
        if len(raw) > _MAX_JOURNAL:
            raise ValueError('Receipt journal exceeds resource limit')
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result: raise ValueError('Invalid receipt journal')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid receipt journal')))
        if (not isinstance(value, dict) or set(value) != {'schema_version','adapter_session_id','receipts','watermarks','history'}
                or value.get('schema_version') != 2 or type(value['adapter_session_id']) is not str
                or type(value['receipts']) is not list or len(value['receipts']) > 576
                or any(type(value[field]) is not dict or len(value[field]) > 26 for field in ('history','watermarks'))):
            raise ValueError('Invalid receipt journal')
        return value

    def write(self, value):
        if self.path is None:
            return False
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(raw) > _MAX_JOURNAL:
            raise ValueError('Receipt journal exceeds resource limit')
        descriptor, temporary = tempfile.mkstemp(prefix='.dooya-journal-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            try: os.unlink(temporary)
            except FileNotFoundError: pass
        return True


class Service:
    def __init__(self, config, connector=None, client=None, *, wall_clock=None,
                 monotonic_clock=None, reports_factory=None, wait=None):
        self.config = config
        self.wall_clock = wall_clock or time.time
        self.monotonic_clock = monotonic_clock or time.monotonic
        self.adapter_session = str(uuid.uuid4())
        self.source_generation = 0
        self.connector = connector or Connector(
            UDPTransport(config.bridge_host, timeout=config.request_timeout_seconds), config.key,
            bridge_mac=config.bridge_mac,
            ids=MessageIDs(state_path=config.message_id_state_file),discovery_correlation=config.discovery_correlation)
        self.shades = {shade.id: shade for shade in config.shades}
        self.by_mac = {shade.mac: shade.id for shade in config.shades}
        self.states = {shade.id: State(shade.invert) for shade in config.shades}
        self.commands = CommandQueue({shade.id: shade.zone for shade in config.shades
            if shade.zone is not None}, adapter_session=self.adapter_session, automatic_released=False)
        self.connected = threading.Event(); self.reset = threading.Event(); self.stop = threading.Event()
        self.reports_factory = reports_factory or ReportSocket
        self.wait = wait or self.stop.wait
        self.ready = False
        self.bridge_seen = None
        self._bridge_seen_mono = None
        self._connection_generation = 0
        self._session_bound = False
        self._publish_lock = threading.RLock()
        self.sent = {}; self._desired = {}; self._pending = OrderedDict(); self._early_acks = {}; self._starting = {}; self._publication_generation = 0
        self._last_health = None
        self._active = {}; self._outcome_deadlines = {}
        self._poll_attempts = {}; self._poll_completed_at = {}
        self.journal = Journal(config.receipt_journal_file)
        self._restored = {}
        self._journal_ok = self.journal.path is not None
        try:
            self._restored = self.journal.read()
            self.commands.import_ledger(self._restored.get('receipts', []))
            for sid, record in self._restored.get('history', {}).items():
                if sid in self.states and not self.states[sid].import_history(record):
                    raise ValueError('Invalid saved source history')
        except (OSError, ValueError, TypeError, RecursionError):
            self._journal_ok = False
            LOG.warning('Receipt journal unavailable; physical commands inhibited')
        self.client = client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, clean_session=True)
        if isinstance(self.client,mqtt.Client):
            self.client.max_queued_messages_set(256)
            self.client.max_inflight_messages_set(20)
        self.client.on_connect = self.on_connect; self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message; self.client.on_publish = self.on_publish
        self.client.will_set(self.topic('service/availability'), 'offline', qos=1, retain=True)
        if config.mqtt_username: self.client.username_pw_set(config.mqtt_username, config.mqtt_password)
        if config.mqtt_tls: self.client.tls_set()
        self.client.reconnect_delay_set(1, 30)
        self._persist()

    def topic(self, suffix):
        return f'{self.config.prefix}/{suffix}'

    def _times(self):
        return int(self.wall_clock() * 1000), self.monotonic_clock()

    def _persist(self):
        if not self._journal_ok: return False
        try:
            with self.commands.lock:
                value = {'schema_version':2, 'adapter_session_id':self.adapter_session,
                    'receipts':self.commands.export_ledger(),
                    'watermarks':{sid:state.export_watermark() for sid,state in self.states.items()},
                    'history':{sid:state.export_history() for sid,state in self.states.items()}}
                self.journal.write(value)
            return True
        except (OSError, ValueError, TypeError, RecursionError):
            self._journal_ok = False
            LOG.warning('Receipt persistence failed; physical commands inhibited')
            return False

    def _invalidate_publications(self):
        with self._publish_lock:
            self._publication_generation += 1
            self.sent.clear(); self._pending.clear(); self._early_acks.clear()
            self._starting.clear(); self._desired.clear()

    def publish(self, suffix, value, retain=True):
        if not self.connected.is_set(): return
        try:
            payload = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) if isinstance(value, dict) else str(value)
        except (ValueError, TypeError): return
        if len(payload.encode('utf-8')) > 32768: return
        with self._publish_lock:
            now = self.monotonic_clock()
            self._desired[suffix] = payload
            if retain and self.sent.get(suffix) == payload: return
            if retain and any(item[0] == suffix and item[1] == payload and now-item[2] < 5 for item in self._pending.values()): return
            if retain and any(item[0] == suffix and item[1] == payload and item[3] for item in self._starting.values()): return
            if len(self._starting) >= 64: return
            token = object()
            generation = self._publication_generation
            self._starting[token] = (suffix,payload,now,retain,generation)
        # Paho invokes on_publish while holding its outbound-message mutex.
        # Never hold the service mutex across publish(), which takes that mutex.
        try:
            info = self.client.publish(self.topic(suffix), payload, qos=1, retain=retain)
        except (OSError,ValueError):
            with self._publish_lock:
                self._starting.pop(token,None)
                if not self._starting: self._early_acks.clear()
            return
        with self._publish_lock:
            self._starting.pop(token,None)
            early = self._early_acks.pop(info.mid,None)
            if retain and info.rc == mqtt.MQTT_ERR_SUCCESS and generation == self._publication_generation:
                if early is not None:
                    ack_generation, failure = early
                    if ack_generation == generation and not failure:
                        if self._desired.get(suffix) == payload:
                            self.sent[suffix] = payload
                        else:
                            self.sent.pop(suffix,None)
                else:
                    self._pending[info.mid] = (suffix,payload,now)
                    while len(self._pending) > 256: self._pending.popitem(last=False)
            if not self._starting: self._early_acks.clear()

    def on_publish(self, client, userdata, mid, reason_code=None, properties=None):
        failure = getattr(reason_code, 'is_failure', False) or (type(reason_code) is int and reason_code >= 128)
        with self._publish_lock:
            item = self._pending.pop(mid, None)
            if item is not None:
                if not failure:
                    if self._desired.get(item[0]) == item[1]:
                        self.sent[item[0]] = item[1]
                    else:
                        # Concurrent callers can reach Paho in a different order.
                        # An older retained payload must not hide the next repair.
                        self.sent.pop(item[0],None)
            elif self._starting and len(self._early_acks) < 256:
                self._early_acks[mid] = (self._publication_generation,failure)

    def event(self, shade, status, reason, request_id=None):
        self.publish(f'{shade}/event', {'schema_version':2, 'request_id':request_id,
            'status':status, 'reason':reason, 'adapter_session_id':self.adapter_session}, retain=False)

    def _record_job_result(self, job, status, reason, *, active_only=False):
        # Callback invalidation/ledger pressure may retire a job during UDP wait.
        # The same lock spans ownership lookup and outcome mutation.
        with self.commands.lock:
            if not self.commands.owns_job(job):
                return False
            receipt=self.commands.receipt(job.envelope.request_id)
            current=receipt['status']
            if current not in ('queued','selected','dispatched','acknowledged','uncertain'):
                return False
            if active_only and current not in ('dispatched','acknowledged','uncertain'):
                return False
            if status=='acknowledged' and current!='dispatched':
                return False
            if current=='uncertain' and status in ('uncertain','rejected'):
                return False  # Preserve the withdrawal/unknown result already committed.
            if current in ('queued','selected') and status not in ('rejected','expired','superseded'):
                return False
            self.commands.record_result(job,status,reason,*self._times())
            return True

    def _record_active_result(self, job, status, reason):
        return self._record_job_result(job,status,reason,active_only=True)

    def _result(self, job):
        receipt = self.commands.receipt(job.envelope.request_id)
        if receipt is not None:
            self.publish('owner/result', receipt, retain=False)
            self.publish(f'{job.envelope.shade_id}/event', receipt, retain=False)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            LOG.warning('MQTT connection rejected'); return
        with self.commands.lock:
            self.ready = False; self._connection_generation += 1
            self.commands.invalidate('mqtt_reconnected'); self.reset.set(); self.connected.set()
            for state in self.states.values(): state.invalidate('mqtt_reconnected')
            self._persist()
        self._invalidate_publications()
        client.subscribe(self.topic('owner/command'), qos=1)
        client.subscribe(self.topic('owner/policy'), qos=1)
        # Detect obsolete writers, but never interpret a bare value as manual authority.
        client.subscribe(self.topic('+/command'), qos=0)

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        with self.commands.lock:
            self.ready = False; self.connected.clear(); self._connection_generation += 1
            self.commands.invalidate('mqtt_disconnected')
            for state in self.states.values(): state.invalidate('mqtt_disconnected')
            self._persist()
        self._invalidate_publications()

    def on_message(self, client, userdata, message):
        if not isinstance(message.topic, str) or not message.topic.startswith(self.topic('')): return
        if message.topic not in (self.topic('owner/policy'), self.topic('owner/command')):
            if message.topic.endswith('/command'): self.event('service', 'rejected', 'legacy_unsigned_command_disabled')
            return
        if message.retain or not self.connected.is_set() or self.config.owner_key is None: return
        try:
            now_ms, mono = self._times()
            if message.topic == self.topic('owner/policy'):
                policy = verify_policy(message.payload, self.config.owner_key)
                with self.commands.lock:
                    effective = self.commands.update_policy(policy, now_ms, mono)
                    durable = self._persist()
                in_flight=[job.envelope.request_id for job in self._active.values()
                           if job.envelope.origin=='automatic']
                self.publish('owner/policy-ack', {'schema':'dooya-policy-ack/v1','schema_version':2,
                    'adapter_session':self.adapter_session, 'adapter_session_id':self.adapter_session,
                    'source_generation':self.source_generation,
                    'generation':policy.generation, 'policy_generation':policy.generation,
                    'owner_session':policy.owner_session, 'policy_issued_at_ms':policy.issued_at_ms,
                    'effective_at_ms':int(self.wall_clock()*1000),
                    'status':'effective' if effective and durable else 'rejected',
                    'effective':bool(effective and durable), 'automatic_released':False,
                    'in_flight_limitation':{'status':'outcome_unknown' if in_flight else 'none',
                        'request_ids':in_flight, 'stop_issued':False},
                    'emitted_at':self.wall_clock()}, retain=False)
            else:
                envelope = verify_command(message.payload, self.config.owner_key)
                if set(envelope.source_context) - _CONTEXT_FIELDS:
                    raise CommandError('Invalid source context')
                with self.commands.lock:
                    receipt = self.commands.submit(envelope, now_ms, mono)
                    self._persist()
                self.publish('owner/result', receipt, retain=False)
        except (ValueError, TypeError, ProtocolError):
            self.event('service', 'rejected', 'invalid_owner_contract')

    def _begin_sources(self, reason):
        self.source_generation += 1; self._session_bound = True
        self.commands.invalidate(reason)
        for sid, shade in self.shades.items():
            family = self.connector.devices.get(shade.mac)
            if family is None:
                self.states[sid].invalidate('device_not_discovered'); continue
            self.states[sid].begin_session(session_id=self.adapter_session,
                source_generation=self.source_generation, bridge_id=self.connector.bridge_mac,
                device_id=shade.mac, device_type=family, commissioned_epoch=shade.commissioned_epoch, now=self.wall_clock(),
                monotonic_now=self.monotonic_clock(), motor_profile=self.connector.device_profiles.get(shade.mac))
            self.states[sid].import_watermark(self._restored.get('watermarks', {}).get(sid))

    def discover_once(self):
        previous = (self.connector.bridge_mac, self.connector.token, dict(self.connector.devices))
        generation = self._connection_generation
        self.connector.discover(cancelled=lambda:self.stop.is_set() or generation != self._connection_generation or self.commands.has_stop())
        with self.commands.lock:
            if generation != self._connection_generation or not self.connected.is_set():
                raise ProtocolError('Discovery generation changed')
            current = (self.connector.bridge_mac, self.connector.token, dict(self.connector.devices))
            if not self._session_bound or previous != current or self.reset.is_set():
                self._begin_sources('bridge_source_reconciled')
            self.bridge_seen=self.wall_clock(); self._bridge_seen_mono=self.monotonic_clock(); self.ready=True
            self._persist()

    def poll_once(self, sid):
        shade=self.shades[sid]
        if not self.ready or shade.mac not in self.connector.devices: return
        if self.connector.devices[shade.mac] not in SINGLE_AXIS_TYPES or shade.mac in self.connector.diagnostic_devices: return
        generation=self._connection_generation
        cancel=lambda:self.stop.is_set() or generation != self._connection_generation or self.commands.has_stop()
        self._poll_attempts[sid]=self.monotonic_clock()
        if not single_axis_profile(self.connector.devices[shade.mac], self.connector.device_profiles.get(shade.mac)):
            response=self.connector.device_request(shade.mac, cancelled=cancel)
            profile=self.connector.device_profiles.get(shade.mac)
            if profile is not None:
                state=self.states[sid]
                state.begin_session(session_id=self.adapter_session,source_generation=self.source_generation,
                    bridge_id=self.connector.bridge_mac,device_id=shade.mac,device_type=self.connector.devices[shade.mac],commissioned_epoch=shade.commissioned_epoch,
                    now=self.wall_clock(),monotonic_now=self.monotonic_clock(),motor_profile=profile)
                state.import_watermark(self._restored.get('watermarks',{}).get(sid))
        else:
            response=self.connector.device_request(shade.mac, {'operation':5}, cancelled=cancel)
        response = decode(json.dumps(response, allow_nan=False).encode('utf-8'))
        if generation != self._connection_generation: return
        self.states[sid].accept(response,self.wall_clock(),monotonic_now=self.monotonic_clock())
        self.bridge_seen=self.wall_clock(); self._bridge_seen_mono=self.monotonic_clock()
        self._poll_completed_at[sid]=self.monotonic_clock(); self._persist()

    def handle_report(self, message, now=None):
        with self.commands.lock:
            if not self.connected.is_set() or not self.ready or not self._session_bound:
                return
            self._handle_report_locked(message, now)

    def _handle_report_locked(self, message, now=None):
        now=self.wall_clock() if now is None else now
        if not isinstance(message,dict): return
        try: mac=canonical_mac(message.get('mac'))
        except ProtocolError: return
        kind=message.get('msgType')
        if kind=='Heartbeat' and mac==self.connector.bridge_mac:
            try: checked=decode(json.dumps(message,allow_nan=False).encode())
            except (ProtocolError,ValueError,UnicodeError): return
            token=self.connector.token
            self.connector.observe(checked)
            if checked.get('token') != self.connector.token: return
            with self.commands.lock:
                if token != self.connector.token: self._begin_sources('bridge_token_rotated')
                self.bridge_seen=now; self._bridge_seen_mono=self.monotonic_clock(); self._persist()
        elif kind=='Report' and mac in self.by_mac and self._session_bound:
            sid=self.by_mac[mac]; state=self.states[sid]
            message={**message,'mac':mac}
            self.connector.observe(message)
            accepted=state.accept(message,now,session_id=self.adapter_session,
                source_generation=self.source_generation,monotonic_now=self.monotonic_clock())
            if accepted:
                self.bridge_seen=now; self._bridge_seen_mono=self.monotonic_clock()
                active=self._active.get(sid)
                if active is not None:
                    target=active.envelope.command
                    native=state.position
                    reached=(type(target) is int and native==target) or (target=='UP' and native==0) or (target=='DOWN' and native==100)
                    if reached:
                        self._record_active_result(active,'target_observed','actor_unknown_no_causal_completion')
                        self._result(active); self._active.pop(sid,None); self._outcome_deadlines.pop(sid,None)
            self._persist()

    def _physical_gate(self, job):
        command=job.envelope; shade=self.shades.get(command.shade_id)
        if shade is None or not self.connected.is_set() or (not self.ready and command.command != 'STOP'): return False
        if self.config.owner_key is None or self.config.bridge_mac is None or self.config.message_id_state_file is None or not self._journal_ok: return False
        if self.connector.bridge_mac != self.config.bridge_mac or self.connector.expected_bridge_mac != self.config.bridge_mac or self.connector.token is None: return False
        if (self.connector.ids.state_path is None
                or self.connector.ids.state_path.resolve() != Path(self.config.message_id_state_file).resolve()): return False
        if not shade.commissioned_epoch or not all((shade.model,shade.firmware,shade.commissioning_ref,shade.slot,shade.zone)): return False
        if not single_axis_profile(self.connector.devices.get(shade.mac),self.connector.device_profiles.get(shade.mac)): return False
        context=command.source_context
        if any(context.get(key)!=value for key,value in {'commissioned_epoch':shade.commissioned_epoch,'source_generation':self.source_generation,
            'bridge_id':self.connector.bridge_mac,'device_id':shade.mac}.items()): return False
        value=command.command
        if type(value) is int:
            if not shade.percentage: return False
        elif value not in shade.supported_operations: return False
        if value!='STOP' and not self.config.allow_commands: return False
        if command.origin=='automatic':
            snap=self.states[shade.id].snapshot(self.wall_clock(),self.config.stale_seconds,monotonic_now=self.monotonic_clock())
            if not snap['usable'] or context.get('observation_id') != (snap['observation'] or {}).get('id'): return False
            if any(context.get(key) is not True for key in ('effect_qualified','manual_hold_clear','safety_clear')): return False
        return True

    def execute(self, job):
        if job is None: return
        generation=self._connection_generation
        source_generation=self.source_generation
        dispatched=False
        def final_admission():
            nonlocal dispatched
            now_ms,mono=self._times()
            if generation != self._connection_generation or not self._physical_gate(job) or not self.commands.mark_dispatched(job,now_ms,mono):
                self._record_job_result(job,'rejected','final_admission_refused')
                return False
            if not self._persist():
                self._record_job_result(job,'uncertain','durability_failed_before_send')
                return False
            # Durability can block: recheck the complete final boundary after fsync.
            final_ms, final_mono = self._times()
            if (generation != self._connection_generation or source_generation != self.source_generation
                    or not self.commands.dispatched_still_valid(job,final_ms,final_mono)
                    or not self._physical_gate(job)):
                self._record_job_result(job,'rejected','expired_or_withdrawn_after_journal_before_send')
                return False
            self.states[job.envelope.shade_id].motion_admitted(job.envelope.command,self.wall_clock(),
                monotonic_now=final_mono,request_id=job.envelope.request_id)
            dispatched=True
            previous=self._active.get(job.envelope.shade_id)
            if previous is not None:
                self._record_active_result(previous,'uncertain','later_request_dispatched_no_causal_outcome')
            self._active[job.envelope.shade_id]=job
            # A local evidence deadline, never a claim about physical travel time.
            self._outcome_deadlines[job.envelope.shade_id]=final_mono+120
            return True
        cancel=lambda:self.stop.is_set() or generation != self._connection_generation or (job.envelope.command!='STOP' and self.commands.has_stop())
        try:
            if not self._physical_gate(job):
                self._record_job_result(job,'rejected','physical_capability_or_source_not_qualified'); return
            shade=self.shades[job.envelope.shade_id]
            response=self.connector.command(shade.mac,job.envelope.command,shade.invert,
                before_send=final_admission,cancelled=cancel,send_guard=lambda:self.commands.lock)
            response = decode(json.dumps(response, allow_nan=False).encode('utf-8'))
            with self.commands.lock:
                current=self.commands.receipt(job.envelope.request_id)
                if (generation != self._connection_generation or source_generation != self.source_generation
                        or not self.connected.is_set()):
                    self._record_job_result(job,'uncertain','source_withdrawn_during_ack_wait')
                    return
                if dispatched and self.commands.owns_job(job) and current['status']=='dispatched':
                    self.states[shade.id].accept(response,self.wall_clock(),monotonic_now=self.monotonic_clock())
                    self.bridge_seen=self.wall_clock(); self._bridge_seen_mono=self.monotonic_clock()
                    self._record_job_result(job,'acknowledged','await_motor_report')
        except (OSError,ProtocolError,ValueError):
            with self.commands.lock:
                if dispatched:
                    self._record_job_result(job,'uncertain','bridge_error_no_retry')
                    if generation==self._connection_generation and source_generation==self.source_generation:
                        self.ready=False
                        self.commands.invalidate('bridge_error_no_retry',preserve_stops=True)
                else:
                    self._record_job_result(job,'rejected','not_dispatched')

        finally:
            self._persist(); self._result(job)

    def _expire_outcomes(self):
        for sid, deadline in list(self._outcome_deadlines.items()):
            if self.monotonic_clock() >= deadline:
                job=self._active.pop(sid,None); self._outcome_deadlines.pop(sid,None)
                if job is not None:
                    self._record_active_result(job,'uncertain','post_command_observation_deadline_elapsed')
                    self._persist(); self._result(job)

    def publish_states(self, now=None):
        self._expire_outcomes()
        now=self.wall_clock() if now is None else now; mono=self.monotonic_clock()
        bridge_ok=self._bridge_seen_mono is not None and 0 <= mono-self._bridge_seen_mono <= 180 and self.connected.is_set()
        if not bridge_ok: self.ready=False
        self.publish('service/availability','online' if self.connected.is_set() else 'offline')
        self.publish('bridge/availability','online' if bridge_ok else 'offline')
        health_key = (self.source_generation, self.ready, bridge_ok, self._journal_ok,
                      self.connector.bridge_mac)
        if self._last_health is None or self._last_health[1] != health_key or mono-self._last_health[0] >= 30:
            self.publish('service/state',{'schema_version':2,'adapter_session_id':self.adapter_session,
                'source_generation':self.source_generation,'ready':self.ready,'emitted_at':now,
                'automatic_released':False,'receipt_journal_healthy':self._journal_ok,
                'discovery_correlation':self.connector.discovery_correlation,
                'poll_coverage_degraded':len(self.shades)*(self.config.request_timeout_seconds+self.config.poll_spacing_seconds)>self.config.poll_seconds})
            self.publish('bridge/state',{'schema_version':2,'adapter_session_id':self.adapter_session,
                'source_generation':self.source_generation,'bridge_id':self.connector.bridge_mac,'reachable':bridge_ok,'emitted_at':now})
            self._last_health=(mono,health_key)
        for sid,state in self.states.items():
            snap=state.snapshot(now,self.config.stale_seconds,monotonic_now=mono)
            available=bridge_ok and snap['usable'] and snap['adapter_session_id']==self.adapter_session and snap['source_generation']==self.source_generation
            self.publish(f'{sid}/availability','online' if available else 'offline')
            self.publish(f'{sid}/position',snap['reported_position'] if available else 'UNDEF')
            self.publish(f'{sid}/state',snap)
            self.publish(f'{sid}/source/state',{'schema_version':2,'adapter_session_id':self.adapter_session,
                'source_generation':self.source_generation,'commissioned_epoch':self.shades[sid].commissioned_epoch,
                'bridge_id':self.connector.bridge_mac,'device_id':self.shades[sid].mac,'available':available})

    def run(self):
        reports=self.reports_factory(self.config.bridge_host,self.config.interface_ip)
        self.client.connect_async(self.config.mqtt_host,self.config.mqtt_port,keepalive=30); self.client.loop_start()
        next_discovery=0.; next_poll=0.; poll_due=0.; poll_ids=deque(); command_streak=0
        try:
            while not self.stop.is_set():
                mono=self.monotonic_clock()
                if self.reset.is_set():
                    self.reset.clear()
                    self._invalidate_publications()
                    self._session_bound=False; self.bridge_seen=None; self._bridge_seen_mono=None
                    next_discovery=next_poll=0.; poll_ids.clear()
                drain_until=self.monotonic_clock()+.01
                for _ in range(32):
                    if self.monotonic_clock()>=drain_until: break
                    message=reports.receive()
                    if message is None: break
                    self.handle_report(message)
                # STOP preempts discovery, normal pacing, and pending polling.
                job=self.commands.pop() if self.commands.has_stop() else None
                if job is not None:
                    self.execute(job); command_streak=0
                elif self.connected.is_set():
                    if mono>=next_discovery:
                        try:
                            self.discover_once(); next_discovery=self.monotonic_clock()+60
                        except (OSError,ProtocolError,ValueError):
                            self.ready=False; self.commands.invalidate('discovery_failed',preserve_stops=True)
                            next_discovery=self.monotonic_clock()+15
                    mono=self.monotonic_clock()
                    if not poll_ids and mono>=next_poll:
                        poll_ids.extend(self.shades); next_poll=mono+self.config.poll_seconds
                    if self.ready and mono>=poll_due:
                        job=self.commands.pop() if command_streak<4 or not poll_ids else None
                        if job is not None:
                            self.execute(job); command_streak+=1
                            poll_due=self.monotonic_clock()+self.config.poll_spacing_seconds
                        elif poll_ids:
                            sid=poll_ids.popleft()
                            try: self.poll_once(sid)
                            except (OSError,ProtocolError,ValueError): pass
                            poll_due=self.monotonic_clock()+self.config.poll_spacing_seconds; command_streak=0
                self.publish_states()
                self.wait(.05)
        finally:
            with self.commands.lock:
                self.ready=False; self.commands.invalidate('service_stopped')
                for state in self.states.values(): state.invalidate('service_stopped')
                self._persist()
            for sid in self.shades:
                self.publish(f'{sid}/availability','offline'); self.publish(f'{sid}/position','UNDEF')
            self.publish('bridge/availability','offline')
            self.publish('service/availability','offline')
            self.client.disconnect(); self.client.loop_stop(); reports.close()
