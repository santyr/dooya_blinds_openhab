"""Public synthetic contract paired with Earthship UI; no installation key."""
import json
from pathlib import Path
from dooya_openhab.commands import signed_command,signed_policy,verify_command,verify_policy


def test_shared_owner_wire_vector_matches_actual_adapter():
    fixture=json.loads((Path(__file__).parent/'fixtures/owner-wire-v1.json').read_text())
    assert fixture['synthetic'] is True
    key=fixture['key'].encode('ascii')
    assert signed_command(fixture['command'],key)==fixture['signed_command']
    assert signed_policy(fixture['policy'],key)==fixture['signed_policy']
    assert verify_command(fixture['signed_command'],key).shade_id=='kitchen_01'
    assert verify_policy(fixture['signed_policy'],key).generation==fixture['policy']['generation']
