#!/usr/bin/env python3
"""Explicit disposable actual Node/Python/MQTT/OpenHAB signed stack qualification."""
from __future__ import annotations
import argparse
import base64
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

from run_openhab_mqtt import Fixture, PREFIX, ROOT, THING, ITEM, install_tree, run, wait_for
from dooya_openhab.commands import signed_command, signed_policy
from dooya_openhab.protocol import Connector, MessageIDs, UDPTransport, access_token

BRIDGE='001122334455'
EPOCH='b79b68d3-4242-446a-b9c8-127dbbe206a9'
KEY=b'K'*32

def private(path,value):
 path.write_text(value if isinstance(value,str) else json.dumps(value));path.chmod(0o600)

def port():
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def zone(number):return 'kitchen' if number<=8 else 'living' if number<=17 else 'bathroom' if number<=21 else 'bedroom'
def device(number):return f'001122334455{number:04X}'

class Bridge:
 def __init__(self,report_port):
  self.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.sock.bind(('127.0.0.1',0));self.sock.settimeout(.1)
  self.port=self.sock.getsockname()[1];self.report_port=report_port;self.ids=MessageIDs()
  self.stop=threading.Event();self.lock=threading.Lock();self.reads=set();self.motions=[];self.packets=[];self.dropped=0;self.drop_next=False;self.emit_motion=True;self.wrong_ack_sent=False
  self.omit_discovery_id=False
  self.positions={device(i):[0,25,50,75,100][(i-1)%5] for i in range(1,27)}
  self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
 def send(self,value,address):self.sock.sendto(json.dumps(value,separators=(',',':'),allow_nan=False).encode(),address)
 def report(self,number,*,payload=None):
  value=payload or {'msgType':'Report','mac':device(number),'deviceType':'10000000','msgID':self.ids.next(),'data':{'type':1,'wirelessMode':1,'operation':2,'currentPosition':self.positions[device(number)],'currentState':3}}
  self.send(value,('127.0.0.1',self.report_port));return value
 def report_all(self):
  for number in range(1,24):self.report(number);time.sleep(.005)
 def loop(self):
  while not self.stop.is_set():
   try:raw,address=self.sock.recvfrom(32769)
   except socket.timeout:continue
   try:
    value=json.loads(raw);self.packets.append(value)
    if value['msgType']=='GetDeviceList':
     ack={'msgType':'GetDeviceListAck','msgID':value['msgID'],'mac':BRIDGE,'deviceType':'02000001','ProtocolVersion':'1.03','token':'37412C478E0FBEAB','data':[{'mac':device(i),'deviceType':'10000001' if i==26 else '10000000'} for i in range(1,27)]}
     if self.omit_discovery_id:ack.pop('msgID')
     self.send(ack,address);continue
    number=int(value['mac'][-4:],16);self.reads.add(number)
    if number==25:continue # Explicit simulated RF/cache timeout.
    data={'type':1,'wirelessMode':0 if number==24 else 1,'currentPosition':self.positions[value['mac']],'operation':2,'currentState':3}
    is_motion=value['msgType']=='WriteDevice' and value.get('data')!={'operation':5}
    if value['msgType']=='WriteDevice':
     assert value['AccessToken']==access_token('74ae544c-d16e-4c','37412C478E0FBEAB')
    if is_motion:
     with self.lock:self.motions.append({'at':time.monotonic(),'packet':value})
     command=value['data']
     if 'targetPosition' in command:self.positions[value['mac']]=command['targetPosition']
     elif command['operation'] in (0,1):self.positions[value['mac']]=100 if command['operation']==0 else 0
     data['currentPosition']=self.positions[value['mac']]
     if self.drop_next:self.drop_next=False;self.dropped+=1;continue
    ack={'msgType':value['msgType']+'Ack','msgID':value['msgID'],'mac':value['mac'],'deviceType':value['deviceType'],'data':data}
    if number==1 and not self.wrong_ack_sent:
     self.send({**ack,'mac':device(2),'data':{**data,'currentPosition':99}},address);self.wrong_ack_sent=True
    self.send(ack,address)
    if is_motion and self.emit_motion:
     threading.Timer(.08,lambda n=number:self.report(n)).start()
   except Exception as error:
    self.failure=type(error).__name__
 def close(self):self.stop.set();self.thread.join(timeout=2);self.sock.close()

class Stack:
 def __init__(self,artifacts,ui_root):
  self.fixture=Fixture(artifacts/'binding-bootstrap');self.artifacts=artifacts;self.ui_root=ui_root
  self.fixture.extra_mqtt_users=[('fixture-owner','synthetic-owner'),('fixture-adapter','synthetic-adapter')]
  self.fixture.extra_mqtt_acl=(f'user fixture-owner\ntopic write {PREFIX}/owner/command\ntopic write {PREFIX}/owner/policy\n'
    f'user fixture-adapter\ntopic read {PREFIX}/owner/command\ntopic read {PREFIX}/owner/policy\ntopic read {PREFIX}/+/command\n'
    f'topic write {PREFIX}/service/#\ntopic write {PREFIX}/bridge/#\n'
    +''.join(f'topic write {PREFIX}/skylight_{i}/#\n' for i in range(1,27))+
    f'topic write {PREFIX}/owner/result\ntopic write {PREFIX}/owner/policy-ack\n')
  self.results=[];self.worker=None;self.owner=None;self.bridge=None;self.messages=[];self.logs=[];self.owner_session=None;self.load_hashes=[]
 def capture_sources(self,phase,files):
  self.load_hashes.append({'phase':phase,'files':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
 def record(self,name,**fields):self.results.append({'scenario':name,'passed':True,**fields});print('PASS',name,flush=True)
 def start(self):
  self.capture_sources('fixture and binding templates',[Path(__file__),ROOT/'tests/integration/run_openhab_mqtt.py',ROOT/'examples/openhab.things',ROOT/'examples/openhab.items'])
  f=self.fixture;print('Starting disposable real binding stack',flush=True);f.start();self.directory=f.root/'signed';self.directory.mkdir(mode=0o700);self.directory.chmod(0o700);report_port=port();self.bridge=Bridge(report_port)
  # Load 25 additional actual binding Things/Items plus the policy-ack channel.
  extra=f.root/'extra';(extra/'things').mkdir(parents=True);(extra/'items').mkdir()
  template=(ROOT/'examples/openhab.things').read_text().replace('mqtt:broker:local','mqtt:broker:fixture')
  items=(ROOT/'examples/openhab.items').read_text()
  (extra/'things/stack.things').write_text('\n'.join(template.replace('skylight_1',f'skylight_{i}') for i in range(2,27))+
    f'\nThing mqtt:topic:stack_policy (mqtt:broker:fixture) {{ Channels: Type string : ack [stateTopic="{PREFIX}/owner/policy-ack"] }}\n')
  (extra/'items/stack.items').write_text('\n'.join(items.replace('Shade_Skylight1',f'Shade_Skylight{i}').replace('dooya_skylight_1',f'dooya_skylight_{i}') for i in range(2,27))+
    '\nString Shade_PolicyAck {channel="mqtt:topic:stack_policy:ack"}\n')
  install_tree(f.openhab,extra,'/openhab/conf')
  wait_for(lambda:len(f.http('/items'))>=26*7,'26 actual binding mappings',30)
  # Bind all late-added shared-topic handlers before the actual producer starts.
  # Unknown/NULL liveness is intentionally unavailable, never guessed ONLINE.
  def bound_health_channels():
   for suffix,value in [('service/availability','offline'),('bridge/availability','offline'),('service/state','{}'),('bridge/state','{}')]:f.writer.publish(suffix,value)
   states={row['name']:row['state'] for row in f.http('/items')}
   return all(states.get(f'Shade_Skylight{i}_ServiceAvailable')=='OFF' and states.get(f'Shade_Skylight{i}_BridgeAvailable')=='OFF'
    and states.get(f'Shade_Skylight{i}_ServiceState')=='{}' and states.get(f'Shade_Skylight{i}_BridgeState')=='{}' for i in range(1,27))
  wait_for(bound_health_channels,'26 binding health handlers initialized unavailable before actual Service',60)
  self.worker_config={'mqtt_port':f.mqtt_port,'report_port':report_port,'bridge_port':self.bridge.port,'bridge_id':BRIDGE,
   'message_ids':str(self.directory/'message-ids.json'),'journal':str(self.directory/'adapter-journal.json'),'allow_commands':False,
   'shades':[{'id':f'skylight_{i}','mac':device(i),'percentage':True,'zone':zone(i),'slot':i,'commissioned_epoch':EPOCH,'model':'SYNTHETIC roller','firmware':'SYNTHETIC-1','commissioning_ref':'OFFLINE SIMULATION ONLY','supported_operations':['UP','DOWN','STOP']} for i in range(1,27)]}
  private(self.directory/'owner.key','K'*32);private(self.directory/'owner.password','synthetic-owner')
  private(self.directory/'publisher.toml',f'owner_key_file="owner.key"\n[mqtt]\nhost="127.0.0.1"\nport={f.mqtt_port}\nusername="fixture-owner"\npassword_file="owner.password"\nprefix="{PREFIX}"\n')
  manual_auth='Basic '+base64.b64encode(b'synthetic-operator:synthetic-manual-password').decode()
  records=[]
  for i in range(1,27):
   records.append({'slot':i,'zone':zone(i),'id':f'skylight_{i}','deviceId':device(i),'commissionedEpoch':EPOCH,'model':'SYNTHETIC roller','firmware':'SYNTHETIC-1','reference':'OFFLINE SIMULATION ONLY','operations':['UP','DOWN','STOP'],'percentage':True,'manualReleased':True,
    'positionItem':f'Shade_Skylight{i}','availabilityItem':f'Shade_Skylight{i}_Available','stateItem':f'Shade_Skylight{i}_Diagnostics','serviceAvailabilityItem':f'Shade_Skylight{i}_ServiceAvailable','bridgeAvailabilityItem':f'Shade_Skylight{i}_BridgeAvailable','serviceStateItem':f'Shade_Skylight{i}_ServiceState','bridgeStateItem':f'Shade_Skylight{i}_BridgeState','serviceThingUID':f'mqtt:topic:dooya_skylight_{i}'})
  private(self.directory/'profile.json',{'schema':'earthship-shade-owner-installation/v1','commandsEnabled':True,'bridgeId':BRIDGE,'ownerKeyFile':'owner.key','ownerJournalFile':'owner-journal.json','manualAuthorizationHash':hashlib.sha256(manual_auth.encode()).hexdigest(),'publisher':{'enabled':True,'python':sys.executable,'config':'publisher.toml'},'policyAckItem':'Shade_PolicyAck','slots':records})
  private(self.directory/'node.json',{'ui_root':str(self.ui_root),'profile':str(self.directory/'profile.json'),'openhab':f'http://127.0.0.1:{f.http_port}','authorization':'Bearer '+f.auth,'manual_authorization':manual_auth})
  self.start_worker(False)
  self.owner_log=(self.directory/'owner-stderr.log').open('w');self.logs.append(self.owner_log)
  self.capture_sources('Node runtime import',[self.ui_root/'src/lib/shades'/name for name in ('executionServer.js','executionOwner.js','ownerLedger.js','ownerWire.js','policyServer.js','policyModel.js','catalog.js','observation.js')]+[ROOT/'tests/integration/fixtures/signed_owner.mjs'])
  self.owner=subprocess.Popen(['node',str(ROOT/'tests/integration/fixtures/signed_owner.mjs'),str(self.directory/'node.json')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.owner_log,text=True,bufsize=1)
 def start_worker(self,allow):
  if self.worker is not None:
   self.worker.kill();self.worker.wait(timeout=5);self.worker=None
  self.bridge.reads.clear()
  self.worker_config['allow_commands']=allow;private(self.directory/'worker.json',self.worker_config)
  log=(self.directory/f'worker-{allow}.log').open('w');self.logs.append(log)
  self.capture_sources('actual Python worker allow_commands='+str(allow),list((ROOT/'src/dooya_openhab').glob('*.py'))+[ROOT/'tests/integration/fixtures/signed_worker.py'])
  previous=len(self.bridge.packets)
  self.worker=subprocess.Popen([sys.executable,str(ROOT/'tests/integration/fixtures/signed_worker.py'),str(self.directory/'worker.json')],stdout=log,stderr=log)
  wait_for(lambda:any(p.get('msgType')=='GetDeviceList' for p in self.bridge.packets[previous:]),'actual worker discovery',20)
  try:wait_for(lambda:len(self.bridge.reads)>=25,'fair first26-device cache-poll pass',20)
  except AssertionError:
   os.kill(self.worker.pid,signal.SIGUSR1);time.sleep(.2);raise
 def call(self,action,request=None):
  assert self.owner.poll() is None,'actual Node owner exited'
  self.owner.stdin.write(json.dumps({'action':action,'request':request})+'\n');self.owner.stdin.flush()
  ready,_,_=select.select([self.owner.stdout],[],[],20)
  assert ready,'Node owner response deadline'
  line=self.owner.stdout.readline();assert line,'Node owner response missing'
  return json.loads(line)
 def observe(self):
  self.last_observations=self.call('observe')['result']['observations'];return self.last_observations
 def collect(self):self.messages.extend(self.fixture.observer.drain());return self.messages
 def result_for(self,rid,status=None):
  values=[]
  for m in self.collect():
   if m['topic']==PREFIX+'/owner/result':
    r=json.loads(m['payload'])
    if r.get('request_id')==rid:values.append(r)
  return next((r for r in reversed(values) if status is None or r.get('status')==status),None)
 def intent(self,command,slot=1):return {'request_id':str(uuid.uuid4()),'requested_at_ms':int(time.time()*1000),'slot':slot,'command':command}
 def signed(self,command,**overrides):
  s=json.loads(self.fixture.http('/items/Shade_Skylight1_ServiceState')['state']);now=int(time.time()*1000)
  env={'schema':'dooya-owner-command/v1','request_id':str(uuid.uuid4()),'shade_id':'skylight_1','command':command,'origin':'manual','created_at_ms':now,'expires_at_ms':now+10000,'adapter_session':s['adapter_session_id'],'owner_session':self.owner_session or str(uuid.uuid4()),'policy_generation':None,'source_context':{'commissioned_epoch':EPOCH,'source_generation':s['source_generation'],'bridge_id':BRIDGE,'device_id':device(1)}}
  env.update(overrides);return signed_command(env,KEY)
 def publish(self,value):self.fixture.writer.publish('owner/command',json.dumps(value),retain=False)
 def qualify(self):
  f=self.fixture;b=self.bridge
  time.sleep(.5)
  observed=self.observe();assert all(row['state']!='reported' for row in observed)
  assert not b.motions
  assert json.loads(f.http('/items/Shade_Skylight1_Diagnostics')['state'])['cache']['currentPosition']==0
  self.record('actual_cache_Acks_do_not_qualify_positions',fair_cache_devices=len(b.reads),wrong_MAC_Ack_sent=b.wrong_ack_sent)
  b.report_all();wait_for(lambda:sum(r['state']=='reported' for r in self.observe())==23,'23 qualified and3 unsupported/unreachable actual sources',20)
  observed=self.observe();assert [r['position'] for r in observed[:5]]==[0,25,50,75,100]
  self.record('26_device_actual_Service_MQTT_binding_UI_parser',reported=23,unavailable=3,native_positions=[r['position'] for r in observed[:5]])
  # Duplicate Reports are real UDP packets. Original timestamp must not renew.
  packet=b.report(1);wait_for(lambda:json.loads(f.http('/items/Shade_Skylight1_Diagnostics')['state']).get('observation',{}).get('upstream_message_id')==packet['msgID'],'new real UDP report reaches binding')
  before=json.loads(f.http('/items/Shade_Skylight1_Diagnostics')['state'])['report_received_at'];b.report(1,payload=packet);b.report(1,payload={**packet,'msgID':'20200101000000000'});time.sleep(.4)
  after=json.loads(f.http('/items/Shade_Skylight1_Diagnostics')['state'])['report_received_at'];assert after==before
  self.record('duplicate_and_older_real_UDP_Reports_do_not_renew_original_time')
  # Actual Node policy signer -> one-shot publisher -> Service policy acknowledgement.
  policy_result=self.call('policy')
  def policy_ack():
   matches=[json.loads(m['payload']) for m in self.collect() if m['topic']==PREFIX+'/owner/policy-ack']
   return matches[-1] if matches and matches[-1].get('status')=='effective' else None
  ack=wait_for(policy_ack,'actual signed policy effective acknowledgement',15)
  self.owner_session=ack['owner_session']
  self.record('actual_Node_policy_signature_transport_and_Service_ack',owner_result=policy_result,automatic_released=ack['automatic_released'])
  no_motion=len(b.motions)
  f.writer.publish('skylight_1/command','UP',retain=False);self.publish({'envelope':{},'signature':'0'*64});time.sleep(.4);assert len(b.motions)==no_motion
  self.record('unsigned_and_forged_HMAC_MQTT_cannot_dispatch')
  request=self.intent('UP');owner_result=self.call('manual',request);assert owner_result['ok'],owner_result;wait_for(lambda:self.result_for(request['request_id'],'rejected'),'observation-only actual Service final rejection');assert len(b.motions)==no_motion
  self.record('actual_Node_owner_signed_request_rejected_when_allow_commands_false')
  assert self.call('automatic',{})['result']['rejected']
  self.record('actual_Node_owner_global_automatic_release_stays_false')
  self.start_worker(True);assert all(row['state']!='reported' for row in self.observe());self.record('actual_adapter_restart_never_qualifies_retained_previous_session');b.report_all();wait_for(lambda:sum(r['state']=='reported' for r in self.observe())==23,'manual-phase reconciled reports',20)
  for command in (25,'UP','DOWN'):
   request=self.intent(command);result=self.call('manual',request);assert result['ok'] and result['result']['status']=='published_awaiting_adapter_result',result
   wait_for(lambda:self.result_for(request['request_id'],'target_observed'),'actual signed operation target observed',15)
   native=command if isinstance(command,int) else 0 if command=='UP' else 100
   wait_for(lambda:self.observe()[0].get('position')==native,'actual UI parser reports target',10)
   def per_shade_result():
    value=json.loads(f.http('/items/Shade_Skylight1_CommandResult')['state'])
    return value if value.get('schema')=='dooya-command-result/v1' and value.get('request_id')==request['request_id'] and value.get('status')=='target_observed' else None
   receipt=wait_for(per_shade_result,'actual per-shade receipt reaches real binding',10)
   self.record('actual_Node_HMAC_Paho_Service_UDP_report_chain_'+str(command),outcome='target_observed_actor_unknown',native_position=native,per_shade_binding_receipt=receipt['status'])
  request=self.intent('STOP');owner_result=self.call('manual',request);assert owner_result['ok'],owner_result;wait_for(lambda:self.result_for(request['request_id'],'acknowledged'),'actual signed STOP acknowledged')
  motions=len(b.motions);assert self.call('manual',request)['ok'];time.sleep(.5);assert len(b.motions)==motions
  wire=[json.loads(m['payload']) for m in self.collect() if m['topic']==PREFIX+'/owner/command' and json.loads(m['payload']).get('envelope',{}).get('request_id')==request['request_id']][-1]
  before_results=sum(1 for m in self.collect() if m['topic']==PREFIX+'/owner/result' and json.loads(m['payload']).get('request_id')==request['request_id'])
  self.publish(wire)
  wait_for(lambda:sum(1 for m in self.collect() if m['topic']==PREFIX+'/owner/result' and json.loads(m['payload']).get('request_id')==request['request_id'])>before_results,'actual adapter duplicate signed delivery receipt')
  assert len(b.motions)==motions
  self.record('actual_Node_and_adapter_identity_dedup_no_movement_replay',node_duplicate_and_actual_MQTT_redelivery=True)
  expired=self.signed('UP',created_at_ms=int(time.time()*1000)-11000,expires_at_ms=int(time.time()*1000)-1000);self.publish(expired)
  wait_for(lambda:self.result_for(expired['envelope']['request_id'],'rejected'),'expired signed actual Service refusal');assert len(b.motions)==motions
  wrong=self.signed('UP');wrong['envelope']['source_context']['device_id']=device(2);wrong=signed_command(wrong['envelope'],KEY);self.publish(wrong)
  wait_for(lambda:self.result_for(wrong['envelope']['request_id'],'rejected'),'wrong source context refused');assert len(b.motions)==motions
  self.record('signed_expired_and_wrong_device_context_refused_at_actual_dispatch')
  # A signed policy cannot release automatic execution in the actual Service.
  now=int(time.time()*1000);generation=str(uuid.uuid4())
  policy={'schema':'dooya-owner-policy/v1','generation':generation,'owner_session':self.owner_session,
   'adapter_session':json.loads(f.http('/items/Shade_Skylight1_ServiceState')['state'])['adapter_session_id'],
   'issued_at_ms':now,'expires_at_ms':now+30000,'zones':{name:{'enabled':True,'authoritative':True} for name in ('kitchen','living','bathroom','bedroom')},
   'override':{'state':'OFF','authoritative':True},'global_released':True}
  def owner_publish(kind,message):
   result=run(sys.executable,'-m','dooya_openhab.owner_publish','--config',str(self.directory/'publisher.toml'),'--publish',input=json.dumps({'kind':kind,'message':message}))
   assert json.loads(result.stdout)['published'] is True
  owner_publish('policy',signed_policy(policy,KEY))
  wait_for(lambda:any(json.loads(m['payload']).get('policy_generation')==generation for m in self.collect() if m['topic']==PREFIX+'/owner/policy-ack'),'release-shaped policy acknowledgement')
  automatic=self.signed('UP',origin='automatic',policy_generation=generation);self.publish(automatic)
  refused=wait_for(lambda:self.result_for(automatic['envelope']['request_id'],'rejected'),'signed automatic inhibited')
  assert refused['reason']=='automatic_not_released',refused
  assert len(b.motions)==motions
  self.record('actual_Service_global_automatic_release_stays_false_even_signed_policy_true',reason=refused['reason'])
  for override,enabled in [('ON',True),('OFF',False)]:
   policy={**policy,'generation':str(uuid.uuid4()),'issued_at_ms':int(time.time()*1000),'expires_at_ms':int(time.time()*1000)+30000,'global_released':False,
    'override':{'state':override,'authoritative':True},'zones':{name:{'enabled':enabled,'authoritative':True} for name in ('kitchen','living','bathroom','bedroom')}}
   owner_publish('policy',signed_policy(policy,KEY))
   wait_for(lambda:any(json.loads(m['payload']).get('policy_generation')==policy['generation'] for m in self.collect() if m['topic']==PREFIX+'/owner/policy-ack'),'policy transition effective ack')
   assert len(b.motions)==motions
  self.record('signed_zone_and_override_transitions_do_not_move_or_release_automation')
  # Lost Ack after the endpoint accepted a motion: no movement retry, STOP stays available.
  duplicate=b.report(2)
  flood_done=threading.Event()
  def flood():
   until=time.monotonic()+1.5
   while time.monotonic()<until:
    b.report(2,payload=duplicate);time.sleep(.002)
   flood_done.set()
  flood_thread=threading.Thread(target=flood,daemon=True);flood_thread.start()
  b.drop_next=True;b.emit_motion=False;request=self.intent('UP');assert self.call('manual',request)['ok']
  wait_for(lambda:b.dropped==1,'synthetic movement accepted with lost Ack');start=time.monotonic();stop=self.intent('STOP');owner_result=self.call('manual',stop);assert owner_result['ok'],owner_result
  wait_for(lambda:any(m['packet']['data'].get('operation')==2 and m['at']>=start for m in b.motions),'actual STOP preempts lost-Ack wait',5)
  latency=next(m['at']-start for m in b.motions if m['packet']['data'].get('operation')==2 and m['at']>=start)
  wait_for(lambda:self.result_for(request['request_id'],'uncertain'),'lost-Ack uncertain receipt');count=len([m for m in b.motions if m['packet']['data'].get('operation')==1]);time.sleep(1)
  assert len([m for m in b.motions if m['packet']['data'].get('operation')==1])==count
  flood_thread.join(timeout=2);assert flood_done.is_set()
  self.record('lost_Ack_and_duplicate_Report_flood_actual_Service_uncertain_no_retry_and_operator_STOP',stop_software_latency_seconds=round(latency,4))
  # Explicit later observation restores observation only; no causal attribution.
  wait_for(lambda:json.loads(f.http('/items/Shade_Skylight1_ServiceState')['state']).get('ready') is True,'actual scheduled rediscovery after uncertain dispatch',75)
  assert len([m for m in b.motions if m['packet']['data'].get('operation')==1])==count
  b.emit_motion=True;b.report_all();wait_for(lambda:self.observe()[0]['state']=='reported','fresh report before actual worker kill',20)
  start=time.monotonic();self.worker.kill();self.worker.wait(timeout=5);self.worker=None
  wait_for(lambda:f.state(ITEM+'_ServiceAvailable')=='OFF','ACTUAL Service process LWT',15)
  late=f.probe();late.subscribe(PREFIX+'/#');snapshot={m['topic']:m['payload'] for m in late.drain()}
  assert snapshot[PREFIX+'/skylight_1/availability']=='online';assert self.observe()[0]['state']=='last_known'
  self.record('SIGKILL_ACTUAL_Service_LWT_gates_retained_position',lwt_to_item_off_seconds=round(time.monotonic()-start,4),retained_shade_online=True)
  run('docker','stop','-t','5',f.broker);run('docker','start',f.broker)
  late=f.probe();late.subscribe(PREFIX+'/#');snapshot={m['topic']:m['payload'] for m in late.drain()}
  assert snapshot[PREFIX+'/service/availability']=='offline';assert not any(k.endswith('/owner/command') for k in snapshot)
  wait_for(lambda:self.observe()[0]['state']=='last_known','broker restart remains historical',30)
  self.record('broker_restart_and_late_subscriber_no_actual_command_replay',service_offline=True)
 def finish(self,error=None):
  try:
   if self.worker and self.worker.poll() is None:
    if error is not None:
     os.kill(self.worker.pid,signal.SIGUSR1);time.sleep(.2)
    self.worker.kill();self.worker.wait(timeout=5)
   if self.owner and self.owner.poll() is None:
    self.owner.terminate()
    try:self.owner.wait(timeout=5)
    except subprocess.TimeoutExpired:self.owner.kill();self.owner.wait(timeout=5)
   if self.bridge:self.bridge.close()
   for log in self.logs:log.close()
   self.artifacts.mkdir(parents=True,exist_ok=True)
   source_files=[ROOT/'src/dooya_openhab/service.py',ROOT/'src/dooya_openhab/protocol.py',ROOT/'src/dooya_openhab/model.py',ROOT/'src/dooya_openhab/commands.py',ROOT/'src/dooya_openhab/owner_publish.py',Path(__file__),ROOT/'tests/integration/fixtures/signed_worker.py',ROOT/'tests/integration/fixtures/signed_owner.mjs',self.ui_root/'src/lib/shades/executionOwner.js',self.ui_root/'src/lib/shades/executionServer.js',self.ui_root/'src/lib/shades/observation.js']
   changed=[name for phase in self.load_hashes for name,digest in phase['files'].items() if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest]
   self.source_changed=bool(changed)
   if error is None and changed:error=AssertionError('Source changed after process import: '+','.join(sorted(set(changed))))
   record={'loaded_source_sha256':self.load_hashes,'synthetic_only':True,'physical_hardware_qualified':False,'automatic_released':False,'passed':error is None,'error':None if error is None else str(error),'scenarios':self.results,'source_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},'wire_boundary':'real loopback UDPTransport and actual ReportSocket.receive; only multicast initialization replaced with loopback bind','owner_boundary':'actual Node runtime/owner and actual Python owner_publish; real OpenHAB REST readAdapter; policy owner may report unavailable absent household revision receipt','worker_configuration':'test-only allow_commands true phase; production defaults unchanged','last_UI_observations':getattr(self,'last_observations',None),'fake_bridge_packets':len(self.bridge.packets) if self.bridge else 0,'fake_read_slots':sorted(self.bridge.reads) if self.bridge else [],'fake_failure':getattr(self.bridge,'failure',None),'fake_motion_packets':len(self.bridge.motions) if self.bridge else 0}
   (self.artifacts/'result.json').write_text(json.dumps(record,indent=2)+'\n')
  finally:self.fixture.save_and_cleanup(error)

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');parser.add_argument('--ui-root',required=True,type=Path);parser.add_argument('--artifacts',required=True,type=Path);args=parser.parse_args()
 if not args.run:parser.error('--run required; no processes or containers started')
 stack=Stack(args.artifacts,args.ui_root);error=None
 bridge=Bridge(port());bridge.omit_discovery_id=True
 try:
  strict=Connector(UDPTransport('127.0.0.1',port=bridge.port,timeout=.1),'74ae544c-d16e-4c',bridge_mac=BRIDGE)
  try:strict.discover();raise AssertionError('Default discovery accepted missing correlation identity')
  except TimeoutError:pass
  optin=Connector(UDPTransport('127.0.0.1',port=bridge.port,timeout=.2),'74ae544c-d16e-4c',bridge_mac=BRIDGE,discovery_correlation='pinned_discovery_without_id')
  assert len(optin.discover())==26 and not bridge.motions
  stack.record('actual_UDP_default_missing_ID_refused_and_explicit_pinned_discovery_profile_accepts',movement_packets=0)
 finally:bridge.close()
 try:stack.start();stack.qualify()
 except BaseException as failure:error=failure;raise
 finally:stack.finish(error)
 if error is None and getattr(stack,'source_changed',False):raise AssertionError('Source changed during actual process lifetime; verification invalid')
if __name__=='__main__':main()
