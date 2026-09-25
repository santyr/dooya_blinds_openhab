# MQTT and openHAB contract, version 1

Default prefix: `earthship/shades/v1`. 0=open, 100=closed.

| Suffix | Payload | Retained |
|---|---|---|
| service/availability | online/offline (last will) | yes |
| bridge/availability | online/offline | yes |
| SHADE/availability | online/offline based on valid recent position report | yes |
| SHADE/command | UP, DOWN, STOP or decimal integer 0..100 | **no** |
| SHADE/position | last reported integer, or UNDEF when stale | yes |
| SHADE/state | JSON with reported_position, report_received_at, stale, source, raw report, cache | yes |
| SHADE/event | command outcome: rejected/acknowledged/uncertain | no |

Retained commands are rejected. Commands received while not ready or while allow_commands=false are rejected. Commands use QoS 0 and are never automatically retried. A timeout is uncertain: the motor might have received the command. STOP clears queued movements for that shade and is processed first after the current network operation. Queue is bounded and coalesces target commands per shade.

Only Report updates authoritative position. ReadDevice/WriteDeviceAck data is retained separately as cache. A fresh report without currentPosition does not refresh position age. A bridge heartbeat does not refresh a shade. Future live interpolation must be a separate estimated value.

Use examples/openhab.things and examples/openhab.items after replacing IDs and broker. Disable autoupdate on the Rollershutter Item; treat stale UNDEF and OFFLINE explicitly. Verify syntax with the installed openHAB version. Generic MQTT scalar topics avoid requiring JSONPath for basic control. JSON telemetry can be linked later after validation.

Manual holds, group outcome aggregation, and thermal rules are not implemented in this initial service. Household rules must not use old restored positions to trigger movement automatically at startup.
