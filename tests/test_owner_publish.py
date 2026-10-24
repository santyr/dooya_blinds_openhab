"""Owner transport tests use synthetic credentials and never contact a broker."""
from pathlib import Path
import json
from types import SimpleNamespace
import pytest
from dooya_openhab.owner_publish import private_bytes,load_profile,validate_input,publish_once
from dooya_openhab.commands import signed_command
from test_command_contract import command_dict,KEY


def test_private_owner_files_and_dry_validation(tmp_path):
    key=tmp_path/'key';key.write_bytes(KEY);key.chmod(0o600)
    password=tmp_path/'password';password.write_text('synthetic-only');password.chmod(0o600)
    profile=tmp_path/'owner.toml';profile.write_text('owner_key_file="key"\n[mqtt]\nhost="127.0.0.1"\nprefix="fixture/shades/v2"\nusername="fixture-owner"\npassword_file="password"\n');profile.chmod(0o600)
    broker,actual,secret=load_profile(profile);assert actual==KEY and secret=='synthetic-only'
    profile.chmod(0o644)
    with pytest.raises(ValueError):private_bytes(profile)
    link=tmp_path/'link';link.symlink_to(key)
    with pytest.raises(OSError):private_bytes(link)


def test_invalid_or_expired_signed_input_never_becomes_a_publish():
    message=command_dict();raw=json.dumps({'kind':'command','message':signed_command(message,KEY)}).encode()
    assert validate_input(raw,KEY,message['created_at_ms'])[0]=='command'
    with pytest.raises(ValueError):validate_input(raw,KEY,message['expires_at_ms'])
    with pytest.raises(ValueError):validate_input(b'{"kind":"command","kind":"policy"}',KEY,0)
    with pytest.raises(ValueError):validate_input(raw,b'X'*32,message['created_at_ms'])


def test_one_shot_transport_does_not_retain_retry_or_claim_movement():
    calls=[]
    class Client:
        def max_queued_messages_set(self,n):assert n==1
        def username_pw_set(self,*args):pass
        def connect(self,*args,**kwargs):self.on_connect(self,None,None,0,None)
        def loop_start(self):pass
        def publish(self,*args,**kwargs):calls.append((args,kwargs));return SimpleNamespace(rc=0,wait_for_publish=lambda **_:None,is_published=lambda:True)
        def disconnect(self):calls.append('disconnect')
        def loop_stop(self):calls.append('stop')
    result=publish_once({'host':'127.0.0.1','username':'fixture','prefix':'fixture'},'synthetic','command','{}',client_factory=Client)
    assert result=={'published':True,'broker_acknowledged':True,'movement_confirmed':False}
    assert calls[0][0][0]=='fixture/owner/command' and calls[0][1]=={'qos':1,'retain':False}


def test_owner_publisher_secrets_cannot_escape_private_profile_directory(tmp_path):
    root=tmp_path/'private';root.mkdir(mode=0o700)
    key=tmp_path/'exposed.bin';key.write_bytes(KEY);key.chmod(0o600)
    profile=root/'publisher.toml'
    profile.write_text('owner_key_file="../exposed.bin"\n[mqtt]\nhost="127.0.0.1"\nprefix="fixture"\nusername="fixture"\npassword_file="password"\n');profile.chmod(0o600)
    with pytest.raises(ValueError,match='private directory'):load_profile(profile)
    root.chmod(0o755)
    with pytest.raises(ValueError,match='Private owner directory'):load_profile(profile)
