from dataclasses import replace
from types import SimpleNamespace
from dooya_openhab.config import Config,Shade
from dooya_openhab.service import Service

class FakeMQTT:
    def __init__(self): self.messages=[]
    def will_set(self,*a,**kw): pass
    def reconnect_delay_set(self,*a): pass
    def subscribe(self,*a,**kw): pass
    def publish(self,topic,payload,**kw):
        self.messages.append((topic,payload,kw)); return SimpleNamespace(rc=0)
class Bridge:
    bridge_mac='bridge'
    def __init__(self): self.calls=[]
    def command(self,*a):
        self.calls.append(a); return {'msgType':'WriteDeviceAck','data':{'currentPosition':100}}
def make():
    c=Config('127.0.0.1','0.0.0.0','x'*16,'localhost',1883,None,None,False,'test',True,600,1800,(Shade('sky','0000000000000001',percentage=True),))
    s=Service(c,Bridge(),FakeMQTT()); s.connected.set(); s.ready=True; return s
def msg(retain=False,payload=b'UP'):
    return SimpleNamespace(topic='test/sky/command',payload=payload,retain=retain)
def test_rejection():
    s=make(); s.on_message(None,None,msg(True)); s.on_message(None,None,msg(payload=b'101'))
    assert s.commands.pop() is None
    s.config=replace(s.config,allow_commands=False); s.on_message(None,None,msg())
    assert s.commands.pop() is None

def test_reconnect_no_replay():
    s=make(); s.on_message(None,None,msg()); s.on_disconnect(None,None,None,None,None)
    assert s.commands.pop() is None
    s.on_connect(s.client,None,None,0,None)
    assert not s.ready and s.reset.is_set()

def test_percentage_and_ack():
    s=make(); s.execute('sky',0); assert not s.connector.calls
    s.states['sky'].cache={'wirelessMode':1}; s.execute('sky',0)
    assert s.connector.calls[-1][1]==0 and s.states['sky'].position is None
    assert 'acknowledged' in s.client.messages[-1][1]

def test_bridge_not_motor():
    s=make(); s.bridge_seen=5; s.publish_states(6)
    v={k:p for k,p,_ in s.client.messages}
    assert v['test/bridge/availability']=='online' and v['test/sky/availability']=='offline'
    assert v['test/sky/position']=='UNDEF'

def test_uncertain_not_retried():
    s=make()
    def timeout(*a): raise TimeoutError()
    s.connector.command=timeout; s.execute('sky','UP')
    assert not s.ready and 'uncertain' in s.client.messages[-1][1]

def test_command_error_discards_other_queued_moves():
    s=make(); s.commands.put('sky','DOWN')
    def timeout(*a): raise TimeoutError()
    s.connector.command=timeout; s.execute('sky','UP')
    assert s.commands.pop() is None

def test_stale_position_is_undef_even_when_bridge_alive():
    s=make(); s.bridge_seen=2000
    s.states['sky'].accept({'msgType':'Report','data':{'currentPosition':0,'wirelessMode':1}},1)
    s.publish_states(2000)
    v={k:p for k,p,_ in s.client.messages}
    assert v['test/sky/position']=='UNDEF'

def test_report_from_unknown_device_is_ignored():
    s=make(); s.handle_report({'msgType':'Report','mac':'unknown','data':{'currentPosition':100,'wirelessMode':1}},1)
    assert s.states['sky'].position is None and s.bridge_seen is None
