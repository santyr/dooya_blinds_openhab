# Deployment and commissioning

## Current preparation boundary (2026-10-24)

The operator confirms the shade hardware is **not installed yet**. This source
work has installed no service, private profile, MQTT ACL, OpenHAB mapping or motor
configuration in the house. OpenHAB 5.2.3-1 and Mosquitto 2.0.18 already exist on
the Ubuntu host. Two configured broker Things are ONLINE using authenticated
loopback connections; do not infer anonymous access or install a second broker.
Their actual credentials remain private and have not been copied into this repo.

The prepared adapter defaults to movement disabled and has no automatic-release
switch. A synthetic fixture enabling manual commands is not an installation
profile. Preserve immutable producer packages, old receipts, original observation
journals and source-bound replay; changing source does not upgrade a running house.

Before a separately authorized installation:

1. Record exact bridge label, firmware, motor family and supported operations,
   LAN interface and actual broker auth/TLS. Resolve the vendor questions in the
   **unsent** `DD7066_VENDOR_REQUEST.md`. No newest exact-model firmware API has
   been established. Keep strict echoed-ID discovery by default. Only explicitly
   select `pinned_discovery_without_id` when its narrowly documented compatibility
   risk is accepted: configured bridge pin, absent discovery msgID only, strict
   source/full inventory/token validation; read/write ACKs remain strict.
2. Create a private service-account-owned configuration/key/password directory;
   files 0600, directory 0700. Configure pinned bridge identity, durable message-ID
   high-water and receipt-journal paths. Preserve corrupt files for diagnosis;
   do not delete them to make readiness appear successful. Keep allow_commands=false.
3. Use separate authenticated MQTT principals/ACLs: adapter reads only signed owner
   inputs and writes telemetry/results/ACKs; owner publishes only command/policy;
   OpenHAB reads state/results and cannot bypass the owner. The sample unsigned
   owner-input topic is not a motion API. Do not expose credentials, key, RF IDs,
   publisher configuration or owner journal through Vite/web roots or logs.
4. Prepare the UI private `earthship-shade-owner-installation/v1` profile outside
   every served directory. Defaults are commandsEnabled=false and slots=[]; no
   publisher is spawned without explicit private configuration. Map each actual
   slot/zone/device, commissioned epoch, model/firmware/reference and operations,
   source-bound state/availability/provider Items, optional result Item, policy ACK
   Item, owner key and durable owner ledger. The publisher configuration is also
   private; its Python subprocess reads signed input on stdin and publishes once.
   Start from `examples/ui-owner-installation.json` and
   `examples/owner-publisher.toml`; both remain unusable for movement unchanged.
   The Node profile, HMAC key, owner journal and publisher TOML must share one
   process-account-owned 0700 directory; existing private files are 0600. The
   Python publisher requires its key and MQTT password as siblings in that same
   directory, with no symlinks or hard links. Vite requires strict file serving and
   rejects a private directory within any configured served root. The executable
   path must name the intended installed Python environment.
   A Basic manual-operator credential hash is separate from the OpenHAB REST
   credential, MQTT password, HMAC key and browser request fields.
5. Install optional provider-status history Items/collector only from explicit
   verified mappings. Its empty template has no runtime effects. Verify actual
   JDBC persistence/readback before claiming historical coverage; v1 readers remain
   available to their original histories. No current observation becomes a training
   qualification or proof of a shade's thermal effect.
6. Confirm all source-pair tests and scoped rollback below. Start with read-only
   discovery/observation, then seek supervised per-device movement authorization.
   No bridge probing or motor movement follows merely from installing these files.



## Prepare

Install on the **existing Ubuntu Server host that runs openHAB**, using Python 3.11+ and a separate native systemd service. Do not install a second openHAB instance or add a Raspberry Pi. Identify the Ubuntu host's LAN interface and P-Box subnet; ideally place them on the same subnet while qualifying multicast. Reserve the P-Box address in DHCP and keep the Ubuntu host's LAN address stable. Required network paths: Ubuntu host → P-Box UDP 32100; P-Box replies to ephemeral request ports; multicast 238.0.0.18:32101 to the Ubuntu host; adapter → MQTT broker. No inbound internet access is needed.

**Check the existing host before configuring the adapter:** confirm its Python version, network interface/address, openHAB version and MQTT Thing/broker configuration. Running openHAB on this host does *not* establish that the MQTT broker is on this host. Use `mqtt.host = "127.0.0.1"` only if the broker actually listens locally; otherwise set its real LAN hostname/IP. If openHAB's broker requires credentials or TLS, configure the adapter accordingly. Ensure the adapter can reach the P-Box over UDP and join the multicast group on the selected host interface. If the Ubuntu host has more than one LAN interface, set `bridge.interface_ip` to the address on the P-Box-facing interface.

Follow [MQTT_BROKER.md](MQTT_BROKER.md): inspect and reuse an existing suitable broker; if none is present, install and configure Mosquitto on this Ubuntu host, set up openHAB's MQTT broker Thing, and verify both clients against the same broker. The owner has authorized that conditional setup. Do the read-only MQTT connectivity and restart checks before enabling shade commands.

Pair bridge/shades in Connector and get the key from About (tap five times). Store the exact 16-byte key in a file readable only by the service account. Do the same for the MQTT password. Never paste keys into shell arguments, Git, issue logs or online crypto tools.

Suggested paths: repository/venv at /opt/dooya-blinds; configuration /etc/dooya-blinds/config.toml; credentials /etc/dooya-blinds/bridge.key and mqtt.password. These are proposed paths, not existing household locations. Create a dedicated unprivileged `dooya` account. Install `pip install .` into the venv and restrict config/credential ownership and permissions. Fill examples/config.toml locally. Preserve allow_commands=false initially.

Run discover then inspect; record device IDs and model/firmware where obtainable. Discovery is read-only and outputs network/device identifiers; sanitize before sharing. No bridge key/token is printed. Start serve in the foreground and check multicast reports and MQTT diagnostics. It requests status (operation 5) periodically. With allow_commands=false it refuses new movement; an authenticated, individually commissioned operator STOP can still pass the remaining capability/source/session/durability gates. The empty owner profile and absent commissioning records cannot authorize that STOP.

The sample unit in deploy/ is a template. Adjust paths/user and install it only after inspecting the Ubuntu host. `systemctl daemon-reload` then `systemctl enable --now dooya-blinds` starts it. The adapter and openHAB are separate services on the same machine; the adapter's MQTT bridge does not require `openhab.service` to be running before it starts. Review journal output. Network failures should be visible as offline, without credentials in logs.

## Supervised movement gate

Set allow_commands=true and release only individually commissioned manual operations after the installer has set mechanical limits and a person is present to observe. Verify UP/DOWN correspond to uncovered/covered fabric, STOP during motion, and 25/50/75/0/100 targets. Inversion must be configured exactly once. Confirm state reflects a motor Report, not the command acknowledgement. Setting allow_commands=true alone is insufficient: the private owner/manual release, current pinned identity/session, supported profile, per-device commissioning record, operation support and healthy durable journal must also pass. The automatic gate remains unreleased.

Check remote/app commands update openHAB. Record battery raw values against battery label, RF signal, travel time, typical report latency and model/firmware. Test every installed shade. Do not repeatedly cycle motors unnecessarily.

## Resilience acceptance

Test each independently: service restart; broker restart; bridge restart; network disconnect; RF-unreachable shade; multicast blocked; expired/stale state; WAN blocked including a bridge reboot. Confirm no retained/backlogged motion replays and that offline values become UNDEF. Local control must remain functional without WAN after provisioning before claiming cloud independence for this installation.

Multicast loss currently prevents fresh report confirmation; the service keeps cached snapshots separate and marks position stale. A cache-only polling compatibility fallback is NOT implemented as an equivalent substitute.

## Rollback

Keep automatic release disabled. Revoke the manual UI profile and signed owner publisher, then stop/disable the adapter only during an authorized maintenance step. Leave existing greywater/feeder/night-load owners unchanged. Use the physical remote or app where installed. Do not reset pairing or motor limits. Keep the previous tested UI/adapter pair and private configuration. Revert both contracts together; never point v1 consumers at v2 topics. Preserve the old journals/receipts as historical artifacts and start a new commissioned epoch for changed hardware. Do not replay retained or journaled requests. Save credentials separately from configuration backups. Do not automatically update bridge firmware.

## Compatibility record (fill during commissioning)

| Field | Result |
|---|---|
| Bridge model / firmware | pending |
| Motor / HT part number | pending |
| App / API protocol | pending |
| Report wirelessMode / type | pending |
| Percentage / remote reports | pending |
| Battery units / charging | pending |
| WAN-off + reboot | pending |
| STOP and full travel time | pending |
| Ubuntu / Python / openHAB / broker versions and broker location | pending |
| Date and tester | pending |
