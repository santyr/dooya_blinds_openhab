import pytest
from dooya_openhab.model import Commands,State,parse_command

@pytest.mark.parametrize('p,v',[(b'0',0),(b'100',100),(b'UP','UP'),(b'STOP','STOP')])
def test_commands(p,v): assert parse_command(p)==v

@pytest.mark.parametrize('p',[b'-1',b'101',b'50.5',b' 0',b'00',b'TOGGLE',b'\xff'])
def test_bad_commands(p):
    with pytest.raises(ValueError): parse_command(p)

def test_cache_not_position():
    s=State(); s.accept({'msgType':'WriteDeviceAck','data':{'currentPosition':100}},1)
    assert s.position is None and s.snapshot(2,10)['stale']

def test_zero_missing_stale():
    s=State(); s.accept({'msgType':'Report','data':{'currentPosition':0,'wirelessMode':1}},1)
    assert s.position==0
    s.accept({'msgType':'Report','data':{'RSSI':-80}},8)
    assert s.position_at==1
    assert not s.snapshot(10,10)['stale']
    assert s.snapshot(12,10)['stale']

def test_unknown_modes():
    for mode in (None,0,4):
        s=State(); s.accept({'msgType':'Report','data':{'currentPosition':10,'wirelessMode':mode}},1)
        assert s.position is None

def test_inversion_raw_and_secrets():
    s=State(True); s.accept({'msgType':'Report','data':{'currentPosition':0,'wirelessMode':1,'batteryLevel':811,'token':'secret'}},1)
    assert s.position==100 and s.report['batteryLevel']==811 and 'token' not in s.report

def test_stop_queue():
    q=Commands(2); assert q.put('a','UP'); assert q.put('b',50)
    assert not q.put('c','DOWN'); assert q.put('b','STOP'); assert not q.put('b',30)
    assert q.pop()==('b','STOP'); q.clear(); assert q.pop() is None
