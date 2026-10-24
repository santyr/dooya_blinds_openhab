import pytest
from dooya_openhab.config import load

def config(tmp_path,extra=''):
    (tmp_path/'bridge.key').write_text('74ae544c-d16e-4c\n')
    p=tmp_path/'config.toml'
    p.write_text('[bridge]\nhost="192.0.2.10"\nkey_file="bridge.key"\n[mqtt]\nhost="localhost"\n'+extra)
    return str(p)
def test_defaults(tmp_path):
    c=load(config(tmp_path)); assert not c.allow_commands and c.key=='74ae544c-d16e-4c'
def test_duplicate(tmp_path):
    s='\n[[shades]]\nid="sky"\nmac="0000000000000001"\n'
    with pytest.raises(ValueError): load(config(tmp_path,s+s))
def test_reserved(tmp_path):
    with pytest.raises(ValueError): load(config(tmp_path,'\n[[shades]]\nid="service"\nmac="0000000000000001"\n'))


def test_configured_identity_is_canonical(tmp_path):
    c = load(config(tmp_path, '\n[[shades]]\nid="sky"\nmac="aabbccddeeff0001"\n'))
    assert c.shades[0].mac == 'AABBCCDDEEFF0001'


def test_duplicate_case_identity_rejected(tmp_path):
    value = '\n[[shades]]\nid="one"\nmac="aabbccddeeff0001"\n[[shades]]\nid="two"\nmac="AABBCCDDEEFF0001"\n'
    with pytest.raises(ValueError): load(config(tmp_path, value))


def test_optional_bridge_pin_and_durable_identity_path(tmp_path):
    path = config(tmp_path)
    from pathlib import Path
    p = Path(path)
    p.write_text(p.read_text().replace('[mqtt]', 'mac="aabbccddeeff"\nmessage_id_state_file="ids"\n[mqtt]'))
    c = load(path)
    assert c.bridge_mac == 'AABBCCDDEEFF'
    assert c.message_id_state_file == str(tmp_path/'ids')


@pytest.mark.parametrize('field,value', [('host','false'), ('mac','false'), ('message_id_state_file','false')])
def test_bridge_configuration_exact_types_are_sanitized(tmp_path, field, value):
    path = config(tmp_path)
    from pathlib import Path
    p = Path(path)
    text = p.read_text()
    if field == 'host': text = text.replace('host="192.0.2.10"', 'host='+value)
    else: text = text.replace('[mqtt]', field+'='+value+'\n[mqtt]')
    p.write_text(text)
    with pytest.raises(ValueError): load(path)


def test_private_owner_key_and_commissioned_inventory(tmp_path):
    key = tmp_path/'owner.key'; key.write_bytes(b'o'*32); key.chmod(0o600)
    (tmp_path/'bridge.key').write_text('74ae544c-d16e-4c')
    path = tmp_path/'commissioned.toml'
    path.write_text('''owner_key_file="owner.key"
receipt_journal_file="journal.json"
[bridge]
host="127.0.0.1"
key_file="bridge.key"
mac="aabbccddeeff"
message_id_state_file="ids"
[mqtt]
host="localhost"
[[shades]]
id="sky"
mac="0000000000000001"
zone="kitchen"
slot=1
commissioned_epoch="11111111-1111-4111-8111-111111111111"
model="synthetic-fixture"
firmware="synthetic-v1"
commissioning_ref="synthetic-test-receipt"
supported_operations=["UP","DOWN","STOP"]
''')
    c=load(str(path))
    assert c.owner_key==b'o'*32 and c.receipt_journal_file==str(tmp_path/'journal.json')
    assert c.prefix=='earthship/shades/v2'
    assert c.shades[0].supported_operations==('UP','DOWN','STOP')
    assert c.shades[0].slot==1 and c.shades[0].zone=='kitchen'


def test_owner_key_rejects_public_or_short_file(tmp_path):
    from pathlib import Path
    path = Path(config(tmp_path))
    key=tmp_path/'owner.key'; key.write_bytes(b'private'); key.chmod(0o644)
    path.write_text('owner_key_file="owner.key"\n'+path.read_text())
    with pytest.raises(ValueError): load(str(path))


def test_duplicate_logical_slot_and_inconsistent_zone_refused(tmp_path):
    value='\n[[shades]]\nid="one"\nmac="0000000000000001"\nslot=1\nzone="kitchen"\n[[shades]]\nid="two"\nmac="0000000000000002"\nslot=1\nzone="kitchen"\n'
    with pytest.raises(ValueError): load(config(tmp_path,value))


def test_discovery_profile_is_explicit_and_requires_pin(tmp_path):
    from pathlib import Path
    p=Path(config(tmp_path)); original=p.read_text()
    assert load(str(p)).discovery_correlation=='echoed_id'
    p.write_text(original.replace('[mqtt]','discovery_correlation="pinned_discovery_without_id"\n[mqtt]'))
    with pytest.raises(ValueError): load(str(p))
    p.write_text(p.read_text().replace('[mqtt]','mac="aabbccddeeff"\n[mqtt]'))
    assert load(str(p)).discovery_correlation=='pinned_discovery_without_id'
    p.write_text(original.replace('[mqtt]','discovery_correlation="automatic"\n[mqtt]'))
    with pytest.raises(ValueError): load(str(p))
