"""Actual Paho 2.1 PUBACK/publish locking under deterministic thread ordering."""
import struct
import sys
import threading
import traceback

import paho.mqtt.client as mqtt

from test_service import make


def test_actual_paho_publish_puback_concurrency_never_inverts_service_lock(tmp_path):
    service,clock,transport=make(tmp_path)
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_publish=service.on_publish
    service.client=client
    # Paho queues a real QoS1 message even without a broker socket. Injecting its
    # PUBACK below exercises the exact installed callback/locking implementation.
    first=client.publish('test/prior',b'synthetic',qos=1)
    client._in_packet={'remaining_length':2,'packet':struct.pack('!H',first.mid)}
    held=threading.Event(); entered=threading.Event(); failures=[]
    publish=client.publish
    def entering(*args,**kwargs):
        entered.set()
        return publish(*args,**kwargs)
    client.publish=entering
    def acknowledgement():
        try:
            with client._out_message_mutex:
                held.set()
                if not entered.wait(1): raise AssertionError('publisher never entered')
                client._handle_pubackcomp('PUBACK')
        except Exception as error: failures.append(type(error).__name__)
    def publication():
        try: service.publish('sky/state',{'reported_position':0})
        except Exception as error: failures.append(type(error).__name__)
    ack=threading.Thread(target=acknowledgement,name='actual-paho-puback',daemon=True)
    worker=threading.Thread(target=publication,name='actual-service-publish',daemon=True)
    ack.start(); assert held.wait(1); worker.start()
    worker.join(.5); ack.join(.5)
    if worker.is_alive() or ack.is_alive():
        frames=sys._current_frames()
        trace='\n'.join(thread.name+'\n'+''.join(traceback.format_stack(frames[thread.ident]))
                        for thread in (worker,ack) if thread.ident in frames)
        (tmp_path/'mqtt-lock-threads.txt').write_text(trace)
        raise AssertionError('Actual Service/Paho lock inversion:\n'+trace)
    assert not failures


def test_concurrent_same_payload_reserves_one_publish_and_reconciles_early_ack(tmp_path):
    service,clock,transport=make(tmp_path)
    publish=service.client.publish
    entered=threading.Event(); release=threading.Event()
    def delayed(*args,**kwargs):
        info=publish(*args,**kwargs)
        entered.set()
        if not release.wait(1): raise AssertionError('publication not released')
        service.on_publish(service.client,None,info.mid,0,None)
        return info
    service.client.publish=delayed
    first=threading.Thread(target=lambda:service.publish('sky/state',{'reported_position':0}),daemon=True)
    first.start(); assert entered.wait(1)
    service.publish('sky/state',{'reported_position':0})
    assert len(service.client.messages)==1 and 'sky/state' not in service.sent
    release.set(); first.join(1)
    assert not first.is_alive() and 'sky/state' in service.sent
    assert not service._starting and not service._early_acks


def test_failed_early_ack_never_populates_deduplication_cache(tmp_path):
    service,clock,transport=make(tmp_path)
    publish=service.client.publish
    def rejected(*args,**kwargs):
        info=publish(*args,**kwargs)
        service.on_publish(service.client,None,info.mid,128,None)
        return info
    service.client.publish=rejected
    service.publish('sky/state',{'reported_position':0})
    assert 'sky/state' not in service.sent and not service._early_acks
    service.publish('sky/state',{'reported_position':0})
    assert len(service.client.messages)==2


def test_reconnect_during_publish_cannot_cache_previous_generation_ack(tmp_path):
    service,clock,transport=make(tmp_path)
    publish=service.client.publish
    def reconnect(*args,**kwargs):
        info=publish(*args,**kwargs)
        service.on_disconnect(None,None,None,None,None)
        service.on_publish(service.client,None,info.mid,0,None)
        return info
    service.client.publish=reconnect
    service.publish('sky/state',{'reported_position':0})
    assert 'sky/state' not in service.sent
    assert not service._pending and not service._early_acks and not service._starting


def test_late_older_payload_ack_cannot_hide_latest_retained_value_repair(tmp_path):
    service,clock,transport=make(tmp_path)
    publish=service.client.publish; entered=threading.Event(); release=threading.Event()
    def reordered(topic,payload,**kwargs):
        if payload=='{"reported_position":0}':
            entered.set()
            if not release.wait(1): raise AssertionError('old publication not released')
        info=publish(topic,payload,**kwargs)
        service.on_publish(service.client,None,info.mid,0,None)
        return info
    service.client.publish=reordered
    old=threading.Thread(target=lambda:service.publish('sky/state',{'reported_position':0}),daemon=True)
    old.start(); assert entered.wait(1)
    service.publish('sky/state',{'reported_position':1})
    release.set(); old.join(1); assert not old.is_alive()
    assert service.client.messages[-1][1]=='{"reported_position":0}'
    service.publish('sky/state',{'reported_position':1})
    assert service.client.messages[-1][1]=='{"reported_position":1}'


def test_actual_paho_unacknowledged_outbound_queue_is_bounded(tmp_path):
    from dooya_openhab.service import Service
    fake,clock,transport=make(tmp_path)
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    service=Service(fake.config,fake.connector,client)
    results=[client.publish('test/synthetic-unacknowledged',str(index),qos=1).rc for index in range(400)]
    assert len(client._out_messages)==256
    assert results[-1]==mqtt.MQTT_ERR_QUEUE_SIZE
    assert client._max_inflight_messages==20
