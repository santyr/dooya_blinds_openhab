"""Exercise the production worker loop with synthetic time and UDP peers."""
from dataclasses import replace
from types import SimpleNamespace
import json
import socket
import threading
import time
import pytest

from dooya_openhab.config import Shade
from dooya_openhab.protocol import Connector, MessageIDs, UDPTransport
from dooya_openhab.service import Service
from test_service import FakeMQTT, Transport, make, policy, command, BRIDGE, MAC


@pytest.mark.parametrize('poll_seconds',[30,60,600])
def test_actual_run_loop_preserves_26_device_tail_with_timeouts_and_flood(tmp_path,poll_seconds):
    service,wall,transport=make(tmp_path)
    shades=tuple(Shade(f'shade_{index:02}',f'{index:016X}') for index in range(1,27))
    config=replace(service.config,shades=shades,poll_seconds=poll_seconds)
    clock=[0.]; attempts=[]
    class SlowTransport(Transport):
        def request(self,packet,**kwargs):
            if packet['msgType']=='GetDeviceList':
                return {'msgType':'GetDeviceListAck','mac':BRIDGE,'token':'0'*16,
                    'data':[{'mac':shade.mac,'deviceType':'10000000'} for shade in shades]}
            if packet['msgType']=='ReadDevice':
                return {'msgType':'ReadDeviceAck','msgID':packet['msgID'],'mac':packet['mac'],'deviceType':'10000000',
                    'data':{'type':1,'wirelessMode':1}}
            attempts.append(packet['mac']); clock[0]+=2
            if len(attempts)>=26: worker.stop.set()
            raise TimeoutError()
    connector=Connector(SlowTransport(),config.key,bridge_mac=BRIDGE)
    connector.discover()
    for shade in shades: connector.device_request(shade.mac)
    class Flood:
        drained=0
        def __init__(self,*args): pass
        def receive(self):
            self.drained+=1; clock[0]+=.0001
            return {}
        def close(self): pass
    def wait(seconds):
        clock[0]+=seconds
        if clock[0]>200: worker.stop.set()
    worker=Service(config,connector,FakeMQTT(),wall_clock=lambda:1000+clock[0],monotonic_clock=lambda:clock[0],reports_factory=Flood,wait=wait)
    worker.connected.set(); worker.run()
    assert attempts[:26]==[shade.mac for shade in shades]
    assert len(set(attempts))==26 and clock[0]<90
    # Full coverage may exceed the requested cycle, and that degradation is explicit.
    health=[json.loads(payload) for topic,payload,_ in worker.client.messages if topic=='test/service/state']
    assert any(record['poll_coverage_degraded'] for record in health)==(poll_seconds<78)


def test_loopback_stop_cancels_lost_ack_wait_with_measured_bound(tmp_path):
    service,clock,fake=make(tmp_path)
    accepted=threading.Event(); stopped=threading.Event(); packets=[]; failures=[]
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1',0)); server.settimeout(3)
        def bridge():
            try:
                for _ in range(4):
                    raw,address=server.recvfrom(32768); packet=json.loads(raw); packets.append(packet)
                    if packet['msgType']=='GetDeviceList':
                        reply={'msgType':'GetDeviceListAck','msgID':packet['msgID'],'mac':BRIDGE,'token':'0'*16,'data':[{'mac':MAC,'deviceType':'10000000'}]}
                    else:
                        operation=packet.get('data',{}).get('operation')
                        if operation==1:
                            accepted.set(); continue
                        if operation==2: stopped.set()
                        reply={'msgType':packet['msgType']+'Ack','msgID':packet['msgID'],'mac':MAC,'deviceType':'10000000','data':{'type':1,'wirelessMode':1}}
                    server.sendto(json.dumps(reply).encode(),address)
            except Exception as error: failures.append(type(error).__name__)
        thread=threading.Thread(target=bridge); thread.start()
        service.connector=Connector(UDPTransport('127.0.0.1',server.getsockname()[1],timeout=2),service.config.key,bridge_mac=BRIDGE,
            ids=MessageIDs(state_path=service.config.message_id_state_file))
        service._session_bound=False; service.discover_once(); service.poll_once('sky'); policy(service,clock)
        request=command(service,clock,'UP'); worker=threading.Thread(target=lambda:service.execute(service.commands.pop())); worker.start()
        assert accepted.wait(1)
        start=time.monotonic(); stop_request=command(service,clock,'STOP')
        worker.join(1); assert not worker.is_alive()
        service.execute(service.commands.pop())
        assert stopped.wait(.5)
        latency=time.monotonic()-start
        thread.join(1)
    assert not failures and not thread.is_alive()
    assert latency<.25
    assert service.commands.receipt(request)['status']=='uncertain'
    assert service.commands.receipt(stop_request)['status']=='acknowledged'
    assert [packet.get('data',{}).get('operation') for packet in packets]==[None,None,1,2]
