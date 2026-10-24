# MQTT and OpenHAB contract, version 2

Default prefix: `earthship/shades/v2`. Native position is 0=open, 100=closed.
Read v1 artifacts with their original producer; never rebind retained v1 messages
to a current v2 session or replay old commands.

| Suffix | Payload | Retained |
|---|---|---|
| `service/availability` | online/offline; offline is the last will | yes |
| `service/state` | v2 adapter session, source generation, ready and emission time | yes |
| `bridge/availability` | online/offline | yes |
| `bridge/state` | v2 session, generation, pinned bridge identity and reachability | yes |
| `SHADE/availability` | online/offline from usable matching Report evidence and bridge health | yes |
| `SHADE/position` | last current native integer or UNDEF | yes |
| `SHADE/state` | v2 current/last-known observation, uncertainty, original identity/time | yes |
| `SHADE/source/state` | v2 configured source/epoch, current session/generation and availability hint | yes |
| `SHADE/event` | full `dooya-command-result/v1` receipt | no |
| `owner/command` | authenticated `dooya-owner-command/v1` envelope | **no** |
| `owner/policy` | authenticated `dooya-owner-policy/v1` envelope | **no** |
| `owner/policy-ack` | exact effective policy/session/generation acknowledgement | no |
| `owner/result` | full `dooya-command-result/v1` receipt | no |
| `owner-input/SHADE` | unsigned binding test/manual intent only; NOT subscribed by adapter | no |

Bare `SHADE/command`, retained commands and unsigned input never authorize motion.
The adapter and the trusted owner share a private HMAC key. Commands bind a UUID,
original creation/expiry, manual/automatic origin, commissioned epoch, pinned
bridge/device, adapter session, source generation and owner session. Python and
Node use the shared golden canonical-JSON fixture. Policies expire within 30
seconds; an effective ACK means final-dispatch inhibition was committed, not that
a motor stopped. An already transmitted movement cannot be undone by a policy ACK.

Only the authenticated explicit manual owner path is prepared for later supervised
release. Automatic movement remains unreleased even for a correctly signed policy
claiming global release. Zone permissions and Bathroom/Bedroom nighttime protection
remain enforced; an expired/unknown policy cannot authorize an automatic move.
Manual operation does not depend on a zone's saved automation preference. All
operations still require their individually commissioned capabilities and identities.
An authenticated, individually commissioned operator STOP can remain available while
`allow_commands=false` disables new movement; STOP still requires the current
source/session, capability and healthy durable journal. STOP has bounded priority
and reserved capacity, without a guaranteed physical stopping deadline. There
are no raw group broadcasts.

Publication acknowledgement, bridge WriteDeviceAck and motor Report are distinct.
An ACK is cache/transport evidence; it never sets measured position or proves
mechanical completion. A reported target is labelled observed with actor unknown.
Timeout is uncertain and never causes automatic movement retry. Durable journals
retain deduplication/outcome and original observation identity; restart restores
history, never an execution queue. Broker publication is QoS 1; its duplicates do
not authorize repeated UDP movement. The one-shot owner publisher never retries.

Use `examples/openhab.things` and `examples/openhab.items` only after mapping actual
commissioned identities. Rollershutter binding uses `on="UP"`, `off="DOWN"`,
`stop="STOP"`; `up`/`down` are unsupported and become numeric endpoints. Autoupdate
is disabled. The example's unsigned owner-input output demonstrates binding
semantics only; it is deliberately disconnected from the adapter command owner.
The UI posts an authenticated explicit request to its server owner instead.

Current UI position requires the eight mapped roles (scalar position, per-shade
availability, diagnostic state, service/bridge availability, service/bridge JSON
state and actual provider Thing), matching commissioned source digest/epoch,
provider ONLINE and current fresh matching sessions. `SHADE/source/state` is a
configuration/availability hint and cannot relabel an older observation or replace
its original source digest/epoch. A per-shade ON value or an
ONLINE MQTT Thing alone is insufficient after broker recovery. Optional result
Item links `SHADE/event` and preserves request-specific outcomes. The prepared UI
provider-history collector has empty mappings by default and does not issue
commands. Its v2 history reader preserves original unknown actor/measurement-time
qualifications; no observation is automatically causal training evidence.

See `DD7066_BINDING_INTEGRATION.md` for actual disposable OpenHAB/Mosquitto evidence
and `DD7066_COMPATIBILITY.md` for unverified physical-protocol boundaries. No real
bridge, motor, radio, remote, app timer or WAN independence has been qualified.
