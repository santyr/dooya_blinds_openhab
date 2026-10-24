#!/usr/bin/env python3
"""Run actual OpenHAB/MQTT binding against disposable containers only.

No production configuration is restored or read. All credentials/data are synthetic.
Requires Docker and this project's installed paho-mqtt dependency. Explicit opt-in:
    python tests/integration/run_openhab_mqtt.py --run --artifacts /tmp/dd7066-binding
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import tempfile
import tarfile
import time
import urllib.error
import urllib.request
import uuid

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "earthship/shades/v2"
ITEM = "Shade_Skylight1"
THING = "mqtt:topic:dooya_skylight_1"
OH_IMAGE = "openhab/openhab:5.2.3-debian"
MQTT_IMAGE = "eclipse-mosquitto:2.0.18"


def run(*args, check=True, timeout=60, input=None):
    return subprocess.run(args, text=True, input=input, capture_output=True, check=check, timeout=timeout)


def install_tree(container, source, destination):
    """Stream local fixture bytes; works with a remote Docker daemon/CLI wrapper."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                body = path.read_bytes()
                member = tarfile.TarInfo(str(path.relative_to(source)))
                member.size = len(body)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(body))
    subprocess.run(["docker", "exec", "-u", "0", "-i", container, "tar",
                    "-xf", "-", "-C", destination], input=output.getvalue(),
                   capture_output=True, check=True, timeout=30)


def wait_for(predicate, description, timeout=120):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, ValueError, urllib.error.URLError, subprocess.SubprocessError):
            pass
        time.sleep(.25)
    raise AssertionError(f"Timed out: {description}; last={last!r}")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Probe:
    def __init__(self, port, user="fixture-observer", password="synthetic-observer"):
        self.messages = queue.Queue()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id="fixture-" + uuid.uuid4().hex)
        self.client.username_pw_set(user, password)
        self.client.on_message = lambda _c, _u, m: self.messages.put(
            {"topic": m.topic, "payload": m.payload.decode(), "retain": m.retain})
        self.client.connect("127.0.0.1", port, keepalive=5)
        self.client.loop_start()
        wait_for(lambda: self.client.is_connected(), "MQTT probe connected", 15)

    def subscribe(self, topic):
        self.client.subscribe(topic, qos=1)
        time.sleep(.35)

    def publish(self, suffix, value, retain=True):
        info = self.client.publish(PREFIX + "/" + suffix, value, qos=1, retain=retain)
        info.wait_for_publish(timeout=5)

    def drain(self):
        result = []
        while True:
            try:
                result.append(self.messages.get_nowait())
            except queue.Empty:
                return result

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


class Fixture:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.name = "dd7066-binding-" + uuid.uuid4().hex[:10]
        self.broker = self.name + "-mqtt"
        self.openhab = self.name + "-oh"
        self.network = self.name + "-net"
        self.root = Path(tempfile.mkdtemp(prefix=self.name + "-"))
        self.http_port, self.mqtt_port = free_port(), free_port()
        self.started = []
        self.probes = []
        self.results = []
        self.extra_mqtt_users = []
        self.extra_mqtt_acl = ""
        self.auth = None
        self.source_hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in [Path(__file__), ROOT / "examples/openhab.things", ROOT / "examples/openhab.items"]}

    def http(self, path, body=None, method="GET", content_type="text/plain"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.http_port}/rest" + path,
            data=None if body is None else body.encode(), method=method,
            headers={"Content-Type": content_type, "Accept": "application/json",
                     **({"Authorization": "Bearer " + self.auth} if self.auth else {})})
        with urllib.request.urlopen(request, timeout=5) as response:
            result = response.read().decode()
            return json.loads(result) if result else None

    def state(self, item=ITEM):
        return self.http("/items/" + item)["state"]

    def thing_status(self):
        return self.http("/things/" + THING)["statusInfo"]["status"]

    def probe(self, *args):
        probe = Probe(self.mqtt_port, *args)
        self.probes.append(probe)
        return probe

    def record(self, scenario, **evidence):
        self.results.append({"scenario": scenario, "passed": True, **evidence})
        print("PASS", scenario, flush=True)

    def start(self):
        for image in (OH_IMAGE, MQTT_IMAGE):
            run("docker", "image", "inspect", image)
        for directory in ("conf/services", "conf/things", "conf/items", "userdata", "addons", "mqtt/data"):
            (self.root / directory).mkdir(parents=True)
        mqtt_dir = self.root / "mqtt"
        (mqtt_dir / "acl").write_text(
            f"user fixture-openhab\ntopic read {PREFIX}/#\ntopic write {PREFIX}/owner-input/+\n"
            f"user fixture-writer\ntopic readwrite {PREFIX}/#\n"
            f"topic readwrite earthship/shades/v1/#\n"
            f"user fixture-observer\ntopic read {PREFIX}/#\n" + self.extra_mqtt_acl)
        (mqtt_dir / "mosquitto.conf").write_text(
            "listener 1883\nallow_anonymous false\npassword_file /mosquitto/config/passwords\n"
            "acl_file /mosquitto/config/acl\npersistence true\n"
            "persistence_location /mosquitto/data/\nautosave_interval 1\nuser root\n")
        (self.root / "conf/services/addons.cfg").write_text("binding=mqtt\n")
        things = (ROOT / "examples/openhab.things").read_text()
        things = things.replace("mqtt:broker:local", "mqtt:broker:fixture")
        # Reproduce D01 through the real binding as an independent legacy channel.
        legacy = ('        Type rollershutter : legacyPosition "Legacy mapping probe" [ '
                  'stateTopic="' + PREFIX + '/skylight_1/position", '
                  'commandTopic="' + PREFIX + '/owner-input/legacy", '
                  'up="UP", down="DOWN", stop="STOP", retained=false ]\n')
        at = things.rfind("}")
        things = things[:at] + legacy + things[at:]
        bridge = ('Bridge mqtt:broker:fixture "Disposable broker" '
                  '[ host="broker", port=1883, secure=false, username="fixture-openhab", '
                  'password="synthetic-openhab", clientID="fixture-openhab", '
                  'reconnectTime=1000 ]\n')
        (self.root / "conf/things/shades.things").write_text(bridge + things)
        (self.root / "conf/items/shades.items").write_text((ROOT / "examples/openhab.items").read_text() +
            '\nRollershutter Fixture_LegacyMapping { channel="mqtt:topic:dooya_skylight_1:legacyPosition", autoupdate="false" }\n')
        run("docker", "network", "create", self.network)
        self.started.append("network")
        # Stream files through stdin: daemon and CLI may not share host paths.
        (mqtt_dir / ".fixture-ready").write_text("synthetic\n")
        (self.root / "conf/.fixture-ready").write_text("synthetic\n")
        for user, password in self.extra_mqtt_users:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", password):
                raise ValueError("Synthetic broker credential format invalid")
        extra_passwords = "".join(f"mosquitto_passwd -b /mosquitto/config/passwords {user} {password} && " for user,password in self.extra_mqtt_users)
        broker_command = (
            "while [ ! -f /mosquitto/config/.fixture-ready ]; do sleep .1; done; "
            "mosquitto_passwd -b -c /mosquitto/config/passwords fixture-openhab synthetic-openhab && "
            "mosquitto_passwd -b /mosquitto/config/passwords fixture-writer synthetic-writer && "
            "mosquitto_passwd -b /mosquitto/config/passwords fixture-observer synthetic-observer && "
            + extra_passwords + "exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf")
        run("docker", "create", "--name", self.broker, "--network", self.network,
            "--network-alias", "broker", "--memory", "96m", "--cpus", ".25",
            "--pids-limit", "64", "--user", "0", "--entrypoint", "sh",
            "-p", f"127.0.0.1:{self.mqtt_port}:1883", MQTT_IMAGE, "-c", broker_command)
        self.started.append(self.broker)
        run("docker", "start", self.broker)
        install_tree(self.broker, mqtt_dir, "/mosquitto/config")
        run("docker", "create", "--name", self.openhab, "--network", self.network,
            "--memory", "2g", "--cpus", "1", "--pids-limit", "384",
            "-p", f"127.0.0.1:{self.http_port}:8080",
            "-e", "EXTRA_JAVA_OPTS=-Xmx768m -Duser.timezone=UTC",
            "--entrypoint", "sh", OH_IMAGE, "-c",
            "while [ ! -f /openhab/conf/.fixture-ready ]; do sleep .1; done; "
            "exec /entrypoint su-exec openhab tini -s ./start.sh")
        self.started.append(self.openhab)
        run("docker", "start", self.openhab)
        install_tree(self.openhab, self.root / "conf", "/openhab/conf")
        wait_for(lambda: self.http("/items/" + ITEM), "actual MQTT binding and linked Item", 300)
        # Preserve the maintained Earthship qualification bootstrap pattern:
        # scripts/qualify-persistence-file-provider.py and qualify-tplink-transport.py.
        # stdin must stay open for Karaf; EOF otherwise closes the console session.
        client = ["docker", "exec", "-i", self.openhab, "/openhab/runtime/bin/client",
                  "-h", "127.0.0.1", "-u", "openhab", "-p", "habopen", "-r", "5", "-d", "2"]
        def console(command):
            return run(*client, command, input="\n", check=False, timeout=20)
        # First boot may briefly expose then reload commands while addons install.
        # Existing qualification profiles wait 20 seconds after REST startup too.
        time.sleep(20)
        def admin_ready():
            listed = console("openhab:users list")
            if listed.returncode == 0 and "fixture-admin (administrator)" in listed.stdout:
                return True
            console("openhab:users add fixture-admin synthetic-admin administrator")
            listed = console("openhab:users list")
            return listed.returncode == 0 and "fixture-admin (administrator)" in listed.stdout
        wait_for(admin_ready, "disposable administrator after addon startup", 180)
        def token_ready():
            console("openhab:users rmApiToken fixture-admin bindingfixture")
            result = console("openhab:users addApiToken fixture-admin bindingfixture ''")
            tokens = list(set(re.findall(r"oh\.[A-Za-z0-9._-]+", result.stdout)))
            if result.returncode == 0 and len(tokens) == 1:
                self.auth = tokens[0]
                return True
            return False
        wait_for(token_ready, "disposable API token after command registration", 120)
        self.observer = self.probe()
        self.observer.subscribe(PREFIX + "/#")
        self.writer = self.probe("fixture-writer", "synthetic-writer")
        self.writer.publish("service/availability", "online")
        self.writer.publish("bridge/availability", "online")
        self.writer.publish("skylight_1/availability", "online")
        self.writer.publish("service/state", '{"session_id":"synthetic-current"}')
        self.writer.publish("bridge/state", '{"session_id":"synthetic-current"}')
        self.writer.publish("skylight_1/state", '{"schema":"dooya-observation/v2","session_id":"synthetic-current","position_eligible":true}')
        self.writer.publish("skylight_1/position", "41")
        wait_for(lambda: self.thing_status() == "ONLINE" and self.state() == "41", "initial linked observation after addon provisioning", 300)
        self.observer.drain()

    def qualify(self):
        legacy_payloads = {}
        for command, expected in [("UP", "0"), ("DOWN", "100")]:
            self.observer.drain()
            self.http("/items/Fixture_LegacyMapping", command, "POST")
            messages = []
            def legacy_seen():
                messages.extend(self.observer.drain())
                return any(m["topic"] == PREFIX + "/owner-input/legacy" for m in messages)
            wait_for(legacy_seen, "legacy mapping reproduction", 10)
            matching = [m for m in messages if m["topic"] == PREFIX + "/owner-input/legacy"]
            assert len(matching) == 1 and matching[0]["payload"] == expected, matching
            legacy_payloads[command] = expected
        self.record("D01_real_binding_legacy_up_down_emit_numeric_endpoints", published=legacy_payloads)
        payloads = []
        for command in ("UP", "DOWN", "STOP", "0", "25", "50", "75", "100"):
            self.observer.drain()
            self.http("/items/" + ITEM, command, "POST")
            received = []
            def seen():
                received.extend(self.observer.drain())
                return any(m["topic"] == PREFIX + "/owner-input/skylight_1" for m in received)
            wait_for(seen, "actual binding publication " + command, 10)
            requests = [m for m in received if m["topic"].startswith(PREFIX + "/owner-input/")]
            assert len(requests) == 1 and requests[0]["payload"] == command and not requests[0]["retain"], requests
            time.sleep(.25)
            assert self.state() == "41", "autoupdate falsely changed observed position"
            assert not any(m["topic"].endswith("/command") for m in received), received
            payloads.append({"command": command, "published": requests[0]})
        self.record("T19_T20_binding_mapping_and_no_optimistic_position", payloads=payloads,
                    adapter_percentage_gate_exercised=False,
                    boundary="unsigned owner input only; does not enter adapter dispatch")
        self.observer.drain()
        self.writer.publish("skylight_1/position", "25")
        wait_for(lambda: self.state() == "25", "incoming position update", 10)
        time.sleep(.8)
        messages = self.observer.drain()
        assert not any("/owner-input/" in m["topic"] or m["topic"].endswith("/command") for m in messages), messages
        self.record("T23_feedback_updates_without_command_echo", observed_native_position=25)
        # Read-only broker identity cannot publish requests or motor-path commands.
        self.observer.drain()
        self.observer.client.publish(PREFIX + "/owner-input/skylight_1", "UP", qos=1)
        self.observer.client.publish(PREFIX + "/skylight_1/command", "UP", qos=1)
        time.sleep(.8)
        assert self.observer.drain() == [], "read-only principal published a write"
        self.record("read_only_acl_refuses_owner_and_motor_topic_writes")
        # Adapter-process stand-in: an actual MQTT connection with retained LWT,
        # killed without DISCONNECT. It owns no UDP socket or physical endpoint.
        code = '''import paho.mqtt.client as m,sys,time
c=m.Client(m.CallbackAPIVersion.VERSION2,client_id="synthetic-adapter-lwt")
c.username_pw_set("fixture-writer","synthetic-writer")
c.will_set(sys.argv[2]+"/service/availability","offline",qos=1,retain=True)
c.connect("127.0.0.1",int(sys.argv[1]),keepalive=2)
c.loop_start()
while not c.is_connected(): time.sleep(.01)
c.publish(sys.argv[2]+"/service/availability","online",qos=1,retain=True).wait_for_publish()
print("READY",flush=True)
while True: time.sleep(1)
'''
        child = subprocess.Popen([sys.executable, "-u", "-c", code, str(self.mqtt_port), PREFIX], stdout=subprocess.PIPE, text=True)
        try:
            assert child.stdout.readline().strip() == "READY"
            start = time.monotonic()
            child.kill()
            child.wait(timeout=5)
            wait_for(lambda: self.thing_status() == "OFFLINE", "service LWT makes Thing offline", 15)
            elapsed = time.monotonic() - start
            wait_for(lambda: self.state(ITEM + "_ServiceAvailable") == "OFF",
                     "service liveness Item changes OFF", 10)
            late = self.probe()
            late.subscribe(PREFIX + "/#")
            snapshot = {m["topic"]: m for m in late.drain()}
            assert snapshot[PREFIX + "/service/availability"]["payload"] == "offline"
            assert snapshot[PREFIX + "/skylight_1/availability"]["payload"] == "online"
            assert snapshot[PREFIX + "/skylight_1/position"]["payload"] == "25"
            assert not any("/owner-input/" in t or t.endswith("/command") for t in snapshot)
            self.record("T21_abrupt_adapter_loss_composite_unavailable", lwt_to_thing_offline_seconds=round(elapsed,3),
                        historical_shade_online_and_position_retained=True, thing_status=self.thing_status(),
                        service_item=self.state(ITEM + "_ServiceAvailable"),
                        bridge_item=self.state(ITEM + "_BridgeAvailable"),
                        historical_shade_item=self.state(ITEM + "_Available"))
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
        # v1 stale observations and retained state cannot be a v2 actuation request.
        self.writer.client.publish("earthship/shades/v1/skylight_1/command", "UP", qos=1, retain=True).wait_for_publish(timeout=5)
        self.writer.client.publish("earthship/shades/v1/skylight_1/position", "100", qos=1, retain=True).wait_for_publish(timeout=5)
        run("docker", "stop", "-t", "5", self.broker)
        run("docker", "start", self.broker)
        wait_for(lambda: self.writer.client.is_connected(), "writer reconnect after broker restart", 30)
        late = self.probe()
        late.subscribe(PREFIX + "/#")
        snapshot = {m["topic"]: m for m in late.drain()}
        assert snapshot[PREFIX + "/service/availability"]["payload"] == "offline"
        assert snapshot[PREFIX + "/skylight_1/position"]["payload"] == "25"
        assert not any("/owner-input/" in t or t.endswith("/command") for t in snapshot)
        recovery_states = []
        def recovered_unavailable():
            status = self.thing_status()
            service = self.state(ITEM + "_ServiceAvailable")
            recovery_states.append({"thing": status, "service": service})
            assert service != "ON", "historical service liveness reactivated on broker reconnect"
            return status == "OFFLINE" and service == "OFF"
        wait_for(recovered_unavailable, "retained service offline reconciled after broker restart", 30)
        self.record("T22_broker_restart_and_late_subscriber_no_request_replay", retained_v2_topics=sorted(snapshot),
                    old_schema_remains_separate=True, current_service_offline=True, recovery_states=recovery_states)
        items = self.http("/items")
        assert len([i for i in items if i["name"] == ITEM]) == 1
        assert not any(i["name"].startswith("Shade_Skylight2") for i in items)
        self.record("T33_one_synthetic_shade_no_blanket_inventory", configured_shades=1,
                    uncommissioned_logical_slots=25, hardware_commissioned=0)

    def save_and_cleanup(self, error=None):
        try:
            self.artifacts.mkdir(parents=True, exist_ok=True)
            for name in (self.openhab, self.broker):
                if name in self.started:
                    log = run("docker", "logs", name, check=False)
                    (self.artifacts / (name + ".log")).write_text(log.stdout + log.stderr)
                    if name == self.openhab:
                        for filename in ("openhab.log", "events.log"):
                            runtime_log = run("docker", "exec", name, "cat", "/openhab/userdata/logs/" + filename, check=False)
                            (self.artifacts / (name + "." + filename)).write_text(runtime_log.stdout)
            report = {"synthetic_only": True, "hardware_commissioned": False,
                      "source_revision": run("git", "-C", str(ROOT), "rev-parse", "HEAD").stdout.strip(),
                      "source_sha256": self.source_hashes,
                      "images": {image: json.loads(run("docker", "image", "inspect", image).stdout)[0]["RepoDigests"]
                                 for image in (OH_IMAGE, MQTT_IMAGE)},
                      "resources": {"openhab": {"memory": "2g", "cpus": 1, "pids": 384, "heap": "768m"},
                                    "mosquitto": {"memory": "96m", "cpus": .25, "pids": 64}},
                      "bind_addresses": "127.0.0.1 only; dedicated disposable Docker bridge",
                      "scenarios": self.results, "passed": error is None,
                      "error": None if error is None else str(error)}
            if self.openhab in self.started:
                report["openhab_runtime"] = self.http("/") if error is None else "see container log"
                report["mosquitto_runtime"] = run("docker", "exec", self.broker, "mosquitto", "-h", check=False).stdout.splitlines()[:2]
            (self.artifacts / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        finally:
            for probe in self.probes:
                probe.close()
            for name in reversed(self.started):
                if name != "network":
                    run("docker", "rm", "-f", "-v", name, check=False)
            if "network" in self.started:
                run("docker", "network", "rm", self.network, check=False)
            # Keep synthetic filesystem for failure diagnosis; no installation secrets.
            print("Artifacts:", self.artifacts, "fixture config:", self.root, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="explicitly start disposable containers")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required; no containers started")
    fixture = Fixture(args.artifacts)
    error = None
    try:
        fixture.start()
        fixture.qualify()
    except BaseException as exc:
        error = exc
        raise
    finally:
        fixture.save_and_cleanup(error)


if __name__ == "__main__":
    main()
