"""One-shot trusted-owner MQTT publisher. Dry validation is the default.

This module is transport only: it cannot mint a manual origin or decide household
policy. Its input is already signed by the authenticated OpenHAB-backed owner.
Use a separate broker principal restricted to owner topics, never a display or
adapter principal. No reconnect, buffering, retention or movement retry.
"""
from pathlib import Path
import argparse
import json
import os
import stat
import sys
import threading
import time
import tomllib

from .commands import verify_command, verify_policy, CommandError


def private_bytes(path, maximum=32768):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size>maximum or info.st_uid!=os.getuid():
            raise ValueError('Private owner configuration required')
        return stream.read(maximum+1)


def load_profile(path):
    import re
    root=Path(path).resolve().parent
    info=root.stat()
    if stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=os.getuid():
        raise ValueError('Private owner directory required')
    value=tomllib.loads(private_bytes(path).decode('utf-8'))
    def sibling(name):
        if not isinstance(name,str) or not name or (root/name).parent.resolve()!=root:
            raise ValueError('Owner files must share the private directory')
        return root/name
    broker=value.get('mqtt',{})
    if (not isinstance(broker.get('host'),str) or not broker['host']
            or type(broker.get('port',1883)) is not int or not 1<=broker.get('port',1883)<=65535
            or not isinstance(broker.get('username'),str) or not broker['username']
            or type(broker.get('tls',False)) is not bool
            or not re.fullmatch(r'[A-Za-z0-9/_-]{1,128}',broker.get('prefix',''))):
        raise ValueError('Invalid owner broker profile')
    key=private_bytes(sibling(value['owner_key_file']),4096)
    if not 32<=len(key)<=4096:raise ValueError('Owner authentication unavailable')
    password=private_bytes(sibling(broker['password_file']),4096).decode('utf-8').rstrip('\r\n')
    if not password:raise ValueError('Owner broker authentication unavailable')
    return broker,key,password


def validate_input(raw,key,now_ms):
    if len(raw)>32768:raise ValueError('Owner publication too large')
    def pairs(rows):
        result={}
        for k,v in rows:
            if k in result:raise ValueError('Invalid owner publication')
            result[k]=v
        return result
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    if type(value) is not dict or set(value)!={'kind','message'}:raise ValueError('Invalid owner publication')
    kind=value['kind']
    if kind=='command':envelope=verify_command(value['message'],key);created=envelope.created_at_ms
    elif kind=='policy':envelope=verify_policy(value['message'],key);created=envelope.issued_at_ms
    else:raise ValueError('Invalid owner publication')
    if created>now_ms+1000 or envelope.expires_at_ms<=now_ms:raise ValueError('Owner publication expired')
    return kind,json.dumps(value['message'],sort_keys=True,separators=(',',':'),allow_nan=False)


def publish_once(broker,password,kind,payload,*,client_factory=None,timeout=5):
    import paho.mqtt.client as mqtt
    client=client_factory() if client_factory else mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,clean_session=True,reconnect_on_failure=False)
    client.connect_timeout=timeout
    client.max_queued_messages_set(1)
    client.username_pw_set(broker['username'],password)
    if broker.get('tls'):client.tls_set()
    connected=threading.Event();disconnected=threading.Event();accepted=[]
    def on_connect(_c,_u,_f,code,_p):
        accepted.append(code==0);connected.set()
    client.on_connect=on_connect
    client.on_disconnect=lambda *_:disconnected.set()
    try:
        client.connect(broker['host'],broker.get('port',1883),keepalive=15)
        client.loop_start()
        if not connected.wait(timeout) or accepted!=[True]:raise RuntimeError('Owner broker unavailable')
        info=client.publish(broker['prefix']+'/owner/'+kind,payload,qos=1,retain=False)
        if info.rc!=mqtt.MQTT_ERR_SUCCESS:raise RuntimeError('Owner publication uncertain')
        info.wait_for_publish(timeout=timeout)
        if not info.is_published():raise RuntimeError('Owner publication uncertain')
        return {'published':True,'broker_acknowledged':True,'movement_confirmed':False}
    finally:
        client.disconnect();client.loop_stop()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--publish',action='store_true',help='Explicitly publish a signed owner message; no retry')
    args=parser.parse_args(argv)
    try:
        broker,key,password=load_profile(args.config)
        raw=sys.stdin.buffer.read(32769)
        kind,payload=validate_input(raw,key,int(time.time()*1000))
        result=publish_once(broker,password,kind,payload) if args.publish else {'validated':True,'published':False}
        print(json.dumps(result,allow_nan=False));return 0
    except (OSError,ValueError,TypeError,KeyError,CommandError,RuntimeError):
        print('Owner publication unavailable or uncertain; do not retry movement automatically',file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
