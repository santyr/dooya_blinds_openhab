# Codex continuation handoff

## Read order

AGENTS.md, README.md, PROJECT.md, PROTOCOL.md, MQTT.md, DEPLOYMENT.md; HUBITAT_REVIEW.md, VALIDATION.md; then src/ and tests/. Read ../HT_Simple_Touch_openHAB_Project_Plan.md for the full plan; its final implementation-baseline section identifies deferred features.

## What is authorized / implemented

The user requested this repository's documentation and coding that can be completed before hardware arrives. Code here provides WLAN v1.03 primitives, read-only discovery/cache inspection, MQTT command handling, raw report preservation, stale-state handling, and deployment templates. It is not commissioned or installed in the household. No movement tests have been run on real shades.

Run `python3 -m venv .venv`, `.venv/bin/pip install -e '.[test]'`, `.venv/bin/pytest`. Test failures must be resolved before deployment. Use existing dependencies; the implementation does not require Home Assistant or Hubitat.

## Next coding priorities

1. Inspect actual hardware replies with the read-only CLI. Check ID formats, msgID echo/source-port behavior, protocol version, token rotation, and error responses against the PDF.
2. Validate multicast reports and cached acknowledgements separately. If firmware does not echo msgID, add a documented, strictly matched compatibility mode with tests; do not disable all correlation.
3. Validate wirelessMode and percentage support. Current service only accepts position targets for configured percentage=true plus observed wirelessMode=1. Mode 4 virtual percentages and TDBU require deliberate extensions.
4. Implement command completion correlation using a post-command Report and tolerance, with a measured travel timeout. Current events expose acknowledged/uncertain but do not claim completed. A later unrelated Report must not blindly complete an old command.
5. Add a structured command envelope with IDs/expiry if robust end-to-end deduplication is needed. Simple MQTT commands currently use QoS 0, no retention, no persistent session, bounded queue, and no automatic retries/replay.
6. Test under a real broker and bridge. Unit/socket tests cannot certify RF or MQTT reconnect behavior.
7. Add qualified voltage scaling and chemistry-aware battery estimation only after label/telemetry validation. Raw batteryLevel is available now.
8. Add favorite recall/group policies/manual-hold rules and Earthship UI only after everyday control passes. No limit programming or thermal policy currently implemented.

## Deployment handoff prompt

“Continue santyr/dooya_blinds_openhab. Read AGENTS.md and all current docs. Inspect the actual Linux host, openHAB version, broker and shade/bridge inventory before editing deployment configuration. Run tests, then use discover/inspect without moving shades. Record sanitized compatibility results. Implement remaining gaps based on observed protocol responses. Enable actuation only during authorized supervised commissioning. Validate physical open/closed direction, stop, percentages, remote updates, stale state, internet isolation and restart behavior. Update docs with measured results; distinguish simulated from hardware tests. Do not commit credentials, re-pair/reset motors or change travel limits as a diagnostic shortcut.”

## Known operational limits

One configured bridge and one running adapter per host/interface are supported. The UDP operation in progress may delay STOP up to the request timeout; it is not an emergency-stop system. Availability reflects recent reports, not proof of mechanical safety. Acknowledgement does not prove motion. Offline detection is deliberately conservative. The daemon does not extrapolate moving position. Broker restart and Linux packaging must still be tested at the installation.
