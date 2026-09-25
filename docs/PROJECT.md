# Project scope and delivery plan

Updated 2026-09-25. Owner: Sat Antyr.

## Decisions

Use standard HT Simple Touch shades; no Matter motor upgrade. Treat Dooya as the OEM. DV24WE/S is a likely motor, not a verified part number. HT uses Dooya's standard Connector app. The owner selected the P-Box on 2026-09-25. DD1554E Mini is background research only and is outside the initial deployment target. USB on the Mini supplies power; the documented integration uses Wi-Fi, not USB data.

The desired daily-use functionality is individual/group open, close, stop, percentage targets, reported final position, external-remote synchronization and available battery telemetry. Home automation and thermal policy remain in openHAB. Run the adapter as a separate service on the **same existing Ubuntu Server host as openHAB**. The user confirmed this deployment target on 2026-09-25; no Raspberry Pi is planned.

## Implementation decision

The initial research proposed wrapping motionblinds. Inspection showed its parser may synthesize a missing currentPosition and its high-level state combines cached responses with fresh reports. A small independent WLAN v1.03 implementation is selected for this first version to retain raw fields and their provenance. Existing motionblinds and Hubitat drivers remain reference implementations. This increases the need for explicit firmware testing; it is not a claim of broader compatibility.

## Delivery phases

1. **Foundation (this repository):** protocol/authentication, strict configuration, read-only diagnostics, MQTT daemon, sample openHAB mapping, simulated tests, runbook.
2. **One shade:** record model/firmware; validate key, discovery, ReadDevice, operation 5 query, Report, endpoints, stop, percentages, remote updates, battery units.
3. **Resilience:** broker/bridge/service restarts, WAN isolation including bridge reboot, multicast loss, stale reports, command timeouts, RF loss. Test that old commands never replay.
4. **Whole installation:** stable device IDs, RF placement, group command staggering, per-shade capability inventory and charging observations.
5. **Thermal automation:** inventory existing temperature/sun/weather Items; explicit manual hold and automation modes; shadow decisions first, then one-zone trial. No thermal commands yet.

## Required household inputs

Shade count/names and RF device IDs; motor, battery and bridge labels; firmware; installed Ubuntu/Python and openHAB versions; broker location/auth; Ubuntu host LAN interface/IP; chosen service installation path. The host is already chosen. Installer quote is $425 per rechargeable motorized skylight shade installed; remote, hub and solar inclusion are unconfirmed. No additional purchases are implied by this repository.

## Charging

The contemplated panel advertises 5V USB-C, 3.6W/720mA. Verify the battery pack's charging label and regulator requirements before connecting it. A 12V motor supply is not a 12V USB charging input. Test one installed panel for cable reach, glazing/light exposure and clearance. Telemetry may only expose pack voltage, not charging power or accurate charge percentage.

## Fallback

ESP32 + CC1101 + ESPHome can be investigated if local bridge access fails. The 40-bit Dooya remote article demonstrates remote commands and receiving remote presses, not complete Radio+ motor position/battery feedback. Do not replace confirmed motor state with timed estimates without an explicit design decision.
