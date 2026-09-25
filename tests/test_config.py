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
