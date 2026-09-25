# Dooya shades for openHAB

Local Linux/Raspberry Pi service connecting a Dooya Connector bridge to openHAB over MQTT. Intended for HT Simple Touch rechargeable skylight shades, without the Matter motor upgrade.

**Status: initial implementation, tested with simulated protocol responses only. No physical HT motor or bridge has been tested.** P-Box DD7006 is the primary target; DD1554E Mini compatibility remains a commissioning check. HT uses the standard Dooya Connector app.

## Start here

- [Project and remaining work](docs/PROJECT.md)
- [Codex handoff](docs/CODEX_HANDOFF.md)
- [Protocol and sources](docs/PROTOCOL.md)
- [MQTT/openHAB contract](docs/MQTT.md)
- [Deployment and commissioning](docs/DEPLOYMENT.md)
- [Updated project plan](HT_Simple_Touch_openHAB_Project_Plan.md) — scope, research, acceptance criteria and current implementation baseline.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
cp examples/config.toml config.toml
# Edit config and install the bridge key / MQTT password files first.
.venv/bin/dooya-openhab --config config.toml discover
# Read-only bridge-cache query (does not move motors):
.venv/bin/dooya-openhab --config config.toml inspect
# Movement commands remain disabled until allow_commands = true.
.venv/bin/dooya-openhab --config config.toml serve
```

Requires Python 3.11+, a configured Connector bridge, its 16-byte app key, and an MQTT broker for service mode. Initial bridge and shade pairing stays in Connector. No Home Assistant installation or extra Pi is necessary if an existing Linux host is available.

The service uses published WLAN v1.03 JSON/UDP messages directly. It deliberately preserves missing values and distinguishes cached responses from unsolicited reports. No calibration, pairing, factory reset, firmware update, or thermal actuation is implemented.
