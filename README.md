# Dooya shades for openHAB

Local service connecting a Dooya Connector P-Box to openHAB over MQTT. Intended for HT Simple Touch rechargeable skylight shades, without the Matter motor upgrade. **Deployment target: the existing Ubuntu Server host that runs openHAB.**

**Status: v0.2.0 pre-deployment software. Actual disposable MQTT/OpenHAB and signed end-to-end tests use a simulated bridge; no physical HT motor or bridge has been tested. Hardware is not installed yet. Automatic movement remains unreleased.** The selected bridge is the **DD7066 P-Box**, distinct from DD7066M and DD7006. Physical label, firmware and motor compatibility remain commissioning checks. HT uses the standard Dooya Connector app.

## Start here

- [Project and remaining work](docs/PROJECT.md)
- [Codex handoff](docs/CODEX_HANDOFF.md)
- [Validation record and baseline](docs/VALIDATION.md)
- [Actual signed-stack qualification](docs/DD7066_SIGNED_STACK.md)
- [DD7066 compatibility evidence](docs/DD7066_COMPATIBILITY.md)
- [Unsent vendor clarification request](docs/DD7066_VENDOR_REQUEST.md)
- [Protocol and sources](docs/PROTOCOL.md)
- [Hubitat driver source review](docs/HUBITAT_REVIEW.md)
- [MQTT/openHAB contract](docs/MQTT.md)
- [MQTT broker check and conditional setup](docs/MQTT_BROKER.md)
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

Requires Python 3.11+, a configured Connector bridge, its 16-byte app key, and an MQTT broker for service mode. Initial bridge and shade pairing stays in Connector. The adapter runs as a separate systemd service on the openHAB Ubuntu host; no additional Pi or Home Assistant installation is planned. The MQTT broker may be local or elsewhere on the LAN.

The service uses published WLAN v1.03 JSON/UDP messages directly. It deliberately preserves missing values and distinguishes cached responses from unsolicited reports. No calibration, pairing, factory reset, firmware update, or thermal actuation is implemented.
