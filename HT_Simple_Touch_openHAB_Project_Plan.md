# HT Simple Touch → openHAB integration plan

Version 1.2 · 25 September 2026 · Owner: Sat Antyr

## 1. Objective and decisions

Integrate standard HT Simple Touch rechargeable cellular skylight shades into the existing openHAB installation, with reliable local operation, percentage positioning, feedback, and eventual thermal automation. The Matter motor upgrade is excluded by the owner's decision.

Working hardware assumption: HT Simple Touch is a rebranded Dooya system. Proceed on that basis. DV24WE/S is the candidate motor family and DD7006-family P-Box is the candidate bridge; exact suffixes, firmware, and motor capabilities remain commissioning inputs, not reasons to delay software design.

Preferred implementation: a standalone Python service implementing the published Connector WLAN v1.03 subset directly, communicating with the bridge locally and exposing MQTT topics to openHAB. The motionblinds library remains a reference; its missing-position defaults and cache/report conflation are avoided in this adapter. Home Assistant is not required. Keep protocol details behind a small adapter so firmware differences can be accommodated without changing openHAB Items or rules.

Repository: https://github.com/santyr/dooya_blinds_openhab. Initial software and deployment templates are being implemented here. Hardware tests, installation, and final Item mappings remain outstanding. README.md and docs/CODEX_HANDOFF.md describe actual implementation status; feature targets below are not all implemented.

## 2. Evidence and confidence

| Finding | Basis | Planning consequence |
|---|---|---|
| Simple Touch has used Dooya motors | Supplier's 2024 guide explicitly identifies a 12V Dooya motor, includes skylight options and HT fabrics [S1] | Adopt Dooya as the working platform |
| HT internal motor identifiers exist | HT-authored guide identifies STP047 small and STP049 large [S2] | Record label photographs and cross-reference during installation |
| DV24WE/S supports bidirectional RF | Dooya product information identifies Bi-RF; catalog lists percentage control and status feedback [S3, S4] | Target feedback-based control |
| Local bridge integration is established | Home Assistant documents DD7006A support and local API-key retrieval [S5] | Start with existing protocol implementation |
| Standalone implementation exists | motionblinds Python library supports Dooya and local control [S6] | Use as an implementation reference; preserve raw report provenance in this project's adapter |
| Manufacturer API is obtainable | Dooya advertises local API documents upon request [S3] | Request exact firmware specification |

Not yet established: actual supplied motor model; exact P-Box variant; HT firmware compatibility; which battery fields are returned; whether speed/limits are configurable through the LAN API; whether local authentication survives extended internet isolation and restarts.

The price quote is $425 per motorized rechargeable skylight shade installed. Confirm remote, P-Box, solar panel, and any extra charging accessories separately. No additional hardware prices are assumed in this plan.

## 3. Scope and capability targets

| Capability | Target | Verification needed |
|---|---|---|
| Individual open/close/stop | Required | Physical travel and correct direction |
| Absolute 0–100% positioning | Required target | Actual motor/bridge supports it and endpoint mapping is correct |
| Reported position | Required target | Distinguish fresh motor report from bridge cache or estimate |
| Movement status and command outcome | Required | Determine report timing; acknowledgement alone is not completion |
| Remote/app state synchronization | Required target | Check reports after physical remote and app movement |
| Multiple shade groups | Required | Sequential dispatch; retain individual outcomes |
| Favorite position recall | Desired | Library and motor behavior |
| Battery voltage / percentage | Desired | Raw units, scaling, freshness, and source |
| Charging indication and RF strength | Optional | Expose only if meaningful reports exist |
| Speed and travel-limit changes | Commissioning extension | Official API support; exclude from ordinary automation |
| Tilt / TDBU | Outside initial skylight scope | Do not implement unused motor functions |

Full daily-use functionality is the goal. If feedback or percentage commands are unavailable on the actual motor, document that as a material capability gap rather than silently replacing it with estimated state.

## 4. Architecture and ownership

Data path: openHAB MQTT binding ⇄ local MQTT broker ⇄ Python shade service ⇄ P-Box LAN API ⇄ motor RF.

The service owns discovery, authentication, RF-device identifiers, protocol normalization, command scheduling, state freshness, and telemetry. openHAB owns household preferences, schedules, manual holds, thermal decisions, UI, and history. The P-Box owns motor pairing and RF transport. Physical remotes remain usable independently.

Run the adapter as a separate systemd service on the **same existing Ubuntu Server host as openHAB**, as confirmed by the owner on 2026-09-25. The source lives in `santyr/dooya_blinds_openhab`. Before deployment, check the Ubuntu host's Python/openHAB versions, LAN interface, and MQTT broker location and credentials. The broker might run on this host or elsewhere; do not infer its location from openHAB's location. The owner authorized Codex to check the host and set up a broker if needed. Reuse a suitable existing broker or install a local Mosquitto broker and connect both services to it as described in `docs/MQTT_BROKER.md`. Actual host state remains unknown until access is available.

Give the P-Box a DHCP reservation. Prefer Ethernet when available. Initially place the Ubuntu host and P-Box on the same subnet; verify multicast and direct-IP operation. Do not expose the API or MQTT publicly. The located v1.03 specification documents UDP 32100 for commands and multicast 238.0.0.18:32101 for reports; verify response behavior against the actual P-Box firmware.

## 5. API specification and first connection

The first email to info@dooya.com bounced. Do not use it again. The later user action records the request as sent; no reply is available yet. The final recipient list is not confirmed here. We subsequently located the Connector WLAN Integration Protocol PDF (v1.03) in the Hubitat driver repository; implementation no longer depends on an email response.

Published alternatives: developer@dooya.com and support@shadeconnector.com in the Connector Google Play listing [S9]. These are published contacts, not deliverability-tested mailboxes. HT lists sales@richview.com and 800-879-9512 [S10].

Request the local LAN/WLAN protocol specification for the delivered model/firmware, authentication/key provisioning, discovery, reads/writes, unsolicited reports, error codes, battery units, speed/limit support, and behavior without internet access. Ask whether HT firmware is identical at the local API layer.

A community repository also hosts documentation described by its maintainer as manufacturer-provided, for DD7002B [S7]. Inspect its documents as background; do not assume that older protocol version is identical to the P-Box.

Commissioning sequence:

1. Photograph bridge and motor labels; record model, firmware, app name/version, battery pack, and charging label.
2. Have the installer set correct mechanical travel limits and demonstrate reliable remote control before automation testing.
3. Pair one shade to the P-Box through the standard Connector app.
4. Try Settings → About → tap five times, the documented Connector API-key method [S5]. HT uses the standard Dooya Connector app, not a separate HT-branded application.
5. Store the key in a restricted local credential file. Preserve its characters exactly; redact it from logs and issue reports.
6. Use a read-only diagnostic command to discover the bridge and shade. Save sanitized responses as fixtures.
7. Test commands on one supervised shade, then test reports from the remote and app.
8. Test local operation with bridge internet access blocked, including bridge and service restarts. Record whether the app is needed only for provisioning.

Do not factory-reset equipment or change limits as part of discovery. If authentication fails, capture the response and firmware version, then compare the protocol implementation and manufacturer specification before changing token handling.

## 6. Proposed software components

| Component | Responsibility |
|---|---|
| `protocol_adapter` | Implement documented UDP/AES subset; discovery, cached reads, movement commands, multicast reports |
| `inventory` | Stable local shade IDs mapped to bridge/device identifiers; capabilities |
| `state_store` | Last reports, observed times, freshness, last command, supported fields |
| `command_worker` | Validate and serialize commands; bounded retries; STOP priority |
| `mqtt_adapter` | Publish state/availability; accept non-retained commands |
| `diagnostics` | Read-only discovery, sanitized snapshots, compatibility report |
| `service` | Startup, reconnect, shutdown, logging, health |

Proposed CLI: `discover`, `inspect`, `serve` (a separate monitor command remains future work). Diagnostic commands should default to read-only. Movement is exposed only by serve mode after allow_commands is explicitly enabled. discover and inspect remain read-only.

Pin dependencies after testing. Keep all motor-specific normalization in the protocol adapter. Add upstream-compatible fixes where possible rather than creating an unrelated protocol implementation. Library calls that block should run in a controlled worker, not block MQTT callbacks. Use one active bridge client/service instance.

Configuration fields: bridge IP/interface; credential-file reference; MQTT broker/credentials; topic prefix; per-shade device ID, name, direction mapping, capabilities, travel time; polling and timeout settings. Unknown shades are reported by discovery but do not automatically become controllable household devices.

## 7. MQTT contract and openHAB model

These are project-defined topics, not Dooya protocol fields. Prefix: `earthship/shades/v1`.

| Topic suffix | Payload | Retained |
|---|---|---|
| `/service/availability` | `online` / `offline`, with MQTT last will | Yes |
| `/bridge/availability` | `online` / `offline` | Yes |
| `/<id>/command` | `UP`, `DOWN`, `STOP`, or integer 0–100 | No |
| `/<id>/favorite/set` | `RECALL` | No |
| `/<id>/position` | Integer 0–100, only when valid | Yes |
| `/<id>/availability` | `online` / `offline` | Yes |
| `/<id>/state` | JSON status and telemetry, schema below | Yes |
| `/<id>/event` | Command result or diagnostic event | No |

Canonical position convention: **0 = fully open; 100 = fully closed**, matching openHAB Rollershutter behavior. Verify physical endpoints and transform motor/library values once, in the adapter. Do not apply a second inversion in openHAB. “Closed” means shade fabric covers the skylight, not a claim about a skylight window actuator.

State schema fields: `schema_version`, `device_id`, `position`, `position_source`, `movement`, `observed_at`, `received_at`, `stale`, `battery_voltage`, `battery_percent`, `charging`, `rssi_dbm`, `last_command_status`. Unsupported or unknown fields are null/omitted, never zero. Identify percentage estimates as derived. A bridge reply timestamp is not necessarily a fresh motor measurement; retain that distinction.

Use unretained commands and a clean MQTT command subscription so old commands are not queued across service outages. Reject retained command messages. Prefer QoS 0 for the initial simple command channel, with application feedback, to avoid silently claiming exactly-once execution. Do not automatically replay commands on reconnect. A later structured command envelope may add IDs and expiration when needed; keep it separate from the simple Rollershutter channel.

On startup, invalidate per-shade availability before accepting commands; refresh device state. Retained positions are historical until revalidated. Clear invalid retained scalar values and use state freshness/availability to set Items to UNDEF when appropriate. Bridge reachability alone must not mark every motor reachable. If the protocol cannot establish motor liveness, expose that limitation.

Proposed Items per shade: Rollershutter `Shade_<Name>`; String movement; Number voltage; Number battery percentage; Switch availability; DateTime last report; String last error; Switch automation enabled; DateTime manual hold until. Global Items: bridge/service health and automation mode. Names are placeholders pending actual room inventory.

Use Generic MQTT Things and channels [S11]. Disable optimistic command-to-state prediction for the position Item so actual reports determine displayed position. Persist position changes, battery readings, availability changes, and command outcomes through the household's existing persistence configuration. Check the actual PostgreSQL/openHAB setup before selecting database tables or retention.

## 8. Command and state behavior

- Validate IDs and range; reject unsupported capabilities explicitly.
- Maintain a bounded queue with per-shade latest-target coalescing. STOP takes priority and removes pending movement for that shade.
- Distinguish received, dispatched, acknowledged, confirmed, failed, and timed-out commands. An acknowledgement is not proof of physical motion.
- Query state before retrying an uncertain movement. Bound retry attempts; avoid loops and continuous motor operation.
- Serialize RF traffic initially; tune small group dispatch delays using hardware results. A group operation reports partial failures.
- Prefer pushed reports, with slow background refresh and a targeted completion query. Initial configurable refresh proposal: 10 minutes, staggered; tune against actual update behavior and battery use.
- Measure full travel time during commissioning. Set completion timeout from measured travel plus margin, rather than assuming an arbitrary universal duration.
- Do not fabricate smooth live position from sparse reports. If interpolation is added for UI later, label it as estimated and keep it separate from reported position.
- Preserve physical-remote changes. If command origin is unknowable, classify unexplained motion as external/unknown, not definitely manual.

## 9. Thermal automation rollout

Begin with manual openHAB control and observed state. Add thermal rules only after the integration passes commissioning.

Proposed modes: OFF (no automation), MANUAL, SCHEDULE, and THERMAL. These are policy modes, not bridge settings. Manual commands override automation and establish a configurable hold; initial proposed hold is two hours, adjustable by the user. Unexpected external movement can also establish a hold when reliable source attribution is unavailable.

Thermal policy should use available indoor temperature, solar exposure/irradiance or a documented proxy, time/sun position, and desired comfort range. Inventory existing sensor Items before implementation. Night closure for heat retention and daytime shading to limit overheating are candidate rules; assess daylight preferences and room behavior first.

Use hysteresis and minimum dwell to prevent oscillation; proposed starting dwell is 15 minutes. Do not issue motion from stale critical sensor data. Missing data leaves the shade in its current position and disables that automatic decision. Low battery should create a notification and reduce nonessential movements only after chemistry and telemetry meaning are established; do not guess a universal voltage cutoff.

Run thermal rules in shadow mode first: record proposed movement and reason without moving shades. Review several representative days, then enable one zone. Expose the reason, mode, manual hold, and last confirmed position in the existing Earthship UI through openHAB. Avoid putting a separate actuator decision loop in the UI or an AI agent.

## 10. Solar charging and installation dependencies

Solar charging is a parallel commissioning task. The considered third-party panel advertises 5V, 3.6W, 720mA with USB-C. That does not itself establish compatibility. Verify the rechargeable pack's charging input label, connector fit, regulated output requirements, and installer/manufacturer guidance before connecting it. Motor supply voltage and battery charging input voltage are different specifications.

Test one panel in its actual skylight position before buying the full quantity. Check sunlight through glazing, cable reach, mounting, shade travel clearance, and charging trend over representative days. A protocol battery reading may not reveal USB input power or charging state. If available, record voltage trend without treating it as an exact state-of-charge gauge.

The integration must work regardless of whether charging is solar or manual. Do not allow charging uncertainty to change protocol assumptions.

## 11. Phased backlog and exit criteria

| Phase | Deliverables | Exit criterion |
|---|---|---|
| 0 — Inventory and API | Hardware matrix, received API docs, host/broker/openHAB/repository inventory | Exact identifiers recorded; implementation environment known |
| 1 — Software foundation | Adapter interface, config, MQTT contract, read-only CLI, fixtures, service skeleton | Simulated state and commands validated; no hardware claims |
| 2 — One-shade proof | Successful local auth/discovery/control, endpoint mapping, push report capture | Open/close/stop and 25/50/75% tested; actual feedback assessed |
| 3 — openHAB integration | Things/Items, UI, manual hold, persistence, errors | Remote/app/openHAB agree; stale state visible |
| 4 — Resilience | Reconnect behavior, WAN isolation, restart checks | No replayed movement; local operation survives tested failures |
| 5 — Full installation | Stable IDs, groups, RF placement, charging observations | Every installed shade passes basic travel and report checks |
| 6 — Thermal operation | Shadow rules, one-zone trial, tuning, final rollout | User-reviewed behavior without oscillation or overridden manual holds |

Can proceed before hardware: repository/environment inspection, protocol/library review, MQTT/service design, simulator fixtures, configuration samples, diagnostic tooling, and openHAB mapping templates. Hardware and API details gate compatibility certification, not all development.

Suggested implementation files: `pyproject.toml`, service modules above, `config.example.yaml`, MQTT schema documentation, sample systemd unit, openHAB Thing/Item/rule templates, compatibility matrix, tests, and commissioning/runbook documents. Match existing repository conventions after inspection.

## 12. Verification matrix

| Test | Expected result |
|---|---|
| 0/25/50/75/100% commands | Correct physical direction and reported final position; record repeatability |
| Stop during travel | Motion stops; pending target does not restart it |
| Physical remote and app movement | openHAB updates on a report or documented refresh interval |
| Lost command/reply | Explicit uncertainty or timeout; no false completion |
| Bridge offline / motor unavailable | Distinct health state; stale position not shown as freshly confirmed |
| Service, broker, and bridge restarts | No old motion replay; orderly rediscovery and state refresh |
| Internet blocked, then bridge restarted | Local control/reporting still works, or dependency documented |
| Multicast unavailable | Direct-IP polling fallback tested; latency documented |
| Group command with one shade unavailable | Successful shades reported individually; failure retained |
| Manual hold and conflicting thermal target | Manual preference remains in effect |
| Missing thermal sensor | No new automatic movement from stale data |
| Solar installed | No mechanical interference; observed charging behavior recorded |

Meaningful automated tests: position inversion boundaries, validation, retained-command rejection, queue/STOP handling, missing/stale telemetry, reconnect without replay, protocol error handling, and fixtures from captured sanitized responses. Hardware confirmation cannot be replaced by mocks. Do not repeatedly cycle shades solely to increase test counts.

## 13. Operations and rollback

Run under a dedicated account with restricted credential access; redact keys/tokens from logs. Back up device mappings and non-secret configuration; back up credentials separately. Log command/result IDs, timing, device, and error categories. Rate-limit repeated outage alerts.

Rollback: disable thermal rules, stop the service, and leave physical remote/app control intact. Preserve pairing and limits. Restore the previous tested dependency version/configuration if a library or bridge update breaks compatibility. Record firmware changes before retesting. Do not automatically update bridge firmware as part of deployment.

Completion means installed shades have tested local manual control, target positioning and feedback (or an explicitly accepted limitation), accurate stale/offline indication, working overrides, documented restart behavior, and a reproducible setup. Thermal automation is enabled only after its separate shadow/trial phase.

## 14. Open decisions and next actions

1. Use the located v1.03 API specification now; await Dooya response for firmware and Mini Bridge clarification.
2. Obtain actual motor/P-Box/battery labels from installer, and confirm what the $425 quote includes.
3. Record shade count, rooms, dimensions, desired names, bridge placement, and panel cable routes.
4. Inspect installed openHAB version, MQTT broker, persistence, Linux host, and Earthship repository conventions.
5. Implement the service foundation and read-only diagnostic tool; choose a tested library version.
6. Commission one shade, capture sanitized traffic, and fill the compatibility matrix.

No new bridge, SDR transmitter, or alternative hub purchase is needed for software planning. Keep RF reverse engineering as a fallback if local P-Box access fails; revisit hardware only against an observed limitation.

## Sources

Sources were reviewed during the project discussion and on 25 September 2026. Links establish platform capabilities, not confirmation of the user's undelivered hardware. All MQTT contracts, defaults, phases, and acceptance criteria above are project design proposals.

- [S1] Premier 2024 cellular guide, Simple Touch section, page 33: https://premierblindsshades.com/wp-content/uploads/2024/05/Premier-Blinds-and-Shades-Ultimate-Series-Cellular-PRICE-CHART-USD-24.05.01.pdf
- [S2] HT Simple Touch motorized shade user guide, hosted by CACO: https://www.cacoinc.com/wp-content/uploads/2024/04/Simple-Touch-motorized-shade-user-guide.pdf
- [S3] Dooya home automation and API availability: https://dooya.in/home-automation-for-curtains-and-blinds/
- [S4] Dooya 2025 interior catalog, DV24WE/S: https://www.dooya.com/data/download/2025/interiorapplication.pdf
- [S5] Home Assistant Motionblinds integration, supported bridges and key retrieval: https://www.home-assistant.io/integrations/motion_blinds/
- [S6] Standalone Python library and methods: https://github.com/starkillerOG/motion-blinds
- [S7] Community-hosted Connector API documentation, DD7002B: https://github.com/alexbacchin/ConnectorBridge
- [S8] Connector CLI, UDP connection and HTTP alternative: https://github.com/alexbacchin/ConnectorBridgeCLI
- [S9] Connector app publisher contact: https://play.google.com/store/apps/details?id=com.smarthome.app.connector&hl=en_US
- [S10] HT contact information: https://www.htwfonline.com/
- [S11] openHAB MQTT Things/channels: https://www.openhab.org/addons/bindings/mqtt/

## 15. Updated implementation baseline (25 September 2026)

This section supersedes earlier proposed defaults when they differ from the implemented contract.

- Canonical app: Connector, published by Shade Connector/Dooya.
- The owner has selected the P-Box. DD1554E is retained only as background research; it is a lower-cost candidate using USB power and Wi-Fi data; exact Mini firmware compatibility is not yet tested. No extra Pi is required.
- The located API specification documents AES token derivation, UDP ports 32100/32101, multicast 238.0.0.18, discovery, cached reads, writes and post-motion reports. Pairing remains app-only.
- The implementation separates bridge cache from Report-derived position. Battery values remain raw until scaling/chemistry is verified.
- Initial commands: UP/DOWN/STOP and 0–100; favorites, completion correlation, thermal rules and group policies are backlog items.
- Movement is disabled by default. Bounded coalescing queues, STOP priority, no retained commands or reconnect replay, and stale UNDEF output are the initial behavior.
- See docs/MQTT.md for exact implemented topics; docs/DEPLOYMENT.md for deployment; docs/CODEX_HANDOFF.md for remaining tasks.
- RF alternative: ESP32/CC1101/ESPHome is documented research, not implemented and not established as full motor telemetry support.

Additional reference: https://github.com/scubamikejax904/Connector-Bridge-Hubitat-direct/blob/main/Connector%20WLAN%20Integration%20Protocol%20-%2020240424.pdf
