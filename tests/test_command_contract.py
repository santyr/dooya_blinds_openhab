"""Signed owner contracts and pure bounded queue: no broker, UDP, or hardware."""
from copy import deepcopy
import importlib.util
import threading
import uuid

import pytest

try:
    from dooya_openhab import commands as contract
except ImportError:
    contract = None

KEY = b'synthetic-owner-key-never-deploy!!'
SESSION = 'synthetic-adapter-session'
OWNER = str(uuid.uuid4())
GENERATION = str(uuid.uuid4())
ZONES = {'k': 'kitchen', 'l': 'living', 'b': 'bathroom', 'r': 'bedroom'}


def command_dict(**changes):
    return {'schema': 'dooya-owner-command/v1', 'request_id': str(uuid.uuid4()),
            'shade_id': 'k', 'command': 'UP', 'origin': 'manual',
            'created_at_ms': 100000, 'expires_at_ms': 120000,
            'adapter_session': SESSION, 'owner_session': OWNER,
            'policy_generation': GENERATION, 'source_context': {}, **changes}


def policy_dict(**changes):
    return {'schema': 'dooya-owner-policy/v1', 'generation': GENERATION,
            'owner_session': OWNER, 'adapter_session': SESSION,
            'issued_at_ms': 100000, 'expires_at_ms': 125000,
            'zones': {z: {'enabled': True, 'authoritative': True} for z in ZONES.values()},
            'override': {'state': 'OFF', 'authoritative': True},
            'global_released': True, **changes}


@pytest.fixture
def api():
    assert contract is not None, 'Actual owner command-contract module must exist'
    return contract


def verified(api, **changes):
    data = command_dict(**changes)
    return api.verify_command(api.signed_command(data, KEY), KEY)


def policy(api, **changes):
    data = policy_dict(**changes)
    return api.verify_policy(api.signed_policy(data, KEY), KEY)


def queue(api, *, released=True):
    q = api.CommandQueue(ZONES, adapter_session=SESSION, automatic_released=released)
    assert q.update_policy(policy(api), 100000, 10)
    return q


def test_actual_module_available():
    assert contract is not None, 'Actual owner command-contract module must exist'
    assert importlib.util.find_spec('dooya_openhab.commands')


def test_signature_binds_all_fields_and_domains(api):
    raw = command_dict()
    signed = api.signed_command(raw, KEY)
    assert api.verify_command(signed, KEY).to_dict() == raw
    changed = deepcopy(signed)
    changed['envelope']['origin'] = 'automatic'
    with pytest.raises(api.CommandError): api.verify_command(changed, KEY)
    with pytest.raises(api.CommandError): api.verify_command(signed, b'wrong-owner-key-xxxxxxxxxxxxxxxx')
    with pytest.raises(api.CommandError): api.verify_policy(signed, KEY)
    signed_policy = api.signed_policy(policy_dict(), KEY)
    with pytest.raises(api.CommandError): api.verify_command(signed_policy, KEY)


def test_verified_command_is_immutable_and_context_not_shared(api):
    envelope = verified(api, source_context={'observation_id': 'synthetic-o1'})
    snapshot = envelope.to_dict()
    snapshot['command'] = 'DOWN'
    envelope.source_context['observation_id'] = 'forged'
    assert envelope.command == 'UP'
    assert envelope.source_context['observation_id'] == 'synthetic-o1'


@pytest.mark.parametrize('changes', [{'command': True}, {'command': 25.0}, {'command': -1},
    {'origin': 'browser'}, {'origin': 'automatic', 'policy_generation': None, 'source_context': {'observation_id': 'o1'}}, {'expires_at_ms': 131000},
    {'created_at_ms': True}, {'source_context': []}, {'request_id': 'not-a-uuid'},
    {'owner_session': 'not-a-uuid'}, {'unexpected': 'field'}])
def test_malformed_signed_envelopes_are_refused(api, changes):
    with pytest.raises(api.CommandError):
        api.verify_command(api.signed_command(command_dict(**changes), KEY), KEY)


def test_untrusted_caller_labels_cannot_submit(api):
    q = queue(api)
    result = q.submit(command_dict(origin='automatic', source_context={'observation_id': 'o1'}), 100001, 11)
    assert result['status'] == 'rejected' and result['reason'] == 'untrusted_command'
    assert q.pop() is None


def test_duplicate_is_idempotent_and_conflicting_reuse_cannot_replace_original(api):
    q = queue(api)
    item = verified(api)
    first = q.submit(item, 100001, 11)
    assert first['status'] == 'queued'
    assert q.submit(item, 100002, 12) == first
    changed = api.verify_command(api.signed_command({**item.to_dict(), 'command': 'DOWN'}, KEY), KEY)
    assert q.submit(changed, 100003, 13)['reason'] == 'request_identity_conflict'
    job = q.pop()
    assert job.envelope.command == 'UP' and q.pop() is None
    assert q.mark_dispatched(job, 100004, 14)
    assert not q.mark_dispatched(job, 100005, 15)


def test_same_origin_not_sent_target_supersedes_and_stop_has_priority(api):
    q = queue(api)
    one, two = verified(api, command=25), verified(api, command=75)
    q.submit(one, 100001, 11)
    q.submit(two, 100002, 12)
    assert q.receipt(one.request_id)['status'] == 'superseded'
    stop = verified(api, command='STOP', policy_generation=None)
    q.submit(stop, 100003, 13)
    assert q.receipt(two.request_id)['status'] == 'superseded'
    assert q.has_stop('k')
    denied = q.submit(verified(api), 100004, 14)
    assert denied['reason'] == 'stop_pending'
    assert q.pop().envelope.request_id == stop.request_id


def test_expiry_is_rechecked_after_another_selected_job_times_out(api):
    q = queue(api)
    first = verified(api, shade_id='k')
    second = verified(api, shade_id='l', expires_at_ms=102000)
    q.submit(first, 100000, 10)
    q.submit(second, 100000, 10)
    job_one, job_two = q.pop(), q.pop()
    assert q.mark_dispatched(job_one, 100000, 10)
    q.record_result(job_one, 'uncertain', 'lost_ack_no_retry', 103000, 13)
    assert not q.validate_for_send(job_two, 103000, 13)
    assert q.receipt(second.request_id)['status'] == 'expired'
    assert q.pop() is None


def test_clock_rollback_cannot_extend_pending_command_deadline(api):
    q = queue(api)
    item = verified(api, expires_at_ms=105000)
    q.submit(item, 100000, 10)
    job = q.pop()
    assert not q.validate_for_send(job, 100001, 16)
    assert q.receipt(item.request_id)['status'] == 'expired'


def test_disconnect_between_pop_and_send_invalidates_generation_and_no_replay(api):
    q = queue(api)
    item = verified(api)
    q.submit(item, 100001, 11)
    job = q.pop()
    q.invalidate('disconnected')
    assert not q.validate_for_send(job, 100002, 12)
    assert q.submit(item, 100003, 13)['status'] == 'rejected'
    assert q.pop() is None


def test_lost_ack_survives_journal_restart_as_uncertain_without_retry(api):
    q = queue(api)
    item = verified(api)
    q.submit(item, 100001, 11)
    job = q.pop()
    assert q.mark_dispatched(job, 100002, 12)
    q.record_result(job, 'uncertain', 'lost_ack_no_retry', 100003, 13)
    restarted = queue(api)
    restarted.import_ledger(q.export_ledger())
    assert restarted.receipt(item.request_id)['status'] == 'uncertain'
    assert restarted.submit(item, 100004, 14)['status'] == 'uncertain'
    assert restarted.pop() is None


def test_restart_never_restores_queued_or_acknowledged_work(api):
    q = queue(api)
    one, two = verified(api), verified(api, shade_id='l')
    q.submit(one, 100001, 11)
    job = q.pop()
    assert q.mark_dispatched(job, 100002, 12)
    q.record_result(job, 'acknowledged', 'await_report', 100003, 13)
    q.submit(two, 100004, 14)
    restarted = queue(api)
    restarted.import_ledger(q.export_ledger())
    assert restarted.receipt(one.request_id)['status'] == 'uncertain'
    assert restarted.receipt(two.request_id)['status'] == 'expired'
    assert restarted.pop() is None


@pytest.mark.parametrize('shade', ['k', 'l', 'b', 'r'])
@pytest.mark.parametrize('override', ['ON', 'OFF', None])
def test_automatic_zone_override_truth_table(api, shade, override):
    q = queue(api)
    generation = str(uuid.uuid4())
    assert q.update_policy(policy(api, generation=generation, issued_at_ms=100001,
        override={'state': override, 'authoritative': override is not None}), 100001, 11)
    item = verified(api, shade_id=shade, origin='automatic', policy_generation=generation,
                    source_context={'observation_id': 'synthetic-o1'})
    accepted = q.submit(item, 100002, 12)['status'] == 'queued'
    assert accepted is (shade in ('k', 'l') or override == 'OFF')


@pytest.mark.parametrize('enabled,authoritative', [(False, True), (None, True), (True, False)])
def test_zone_off_or_unknown_blocks_only_automatic(api, enabled, authoritative):
    q = queue(api)
    gen = str(uuid.uuid4())
    zones = policy_dict()['zones']
    zones['bathroom'] = {'enabled': enabled, 'authoritative': authoritative}
    assert q.update_policy(policy(api, generation=gen, issued_at_ms=100001, zones=zones,
        override={'state': 'ON', 'authoritative': True}), 100001, 11)
    automatic = verified(api, shade_id='b', origin='automatic', policy_generation=gen,
                         source_context={'observation_id': 'o1'})
    assert q.submit(automatic, 100002, 12)['status'] == 'rejected'
    manual = verified(api, shade_id='b', policy_generation=gen)
    assert q.submit(manual, 100003, 13)['status'] == 'queued'


def test_initial_local_automatic_release_is_always_closed(api):
    q = queue(api, released=False)
    item = verified(api, origin='automatic', source_context={'observation_id': 'o1'})
    assert q.submit(item, 100001, 11)['reason'] == 'automatic_not_released'


def test_policy_off_on_never_revives_old_selected_automatic_request(api):
    q = queue(api)
    old = verified(api, origin='automatic', source_context={'observation_id': 'o1'})
    q.submit(old, 100001, 11)
    job = q.pop()
    zones = policy_dict()['zones']
    zones['kitchen']['enabled'] = False
    off = str(uuid.uuid4())
    assert q.update_policy(policy(api, generation=off, issued_at_ms=100002, zones=zones), 100002, 12)
    on = str(uuid.uuid4())
    assert q.update_policy(policy(api, generation=on, issued_at_ms=100003), 100003, 13)
    assert not q.validate_for_send(job, 100004, 14)
    assert q.receipt(old.request_id)['status'] == 'superseded'
    assert q.submit(old, 100005, 15)['status'] == 'superseded'
    assert q.pop() is None


def test_policy_generation_cannot_be_reused_for_permission_change_or_replayed(api):
    q = queue(api)
    assert not q.update_policy(policy(api, global_released=False, issued_at_ms=100001), 100001, 11)
    newer = str(uuid.uuid4())
    assert q.update_policy(policy(api, generation=newer, issued_at_ms=100002), 100002, 12)
    assert not q.update_policy(policy(api, issued_at_ms=100003), 100003, 13)
    assert q.policy.generation == newer


def test_same_policy_fresh_read_renews_it_but_never_command_deadline(api):
    q = queue(api)
    item = verified(api, expires_at_ms=105000)
    q.submit(item, 100001, 11)
    assert q.update_policy(policy(api, issued_at_ms=104000, expires_at_ms=130000), 104000, 14)
    assert not q.validate_for_send(q.pop(), 106000, 16)
    assert q.receipt(item.request_id)['status'] == 'expired'


def test_stop_can_be_admitted_without_current_policy_and_null_generation(api):
    q = api.CommandQueue(ZONES, adapter_session=SESSION)
    item = verified(api, command='STOP', policy_generation=None)
    assert q.submit(item, 100001, 11)['status'] == 'queued'
    assert q.mark_dispatched(q.pop(), 100002, 12)


def test_foreign_adapter_and_owner_session_cannot_dispatch(api):
    q = queue(api)
    assert q.submit(verified(api, adapter_session='old-adapter'), 100001, 11)['reason'] == 'adapter_session_mismatch'
    assert q.submit(verified(api, owner_session=str(uuid.uuid4())), 100001, 11)['reason'] == 'owner_session_mismatch'


def test_group_is_explicit_per_shade_membership_not_unfiltered_broadcast(api):
    q = queue(api)
    accepted = []
    for sid in ['k', 'l', 'b', 'r', 'ALL']:
        item = verified(api, shade_id=sid, origin='automatic', source_context={'group_id': 'synthetic-group'})
        accepted.append(q.submit(item, 100001, 11)['status'])
    assert accepted == ['queued'] * 4 + ['rejected']
    assert [q.pop().envelope.shade_id for _ in range(4)] == list(ZONES)


def test_queue_and_dedupe_ledger_are_bounded_without_early_identity_eviction(api):
    q = api.CommandQueue(ZONES, adapter_session=SESSION, limit=1, ledger_limit=2)
    assert q.update_policy(policy(api), 100000, 10)
    one = verified(api)
    q.submit(one, 100001, 11)
    second = verified(api, shade_id='l')
    assert q.submit(second, 100002, 12)['reason'] == 'queue_full'
    third = verified(api, shade_id='b')
    assert q.submit(third, 100003, 13)['reason'] == 'identity_ledger_full'
    assert len(q.export_ledger()) <= 2
    assert q.submit(one, 100004, 14)['status'] == 'queued'


def test_dispatch_and_policy_commit_share_effective_lock_order(api):
    q = queue(api)
    item = verified(api, origin='automatic', source_context={'observation_id': 'o1'})
    q.submit(item, 100001, 11)
    job = q.pop()
    entered = threading.Event()
    finished = threading.Event()
    sent = []
    def send():
        entered.set()
        with q.lock:
            if q.mark_dispatched(job, 100003, 13): sent.append(item.request_id)
        finished.set()
    with q.lock:
        thread = threading.Thread(target=send)
        thread.start()
        assert entered.wait(1)
        zones = policy_dict()['zones']
        zones['kitchen']['enabled'] = False
        assert q.update_policy(policy(api, generation=str(uuid.uuid4()), issued_at_ms=100002,
            zones=zones), 100002, 12)
    thread.join(1)
    assert finished.is_set() and sent == []



def test_trusted_manual_can_operate_without_policy_or_automation_generation(api):
    q = api.CommandQueue(ZONES, adapter_session=SESSION)
    item = verified(api, policy_generation=None)
    assert q.submit(item, 100001, 11)['status'] == 'queued'
    assert q.mark_dispatched(q.pop(), 100002, 12)


def test_manual_does_not_depend_on_expired_zone_policy_or_generation(api):
    q = queue(api)
    item = verified(api, created_at_ms=126000, expires_at_ms=150000,
                    policy_generation=str(uuid.uuid4()))
    assert q.submit(item, 126001, 36)['status'] == 'queued'
    assert q.mark_dispatched(q.pop(), 126002, 37)


def test_stop_preserving_invalidation_keeps_priority_and_generation_valid(api):
    q = queue(api)
    normal = verified(api, shade_id='l')
    stop = verified(api, command='STOP', policy_generation=None)
    q.submit(normal, 100001, 11)
    selected = q.pop()
    q.submit(stop, 100002, 12)
    q.invalidate('poll_cancelled_for_stop', preserve_stops=True)
    assert not q.validate_for_send(selected, 100003, 13)
    assert q.has_stop()
    assert q.mark_dispatched(q.pop(), 100004, 14)


def test_pending_stop_blocks_normal_selected_for_another_shade_before_send(api):
    q = queue(api)
    q.submit(verified(api, shade_id='l'), 100001, 11)
    selected = q.pop()
    stop = verified(api, command='STOP', policy_generation=None)
    q.submit(stop, 100002, 12)
    assert not q.validate_for_send(selected, 100003, 13)
    assert q.mark_dispatched(q.pop(), 100004, 14)


def test_outcome_api_cannot_mark_unvalidated_queued_request_dispatched_or_observed(api):
    q = queue(api)
    item = verified(api)
    q.submit(item, 100001, 11)
    job = q.pop()
    with pytest.raises(api.CommandError):
        q.record_result(job, 'dispatched', 'bypass_final_validation', 100002, 12)
    with pytest.raises(api.CommandError):
        q.record_result(job, 'target_observed', 'not_sent', 100003, 13)
    assert q.receipt(item.request_id)['status'] == 'selected'


def test_automatic_stop_cannot_interrupt_unowned_manual_move(api):
    q = queue(api)
    manual = verified(api)
    q.submit(manual, 100001, 11)
    assert q.mark_dispatched(q.pop(), 100002, 12)
    automatic_stop = verified(api, command='STOP', origin='automatic', policy_generation=None,
                              source_context={'owned_request_id': manual.request_id})
    assert q.submit(automatic_stop, 100003, 13)['status'] == 'rejected'


def test_automatic_stop_only_references_latest_owned_inflight_automatic_move(api):
    q = queue(api)
    auto = verified(api, origin='automatic', source_context={'observation_id': 'o1'})
    q.submit(auto, 100001, 11)
    assert q.mark_dispatched(q.pop(), 100002, 12)
    zones = policy_dict()['zones']
    zones['kitchen']['enabled'] = False
    generation = str(uuid.uuid4())
    assert q.update_policy(policy(api, generation=generation, issued_at_ms=100003, zones=zones), 100003, 13)
    stop = verified(api, command='STOP', origin='automatic', policy_generation=None,
                    source_context={'owned_request_id': auto.request_id})
    assert q.submit(stop, 100004, 14)['status'] == 'queued'
    assert q.mark_dispatched(q.pop(), 100005, 15)


def test_later_manual_dispatch_revokes_old_automatic_stop_ownership(api):
    q = queue(api)
    auto = verified(api, origin='automatic', source_context={'observation_id': 'o1'})
    q.submit(auto, 100001, 11)
    assert q.mark_dispatched(q.pop(), 100002, 12)
    manual = verified(api)
    q.submit(manual, 100003, 13)
    assert q.mark_dispatched(q.pop(), 100004, 14)
    stop = verified(api, command='STOP', origin='automatic', policy_generation=None,
                    source_context={'owned_request_id': auto.request_id})
    assert q.submit(stop, 100005, 15)['status'] == 'rejected'


def test_expired_waiting_jobs_do_not_permanently_occupy_bounded_queue(api):
    q = api.CommandQueue(ZONES, adapter_session=SESSION, limit=1)
    old = verified(api, expires_at_ms=101000, policy_generation=None)
    q.submit(old, 100000, 10)
    new = verified(api, shade_id='l', created_at_ms=102000, expires_at_ms=120000,
                   policy_generation=None)
    assert q.submit(new, 102000, 12)['status'] == 'queued'
    assert q.pop().envelope.request_id == new.request_id


@pytest.mark.parametrize('context', [{'observation_id': float('nan')},
    {'revision': 2**53}, {'oversized': 'x' * 5000}])
def test_source_context_is_bounded_and_javascript_interoperable(api, context):
    with pytest.raises(api.CommandError):
        verified(api, source_context=context)


def test_signed_json_duplicate_keys_are_refused_before_authority(api):
    import json
    signed = api.signed_command(command_dict(), KEY)
    raw = json.dumps(signed)
    raw = raw.replace('"origin": "manual"', '"origin": "automatic", "origin": "manual"')
    with pytest.raises(api.CommandError): api.verify_command(raw, KEY)


def test_policy_unknown_or_malformed_fields_do_not_install_partial_authority(api):
    q = queue(api)
    prior = q.policy.to_dict()
    malformed = policy_dict(generation=str(uuid.uuid4()))
    malformed['zones']['bedroom']['enabled'] = 'ON'
    with pytest.raises(api.CommandError):
        api.verify_policy(api.signed_policy(malformed, KEY), KEY)
    assert q.policy.to_dict() == prior
    assert not q.update_policy(policy_dict(), 100001, 11)


def test_repeating_expired_policy_cannot_acknowledge_current_authority(api):
    q = queue(api)
    assert not q.update_policy(policy(api), 100001, 36)
    auto = verified(api, origin='automatic', source_context={'observation_id': 'o1'})
    assert q.submit(auto, 100002, 37)['status'] == 'rejected'


def test_old_expired_policy_generations_do_not_permanently_fill_capacity(api):
    q = queue(api)
    for index in range(520):
        now = 100001 + index * 31000
        assert q.update_policy(policy(api, generation=str(uuid.uuid4()),
            issued_at_ms=now, expires_at_ms=now + 20000), now, 11 + index * 31)


def test_no_full_ledger_identity_eviction_can_replay_after_utc_rollback(api):
    q = api.CommandQueue(ZONES, adapter_session=SESSION, limit=1, ledger_limit=1)
    original = verified(api, policy_generation=None, expires_at_ms=101000)
    q.submit(original, 100000, 10)
    job = q.pop()
    assert q.mark_dispatched(job, 100001, 10.001)
    q.record_result(job, 'uncertain', 'lost_ack_no_retry', 100002, 10.002)
    newer = verified(api, shade_id='l', created_at_ms=102000, expires_at_ms=103000,
                     policy_generation=None)
    assert q.submit(newer, 102000, 12)['status'] == 'queued'
    q.invalidate('disconnected')
    replay = q.submit(original, 100000, 14)
    assert replay['status'] == 'rejected'
    assert q.pop() is None


def test_operator_stop_uses_reserved_identity_after_normal_ledger_saturation(api):
    q = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    move = verified(api, policy_generation=None)
    q.submit(move, 100001, 11)
    move_job = q.pop()
    assert q.mark_dispatched(move_job, 100002, 12)
    stop = verified(api, command='STOP', policy_generation=None)
    result = q.submit(stop, 100003, 13)
    assert result['status'] == 'queued'
    assert result['capacity_class'] == 'manual_stop_reserve'
    assert len(q.export_ledger()) == 2
    assert q.submit(stop, 100004, 14) == result
    conflict = api.verify_command(api.signed_command({**stop.to_dict(), 'command': 'UP'}, KEY), KEY)
    assert q.submit(conflict, 100005, 15)['reason'] == 'request_identity_conflict'
    assert q.mark_dispatched(q.pop(), 100006, 16)
    assert q.submit(move, 100007, 17)['status'] == 'dispatched'


def test_all_26_shades_keep_operator_stop_reserve_when_normal_queue_and_ledger_full(api):
    shades = {f's{index}': 'kitchen' for index in range(26)}
    q = api.CommandQueue(shades, adapter_session=SESSION, limit=26, ledger_limit=26)
    moves = [verified(api, shade_id=sid, policy_generation=None) for sid in shades]
    for move in moves:
        assert q.submit(move, 100001, 11)['status'] == 'queued'
    selected = q.pop()
    assert q.mark_dispatched(selected, 100002, 12)
    stops = [verified(api, shade_id=sid, command='STOP', policy_generation=None) for sid in shades]
    for stop in stops:
        assert q.submit(stop, 100003, 13)['status'] == 'queued'
    assert len(q.export_ledger()) == 52
    assert len(q.pending) == 26
    for _ in shades:
        job = q.pop()
        assert job.envelope.command == 'STOP'
        assert q.mark_dispatched(job, 100004, 14)
    assert q.pop() is None
    for move in moves:
        assert q.submit(move, 100005, 15)['status'] in ('dispatched', 'superseded')


def test_full_normal_queue_for_other_shades_cannot_block_operator_stop(api):
    q = api.CommandQueue(ZONES, adapter_session=SESSION, limit=1)
    q.submit(verified(api, shade_id='l', policy_generation=None), 100001, 11)
    stop = verified(api, shade_id='k', command='STOP', policy_generation=None)
    assert q.submit(stop, 100002, 12)['status'] == 'queued'
    assert len(q.pending) == 2
    assert q.pop().envelope.request_id == stop.request_id


def test_stop_reserve_is_not_available_to_raw_unknown_or_automatic_requests(api):
    q = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1,
                         automatic_released=True)
    assert q.update_policy(policy(api), 100000, 10)
    auto = verified(api, origin='automatic', source_context={'observation_id': 'o1'})
    q.submit(auto, 100001, 11)
    assert q.mark_dispatched(q.pop(), 100002, 12)
    automatic_stop = verified(api, origin='automatic', command='STOP', policy_generation=None,
                              source_context={'owned_request_id': auto.request_id})
    assert q.submit(automatic_stop, 100003, 13)['status'] == 'rejected'
    assert q.submit(command_dict(command='STOP', policy_generation=None), 100003, 13)['status'] == 'rejected'
    assert q.submit(verified(api, shade_id='unknown', command='STOP', policy_generation=None),
                    100003, 13)['status'] == 'rejected'
    stop = verified(api, command='STOP', policy_generation=None)
    assert q.submit(stop, 100004, 14)['status'] == 'queued'
    second = verified(api, command='STOP', policy_generation=None)
    assert q.submit(second, 100005, 15)['reason'] == 'manual_stop_reserve_full'
    assert len(q.export_ledger()) == 2
    assert q.pop().envelope.request_id == stop.request_id


def test_reserved_stop_journal_roundtrip_preserves_dedup_without_replay(api):
    q = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    move = verified(api, policy_generation=None)
    q.submit(move, 100001, 11)
    assert q.mark_dispatched(q.pop(), 100002, 12)
    stop = verified(api, command='STOP', policy_generation=None)
    q.submit(stop, 100003, 13)
    assert q.mark_dispatched(q.pop(), 100004, 14)
    restored = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    restored.import_ledger(q.export_ledger())
    assert len(restored.export_ledger()) == 2
    assert restored.submit(stop, 100005, 15)['status'] == 'uncertain'
    assert restored.pop() is None


def test_terminal_uncertainty_is_retained_until_capacity_pressure_requires_eviction(api):
    q = api.CommandQueue(ZONES, adapter_session=SESSION, limit=1, ledger_limit=2)
    old = verified(api, policy_generation=None, expires_at_ms=101000)
    q.submit(old, 100000, 10)
    job = q.pop()
    assert q.mark_dispatched(job, 100001, 10.001)
    before = q.record_result(job, 'uncertain', 'lost_ack_no_retry', 100002, 10.002)
    newer = verified(api, shade_id='l', policy_generation=None, created_at_ms=102000,
                     expires_at_ms=103000)
    q.submit(newer, 102000, 12)
    assert q.receipt(old.request_id) == before
    q.invalidate('disconnected')
    newest = verified(api, shade_id='b', policy_generation=None, created_at_ms=104000,
                      expires_at_ms=105000)
    assert q.submit(newest, 104000, 14)['status'] == 'queued'
    assert q.receipt(old.request_id) is None
    assert q.receipt(newer.request_id) is not None


def test_dispatched_guard_rechecks_policy_after_durable_commit_without_restamping_receipt(api):
    q = queue(api)
    assert q.update_policy(policy(api, generation=str(uuid.uuid4()), issued_at_ms=100001,
        expires_at_ms=101001), 100001, 11)
    item = verified(api, origin='automatic', policy_generation=q.policy.generation,
                    source_context={'observation_id': 'o1'})
    q.submit(item, 100002, 11.001)
    job = q.pop()
    assert q.owns_job(job)
    assert q.mark_dispatched(job, 100003, 11.002)
    before = q.receipt(item.request_id)
    assert q.dispatched_still_valid(job, 100004, 11.003)
    assert not q.dispatched_still_valid(job, 101102, 12.101)
    assert q.receipt(item.request_id) == before
    assert not q.owns_job(None)


def test_dispatched_guard_keeps_signed_manual_independent_of_expired_policy(api):
    q = queue(api)
    item = verified(api, policy_generation=None, expires_at_ms=129000)
    q.submit(item, 100001, 11)
    job = q.pop()
    assert q.mark_dispatched(job, 100002, 12)
    assert q.dispatched_still_valid(job, 126000, 36)
    assert not q.dispatched_still_valid(job, 129001, 40)


def test_dispatched_guard_rechecks_disconnect_and_pending_operator_stop(api):
    q = queue(api)
    item = verified(api, policy_generation=None)
    q.submit(item, 100001, 11)
    job = q.pop()
    assert q.mark_dispatched(job, 100002, 12)
    stop = verified(api, command='STOP', policy_generation=None)
    q.submit(stop, 100003, 13)
    assert not q.dispatched_still_valid(job, 100004, 14)
    stop_job = q.pop()
    assert q.mark_dispatched(stop_job, 100005, 15)
    q.invalidate('disconnected')
    assert not q.dispatched_still_valid(stop_job, 100006, 16)


def test_selected_normal_is_withdrawn_for_reserved_operator_stop(api):
    q = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    move = verified(api, policy_generation=None)
    q.submit(move, 100001, 11)
    selected = q.pop()
    stop = verified(api, command='STOP', policy_generation=None)
    assert q.submit(stop, 100002, 12)['status'] == 'queued'
    assert q.receipt(move.request_id)['status'] == 'superseded'
    assert not q.validate_for_send(selected, 100003, 13)
    assert q.mark_dispatched(q.pop(), 100004, 14)


def test_reserved_identity_can_be_reclaimed_only_after_terminal_expiration(api):
    q = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    q.submit(verified(api, policy_generation=None), 100000, 10)
    assert q.mark_dispatched(q.pop(), 100001, 10.001)
    old = verified(api, command='STOP', policy_generation=None, expires_at_ms=101000)
    q.submit(old, 100002, 10.002)
    job = q.pop()
    assert q.mark_dispatched(job, 100003, 10.003)
    q.record_result(job, 'uncertain', 'lost_ack_no_retry', 100004, 10.004)
    another = verified(api, command='STOP', policy_generation=None)
    assert q.submit(another, 100005, 10.005)['reason'] == 'manual_stop_reserve_full'
    assert q.submit(old, 100006, 10.006)['status'] == 'uncertain'
    newer = verified(api, command='STOP', policy_generation=None, created_at_ms=101001)
    assert q.submit(newer, 101001, 11.001)['status'] == 'queued'
    assert q.receipt(old.request_id) is None
    assert len(q.export_ledger()) == 2


def test_history_cannot_forge_extra_stop_reserve_slots(api):
    q = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    move = verified(api, policy_generation=None)
    q.submit(move, 100001, 11)
    assert q.mark_dispatched(q.pop(), 100002, 12)
    stop = verified(api, command='STOP', policy_generation=None)
    q.submit(stop, 100003, 13)
    valid = q.export_ledger()
    invalid = deepcopy(valid)
    invalid[0]['capacity_class'] = 'manual_stop_reserve'
    restored = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    with pytest.raises(api.CommandError): restored.import_ledger(invalid)
    assert restored.export_ledger() == []


def test_dispatched_guard_accepts_owned_automatic_stop_after_dispatch_recording(api):
    q = queue(api)
    auto = verified(api, origin='automatic', source_context={'observation_id': 'o1'})
    q.submit(auto, 100001, 11)
    assert q.mark_dispatched(q.pop(), 100002, 12)
    stop = verified(api, command='STOP', origin='automatic', policy_generation=None,
                    source_context={'owned_request_id': auto.request_id})
    q.submit(stop, 100003, 13)
    job = q.pop()
    assert q.mark_dispatched(job, 100004, 14)
    assert q.dispatched_still_valid(job, 100005, 15)


def test_journal_reserve_cannot_change_shade_outside_fingerprinted_envelope(api):
    q = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    q.submit(verified(api, policy_generation=None), 100001, 11)
    assert q.mark_dispatched(q.pop(), 100002, 12)
    q.submit(verified(api, command='STOP', policy_generation=None), 100003, 13)
    records = q.export_ledger()
    records[-1]['shade_id'] = 'other'
    restored = api.CommandQueue({'k': 'kitchen'}, adapter_session=SESSION, limit=1, ledger_limit=1)
    with pytest.raises(api.CommandError): restored.import_ledger(records)
    assert restored.export_ledger() == []
