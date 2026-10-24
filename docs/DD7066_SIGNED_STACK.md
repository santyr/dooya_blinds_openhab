# Disposable actual signed-stack verification

Run explicitly from the adapter's installed test environment:

```sh
python tests/integration/run_signed_stack.py --run \
  --ui-root /path/to/current/earthship-ui \
  --artifacts /tmp/dd7066-signed-stack-evidence
```

Use the exact OpenHAB 5.2.3/Mosquitto 2.0.18 images described in [the binding profile](../tests/integration/README.md). The runner reuses that maintained container bootstrap and resource limits, then starts the actual Python adapter Service and a Node process importing the current Earthship execution runtime, execution owner, durable owner ledger and observation consumer. The publisher subprocess is the actual `dooya_openhab.owner_publish` module with its real Paho MQTT client. Dedicated synthetic owner and adapter broker principals have separate ACLs; a read-only observer cannot publish actuator requests.

All credentials, commissioning records, device identities and captures are synthetic. The temporary owner state directory is mode 0700 and keys/profiles/journals are private files. Neither repository's deployed configuration, services, broker, OpenHAB, motors nor private owner profile is accessed. The test-only manual-enabled adapter configuration exists only inside the disposable fixture; production defaults and the actual Service's hard-coded automatic release gate remain disabled.

The fake bridge binds only an ephemeral loopback UDP port. Requests traverse the actual UDPTransport, Connector, AES authentication, source/message/device matching, State capture, signed queue, durable receipts and MQTT publication. The report reader uses the actual ReportSocket receive/decode/source-filtering implementation; only its constructor is replaced to bind loopback instead of joining LAN multicast. Therefore the profile does **not** qualify multicast routing, Ethernet/Wi-Fi, real DD7066 firmware, RF delivery, position polarity, travel limits or physical actuation.

The fixture contains 26 synthetic devices: 23 supported report-capable profiles, one one-way profile, one nonresponding endpoint and one TDBU family. It creates 26 actual OpenHAB MQTT Things and linked Items from the maintained examples. Before the actual producer starts, setup initializes every late-added shared-topic health handler to explicitly unavailable. Unknown channel state is never guessed online. Real producer Reports then travel through MQTT and the actual binding into the current UI observation parser, including the expected opaque source digest and commissioning epoch computed by the actual Node public-installation producer.

The profile exercises:

- Actual UDP default refusal of a missing discovery ID, plus the explicit pinned discovery compatibility profile, without sending movement.
- Fair first-pass cache coverage, wrong-MAC Ack rejection, no position qualification from cache Acks, native 0/25/50/75/100 and unsupported/unreachable members.
- Real duplicate/older Reports without timestamp renewal; old retained observations remain historical after the actual adapter restarts.
- Actual Node authentication and durable identity reservation, HMAC signing, one-shot owner publisher, broker delivery, actual Service admission and loopback UDP dispatch. Percentage/open/close results are observed through real binding feedback; target observed retains unknown causal actor.
- Full per-shade command-result receipts reaching the feedback-only OpenHAB String channel, separately from position evidence.
- Unsigned/forged messages, expired signatures, wrong source context, owner identity deduplication and actual repeated signed MQTT delivery without movement replay.
- Signed policy acknowledgement through the actual Node/runtime/publisher/adapter chain. The fixture has no household JDBC policy receipt, so unavailable policy remains inhibited; manual permission is independent of automatic preference.
- Even a synthetically signed release-shaped policy cannot open the actual Service's global automatic gate. Zone/override policy transitions publish acknowledgements without moving anything.
- A lost Ack after the endpoint accepts a test motion, duplicate Report traffic, a distinct explicit STOP, uncertainty and no movement retry after actual scheduled rediscovery. Measured latency is a software-fixture observation, never an emergency-stop guarantee.
- SIGKILL of the actual adapter process, its real LWT, retained per-shade online/history, consumer inhibition, actual broker restart and late subscriptions without command replay.

The JSON result records actual scenario outcomes, loaded source hashes, exact boundaries and packet counts. Source hashes are captured before process import and compared again at completion; changing a consumed source during the run invalidates the result. Container/runtime identities and enforced limits are retained in the accompanying binding-bootstrap artifacts. Test processes, containers, anonymous volumes and networks are removed even on failure; private synthetic diagnostic files remain outside Git.

The Node fixture calls the actual authenticated owner through a private stdin harness; it does not claim to test the browser's HTTP authentication/origin checks. Those have separate Node/browser regressions. Likewise this fixture does not replace fake-clock queue/race/resource tests or actual hardware commissioning. No result authorizes unattended automatic actuation.
