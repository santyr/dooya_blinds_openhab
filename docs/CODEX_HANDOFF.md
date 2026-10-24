# Codex continuation handoff

## Read order and current boundary

Read AGENTS.md, README.md, docs/PROJECT.md, docs/PROTOCOL.md, docs/MQTT.md,
docs/MQTT_BROKER.md and docs/DEPLOYMENT.md; then docs/DD7066_COMPATIBILITY.md,
docs/DD7066_BINDING_INTEGRATION.md, docs/DD7066_SIGNED_STACK.md,
docs/VALIDATION.md, src/ and tests/. The project plan retains earlier design
history; use the current source and versioned contracts for implementation facts.

The owner confirms that the shade hardware is **not installed yet**. The chosen
bridge is DD7066 P-Box, distinct from DD7066M and DD7006. The intended deployment
is a separate adapter service on the existing Ubuntu Server host alongside
OpenHAB. This preparation has installed no shade service, owner profile, MQTT
ACL, provider-history mapping or motor configuration in the house, and has sent
no real bridge/motor packets. Physical label, supplied firmware, RF behavior,
limits, polarity, reports and WAN independence remain unverified.

The host already has OpenHAB 5.2.3-1 and Mosquitto 2.0.18, with authenticated
loopback broker Things observed ONLINE. Reuse the existing installation; do not
infer anonymous access, copy credentials into source or install a second broker.
The owner's earlier conditional broker-setup authorization remains conditional
on actual need and host access. It does not authorize movement or replacement of
existing household configuration.

## Implemented software

- WLAN v1.03 AES/UDP primitives, read-only inventory/cache requests, pinned-source
  checks, durable outgoing message-ID high-water and strict echoed-ID correlation.
  The explicit `pinned_discovery_without_id` profile permits only an absent
  discovery msgID with a configured bridge pin and the other validation intact.
  It is never an automatic fallback; read/write acknowledgements stay strict.
- Supported profile classification requires family `10000000`, wirelessMode 1
  and type 1 or 4, without contradictory dual-axis fields. Unknown, one-way,
  virtual-percentage and TDBU profiles do not gain movement capability.
- State schema v2 preserves original Report identity, receipt/validation clocks,
  commissioning epoch and source digest. Current usability requires matching
  session/generation, supported valid new Report ordering and bounded freshness.
  Cache acknowledgements cannot renew reported position. Motion admission makes
  current position uncertain until new supported Report evidence arrives.
  Last-known history stays readable, including after restart; unknown physical
  boot, measurement time, delivery delay and actor remain explicitly unknown.
- HMAC-authenticated, separately domain-signed command and policy envelopes,
  original expiry of at most 30 seconds, durable bounded deduplication/outcomes,
  final checks after journal fsync, policy-generation withdrawal and no automatic
  retry/replay. Signed inputs use QoS 1 without retention. Raw MQTT command values
  cannot assert manual authority. The one-shot `owner_publish` CLI validates by
  default; only `--publish` attempts publication of already signed stdin input.
- STOP priority, cancellable UDP waits, bounded per-shade manual STOP reserve and
  paced polling. Dispatch results distinguish acknowledgement, uncertainty and a
  later observed target with unknown actor; they never claim causal completion.
- The actual Service keeps automatic movement unreleased. `allow_commands=false`
  disables new movement; a separately commissioned, authenticated operator STOP
  may still pass its source/capability/session/durability gates. Commands require
  explicit individually commissioned capabilities, not merely a Boolean flag.
- A paired source-only Earthship UI owner supports authenticated manual requests,
  private installation records, source-bound observation display and individual
  group-member outcomes. Its automatic zone/night policy remains authoritative;
  manual permission is independent of the saved automation preference. No second
  household preference store or forecast/model release was introduced.
- Versioned v2 retained-history readers and an optional prepared provider-status
  collector exist in the paired UI source. The collector's default mapping is
  empty. Legacy v1 history readers and their producer artifacts remain separate.
  Partial individual Report positions are observations even when automation is
  OFF; no received-report interval becomes an action/causal training label.

The protocol PDF actually read is v1.03 dated 2022-03-17; its filename does not
establish a newer protocol. Exact-model firmware/API questions remain in the
**unsent** DD7066_VENDOR_REQUEST.md. Raw battery values remain unscaled pending
label/chemistry validation. No pairing, reset, limit programming, firmware update
or thermal actuation path was added.

## Configuration and verification handoff

Use Python 3.11+ in a dedicated environment and install the existing declared
project/test dependencies. Run the maintained unit/socket suites and the scoped
source-pair gates. Run the disposable binding and signed-stack profiles only via
their explicit `--run` entry points; they use synthetic identities and isolated
OpenHAB/Mosquitto/loopback endpoints. Their records distinguish actual adapter,
Node owner, Python publisher and binding paths from simulated physical devices.
Read their source hashes/results for the exact run; do not carry a historical
pass count forward as evidence for a modified source pair.

`examples/config.toml` keeps adapter movement disabled. The empty
`examples/ui-owner-installation.json` keeps the paired UI unmapped and publisher
disabled. `examples/owner-publisher.toml` is deliberately unconfigured and cannot
publish unchanged. Copy installation files outside Git and every served/web
root; do not put real identities or secrets into these examples.

The Node owner profile, key, owner journal and publisher config must be siblings
in a directory owned by the executing account with mode 0700. Existing private
files must be account-owned regular 0600 files; key/config/password readers
reject symlinks and hard links. The Python publisher also requires its key and
MQTT password in that same private directory. The Vite integration rejects a
profile directory inside any served root and requires strict filesystem serving.
The adapter has separate proposed systemd/config/state paths; its owner key must
contain the same exact raw bytes as the owner's key. Do not add a key newline.
Manual Basic credential material is separate from the OpenHAB REST credential,
MQTT password and HMAC key.

Keep immutable prior producer packages, original observation journals and
receipts. Preserve corrupt state for diagnosis; never delete a high-water or
journal file just to make readiness appear successful. Restart imports history
and deduplication only, never pending movement. Rollback must preserve the source
pair/version boundary and must not reinterpret v1 retained data as v2 evidence.

## Installation and physical evidence still required

Follow docs/DEPLOYMENT.md only during a separately authorized installation. Map
actual private bridge/device IDs, commissioning epoch, model/firmware/reference,
operations, Items and provider Thing; verify broker ACLs and JDBC readback. Start
read-only. A field set in a profile is not physical commissioning evidence.

Supervised per-device acceptance must establish UP/DOWN polarity, exactly-once
inversion, STOP, supported partial targets, limits/travel, remote/app reports,
battery interpretation, multicast, token/reboot behavior and stale/offline
transitions. Test broker/adapter/bridge/network/RF failures and WAN-off reboot
without replay. A measured Report or fixture timing cannot certify physical
completion, RF ordering, cloud independence or an emergency-stop deadline.

One configured bridge and one running adapter per host/interface are supported.
STOP can interrupt normal request waiting/pacing, but OS scheduling, journal
storage, network and radio delays remain; this is not an emergency-stop system.
Availability is bounded received evidence, not proof of mechanical safety. The
daemon does not extrapolate moving position, and cache-only polling cannot replace
fresh motor Reports. Favorite recall, motor programming and battery estimation
remain outside the implemented qualified contract.
