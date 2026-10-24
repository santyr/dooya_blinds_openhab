"""Actual Service worker; replaces multicast initialization with loopback binding only."""
import json
import faulthandler
from pathlib import Path
import signal
import socket
import sys

from dooya_openhab.config import Config, Shade
from dooya_openhab.protocol import Connector, MessageIDs, ReportSocket, UDPTransport
from dooya_openhab.service import Service

faulthandler.enable()
faulthandler.register(signal.SIGUSR1, all_threads=True)

value=json.loads(Path(sys.argv[1]).read_text())
def reports_factory(_bridge,_interface):
    # Actual ReportSocket.receive and decode/source filtering remain unchanged.
    reader=ReportSocket.__new__(ReportSocket)
    reader.bridge_ip='127.0.0.1'
    reader.sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    reader.sock.bind(('127.0.0.1',value['report_port']))
    reader.sock.setblocking(False)
    return reader
shades=tuple(Shade(**row) for row in value['shades'])
config=Config(bridge_host='127.0.0.1',interface_ip='127.0.0.1',key='74ae544c-d16e-4c',
 mqtt_host='127.0.0.1',mqtt_port=value['mqtt_port'],mqtt_username='fixture-adapter',
 mqtt_password='synthetic-adapter',mqtt_tls=False,prefix='earthship/shades/v2',
 allow_commands=value['allow_commands'],poll_seconds=30,stale_seconds=300,shades=shades,
 bridge_mac=value['bridge_id'],message_id_state_file=value['message_ids'],owner_key=b'K'*32,
 receipt_journal_file=value['journal'],request_timeout_seconds=.5,poll_spacing_seconds=.1)
connector=Connector(UDPTransport('127.0.0.1',port=value['bridge_port'],timeout=.5),config.key,
 bridge_mac=config.bridge_mac,ids=MessageIDs(state_path=config.message_id_state_file))
service=Service(config,connector=connector,reports_factory=reports_factory)
signal.signal(signal.SIGTERM,lambda *_:service.stop.set())
service.run()
