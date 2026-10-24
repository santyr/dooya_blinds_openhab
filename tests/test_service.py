"""Synthetic owner/bridge integration; no production network or RF traffic."""
from dataclasses import replace
from types import SimpleNamespace
from contextlib import nullcontext
import json
import uuid
import pytest
from dooya_openhab.config import Config,Shade
from dooya_openhab.protocol import Connector, MessageIDs, ProtocolError
from dooya_openhab.service import Service

MAC='0000000000000001'; BRIDGE='AABBCCDDEEFF'; KEY=b'o'*32
OWNER='22222222-2222-4222-8222-222222222222'; GENERATION='33333333-3333-4333-8333-333333333333'
EPOCH='11111111-1111-4111-8111-111111111111'

class FakeMQTT:
    def __init__(self): self.messages=[]; self.mid=0; self.will=None
    def will_set(self,*args,**kwargs): self.will=(args,kwargs)
    def reconnect_delay_set(self,*args): pass
    def subscribe(self,*args,**kwargs): pass
    def publish(self,topic,payload,**kwargs):
        self.mid+=1; self.messages.append((topic,payload,kwargs)); return SimpleNamespace(rc=0,mid=self.mid,wait_for_publish=lambda **kwargs:None)
    def connect_async(self,*args,**kwargs): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass

class Transport:
    def __init__(self): self.sent=[]; self.fail=False
    def request(self,packet,*,before_send=None,cancelled=None,send_guard=None):
        with send_guard() if send_guard else nullcontext():
            if cancelled and cancelled(): raise TimeoutError()
            if before_send and before_send() is False: raise ValueError('refused')
            self.sent.append(packet)
        if self.fail: raise TimeoutError()
        if packet['msgType']=='GetDeviceList':
            return {'msgType':'GetDeviceListAck','mac':BRIDGE,'token':'0'*16,'data':[{'mac':MAC,'deviceType':'10000000'}]}
        return {'msgType':packet['msgType']+'Ack','msgID':packet['msgID'],'mac':MAC,'deviceType':'10000000','data':{'type':1,'wirelessMode':1,'currentPosition':0}}


def make(tmp_path, allow=True):
    clock=[1000.0]
    c=Config('127.0.0.1','0.0.0.0','x'*16,'localhost',1883,None,None,False,'test',allow,600,1800,
        (Shade('sky',MAC,percentage=True,zone='kitchen',slot=1,commissioned_epoch=EPOCH,model='synthetic-fixture',firmware='synthetic-v1',commissioning_ref='synthetic-test',supported_operations=('UP','DOWN','STOP')),),
        bridge_mac=BRIDGE,message_id_state_file=str(tmp_path/'ids'),owner_key=KEY,receipt_journal_file=str(tmp_path/'journal'))
    transport=Transport(); connector=Connector(transport,c.key,bridge_mac=BRIDGE,ids=MessageIDs(state_path=c.message_id_state_file))
    service=Service(c,connector,FakeMQTT(),wall_clock=lambda:clock[0],monotonic_clock=lambda:clock[0])
    service.connected.set(); service.discover_once(); service.poll_once('sky')
    return service,clock,transport


def policy(service,clock,**changes):
    from dooya_openhab.commands import signed_policy
    envelope={'schema':'dooya-owner-policy/v1','generation':GENERATION,'owner_session':OWNER,'adapter_session':service.adapter_session,
        'issued_at_ms':int(clock[0]*1000),'expires_at_ms':int(clock[0]*1000)+30000,
        'zones':{z:{'enabled':True,'authoritative':True} for z in ('kitchen','living','bathroom','bedroom')},
        'override':{'state':'OFF','authoritative':True},'global_released':False,**changes}
    service.on_message(None,None,SimpleNamespace(topic='test/owner/policy',payload=json.dumps(signed_policy(envelope,KEY)).encode(),retain=False))


def command(service,clock,value='UP',**changes):
    from dooya_openhab.commands import signed_command
    envelope={'schema':'dooya-owner-command/v1','request_id':str(uuid.uuid4()),'shade_id':'sky','command':value,'origin':'manual',
        'created_at_ms':int(clock[0]*1000),'expires_at_ms':int(clock[0]*1000)+10000,
        'adapter_session':service.adapter_session,'owner_session':OWNER,'policy_generation':GENERATION,
        'source_context':{'commissioned_epoch':EPOCH,'source_generation':service.source_generation,'bridge_id':BRIDGE,'device_id':MAC},**changes}
    service.on_message(None,None,SimpleNamespace(topic='test/owner/command',payload=json.dumps(signed_command(envelope,KEY)).encode(),retain=False))
    return envelope['request_id']


def test_legacy_unsigned_and_retained_commands_never_queue(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock)
    service.on_message(None,None,SimpleNamespace(topic='test/sky/command',payload=b'UP',retain=False))
    service.on_message(None,None,SimpleNamespace(topic='test/owner/command',payload=b'UP',retain=False))
    assert service.commands.pop() is None


def test_signed_manual_ack_is_not_motor_position(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock)
    request=command(service,clock,0)
    service.execute(service.commands.pop())
    assert transport.sent[-1]['data']=={'targetPosition':0}
    assert service.states['sky'].position is None
    assert service.commands.receipt(request)['status']=='acknowledged'


def test_expiry_after_pop_and_disconnect_never_send(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock)
    command(service,clock); job=service.commands.pop(); previous=len(transport.sent)
    clock[0]+=11; service.execute(job)
    assert len(transport.sent)==previous
    clock[0]+=1; policy(service,clock); command(service,clock); job=service.commands.pop()
    service.on_disconnect(None,None,None,None,None); service.execute(job)
    assert len(transport.sent)==previous


def test_stop_remains_available_when_new_movement_disabled(tmp_path):
    service,clock,transport=make(tmp_path,allow=False); policy(service,clock)
    command(service,clock,'UP'); service.execute(service.commands.pop())
    assert transport.sent[-1]['msgType']=='ReadDevice'
    request=command(service,clock,'STOP'); service.execute(service.commands.pop())
    assert transport.sent[-1]['data']=={'operation':2}
    assert service.commands.receipt(request)['status']=='acknowledged'


def test_timeout_is_uncertain_and_never_replayed_from_journal(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock)
    request=command(service,clock); transport.fail=True; service.execute(service.commands.pop())
    assert service.commands.receipt(request)['status']=='uncertain'
    fresh,_,_=make(tmp_path)
    assert fresh.commands.pop() is None
    assert fresh.commands.receipt(request)['status']=='uncertain'


def test_acknowledged_publish_only_deduplicates_after_broker_ack(tmp_path):
    service,clock,transport=make(tmp_path)
    service.publish('sky/state',{'reported_position':0})
    assert 'sky/state' not in service.sent
    mid=service.client.mid
    service.on_publish(service.client,None,mid,None,None)
    assert 'sky/state' in service.sent
    count=len(service.client.messages); service.publish('sky/state',{'reported_position':0})
    assert len(service.client.messages)==count
    assert service.client.messages[-1][2]['qos']==1


def test_composite_session_state_preserves_historical_report_time(tmp_path):
    service,clock,transport=make(tmp_path)
    report={'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':0}}
    service.handle_report(report,clock[0]); clock[0]+=10; service.publish_states(clock[0])
    messages={topic:payload for topic,payload,_ in service.client.messages}
    state=json.loads(messages['test/sky/state']); bridge=json.loads(messages['test/bridge/state']); process=json.loads(messages['test/service/state'])
    assert state['report_received_at']==1000
    assert state['adapter_session_id']==bridge['adapter_session_id']==process['adapter_session_id']
    assert state['source_generation']==bridge['source_generation']==process['source_generation']
    assert service.client.will[0][0]=='test/service/availability' and service.client.will[0][1]=='offline'


def test_invalid_report_does_not_renew_bridge_or_motor(tmp_path):
    service,clock,transport=make(tmp_path); before=service.bridge_seen
    clock[0]+=10
    service.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':True,'currentPosition':25,'RSSI':float('nan')}},clock[0])
    assert service.bridge_seen==before and service.states['sky'].position is None


def test_health_heartbeat_is_paced_without_restamping_reports(tmp_path):
    service,clock,transport=make(tmp_path)
    service.publish_states()
    initial=sum(topic=='test/service/state' for topic,_,_ in service.client.messages)
    for _ in range(20):
        clock[0]+=.05; service.publish_states()
    assert sum(topic=='test/service/state' for topic,_,_ in service.client.messages)==initial
    clock[0]+=30; service.publish_states()
    assert sum(topic=='test/service/state' for topic,_,_ in service.client.messages)==initial+1


def test_unsupported_or_malformed_ack_cannot_renew_liveness(tmp_path):
    service,clock,transport=make(tmp_path); old=service.bridge_seen; clock[0]+=10
    previous=transport.request
    def malformed(packet,**kwargs):
        response=previous(packet,**kwargs)
        response['data']['RSSI']=float('nan')
        return response
    transport.request=malformed
    with pytest.raises((ValueError,ProtocolError)):
        service.poll_once('sky')
    assert service.bridge_seen==old


def test_disconnect_at_transport_final_boundary_prevents_send(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock); command(service,clock)
    job=service.commands.pop(); previous=len(transport.sent); request=transport.request
    def raced(packet,**kwargs):
        service.on_disconnect(None,None,None,None,None)
        return request(packet,**kwargs)
    transport.request=raced
    service.execute(job)
    assert len(transport.sent)==previous


def test_journal_delay_past_request_ttl_prevents_actual_send(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock); request=command(service,clock)
    job=service.commands.pop(); before=len(transport.sent); persist=service._persist
    def delayed():
        clock[0]+=11
        return persist()
    service._persist=delayed
    service.execute(job)
    assert len(transport.sent)==before
    assert service.commands.receipt(request)['status']!='acknowledged'


def test_missing_post_command_report_eventually_becomes_uncertain(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock); request=command(service,clock)
    service.execute(service.commands.pop()); clock[0]+=121; service.publish_states()
    assert service.commands.receipt(request)['status']=='uncertain'


def test_restart_preserves_original_history_but_never_current_usability(tmp_path):
    service,clock,transport=make(tmp_path)
    service.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':25}},clock[0])
    other,otherclock,_=make(tmp_path)
    snap=other.states['sky'].snapshot(otherclock[0],1800,monotonic_now=otherclock[0])
    assert snap['reported_position']==25 and snap['report_received_at']==1000
    assert not snap['usable']
    other.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':25}},otherclock[0])
    assert not other.states['sky'].snapshot(otherclock[0],1800,monotonic_now=otherclock[0])['usable']


def test_policy_ack_is_bound_to_exact_owner_snapshot_and_commit(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock)
    acknowledgements=[json.loads(payload) for topic,payload,_ in service.client.messages if topic=='test/owner/policy-ack']
    ack=acknowledgements[-1]
    assert ack['schema']=='dooya-policy-ack/v1' and ack['status']=='effective'
    assert ack['adapter_session']==service.adapter_session and ack['source_generation']==service.source_generation
    assert ack['owner_session']==OWNER and ack['policy_generation']==GENERATION
    assert ack['policy_issued_at_ms']==1000000 and ack['effective_at_ms']>=ack['policy_issued_at_ms']
    assert ack['automatic_released'] is False


def test_malformed_private_journal_is_inhibited_without_startup_crash(tmp_path):
    service,clock,transport=make(tmp_path)
    path=tmp_path/'journal'; path.write_text('{"schema_version":2,"history":null}'); path.chmod(0o600)
    other,_,_=make(tmp_path)
    assert not other._journal_ok


def test_configured_path_without_durable_generator_cannot_dispatch(tmp_path):
    from dooya_openhab.protocol import MessageIDs
    service,clock,transport=make(tmp_path); policy(service,clock); request=command(service,clock)
    service.connector.ids=MessageIDs()
    before=len(transport.sent); service.execute(service.commands.pop())
    assert len(transport.sent)==before and service.commands.receipt(request)['status']=='rejected'


def test_report_after_disconnect_cannot_requalify_old_source_session(tmp_path):
    service,clock,transport=make(tmp_path)
    service.on_disconnect(None,None,None,None,None)
    service.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':25}},clock[0])
    snap=service.states['sky'].snapshot(clock[0],1800,monotonic_now=clock[0])
    assert not snap['usable'] and service.states['sky'].position is None


def test_uncertain_write_withdraws_new_movement_but_preserves_operator_stop(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock); request=command(service,clock)
    transport.fail=True; service.execute(service.commands.pop())
    assert not service.ready
    transport.fail=False
    command(service,clock,'DOWN'); before=len(transport.sent); service.execute(service.commands.pop())
    assert len(transport.sent)==before
    stop=command(service,clock,'STOP'); service.execute(service.commands.pop())
    assert transport.sent[-1]['data']=={'operation':2}
    assert service.commands.receipt(stop)['status']=='acknowledged'


def test_pruned_uncertain_active_request_never_crashes_worker(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock); old=command(service,clock)
    transport.fail=True; service.execute(service.commands.pop())
    clock[0]+=11; transport.fail=False
    command(service,clock,'DOWN')
    clock[0]+=120
    service.publish_states()
    assert not service.states['sky'].snapshot(clock[0],1800,monotonic_now=clock[0])['usable']


def test_synthetic_released_auto_dispatch_checks_evidence_before_marking_motion(tmp_path):
    service,clock,transport=make(tmp_path)
    assert service.commands.automatic_released is False
    service.commands.automatic_released=True # isolated fake transport only; production constructor remains closed
    policy(service,clock,global_released=True)
    service.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':0}},clock[0])
    observation=service.states['sky'].snapshot(clock[0],1800,monotonic_now=clock[0])['observation']['id']
    context={'commissioned_epoch':EPOCH,'source_generation':service.source_generation,'bridge_id':BRIDGE,'device_id':MAC,
        'observation_id':observation,'effect_qualified':True,'manual_hold_clear':True,'safety_clear':True}
    request=command(service,clock,'DOWN',origin='automatic',source_context=context)
    service.execute(service.commands.pop())
    assert transport.sent[-1].get('data')=={'operation':0}
    assert service.commands.receipt(request)['status']=='acknowledged'
    assert not service.states['sky'].snapshot(clock[0],1800,monotonic_now=clock[0])['usable']


def test_synthetic_policy_inhibition_after_pop_prevents_final_udp_send(tmp_path):
    service,clock,transport=make(tmp_path); service.commands.automatic_released=True
    policy(service,clock,global_released=True)
    service.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':0}},clock[0])
    observation=service.states['sky'].snapshot(clock[0],1800,monotonic_now=clock[0])['observation']['id']
    context={'commissioned_epoch':EPOCH,'source_generation':service.source_generation,'bridge_id':BRIDGE,'device_id':MAC,
        'observation_id':observation,'effect_qualified':True,'manual_hold_clear':True,'safety_clear':True}
    request=command(service,clock,'DOWN',origin='automatic',source_context=context)
    job=service.commands.pop(); before=len(transport.sent)
    clock[0]+=.001
    policy(service,clock,generation='44444444-4444-4444-8444-444444444444',global_released=True,
        zones={z:{'enabled':False,'authoritative':True} for z in ('kitchen','living','bathroom','bedroom')})
    service.execute(job)
    assert len(transport.sent)==before and service.commands.receipt(request)['status']=='superseded'


def test_synchronous_broker_ack_race_is_reconciled(tmp_path):
    service,clock,transport=make(tmp_path)
    publish=service.client.publish
    def synchronous(topic,payload,**kwargs):
        info=publish(topic,payload,**kwargs)
        service.on_publish(service.client,None,info.mid,0,None)
        return info
    service.client.publish=synchronous
    service.publish('sky/state',{'reported_position':0})
    assert 'sky/state' in service.sent and not service._early_acks
    service.publish('sky/event',{'status':'uncertain'},retain=False)
    assert not service._early_acks


def test_invalid_saved_history_inhibits_commands_and_keeps_original_file(tmp_path):
    service,clock,transport=make(tmp_path)
    path=tmp_path/'journal'; value=json.loads(path.read_text()); value['history']['sky']['reported_position']=False
    original=json.dumps(value); path.write_text(original); path.chmod(0o600)
    fresh,_,_=make(tmp_path)
    assert not fresh._journal_ok and path.read_text()==original


@pytest.mark.parametrize('late_outcome',['ack','timeout'])
def test_delayed_ack_after_reconnect_and_receipt_eviction_is_ignored(tmp_path,late_outcome):
    from dooya_openhab.commands import CommandQueue
    service,clock,transport=make(tmp_path)
    service.commands=CommandQueue({'sky':'kitchen'},adapter_session=service.adapter_session,limit=1,ledger_limit=1)
    policy(service,clock)
    old=command(service,clock,expires_at_ms=1001000)
    job=service.commands.pop(); previous=transport.request; fresh=[]
    bridge_seen=service.bridge_seen
    def reconnect_before_ack(packet,**kwargs):
        response=previous(packet,**kwargs)
        if packet['msgType']=='WriteDevice':
            response['data']['currentPosition']=100
            service.on_disconnect(None,None,None,None,None)
            clock[0]+=1.1
            service.on_connect(service.client,None,None,0,None)
            fresh.append(command(service,clock,'DOWN'))
            if late_outcome=='timeout': raise TimeoutError()
        return response
    transport.request=reconnect_before_ack
    service.execute(job)
    assert service.commands.receipt(old) is None
    assert service.commands.receipt(fresh[0])['status']=='queued'
    assert service.bridge_seen==bridge_seen and service.states['sky'].cache['currentPosition']==0
    assert not service.ready


def test_post_journal_policy_expiry_refuses_automatic_packet(tmp_path):
    service,clock,transport=make(tmp_path); service.commands.automatic_released=True
    policy(service,clock,global_released=True,expires_at_ms=1001000)
    service.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':0}},clock[0])
    observation=service.states['sky'].snapshot(clock[0],1800,monotonic_now=clock[0])['observation']['id']
    context={'commissioned_epoch':EPOCH,'source_generation':service.source_generation,'bridge_id':BRIDGE,'device_id':MAC,
        'observation_id':observation,'effect_qualified':True,'manual_hold_clear':True,'safety_clear':True}
    request=command(service,clock,'DOWN',origin='automatic',source_context=context)
    job=service.commands.pop(); before=len(transport.sent); persist=service._persist
    def delayed():
        clock[0]+=1.1
        return persist()
    service._persist=delayed
    service.execute(job)
    assert len(transport.sent)==before
    assert service.commands.receipt(request)['status']=='rejected'


def test_private_journal_fits_bounded_full_ledger_and_operator_stop_reserve(tmp_path):
    from dooya_openhab.commands import CommandQueue,signed_command,verify_command
    from dooya_openhab.service import Journal
    service,clock,transport=make(tmp_path); request=command(service,clock)
    base=service.commands.receipt(request)['envelope']
    base['source_context']={**base['source_context'],**{key:'x'*512 for key in ('decision_id','batch_id','source_schema','observation_id','zone')}}
    queue=CommandQueue({'sky':'kitchen'},adapter_session=service.adapter_session)
    for _ in range(512):
        item={**base,'request_id':str(uuid.uuid4())}
        queue.submit(verify_command(signed_command(item,KEY),KEY),1000000,1000)
    stop={**base,'request_id':str(uuid.uuid4()),'command':'STOP'}
    assert queue.submit(verify_command(signed_command(stop,KEY),KEY),1000000,1000)['status']=='queued'
    records=queue.export_ledger()
    assert len(records)==513
    value={'schema_version':2,'adapter_session_id':service.adapter_session,'receipts':records,'history':{},'watermarks':{}}
    journal=Journal(tmp_path/'full-journal')
    journal.write(value)
    assert len(journal.read()['receipts'])==513


def test_post_journal_manual_owner_rotation_refuses_packet(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock)
    request=command(service,clock); job=service.commands.pop(); before=len(transport.sent)
    persist=service._persist; changed=[False]
    def rotated():
        if not changed[0]:
            changed[0]=True; clock[0]+=.001
            policy(service,clock,owner_session='55555555-5555-4555-8555-555555555555',generation='66666666-6666-4666-8666-666666666666')
        return persist()
    service._persist=rotated
    service.execute(job)
    assert len(transport.sent)==before and service.commands.receipt(request)['status']=='rejected'


def test_post_journal_manual_is_independent_of_policy_expiry(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock,expires_at_ms=1001000)
    request=command(service,clock); job=service.commands.pop(); persist=service._persist
    def delayed():
        clock[0]+=1.1
        return persist()
    service._persist=delayed
    service.execute(job)
    assert transport.sent[-1].get('data')=={'operation':1}
    assert service.commands.receipt(request)['status']=='acknowledged'


def test_report_identity_digest_binds_original_commissioning_epoch(tmp_path):
    import hashlib
    service,clock,transport=make(tmp_path)
    service.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':25}},clock[0])
    snap=service.states['sky'].snapshot(clock[0],1800,monotonic_now=clock[0])
    expected=hashlib.sha256(b'{"bridge_id":"AABBCCDDEEFF","commissioned_epoch":"11111111-1111-4111-8111-111111111111","device_id":"0000000000000001"}').hexdigest()
    assert snap['observation']['commissioned_epoch']==EPOCH
    assert snap['observation']['source_identity_sha256']==expected
    assert snap['motor_contact']['commissioned_epoch']==EPOCH
    assert snap['motor_contact']['source_identity_sha256']==expected
    assert all(packet['msgType']!='WriteDevice' for packet in transport.sent)


def test_new_commissioning_epoch_never_relabels_restored_history(tmp_path):
    import hashlib
    service,clock,transport=make(tmp_path)
    service.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000000','data':{'type':1,'wirelessMode':1,'currentPosition':25}},clock[0])
    old=service.states['sky'].snapshot(clock[0],1800,monotonic_now=clock[0])['observation']
    new_epoch='77777777-7777-4777-8777-777777777777'
    config=replace(service.config,shades=(replace(service.config.shades[0],commissioned_epoch=new_epoch),))
    peer=Transport(); connector=Connector(peer,config.key,bridge_mac=BRIDGE,ids=MessageIDs(state_path=config.message_id_state_file))
    fresh=Service(config,connector,FakeMQTT(),wall_clock=lambda:1005.,monotonic_clock=lambda:5.)
    fresh.connected.set(); fresh.discover_once(); fresh.poll_once('sky')
    restored=fresh.states['sky'].snapshot(1005.,1800,monotonic_now=5.)
    assert not restored['usable'] and restored['reported_position']==25
    assert restored['observation']['received_at']==1000.
    assert restored['observation']['commissioned_epoch']==EPOCH
    assert restored['observation']['source_identity_sha256']==old['source_identity_sha256']
    fresh.handle_report({'msgType':'Report','mac':MAC,'deviceType':'10000000','msgID':'20261024120000001','data':{'type':1,'wirelessMode':1,'currentPosition':50}},1005.)
    current=fresh.states['sky'].snapshot(1005.,1800,monotonic_now=5.)
    expected=hashlib.sha256(b'{"bridge_id":"AABBCCDDEEFF","commissioned_epoch":"77777777-7777-4777-8777-777777777777","device_id":"0000000000000001"}').hexdigest()
    assert current['usable'] and current['observation']['commissioned_epoch']==new_epoch
    assert current['observation']['source_identity_sha256']==expected!=old['source_identity_sha256']
    assert [packet['msgType'] for packet in peer.sent]==['GetDeviceList','ReadDevice']


def test_service_constructs_explicit_discovery_profile_with_pin(tmp_path):
    service,clock,transport=make(tmp_path)
    config=replace(service.config,discovery_correlation='pinned_discovery_without_id')
    current=Service(config,client=FakeMQTT())
    assert current.connector.discovery_correlation=='pinned_discovery_without_id'
    assert current.connector.transport.discovery_correlation=='pinned_discovery_without_id'
    assert current.connector.transport.expected_bridge_mac==BRIDGE
    assert not current.ready and current.commands.automatic_released is False


def test_per_shade_result_is_full_unretained_bound_receipt(tmp_path):
    service,clock,transport=make(tmp_path); policy(service,clock); request=command(service,clock)
    service.execute(service.commands.pop())
    messages=[(json.loads(payload),options) for topic,payload,options in service.client.messages if topic=='test/sky/event']
    value,options=messages[-1]
    assert value['schema']=='dooya-command-result/v1' and value['request_id']==request
    assert value['envelope']['command']=='UP'
    assert value['envelope']['source_context']['commissioned_epoch']==EPOCH
    assert options['retain'] is False
