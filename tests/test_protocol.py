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
        return {'msgType':msg['msgType']+'Ack','data':{'currentPosition':0}}

def test_commands():
    t=FakeTransport(); b=Connector(t,KEY); b.discover()
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
    t=FakeTransport(); b=Connector(t,KEY); b.discover()
    t.request=lambda message: {'actionResult':'private credential material'}
    with pytest.raises(ProtocolError) as error: b.command(MAC,'UP')
    assert 'private' not in str(error.value)

def test_bridge_heartbeat_rotates_token():
    b=Connector(FakeTransport(),KEY); b.discover()
    b.observe({'msgType':'Heartbeat','mac':'aabbccddeeff','token':'0'*16})
    assert b.token=='0'*16
