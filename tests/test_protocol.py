import json
import socket
import threading
import pytest
from dooya_openhab.protocol import Connector, MessageIDs, ProtocolError, UDPTransport, access_token, decode
KEY = '74ae544c-d16e-4c'
TOKEN = '37412C478E0FBEAB'
MAC = '0000000000000001'

def test_published_aes_vector():
    assert access_token(KEY, TOKEN) == '8570A96BC18ADB21D1FC155B24ECFD73'

@pytest.mark.parametrize('k,t', [('',TOKEN),(KEY,'short'),('é'*16,TOKEN)])
def test_invalid_keys(k,t):
    with pytest.raises(ProtocolError): access_token(k,t)

def test_ids_increase():
    ids=MessageIDs()
    result=[ids.next() for _ in range(100)]
    assert result == sorted(set(result))

@pytest.mark.parametrize('p',[b'[]',b'null',b'bad',b'{"msgType":4}'])
def test_invalid_packets(p):
    with pytest.raises(ProtocolError): decode(p)

class FakeTransport:
    def __init__(self): self.sent=[]
    def request(self,msg):
        self.sent.append(msg)
        if msg['msgType']=='GetDeviceList':
            return {'msgType':'GetDeviceListAck','mac':'aabbccddeeff','token':TOKEN,'data':[{'mac':MAC,'deviceType':'10000000'}]}
        return {'msgType':msg['msgType']+'Ack','mac':msg['mac'],'deviceType':msg['deviceType'],'data':{'currentPosition':0,'type':1,'wirelessMode':1}}

def test_commands():
    t=FakeTransport(); b=Connector(t,KEY); b.discover(); b.device_request(MAC)
    b.command(MAC,0)
    assert t.sent[-1]['data']=={'targetPosition':0}
    assert t.sent[-1]['AccessToken']==access_token(KEY,TOKEN)
    b.command(MAC,0,True)
    assert t.sent[-1]['data']=={'targetPosition':100}
    b.command(MAC,'UP',True)
    assert t.sent[-1]['data']=={'operation':0}
    b.command(MAC,'STOP',True)
    assert t.sent[-1]['data']=={'operation':2}
    with pytest.raises(ProtocolError): b.command('other','UP')

def test_token_source():
    b=Connector(FakeTransport(),KEY); b.discover()
    b.observe({'msgType':'Heartbeat','mac':'other','token':'0'*16})
    assert b.token==TOKEN

def test_udp_correlation():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1',0))
        def reply():
            raw,addr=server.recvfrom(65535); req=json.loads(raw)
            base={'msgType':'ReadDeviceAck','msgID':req['msgID'],'mac':MAC,'data':{}}
            for packet in [b'bad',json.dumps({**base,'msgID':'bad'}).encode(),json.dumps({**base,'mac':'bad'}).encode(),json.dumps(base).encode()]: server.sendto(packet,addr)
        worker=threading.Thread(target=reply); worker.start()
        response=UDPTransport('127.0.0.1',server.getsockname()[1],.5).request({'msgType':'ReadDevice','msgID':'123','mac':MAC})
        worker.join(1)
        assert response['mac']==MAC

def test_udp_timeout():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1',0))
        with pytest.raises(TimeoutError): UDPTransport('127.0.0.1',server.getsockname()[1],.02).request({'msgType':'GetDeviceList','msgID':'1'})

def test_remote_error_is_sanitized():
    t=FakeTransport(); b=Connector(t,KEY); b.discover(); b.device_request(MAC)
    t.request=lambda message: {'actionResult':'private credential material'}
    with pytest.raises(ProtocolError) as error: b.command(MAC,'UP')
    assert 'private' not in str(error.value)

def test_bridge_heartbeat_rotates_token():
    b=Connector(FakeTransport(),KEY); b.discover()
    b.observe({'msgType':'Heartbeat','mac':'aabbccddeeff','token':'0'*16})
    assert b.token=='0'*16

# DD7066 pre-deployment regressions exercise the actual protocol module.
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

@pytest.mark.parametrize('stamp,want', [
    ('2026-10-24T12:59:59.999000+00:00', '20261024130000000'),
    ('2026-10-31T23:59:59.999000+00:00', '20261101000000000'),
    ('2026-12-31T23:59:59.999000+00:00', '20270101000000000'),
])
def test_ids_calendar_rollover(monkeypatch, stamp, want):
    import dooya_openhab.protocol as protocol
    frozen = dt.datetime.fromisoformat(stamp)
    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None): return frozen
    monkeypatch.setattr(protocol.dt, 'datetime', Frozen)
    ids = MessageIDs()
    ids.next()
    assert ids.next() == want


def test_ids_rollback_and_concurrency(monkeypatch):
    import dooya_openhab.protocol as protocol
    wall = [dt.datetime(2026, 12, 31, 23, 59, 59, 999000, dt.timezone.utc)]
    class Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None): return wall[0]
    monkeypatch.setattr(protocol.dt, 'datetime', Frozen)
    ids = MessageIDs()
    first = ids.next()
    wall[0] = wall[0] - dt.timedelta(days=1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: ids.next(), range(200)))
    assert len(set(values + [first])) == 201
    for value in values: dt.datetime.strptime(value, '%Y%m%d%H%M%S%f')
    assert min(values) > first

@pytest.mark.parametrize('raw', [
    b'{"msgType":"Report","msgType":"Heartbeat"}',
    b'{"msgType":"Report","data":{"RSSI":NaN}}',
    b'{"msgType":"Report","data":{"RSSI":Infinity}}',
    b'{"msgType":"Report","data":{"wirelessMode":true}}',
    b'{"msgType":"Report","data":{"currentPosition":true}}',
    b'{"msgType":"Report","data":{"operation":false}}',
    b'{"msgType":"Report","data":{"unknown":{"duplicate":1,"duplicate":2}}}',
    b'{"msgType":"Report","data":' + b'['*40 + b'0' + b']'*40 + b'}',
    b'{"msgType":"Report","data":{"padding":"' + b'x'*70000 + b'"}}',
])
def test_strict_bounded_json(raw):
    with pytest.raises(ProtocolError) as error: decode(raw)
    assert 'padding' not in str(error.value)


def test_decode_canonicalizes_identities_preserves_unknown_fields_and_zero():
    packet = decode(b'{"msgType":"Report","mac":"aabbccddeeff","data":{"wirelessMode":1,"currentPosition":0,"newField":{"raw":0}}}')
    assert packet['mac'] == 'AABBCCDDEEFF'
    assert packet['data']['currentPosition'] == 0
    assert packet['data']['newField'] == {'raw': 0}
    assert 'batteryLevel' not in packet['data']


def test_discovery_invalid_inventory_does_not_rotate_token_or_identity():
    transport = FakeTransport(); bridge = Connector(transport, KEY); bridge.discover()
    previous = (bridge.bridge_mac, bridge.token, dict(bridge.devices))
    transport.request = lambda packet: {'msgType':'GetDeviceListAck','mac':'aabbccddeeff','token':'0'*16,'data':[{'mac':MAC,'deviceType':'10000000'}, {'mac':False,'deviceType':'10000000'}]}
    with pytest.raises(ProtocolError): bridge.discover()
    assert (bridge.bridge_mac, bridge.token, bridge.devices) == previous


def test_foreign_discovery_does_not_replace_bridge():
    transport = FakeTransport(); bridge = Connector(transport, KEY); bridge.discover()
    transport.request = lambda packet: {'msgType':'GetDeviceListAck','mac':'111111111111','token':'0'*16,'data':[]}
    with pytest.raises(ProtocolError): bridge.discover()
    assert bridge.token == TOKEN
    assert bridge.bridge_mac.upper() == 'AABBCCDDEEFF'

@pytest.mark.parametrize('device_type', ['10000001', '10009999', '22000000'])
def test_unknown_and_dual_axis_inventory_diagnostic_only(device_type):
    transport = FakeTransport()
    transport.request = lambda packet: {'msgType':'GetDeviceListAck','mac':'aabbccddeeff','token':TOKEN,'data':[{'mac':MAC,'deviceType':device_type}]}
    bridge = Connector(transport, KEY)
    assert bridge.discover() == [{'mac':MAC,'deviceType':device_type}]
    for data in ({'operation':5}, {'operation':1}, {'targetPosition':25}):
        with pytest.raises(ProtocolError): bridge.device_request(MAC, data)


def test_discovery_duplicate_case_identity_rejected():
    transport = FakeTransport()
    transport.request = lambda packet: {'msgType':'GetDeviceListAck','mac':'aabbccddeeff','token':TOKEN,'data':[{'mac':'aabbccddeeff0001','deviceType':'10000000'}, {'mac':'AABBCCDDEEFF0001','deviceType':'10000000'}]}
    bridge = Connector(transport, KEY)
    with pytest.raises(ProtocolError): bridge.discover()
    assert bridge.token is None and bridge.devices == {}


def test_unbound_heartbeat_cannot_install_token():
    bridge = Connector(FakeTransport(), KEY)
    bridge.observe({'msgType':'Heartbeat','mac':'aabbccddeeff','token':TOKEN})
    assert bridge.token is None


def test_durable_ids_cross_process_restart_and_file_permissions(tmp_path):
    import subprocess
    import sys
    path = tmp_path / 'message-id'
    script = '''import datetime as dt,sys
from dooya_openhab.protocol import MessageIDs
ids=MessageIDs(clock=lambda:dt.datetime(2026,12,31,23,59,59,999000,dt.timezone.utc), state_path=sys.argv[1])
print(ids.next())'''
    first = subprocess.check_output([sys.executable, '-c', script, str(path)], text=True).strip()
    second = subprocess.check_output([sys.executable, '-c', script, str(path)], text=True).strip()
    assert (first, second) == ('20261231235959999', '20270101000000000')
    assert path.stat().st_mode & 0o777 == 0o600


def test_durable_ids_shared_by_processes_do_not_reuse(tmp_path):
    import subprocess
    import sys
    script = 'from dooya_openhab.protocol import MessageIDs; import sys,datetime as dt; print(MessageIDs(clock=lambda:dt.datetime(2026,12,31,23,59,59,999000,dt.timezone.utc),state_path=sys.argv[1]).next())'
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: subprocess.check_output([sys.executable, '-c', script, str(tmp_path/'ids')], text=True).strip(), range(8)))
    assert len(set(values)) == 8


def test_corrupt_durable_identity_fails_closed_without_secret_error(tmp_path):
    path = tmp_path/'ids'
    path.write_text('privatecredential')
    path.chmod(0o600)
    with pytest.raises(ProtocolError) as error: MessageIDs(state_path=path).next()
    assert 'privatecredential' not in str(error.value)


def test_udp_final_guard_and_cancel_prevent_packet(monkeypatch):
    import dooya_openhab.protocol as protocol
    from contextlib import contextmanager
    events = []
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def sendto(self, *args): events.append('sent')
        def settimeout(self, value): pass
    monkeypatch.setattr(protocol.socket, 'socket', lambda *args: Socket())
    @contextmanager
    def guard():
        events.append('enter')
        yield
        events.append('exit')
    transport = UDPTransport('127.0.0.1')
    with pytest.raises(ProtocolError):
        transport.request({'msgType':'GetDeviceList','msgID':'1'}, send_guard=guard, before_send=lambda: False)
    assert events == ['enter']
    events.clear()
    with pytest.raises(ProtocolError):
        transport.request({'msgType':'GetDeviceList','msgID':'2'}, cancelled=lambda: True)
    assert events == []


def test_udp_wrong_packets_cannot_extend_monotonic_deadline(monkeypatch):
    import dooya_openhab.protocol as protocol
    clock = [0.0]
    sent = []
    base = {'msgType':'ReadDeviceAck','msgID':'123','mac':MAC, 'deviceType':'10000000'}
    packets = [
        (base, ('127.0.0.2', 32100)),
        (base, ('127.0.0.1', 32101)),
        ({**base, 'msgID':'wrong'}, ('127.0.0.1', 32100)),
        ({**base, 'msgType':'WriteDeviceAck'}, ('127.0.0.1', 32100)),
        ({**base, 'mac':'0000000000000002'}, ('127.0.0.1', 32100)),
        ({**base, 'deviceType':'10000001'}, ('127.0.0.1', 32100)),
    ]
    class Socket:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def settimeout(self, value): assert 0 < value <= .05
        def sendto(self, raw, addr): sent.append(raw)
        def recvfrom(self, size):
            index = len(sent) - 1
            clock[0] += .01
            packet, sender = packets[index % len(packets)]
            sent.append(None)
            return json.dumps(packet).encode(), sender
    monkeypatch.setattr(protocol.socket, 'socket', lambda *args: Socket())
    with pytest.raises(TimeoutError):
        UDPTransport('127.0.0.1', timeout=.1, clock=lambda: clock[0]).request({'msgType':'ReadDevice','msgID':'123','mac':MAC,'deviceType':'10000000'})
    assert .1 <= clock[0] <= .111


def test_connector_rejects_unsupported_programming_operations():
    bridge = Connector(FakeTransport(), KEY); bridge.discover()
    for data in ({'operation':3}, {'operation':True}, {'targetPosition':False}, {'operation':1,'programming':1}):
        with pytest.raises(ProtocolError): bridge.device_request(MAC, data)

@pytest.mark.parametrize('field,value', [
    ('wirelessMode', 6), ('currentState', True), ('currentState', 99),
    ('voltageMode', -1), ('chargingState', 2), ('operation', 3),
    ('RSSI', 'signal'), ('batteryLevel', False),
])
def test_documented_scalar_domains_are_exact(field, value):
    with pytest.raises(ProtocolError):
        decode(json.dumps({'msgType':'Report','data':{field:value}}).encode())


def test_udp_send_occurs_inside_final_guard(monkeypatch):
    import dooya_openhab.protocol as protocol
    from contextlib import contextmanager
    events = []
    @contextmanager
    def guard():
        events.append('locked')
        try: yield
        finally: events.append('unlocked')
    class Socket:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def settimeout(self, value): pass
        def sendto(self, raw, addr): events.append('sent')
        def recvfrom(self, size):
            assert events == ['locked','checked','sent','unlocked']
            return b'{"msgType":"GetDeviceListAck","msgID":"1"}', ('127.0.0.1',32100)
    monkeypatch.setattr(protocol.socket,'socket',lambda *args:Socket())
    UDPTransport('127.0.0.1').request({'msgType':'GetDeviceList','msgID':'1'}, send_guard=guard, before_send=lambda:events.append('checked'))
    assert events == ['locked','checked','sent','unlocked']


def test_pinned_bridge_refuses_first_foreign_discovery():
    bridge = Connector(FakeTransport(), KEY, bridge_mac='111111111111')
    with pytest.raises(ProtocolError): bridge.discover()
    assert bridge.token is None and bridge.devices == {}


def test_missing_token_cannot_reuse_prior_discovery_authentication():
    transport = FakeTransport(); bridge = Connector(transport, KEY); bridge.discover()
    transport.request = lambda packet: {'msgType':'GetDeviceListAck','mac':'aabbccddeeff','data':[]}
    with pytest.raises(ProtocolError): bridge.discover()
    assert bridge.token == TOKEN and MAC in bridge.devices


def test_utf8_key_byte_length_is_exact_without_character_restriction():
    result = access_token('é'*8, TOKEN)
    assert len(result) == 32 and result == result.upper()
    assert result != access_token(KEY, TOKEN)

@pytest.mark.parametrize('profile', [
    {'type':1,'wirelessMode':0}, {'type':4,'wirelessMode':2},
    {'type':9,'wirelessMode':1}, {'type':2,'wirelessMode':1},
    {'type':4,'wirelessMode':1,'currentPosition_T':25},
    {'type':4,'wirelessMode':1,'operation_B':1},
    {'type':4}, {'wirelessMode':1},
])
def test_unqualified_radio_profiles_never_authorize_write(profile):
    transport = FakeTransport(); bridge = Connector(transport, KEY); bridge.discover()
    bridge.observe({'msgType':'Report','mac':MAC,'deviceType':'10000000','data':profile})
    for value in ({'operation':5}, {'operation':1}, {'targetPosition':25}):
        with pytest.raises(ProtocolError): bridge.device_request(MAC, value)


def test_profile_required_before_first_write_and_revoked_on_contradictory_report():
    transport = FakeTransport(); bridge = Connector(transport, KEY); bridge.discover()
    with pytest.raises(ProtocolError): bridge.command(MAC,'UP')
    bridge.device_request(MAC)
    bridge.command(MAC,'UP')
    bridge.observe({'msgType':'Report','mac':MAC,'deviceType':'10000000','data':{'type':9,'wirelessMode':1}})
    with pytest.raises(ProtocolError): bridge.command(MAC,'STOP')


def test_heartbeat_token_rotation_revokes_radio_profile():
    bridge = Connector(FakeTransport(), KEY); bridge.discover(); bridge.device_request(MAC)
    bridge.observe({'msgType':'Heartbeat','mac':'aabbccddeeff','token':'0'*16})
    with pytest.raises(ProtocolError): bridge.command(MAC,'UP')

@pytest.mark.parametrize('device_type,data', [([], {}), ('10000000', {False:1,'type':1,'wirelessMode':1})])
def test_profile_classifier_refuses_non_scalar_identity_and_invalid_keys(device_type, data):
    from dooya_openhab.protocol import single_axis_profile
    assert not single_axis_profile(device_type, data)


def test_contradictory_discovery_subtype_stays_diagnostic():
    transport = FakeTransport()
    request = transport.request
    transport.request = lambda packet: {'msgType':'GetDeviceListAck','mac':'aabbccddeeff','token':TOKEN,'data':[{'mac':MAC,'deviceType':'10000000','type':9}]} if packet['msgType']=='GetDeviceList' else request(packet)
    bridge = Connector(transport, KEY); bridge.discover(); bridge.device_request(MAC)
    with pytest.raises(ProtocolError): bridge.command(MAC,'UP')


def test_contradictory_write_ack_revokes_old_profile_without_retry():
    transport = FakeTransport(); bridge = Connector(transport, KEY); bridge.discover(); bridge.device_request(MAC)
    transport.request = lambda packet: {'msgType':'WriteDeviceAck','mac':MAC,'deviceType':'10000000','data':{'type':9,'wirelessMode':1}}
    bridge.command(MAC,'UP')
    with pytest.raises(ProtocolError): bridge.command(MAC,'DOWN')


def _public_discovery():
    return {'msgType':'GetDeviceListAck','mac':'AABBCCDDEEFF','deviceType':'02000001',
        'token':TOKEN,'data':[{'mac':MAC,'deviceType':'10000000'}]}


def _loopback_discovery(packets, profile='echoed_id'):
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1',0)); server.settimeout(1)
        errors=[]
        def reply():
            try:
                raw,address=server.recvfrom(32768); request=json.loads(raw)
                for packet in packets:
                    payload=packet(request) if callable(packet) else packet
                    server.sendto(json.dumps(payload).encode(),address)
            except Exception as error: errors.append(type(error).__name__)
        thread=threading.Thread(target=reply,daemon=True); thread.start()
        transport=UDPTransport('127.0.0.1',server.getsockname()[1],timeout=.04,
            discovery_correlation=profile,bridge_mac='AABBCCDDEEFF')
        bridge=Connector(transport,KEY,bridge_mac='AABBCCDDEEFF',discovery_correlation=profile)
        try: return bridge.discover()
        finally:
            thread.join(1)
            assert not errors and not thread.is_alive()


def test_public_no_id_discovery_stays_disabled_by_default():
    with pytest.raises(TimeoutError): _loopback_discovery([_public_discovery()])


def test_optin_pinned_discovery_accepts_only_valid_public_no_id_contract():
    invalid=[{**_public_discovery(),'mac':'111111111111'},
        {**_public_discovery(),'deviceType':'10000000'},
        {**_public_discovery(),'token':'invalid'},
        {**_public_discovery(),'data':[{'mac':False,'deviceType':'10000000'}]},
        {**_public_discovery(),'msgID':'wrong'}]
    assert _loopback_discovery(invalid+[_public_discovery()],'pinned_discovery_without_id')==[{'mac':MAC,'deviceType':'10000000'}]


@pytest.mark.parametrize('identity',[None,'','wrong'])
def test_optin_never_uses_missing_id_exception_for_present_invalid_id(identity):
    with pytest.raises(TimeoutError):
        _loopback_discovery([{**_public_discovery(),'msgID':identity}],'pinned_discovery_without_id')


def test_optin_can_also_accept_normal_exact_echo():
    assert _loopback_discovery([lambda request:{**_public_discovery(),'msgID':request['msgID']}],
        'pinned_discovery_without_id')==[{'mac':MAC,'deviceType':'10000000'}]


def test_optin_requires_explicit_pin_at_construction():
    with pytest.raises((ValueError,ProtocolError)):
        UDPTransport('127.0.0.1',discovery_correlation='pinned_discovery_without_id')
    with pytest.raises((ValueError,ProtocolError)):
        Connector(FakeTransport(),KEY,discovery_correlation='pinned_discovery_without_id')


def test_optin_never_applies_to_read_device_ack():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1',0))
        def reply():
            raw,address=server.recvfrom(32768)
            server.sendto(json.dumps({'msgType':'ReadDeviceAck','mac':MAC,'deviceType':'10000000','data':{}}).encode(),address)
        thread=threading.Thread(target=reply,daemon=True); thread.start()
        transport=UDPTransport('127.0.0.1',server.getsockname()[1],timeout=.04,
            discovery_correlation='pinned_discovery_without_id',bridge_mac='AABBCCDDEEFF')
        with pytest.raises(TimeoutError):
            transport.request({'msgType':'ReadDevice','msgID':'123','mac':MAC,'deviceType':'10000000'})
        thread.join(1); assert not thread.is_alive()


def test_optin_no_id_packet_from_wrong_source_port_is_ignored():
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as foreign:
        server.bind(('127.0.0.1',0)); foreign.bind(('127.0.0.1',0))
        def reply():
            raw,address=server.recvfrom(32768)
            foreign.sendto(json.dumps(_public_discovery()).encode(),address)
        thread=threading.Thread(target=reply,daemon=True); thread.start()
        transport=UDPTransport('127.0.0.1',server.getsockname()[1],timeout=.04,
            discovery_correlation='pinned_discovery_without_id',bridge_mac='AABBCCDDEEFF')
        with pytest.raises(TimeoutError): transport.request({'msgType':'GetDeviceList','msgID':'123'})
        thread.join(1); assert not thread.is_alive()


def test_optin_invalid_no_id_flood_keeps_one_monotonic_deadline(monkeypatch):
    import dooya_openhab.protocol as protocol
    clock=[0.]; sockets=[]
    class Socket:
        def __init__(self,*args): sockets.append(self)
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def settimeout(self,remaining): assert 0 < remaining <= .05
        def sendto(self,*args): pass
        def recvfrom(self,size):
            clock[0]+=.01
            return json.dumps({**_public_discovery(),'token':'bad'}).encode(),('127.0.0.1',32100)
    transport=UDPTransport('127.0.0.1',timeout=.1,clock=lambda:clock[0],socket_factory=Socket,
        discovery_correlation='pinned_discovery_without_id',bridge_mac='AABBCCDDEEFF')
    for _ in range(2):
        start=clock[0]
        with pytest.raises(TimeoutError): transport.request({'msgType':'GetDeviceList','msgID':'123'})
        assert .1 <= clock[0]-start <= .111
    assert len(sockets)==2 and sockets[0] is not sockets[1]


def test_optin_failed_discovery_does_not_change_authentication_inventory():
    transport=FakeTransport()
    transport.request=lambda packet:_public_discovery()
    bridge=Connector(transport,KEY,bridge_mac='AABBCCDDEEFF',discovery_correlation='pinned_discovery_without_id')
    bridge.discover(); previous=(bridge.token,dict(bridge.devices),bridge.bridge_mac)
    transport.request=lambda packet:{**_public_discovery(),'token':'invalid','data':[]}
    with pytest.raises(ProtocolError): bridge.discover()
    assert (bridge.token,bridge.devices,bridge.bridge_mac)==previous
