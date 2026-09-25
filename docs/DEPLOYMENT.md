# Deployment and commissioning

## Prepare

Use Python 3.11+ on an always-on Linux host, including Raspberry Pi OS with a suitable Python version. Prefer a native systemd service. Place it on the bridge subnet initially. Give bridge and host DHCP reservations. Required network paths: host → bridge UDP32100; bridge replies to ephemeral request ports; multicast 238.0.0.18:32101 to host; MQTT to the selected broker. No inbound internet access is needed.

Pair bridge/shades in Connector and get the key from About (tap five times). Store the exact 16-byte key in a file readable only by the service account. Do the same for the MQTT password. Never paste keys into shell arguments, Git, issue logs or online crypto tools.

Suggested paths: repository/venv at /opt/dooya-blinds; configuration /etc/dooya-blinds/config.toml; credentials /etc/dooya-blinds/bridge.key and mqtt.password. These are proposed paths, not existing household locations. Create a dedicated unprivileged `dooya` account. Install `pip install .` into the venv and restrict config/credential ownership and permissions. Fill examples/config.toml locally. Preserve allow_commands=false initially.

Run discover then inspect; record device IDs and model/firmware where obtainable. Discovery is read-only and outputs network/device identifiers; sanitize before sharing. No bridge key/token is printed. Start serve in the foreground and check multicast reports and MQTT diagnostics. It requests status (operation 5) periodically but sends no motion while allow_commands=false.

The sample unit in deploy/ is a template. Adjust paths/user and install it only after inspecting the host. `systemctl daemon-reload` then `systemctl enable --now dooya-blinds` starts it. Review journal output. Network failures should be visible as offline, without credentials in logs.

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
| openHAB / broker / Linux versions | pending |
| Date and tester | pending |
