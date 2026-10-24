# Disposable MQTT/OpenHAB binding evidence

This profile qualifies the installed-version **software binding contract**, using synthetic state and isolated containers. It accesses no production broker/OpenHAB and sends no physical-device packets. Actual hardware observation, supervised movement and automatic release remain separate.

Run [the maintained profile](../tests/integration/README.md) from the adapter test environment. The runner records its starting source hashes, image identities, runtime versions, exact payloads, scenario outcomes and LWT-to-Thing-offline latency into the requested external artifact directory. Final verified-run evidence belongs in the audit validation/handoff record alongside its source revision; a profile's existence alone is not a pass.

The corrected template uses `on="UP"`, `off="DOWN"`, `stop="STOP"`, non-retained publications and Rollershutter `autoupdate="false"`. The legacy-channel regression verifies the original `up`/`down` properties emitted numeric endpoints through the actual binding. Native positions cross the binding unchanged; motor percentage capability remains a separate adapter/commissioning gate. The mapping follows the [official MQTT channel contract](https://www.openhab.org/addons/bindings/mqtt/).

Commands are **unsigned owner-input requests only**. A trusted manual request ledger/owner must independently validate user intent and policy, create expiring signed requests and obtain dispatch/results. Neither an Item command nor a raw MQTT binding payload is evidence of automatic/manual authorization or physical movement. The adapter must never subscribe to owner-input. Broker ACLs must give display principals read-only access; the profile exercises this using separate synthetic identities.

Service LWT directly gates the Thing. Explicit adapter, bridge, per-shade availability and session/source JSON channels support the consumer's composite decision. Abrupt adapter loss can leave retained per-shade availability online and a historical position intact; the profile deliberately proves that condition while the Thing becomes offline. Consumers must require all liveness flags and reconciled current-session observation eligibility. Merely retaining an old online value, receiving a cached position or reconnecting MQTT cannot restore actionability.

Broker restart and late subscriptions verify that non-retained owner requests are absent, the service remains offline, historical v2 observations persist, and retained v1 topics stay outside the v2 command path. This does not prove a stale retained v2 payload's schema/session eligibility; actual adapter and owner tests cover that semantic boundary. The fixture's single synthetic shade supplies limited T33 inventory evidence; full 26-slot UI behavior requires browser/integration tests.

## Executed verification, 2026-10-24

Command (exit **0**):

```sh
/tmp/dd7066-protocol-venv/bin/python tests/integration/run_openhab_mqtt.py --run --artifacts /tmp/dd7066-evidence-20261024/binding-final
```

The run used OpenHAB **5.2.3 Release Build**, Mosquitto **2.0.18**, and Paho MQTT **2.1.0**. Image digests: OpenHAB `sha256:fa50a6934dfd5bcbf21db18af66f2558f58c6c493ba23bdeb7a894e764507a99`; Mosquitto `sha256:d12c8f80dfc65b768bb9acecc7ef182b976f71fb681640b66358e5e0cf94e9e9`. Docker inspection confirmed the stated CPU/memory/PID limits, non-privileged containers and loopback-only published ports. Afterward no fixture containers or test networks remained.

Seven scenario groups passed:

- D01: original `up`/`down` properties actually published numeric `0`/`100` for UP/DOWN.
- T19/T20 binding seam: corrected mapping published exactly UP, DOWN, STOP, 0, 25, 50, 75 and 100; each request was non-retained, with observed Item position unchanged until feedback.
- T23: incoming position 25 updated the Item without generating an owner request or adapter command.
- ACL: a read-only principal could publish neither owner-input requests nor adapter command topics.
- T21 broker/binding seam: killing the actual synthetic MQTT client process without DISCONNECT produced retained LWT. The Thing went OFFLINE in **0.006 seconds** in this run; ServiceAvailable became OFF while bridge/per-shade Items remained ON and position 25 remained historical. This measured fixture result is not a latency guarantee or a test of terminating the actual adapter Service/UDP process.
- T22: broker restart and late subscription retained historical state/service offline but replayed no v2 owner request; v1 retained topics remained separate. **The Thing temporarily became ONLINE during reconnect while ServiceAvailable remained OFF**, then reconciled OFFLINE. Consumers must enforce explicit liveness and current-session evidence together; Thing status alone is insufficient.
- Limited T33 seam: one synthetic shade was configured; no other logical slot or physical capability was marked commissioned. Full UI partial/mixed-inventory behavior remains outside this profile.

External result artifact: `/tmp/dd7066-evidence-20261024/binding-final/result.json`, SHA-256 `244a7d2a6d70ab79053b06710dc0606e9476a3c7ab3a37602abd467219a78446`. The parent checkout was `88e09b098cc65ec9a528b3dba0234d39b41209e2` with the following uncommitted tested source hashes; each still matched after the run:

| File | SHA-256 |
|---|---|
| `tests/integration/run_openhab_mqtt.py` | `04290e7ea9c34993b40dd4366db44366c9bd1ccd81a916cc3cd7b0e7d94ad2b2` |
| `examples/openhab.things` | `80177dd08b7905be6e33a536b9c023ba0528126bb65b425d3d625cdafbd7658e` |
| `examples/openhab.items` | `98b804ac40defd97e873988306e691067572cfeaca863a207578bfc8a4b84acb` |

The profile does not exercise adapter percentage admission, signed owner policy/command envelopes, actual adapter termination, real motor movement or DD7066 firmware. Those require the actual-module/full-chain and staged commissioning tests. Early fixture startup failures were resolved before this final run; their counts are not additional passing tests.
