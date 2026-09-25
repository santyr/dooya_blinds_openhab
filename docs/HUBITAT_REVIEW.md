# Review of Connector-Bridge-Hubitat-direct

Reviewed 2026-09-25 for the P-Box implementation. This is a targeted source review, not a full audit or hardware verification.

Source: https://github.com/scubamikejax904/Connector-Bridge-Hubitat-direct

Parent `Driver - Parent` blob: `485f9fab1fb0c3cfe3d14555bf482dd9e959527b` (header v32).
Roller child blob: `d2474efb2f8977f8328303762e023b497912a6d8` (header v30).
README blob: `bbd146be82ab43573a65bfd7683251fc80b5bf2b` (lists parent v31; source is newer).

## Findings and project decisions

| Area | Source behavior | Our implementation |
|---|---|---|
| Authentication | AES/ECB/PKCS5Padding, then first 32 hex characters | Encrypt exactly one 16-byte block; equivalent first-block result, tested against PDF vector |
| Tokens | Discovery and heartbeat update token; AccessToken error triggers rediscovery | Discovery/bridge heartbeat tokens accepted; failed movement is not replayed; periodic rediscovery restores session |
| Queue | Stored command list is emptied then iterated when token is present | Bounded per-shade coalescing queue; STOP preempts pending movement; queued jobs discarded on disconnect/failure |
| UDP | Bridge port 32100, multicast 238.0.0.18:32101 | Same endpoints, plus strict unicast source/msgID/device correlation |
| Device typing | Uses discovered deviceType; honeycomb is type 4 | Preserve discovered deviceType; one-axis shade commands only initially |
| Position | Parent converts bridge 0=open to Hubitat 100=open | openHAB already uses 0=open; no default inversion needed |
| Source of state | ReadDeviceAck, WriteDeviceAck and Report all update the same child state | Cache and Report are separate; acknowledgements never confirm completion |
| Battery | Formula maps raw 700–850 linearly to 0–100% | Raw batteryLevel only until actual battery chemistry and units are verified |
| Motion display | Optional travel-time interpolation publishes position | No interpolation in initial adapter; actual reports only |
| Logging | Debug logs include generated AccessToken | Credentials and token never logged |

## Specific pitfalls to avoid

The roller child uses `data.currentPosition ?: data.position ?: data.level`. Groovy considers numeric zero false, so a valid zero can be skipped. Other fallback expressions similarly risk treating zero as missing. The parent's direct position path has an explicit null check, so this observation does not mean every position update is broken. Our tests specifically preserve 0 and 100.

The child also publishes an estimated final target when its interpolation timer expires. That is useful UI animation but is not proof of motor arrival. The initial adapter deliberately emits only acknowledged/uncertain command outcomes; completion correlation remains a documented next task.

The source's queue/token handling establishes useful compatibility evidence but does not justify replaying an uncertain movement after an outage. Our implementation serializes network calls and clears queued motions on a command error or MQTT disconnect.

The README says testing covered DD7006 and DD7002B and warns unidirectional motors lack position reporting. It does not qualify the exact HT motor or DD1554E. TDBU and Venetian code exists, but those functions are not required for this skylight project and have not been ported.

## Reuse / attribution

No Groovy source is copied into this implementation. The repository is a reference alongside its linked WLAN specification. Its README identifies Apache-2.0 licensing; preserve applicable attribution/license obligations if code is copied in future. The PDF's redistribution terms should be checked separately; this repo links it instead of vendoring it.

## Remaining checks

Validate actual firmware's msgID echo, source port, token length, case conventions, and Report fields. Our strict matching may reveal a compatibility difference; add narrow tested handling rather than accepting arbitrary packets. Verify remote-triggered motor reports, and battery units before calculating percentage.
