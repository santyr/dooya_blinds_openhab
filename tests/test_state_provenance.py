"""Synthetic observations only: actual State module, no RF or broker IO."""
import json

import pytest

from dooya_openhab.model import State, safe_data

MAC = '0000000000000001'
BRIDGE = 'aabbccddeeff'
TYPE = '10000000'


def report(stamp='20261024120001000', **data):
    return {'msgType': 'Report', 'mac': MAC, 'deviceType': TYPE, 'msgID': stamp,
            'data': {'wirelessMode': 1, 'type': 1, 'currentPosition': 25, **data}}


def identified(invert=False):
    state = State(invert)
    state.begin_session(session_id='adapter-a', source_generation=1, bridge_id=BRIDGE,
                        device_id=MAC, device_type=TYPE, now=1000, monotonic_now=10)
    return state


def accept(state, message=None, now=1001, mono=11, session='adapter-a', generation=1):
    return state.accept(message or report(), now, session_id=session,
                        source_generation=generation, monotonic_now=mono)


def snapshot(state, now=1002, mono=12, stale=10):
    return state.snapshot(now, stale, monotonic_now=mono)


@pytest.mark.parametrize('field,value', [('wirelessMode', True), ('operation', False),
    ('currentPosition', True), ('currentPosition', 25.0), ('RSSI', float('nan')),
    ('RSSI', float('inf')), ('batteryLevel', float('-inf')), ('chargingState', True),
    ('wirelessMode', '1'), ('currentState', 1.5)])
def test_malformed_known_fields_cannot_qualify_position(field, value):
    state = State()
    state.accept(report(**{field: value}), 1)
    assert state.position is None
    assert field not in safe_data(report(**{field: value}))
    json.dumps(state.snapshot(2, 10), allow_nan=False)


@pytest.mark.parametrize('native', [0, 25, 50, 75, 100])
@pytest.mark.parametrize('invert', [False, True])
def test_valid_endpoints_and_inversion_with_identified_observation(native, invert):
    state = identified(invert)
    assert accept(state, report(currentPosition=native, RSSI=-80.5, batteryLevel=811))
    snap = snapshot(state)
    assert snap['schema_version'] == 2
    assert snap['reported_position'] == (100 - native if invert else native)
    assert snap['current_position'] == snap['reported_position']
    assert snap['usable'] and not snap['stale']
    assert snap['report']['currentPosition'] == native
    assert 'chargingState' not in snap['report']
    assert snap['observation']['adapter_session_id'] == 'adapter-a'
    assert snap['observation']['source_generation'] == 1
    assert snap['observation']['device_id'] == MAC
    assert snap['observation']['hardware_boot_known'] is False
    assert snap['observation']['measurement_time_known'] is False
    assert snap['observation']['origin'] == 'unknown'
    assert snap['observation']['id']


def test_ack_does_not_renew_observation_or_replace_measured_position():
    state = identified()
    accept(state)
    before = snapshot(state)['observation']
    state.accept({'msgType': 'WriteDeviceAck', 'data': {'currentPosition': 100, 'wirelessMode': 1}},
                 1008, monotonic_now=18)
    snap = snapshot(state, 1009, 19)
    assert snap['reported_position'] == 25 and snap['cache']['currentPosition'] == 100
    assert snap['observation'] == before
    assert snapshot(state, 1012, 22)['stale']


@pytest.mark.parametrize('stamp', ['20261024120001000', '20261024120000000'])
def test_duplicate_and_out_of_order_report_never_renew_time(stamp):
    state = identified()
    accept(state)
    observation = snapshot(state)['observation']
    assert not accept(state, report(stamp, currentPosition=99), 1009, 19)
    snap = snapshot(state, 1012, 22)
    assert snap['reported_position'] == 25
    assert snap['report_received_at'] == 1001
    assert snap['observation'] == observation
    assert snap['stale']


@pytest.mark.parametrize('stamp', [None, 'opaque-123', '20260230120001000', 20261024120001000])
def test_unorderable_report_retains_uncertainty_without_freshness(stamp):
    state = identified()
    assert not accept(state, report(stamp))
    snap = snapshot(state)
    assert not snap['usable'] and snap['current_position'] is None
    assert snap['uncertainty_reason'] == 'report_order_unknown'


def test_previous_session_and_known_delayed_report_cannot_requalify():
    state = identified()
    accept(state)
    state.begin_session(session_id='adapter-b', source_generation=2, bridge_id=BRIDGE,
                        device_id=MAC, device_type=TYPE, now=1005, monotonic_now=15)
    assert not accept(state, report('20261024120002000'), 1006, 16)
    assert not accept(state, report(), 1007, 17, 'adapter-b', 2)
    snap = snapshot(state, 1008, 18)
    assert snap['reported_position'] == 25 and snap['current_position'] is None
    assert not snap['usable']
    assert accept(state, report('20261024120003000'), 1009, 19, 'adapter-b', 2)


@pytest.mark.parametrize('field,value', [('mac', '0000000000000002'), ('deviceType', '10000002')])
def test_inventory_identity_mismatch_cannot_qualify(field, value):
    state = identified()
    message = report()
    message[field] = value
    assert not accept(state, message)
    assert state.position is None


def test_monotonic_usable_interval_does_not_extend_under_wall_clock_rollback():
    state = identified()
    accept(state)
    snap = snapshot(state, now=100, mono=22)
    assert snap['stale'] and not snap['usable']
    assert snap['observation']['received_at'] == 1001


def test_monotonic_rollback_is_unusable_instead_of_negative_age():
    state = identified()
    accept(state)
    assert not snapshot(state, mono=9)['usable']


def test_motion_invalidates_current_and_ack_cannot_restore_it():
    state = identified()
    accept(state)
    state.motion_admitted(100, 1003, monotonic_now=13, request_id='request-a')
    snap = snapshot(state, 1004, 14)
    assert snap['reported_position'] == 25
    assert snap['current_position'] is None and not snap['usable']
    assert snap['requested_target'] == 100
    assert snap['movement_state'] == 'uncertain'
    state.accept({'msgType': 'WriteDeviceAck', 'data': {'currentPosition': 100}}, 1005)
    assert not snapshot(state, 1006, 16)['usable']
    assert not accept(state, report(), 1006, 16)
    assert accept(state, report('20261024120002000', currentPosition=100), 1007, 17)
    snap = snapshot(state, 1008, 18)
    assert snap['current_position'] == 100 and snap['movement_state'] == 'stationary_reported'
    assert snap['observation']['origin'] == 'unknown'


def test_later_unsupported_report_invalidates_current_without_erasing_history():
    state = identified()
    accept(state)
    assert not accept(state, report('20261024120002000', wirelessMode=4), 1003, 13)
    snap = snapshot(state, 1004, 14)
    assert snap['reported_position'] == 25 and snap['report_received_at'] == 1001
    assert snap['current_position'] is None and not snap['usable']


def test_legacy_context_cannot_assert_current_session_evidence():
    state = State()
    state.accept(report(), 1001)
    snap = state.snapshot(1002, 10)
    assert snap['reported_position'] == 25
    assert not snap['usable'] and snap['current_position'] is None
    assert snap['uncertainty_reason'] == 'source_session_unknown'


@pytest.mark.parametrize('changes', [{'RSSI': float('nan')}, {'operation': True},
    {'currentPosition': None}])
def test_later_malformed_report_preserves_history_but_withdraws_usability(changes):
    state = identified()
    accept(state)
    assert not accept(state, report('20261024120002000', **changes), 1003, 13)
    snap = snapshot(state, 1004, 14)
    assert snap['reported_position'] == 25 and snap['current_position'] is None
    assert snap['report_received_at'] == 1001 and not snap['usable']
    assert snap['uncertainty_reason'] == 'malformed_report'
    json.dumps(snap, allow_nan=False)


def test_watermark_restart_prevents_known_report_reuse_without_restoring_observation():
    old = identified()
    accept(old)
    record = old.export_watermark()
    restarted = identified()
    assert restarted.import_watermark(record)
    assert restarted.position is None
    assert not accept(restarted)
    assert not snapshot(restarted)['usable']
    assert accept(restarted, report('20261024120002000'), 1003, 13)


@pytest.mark.parametrize('field,value', [('device_id', '0000000000000002'),
    ('bridge_id', 'ffeeaabbccdd'), ('device_type', '10000002'),
    ('msg_id', 'opaque-id'), ('msg_id', '20260230120001000')])
def test_watermark_cannot_cross_inventory_identity_or_import_unorderable_id(field, value):
    state = identified()
    accept(state)
    original = state.export_watermark()
    invalid = {**original, field: value}
    assert not state.import_watermark(invalid)
    assert state.export_watermark() == original


def test_watermark_import_never_rolls_back_order():
    state = identified()
    accept(state, report('20261024120002000'))
    old_record = {**state.export_watermark(), 'msg_id': '20261024120001000'}
    assert state.import_watermark(old_record)
    assert not accept(state, report('20261024120002000'), 1005, 15)
    assert state.position_at == 1001


def test_new_source_epoch_preserves_last_known_but_not_old_watermark_or_usability():
    state = identified()
    accept(state)
    state.begin_session(session_id='adapter-b', source_generation=2, bridge_id='ffeeaabbccdd',
                        device_id=MAC, device_type=TYPE, now=1005, monotonic_now=15)
    assert state.position == 25
    assert state.export_watermark() is None
    assert not snapshot(state, 1006, 16)['usable']


def test_source_loss_and_snapshot_do_not_extend_or_mutate_original_observation():
    state = identified()
    accept(state)
    initial = snapshot(state)['observation']
    state.invalidate('bridge_unavailable')
    for mono in (12, 13, 14):
        snap = snapshot(state, 1000 + mono, mono)
        assert snap['reported_position'] == 25 and not snap['usable']
        assert snap['observation'] == initial
        assert snap['uncertainty_reason'] == 'bridge_unavailable'


def test_received_unique_report_is_not_automatically_action_or_measurement_qualified():
    state = identified()
    accept(state)
    snap = snapshot(state)
    assert snap['usable']
    assert not snap['action_qualified']
    assert not snap['observation']['measurement_time_known']
    assert not snap['observation']['delayed_delivery_excluded']


@pytest.mark.parametrize('device_type', ['10000002', '10000001', 'unknown'])
def test_learned_unsupported_family_remains_diagnostic_even_with_position(device_type):
    state = State()
    state.begin_session(session_id='adapter-a', source_generation=1, bridge_id=BRIDGE,
                        device_id=MAC, device_type=device_type, now=1000, monotonic_now=10)
    message = {**report(), 'deviceType': device_type}
    assert not accept(state, message)
    assert state.position is None
    assert not snapshot(state)['usable']
    assert snapshot(state)['uncertainty_reason'] == 'device_type_not_qualified'


def test_unique_supported_report_without_position_records_contact_not_position():
    state = identified()
    accept(state)
    message = report('20261024120002000')
    del message['data']['currentPosition']
    assert not accept(state, message, 1003, 13)
    snap = snapshot(state, 1004, 14)
    assert snap['reported_position'] == 25 and snap['report_received_at'] == 1001
    assert snap['motor_contact']['received_at'] == 1003
    assert snap['motor_contact']['upstream_message_id'] == '20261024120002000'
    assert not snap['usable']
    assert not accept(state, message, 1005, 15)
    assert snapshot(state, 1006, 16)['motor_contact'] == snap['motor_contact']


@pytest.mark.parametrize('motor_type', [0, 9, True, 1.0])
def test_unsupported_or_malformed_motor_subtype_cannot_qualify(motor_type):
    state = identified()
    assert not accept(state, report(type=motor_type))
    assert state.position is None


def test_report_dual_axis_fields_cannot_be_filtered_into_ordinary_shade_position():
    state = identified()
    assert not accept(state, report(currentPosition_T=10, currentPosition_B=90))
    assert state.position is None


def test_missing_report_subtype_without_reconciled_profile_withholds_position():
    state = identified()
    message = report()
    del message['data']['type']
    assert not accept(state, message)
    assert state.position is None


def test_explicit_reconciled_profile_can_fill_missing_report_subtype():
    state = State()
    state.begin_session(session_id='adapter-a', source_generation=1, bridge_id=BRIDGE,
                        device_id=MAC, device_type=TYPE, now=1000, monotonic_now=10,
                        motor_profile={'type': 4, 'wirelessMode': 1})
    message = report()
    del message['data']['type']
    assert accept(state, message)
    assert snapshot(state)['usable']
    assert 'type' not in state.report  # Preserve actual received fields.


def test_report_cannot_contradict_explicit_reconciled_motor_subtype():
    state = State()
    state.begin_session(session_id='adapter-a', source_generation=1, bridge_id=BRIDGE,
                        device_id=MAC, device_type=TYPE, now=1000, monotonic_now=10,
                        motor_profile={'type': 4, 'wirelessMode': 1})
    assert not accept(state, report(type=1))
    assert state.position is None


@pytest.mark.parametrize('field,value', [('operation', 3), ('operation', 999),
    ('currentState', -1), ('currentState', 5), ('voltageMode', 2),
    ('chargingState', 2), ('wirelessMode', 6)])
def test_out_of_domain_diagnostics_do_not_qualify_report(field, value):
    state = identified()
    assert not accept(state, report(**{field: value}))
    assert state.position is None
    assert field not in state.report


def test_history_restart_preserves_original_position_and_times_without_current_authority():
    old = identified()
    accept(old)
    old.motion_admitted(100, 1002, monotonic_now=12, request_id='synthetic-r1')
    record = old.export_history()
    restarted = State()
    assert restarted.import_history(record)
    snap = restarted.snapshot(1005, 10, monotonic_now=15)
    assert snap['reported_position'] == 25 and snap['report_received_at'] == 1001
    assert snap['observation'] == record['observation']
    assert not snap['usable'] and snap['current_position'] is None
    assert snap['uncertainty_reason'] == 'previous_session_history'
    assert snap['requested_target'] is None
    restarted.begin_session(session_id='adapter-b', source_generation=2, bridge_id=BRIDGE,
                            device_id=MAC, device_type=TYPE, now=1005, monotonic_now=15)
    assert not accept(restarted, report(), 1006, 16, 'adapter-b', 2)
    assert accept(restarted, report('20261024120002000'), 1007, 17, 'adapter-b', 2)


@pytest.mark.parametrize('field,value', [('reported_position', True),
    ('reported_position', -1), ('report_received_at', float('nan')),
    ('report', {'RSSI': float('nan')}), ('cache', {'token': 'must-not-retain'})])
def test_malformed_history_is_transactionally_refused(field, value):
    old = identified()
    accept(old)
    record = {**old.export_history(), field: value}
    restarted = State()
    assert not restarted.import_history(record)
    assert restarted.position is None and restarted.position_at is None


def test_history_inversion_ownership_must_match_and_new_source_cannot_reuse_old_watermark():
    old = identified()
    accept(old)
    inverted = State(True)
    assert not inverted.import_history(old.export_history())
    restarted = State()
    assert restarted.import_history(old.export_history())
    restarted.begin_session(session_id='adapter-b', source_generation=2, bridge_id='ffeeaabbccdd',
                            device_id=MAC, device_type=TYPE, now=1005, monotonic_now=15)
    assert restarted.export_watermark() is None
    assert restarted.position == 25


@pytest.mark.parametrize('invalid', [{'unknown': {'nested': float('nan')}},
    {'unknown': [float('inf')]}, {'unknown': [[[[[[[[[[[[[[[[[[1]]]]]]]]]]]]]]]]]]}])
def test_unknown_invalid_or_deep_fields_cannot_renew_report_position(invalid):
    state = identified()
    accept(state)
    assert not accept(state, report('20261024120002000', currentPosition=99, **invalid), 1003, 13)
    snap = snapshot(state, 1004, 14)
    assert snap['reported_position'] == 25 and snap['report_received_at'] == 1001
    assert not snap['usable'] and snap['uncertainty_reason'] == 'malformed_report'
    assert not accept(state, report('20261024120002000', currentPosition=99), 1005, 15)
    json.dumps(snap, allow_nan=False)


EPOCH_A = '11111111-1111-4111-8111-111111111111'
EPOCH_B = '22222222-2222-4222-8222-222222222222'


def commissioned(epoch=EPOCH_A):
    state = State()
    state.begin_session(session_id='adapter-a', source_generation=1, bridge_id=BRIDGE,
                        device_id=MAC, device_type=TYPE, now=1000, monotonic_now=10,
                        commissioned_epoch=epoch)
    return state


def test_public_source_identity_digest_matches_literal_ascii_contract_vector():
    from dooya_openhab.model import source_identity_sha256
    # Shared public vector; inputs are entirely synthetic.
    assert source_identity_sha256(BRIDGE, MAC, EPOCH_A) == '334799b22fe71f527841cd037f5b3339a79e2ef6b115e241a3d630ca27457641'
    assert source_identity_sha256(BRIDGE, MAC, EPOCH_B) != source_identity_sha256(BRIDGE, MAC, EPOCH_A)


def test_fresh_position_and_contact_preserve_commissioning_identity_provenance():
    from dooya_openhab.model import source_identity_sha256
    state = commissioned()
    assert accept(state)
    snap = snapshot(state)
    for record in (snap['observation'], snap['motor_contact']):
        assert record['commissioned_epoch'] == EPOCH_A
        assert record['source_identity_sha256'] == source_identity_sha256(BRIDGE, MAC, EPOCH_A)
    assert snap['observation']['hardware_boot_known'] is False
    assert snap['observation']['measurement_time_known'] is False
    assert snap['action_qualified'] is False


def test_epoch_change_does_not_relabel_old_history_or_requalify_known_report():
    from dooya_openhab.model import source_identity_sha256
    old = commissioned()
    accept(old)
    history = old.export_history()
    original_observation = dict(history['observation'])
    original_contact = dict(history['motor_contact'])
    restarted = State()
    assert restarted.import_history(history)
    restarted.begin_session(session_id='adapter-b', source_generation=2, bridge_id=BRIDGE,
        device_id=MAC, device_type=TYPE, now=1005, monotonic_now=15, commissioned_epoch=EPOCH_B)
    snap = snapshot(restarted, 1006, 16)
    assert snap['observation'] == original_observation
    assert snap['motor_contact'] == original_contact
    assert snap['reported_position'] == 25 and snap['report_received_at'] == 1001
    assert snap['current_position'] is None and not snap['usable']
    assert not accept(restarted, report(), 1007, 17, 'adapter-b', 2)
    assert snapshot(restarted, 1008, 18)['observation'] == original_observation
    assert accept(restarted, report('20261024120002000', currentPosition=75), 1009, 19, 'adapter-b', 2)
    fresh = snapshot(restarted, 1010, 20)
    assert fresh['observation']['commissioned_epoch'] == EPOCH_B
    assert fresh['observation']['source_identity_sha256'] == source_identity_sha256(BRIDGE, MAC, EPOCH_B)
    assert fresh['observation']['source_identity_sha256'] != original_observation['source_identity_sha256']
    assert fresh['current_position'] == 75
    assert history['observation'] == original_observation


def test_legacy_history_without_identity_fields_stays_unknown_after_new_commissioning():
    old = identified()
    accept(old)
    history = old.export_history()
    for key in ('observation', 'motor_contact'):
        history[key].pop('commissioned_epoch', None)
        history[key].pop('source_identity_sha256', None)
    restarted = State()
    assert restarted.import_history(history)
    restarted.begin_session(session_id='adapter-b', source_generation=2, bridge_id=BRIDGE,
        device_id=MAC, device_type=TYPE, now=1005, monotonic_now=15, commissioned_epoch=EPOCH_B)
    snap = snapshot(restarted, 1006, 16)
    assert snap['reported_position'] == 25 and snap['report_received_at'] == 1001
    assert not snap['usable']
    for record in (snap['observation'], snap['motor_contact']):
        assert record['commissioned_epoch'] is None
        assert record['source_identity_sha256'] is None


@pytest.mark.parametrize('field,value', [('commissioned_epoch', EPOCH_B),
    ('source_identity_sha256', '0' * 64)])
def test_history_digest_or_epoch_mismatch_is_rejected_transactionally(field, value):
    state = commissioned()
    accept(state)
    history = state.export_history()
    history['observation'][field] = value
    restored = State()
    assert not restored.import_history(history)
    assert restored.position is None


@pytest.mark.parametrize('epoch', [True, 0, 'not-a-uuid', {}, []])
def test_invalid_commissioning_epoch_never_rebinds_existing_session(epoch):
    state = commissioned()
    accept(state)
    before = snapshot(state)
    with pytest.raises(ValueError):
        state.begin_session(session_id='adapter-b', source_generation=2, bridge_id=BRIDGE,
            device_id=MAC, device_type=TYPE, now=1005, monotonic_now=15, commissioned_epoch=epoch)
    assert snapshot(state) == before
