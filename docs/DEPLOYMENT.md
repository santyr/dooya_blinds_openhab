# Deployment and commissioning

## Prepare

Install on the **existing Ubuntu Server host that runs openHAB**, using Python 3.11+ and a separate native systemd service. Do not install a second openHAB instance or add a Raspberry Pi. Identify the Ubuntu host's LAN interface and P-Box subnet; ideally place them on the same subnet while qualifying multicast. Reserve the P-Box address in DHCP and keep the Ubuntu host's LAN address stable. Required network paths: Ubuntu host → P-Box UDP 32100; P-Box replies to ephemeral request ports; multicast 238.0.0.18:32101 to the Ubuntu host; adapter → MQTT broker. No inbound internet access is needed.

**Check the existing host before configuring the adapter:** confirm its Python version, network interface/address, openHAB version and MQTT Thing/broker configuration. Running openHAB on this host does *not* establish that the MQTT broker is on this host. Use `mqtt.host = "127.0.0.1"` only if the broker actually listens locally; otherwise set its real LAN hostname/IP. If openHAB's broker requires credentials or TLS, configure the adapter accordingly. Ensure the adapter can reach the P-Box over UDP and join the multicast group on the selected host interface. If the Ubuntu host has more than one LAN interface, set `bridge.interface_ip` to the address on the P-Box-facing interface.

Follow [MQTT_BROKER.md](MQTT_BROKER.md): inspect and reuse an existing suitable broker; if none is present, install and configure Mosquitto on this Ubuntu host, set up openHAB's MQTT broker Thing, and verify both clients against the same broker. The owner has authorized that conditional setup. Do the read-only MQTT connectivity and restart checks before enabling shade commands.

Pair bridge/shades in Connector and get the key from About (tap five times). Store the exact 16-byte key in a file readable only by the service account. Do the same for the MQTT password. Never paste keys into shell arguments, Git, issue logs or online crypto tools.

Suggested paths: repository/venv at /opt/dooya-blinds; configuration /etc/dooya-blinds/config.toml; credentials /etc/dooya-blinds/bridge.key and mqtt.password. These are proposed paths, not existing household locations. Create a dedicated unprivileged `dooya` account. Install `pip install .` into the venv and restrict config/credential ownership and permissions. Fill examples/config.toml locally. Preserve allow_commands=false initially.

Run discover then inspect; record device IDs and model/firmware where obtainable. Discovery is read-only and outputs network/device identifiers; sanitize before sharing. No bridge key/token is printed. Start serve in the foreground and check multicast reports and MQTT diagnostics. It requests status (operation 5) periodically but sends no motion while allow_commands=false.

The sample unit in deploy/ is a template. Adjust paths/user and install it only after inspecting the Ubuntu host. `systemctl daemon-reload` then `systemctl enable --now dooya-blinds` starts it. The adapter and openHAB are separate services on the same machine; the adapter's MQTT bridge does not require `openhab.service` to be running before it starts. Review journal output. Network failures should be visible as offline, without credentials in logs.

## Supervised movement gate

Set allow_commands=true only after the installer has set mechanical limits and a person is present to observe. Verify UP/DOWN correspond to uncovered/covered fabric, STOP during motion, and 25/50/75/0/100 targets. Inversion must be configured exactly once. Confirm state reflects a motor Report, not the command acknowledgement.

Check remote/app commands update openHAB. Record battery raw values against battery label, RF signal, travel time, typical report latency and model/firmware. Test every installed shade. Do not repeatedly cycle motors unnecessarily.

## Resilience acceptance

Test each independently: service restart; broker restart; bridge restart; network disconnect; RF-unreachable shade; multicast blocked; expired/stale state; WAN blocked including a bridge reboot. Confirm no retained/backlogged motion replays and that offline values become UNDEF. Local control must remain functional without WAN after provisioning before claiming cloud independence for this installation.

Multicast loss currently prevents fresh report confirmation; the service keeps cached snapshots separate and marks position stale. A cache-only polling compatibility fallback is NOT implemented as an equivalent substitute.

## Rollback

Disable household automation, stop/disable the daemon, and use the physical remote or app. Do not reset pairing or motor limits. Keep the previous tested package/config to revert software updates. Save credentials separately from configuration backups. Do not automatically update bridge firmware.

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
