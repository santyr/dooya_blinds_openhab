# Disposable actual-binding qualification

Run explicitly from the repository root in the adapter test environment:

```sh
docker pull openhab/openhab:5.2.3-debian
docker pull eclipse-mosquitto:2.0.18
python tests/integration/run_openhab_mqtt.py --run --artifacts /tmp/dd7066-binding-evidence
```

The profile uses real OpenHAB MQTT binding 5.2.3 and Mosquitto 2.0.18. It starts only named disposable containers on a dedicated Docker network, publishes host ports on `127.0.0.1`, and uses wholly synthetic state/accounts. No production snapshots, tokens, broker, OpenHAB configuration, services or DD7066 devices are accessed. Initial addon retrieval needs Internet access. Files are streamed through container stdin so a remote Docker CLI/daemon is supported. Containers, anonymous volumes and the test network are removed in `finally`; synthetic config and checksum-backed result/log artifacts remain outside Git.

Resource limits: OpenHAB 2 GiB, one CPU, 384 PIDs, 768 MiB Java heap; Mosquitto 96 MiB, 0.25 CPU, 64 PIDs. Authentication stays enabled: the fixture creates its own first administrator through the maintained in-container Karaf bootstrap pattern, and three synthetic MQTT principals have separate ACLs.

The runner loads the maintained `examples/openhab.things` and `.items`. An extra legacy channel reproduces the original `up`/`down` mapping defect. Assertions verify exact UP/DOWN/STOP and native 0/25/50/75/100 payloads, no optimistic position change, update-only feedback, read-only MQTT ACL denial, abrupt synthetic adapter-client termination/LWT, retained historical shade data, broker restart, late subscriptions and isolation from retained v1 topics. Result JSON records source hashes, image digests, exact runtime versions and observed latency.

Binding output goes only to `earthship/shades/v2/owner-input/<sid>`. It is an unsigned request, never an actuator authorization. The fixture intentionally has no trusted-owner signer, adapter UDP transport or motor endpoint. Adapter percentage gating, signed command/policy admission, current-session evidence, race handling and full UI mixed-inventory behavior require their own tests. The single synthetic shade does not commission the other 25 logical slots. No result from this profile establishes DD7066 compatibility, real position, physical STOP, or automatic release.

For the actual producer/consumer chain, run `run_signed_stack.py --run --ui-root /path/to/earthship-ui --artifacts /tmp/dd7066-signed-stack` in the installed adapter environment. It imports the actual Node owner/runtime and observation parser, invokes the real Python signed publisher and Service, and uses loopback UDP endpoints plus 26 actual OpenHAB bindings. [The signed-stack record](../../docs/DD7066_SIGNED_STACK.md) explains the exact boundaries and evidence. This extends the earlier binding-only/LWT-client profile; it does not reclassify that earlier stand-in as an actual Service test.
