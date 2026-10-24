# DD7066 evidence and provisional compatibility contract

Reviewed 2026-10-24. Target: **Dooya P-Box DD7066**. No bridge or motor was available to this review. This record establishes a public-document baseline and software acceptance requirements, not installed compatibility, permission to move, or automatic release.

## Sources and reproducibility

The pinned [WLAN Integration Guide](https://github.com/scubamikejax904/Connector-Bridge-Hubitat-direct/blob/05b2e646e56457997f8fb2542bfaa6660d7ad6b1/Connector%20WLAN%20Integration%20Protocol%20-%2020240424.pdf) was downloaded and all 26 pages extracted locally. Revision, family and authentication tables were also rendered and visually checked. The document identifies Connector as author; the public distributor is a community repository, not a firmware certification from Dooya.

| Record | Verified value |
|---|---|
| Retrieval | 2026-10-24T15:28:43Z (download completion file time); provenance is in the temporary evidence manifest |
| Current upstream tree checked 15:31 UTC | `05b2e646e56457997f8fb2542bfaa6660d7ad6b1` |
| PDF Git blob | `973f2d224bb15a40933932085cb28db8d83be12d` |
| PDF SHA-256 | `39cd7ae5e4aa5da75b48c4735b723209bba023714b5eaa6f4915b8ceb4a9ac81` |
| Size | 317734 bytes |
| Internal revision | v1.03; last revision row 2022-03-17, page 2 |
| Filename / metadata | Filename carries 20240424; creation/modification metadata also carries 2024-04-24; neither changes the internal revision |
| Local evidence directory | `/tmp/dd7066-evidence-20261024` (temporary, outside Git) |

The version table progresses from v0.93 (2019-12-30) through authentication, message-ID and interface revisions, then v1.02 (2020-11-04, TDBU), and v1.03 (vertical blinds and charging telemetry). The guide does not name DD7066 firmware. **No newer public exact-DD7066 LAN API revision was established** by current manufacturer pages/catalogue and targeted searches. v1.03 is the retrieved working reference, not a claim about the manufacturer's latest private API.

[Dooya India's regional product page](https://dooya.in/home-automation-for-curtains-and-blinds/) explicitly separates DD7066 P-Box from DD7066M Matter boxes, advertises Ethernet/Wi-Fi and both RF directions for DD7066, and gives a common 30-motor capacity. It offers local API material on request. Snapshot retrieved 2026-10-24T15:30:30Z, SHA-256 `913afdcebdca1d5feb340f328023b3692c3c1981d266a2f9d9e7913bcbffdc10`. The site is operated by Vyoma Exim; treat it as regional product evidence, not a firmware-specific wire specification.

The manufacturer's [2026 interior catalogue](https://www.dooya.com/data/download/2026/interiorapplication.pdf), retrieved the same day, SHA-256 `327b53bb166b6072f54da70536b7744590f7bd010be5ac2de1850ddb4ed0ecb7`, identifies DD7066M as a Matter ME-Box (PDF pages 44–45; printed 39–40), while its P-Box table uses DD7006 variants (PDF page 36). It does not establish DD7066/DD7066M equivalence. DD7006, DD7002B and DD1554 implementations remain related-family corroboration only. The current [Home Assistant bridge list](https://www.home-assistant.io/integrations/motion_blinds/) does not explicitly list DD7066; [motionblinds](https://pypi.org/project/motionblinds/) still reports 0.6.30, released 2025-07-31. Neither certifies this installation.

## Wire evidence versus adapter decisions

Page references below refer to the 26-page guide. These constraints are provisional for actual DD7066 firmware.

| Area | Direct evidence | Conservative adapter contract / unresolved check |
|---|---|---|
| Transport | JSON/UDP, command port 32100; multicast `238.0.0.18:32101` for heartbeat/report (pp3,8,21) | Verify Ethernet/Wi-Fi routes, source ports and multicast at commissioning. No home address belongs in public evidence. |
| Reply routing | Discovery/read prose says client port 32101, while examples return from server 32100 to the originating client port (pp6–7,24–26) | Preserve expected source/address and bounded deadline. Firmware exceptions need isolated tests and evidence; never accept arbitrary senders. |
| IDs | Timestamp strings, increased per interface operation (p3); examples contain 17 decimal digits | Generate valid calendar `yyyyMMddHHmmssSSS` IDs. This shape is inferred from examples; UTC, clock rollback and restart uniqueness are adapter choices. Persist a high-water mark or refuse unsafe restart reuse. Numeric addition to a formatted timestamp is invalid at carries. |
| Acknowledgements | Read/write examples echo request IDs and child MAC; discovery response table/example omits `msgID` (pp7,12–20,26) | Strict ID/type/target/source matching remains the default. Strict discovery may refuse otherwise documented replies: report this limitation. A missing-ID exception requires a narrow explicit contract; no silent fallback or relaxed matching. |
| Authentication | Key and token are each 16 bytes; AES-128 ECB result becomes uppercase hexadecimal (pp3–5) | Validate encoded bytes, preserve hyphens/case/whitespace, use one block without padding, and keep secrets local. This authenticates writes; it does not encrypt all JSON or authenticate Reports. |
| Regular family | `10000000` is the ordinary RF motor code; `02000001` denotes bridge. Other listed codes are Wi-Fi curtain `22000000`, tubular `22000002`, receiver `22000005` (p6) | Allow only the implemented exact family; prefix matching is insufficient. Known family alone does not qualify every capability. |
| TDBU | Examples use `10000001`, `type=9` and separate top/bottom attributes (pp13,16,18,20,23) | Diagnostic inventory only. Never collapse into normal position/control. Contradictory `type=9`, axis fields or unknown family cannot qualify single-axis movement or measurement. |
| RF modes | Mode 0 is one-way; 1 two-way; 2 mechanical-limit two-way; 3 Wi-Fi; 4 virtual percentage; 5 other (p10) | Only exact integer mode 1 qualifies this adapter's position path. Boolean `true` is invalid. Missing, changed or unsupported mode inhibits new eligibility while preserving historical values. |
| Motion subset | Operations 0/1/2 represent close/open/stop; percentage range 0–100 (p9) | Retain native adapter/OpenHAB 0=open,100=closed and one inversion boundary; per-motor polarity still needs physical verification. No guessed operation 3, limit, angle or favorite programming. |
| Status query | Operation 5 is labelled as a status request; ReadDevice and Reports are limited to mode 1 (pp9,21,24) | Operation 5 is a narrowly permitted non-motion request for the supported family. The guide does not guarantee it triggers fresh RF contact or a new Report for the supplied motors. Validate those effects at Gate B. |
| Position evidence | Reports are described after stopping, not as a continuous trace (p21). Acks include position-looking fields | Ack values remain bridge-cache history, never fresh measured position or completion. This is a trust rule; the PDF does not expressly specify cache age/RF refresh semantics. A matching target Report means target observed, not proof of causal actor. |
| Optional diagnostics | Voltage/battery, RSSI and charging fields are listed; battery is voltage-related (pp10,22,25) | Preserve finite typed raw values. No undocumented voltage divisor or battery percentage. Missing is not zero; unknown fields do not authorize capability. |
| Resource limits | No payload-size ceiling, recommended polling/rate, sequencing or session/boot protocol was found | Packet size/depth/count, queue size, timeout and spacing limits are defensive adapter limits, not vendor-certified limits. Record exact configured bounds with software tests. |

Public authentication fixture (p4), safe for offline tests: key `74ae544c-d16e-4c`, token `37412C478E0FBEAB`, expected `8570A96BC18ADB21D1FC155B24ECFD73`. These are published sample values, not installation credentials. Test byte length and wrong-key cases without logging secret material or using an online encryption tool.

## Observation ordering and session boundary

The guide supplies no reliable report sequence number, motor sample clock, boot identity, delivery guarantee or link from an unsolicited Report to a particular actor. A timestamp-shaped `msgID` cannot alone establish trusted motor sample time. UDP can duplicate, reorder and delay traffic. Source matching is correlation, not cryptographic authenticity.

The adapter must keep upstream ID, local receive time, validation time, owner/session and measurement eligibility separate. Same-ID or exact duplicate content cannot renew freshness; older IDs cannot overwrite newer accepted evidence. Malformed/unsupported later evidence must inhibit actionability rather than leaving an apparently qualified old value. Use monotonic local deadlines and retain last-known history as history.

After reconnect/restart, retained values are historical until reconciled with current ownership and composite service/bridge/device liveness. Because the wire has no proven boot/session marker, a previously unseen delayed datagram can remain indistinguishable from current traffic. State this ambiguity explicitly; local session generation and duplicate caches reduce risk but do not prove fresh RF contact. Commissioning must establish the actual status-query/report behavior and tolerated age. Never claim exact movement timing or command causation from receipt order alone.

## Required evidence before capability release

Offline acceptance imports the actual modules and covers: public AES fixture; calendar boundaries/rollback/concurrency/restart IDs; wrong/late sender/type/ID/device; strict JSON finite numbers, duplicate keys, size/depth; unknown/TDBU/one-way families; atomic discovery/token updates; cached Ack isolation; duplicate/reordered/old-session Reports; honest uncertainty after lost Ack and no automatic movement replay. See audit T01–T10 and T39. Passing simulation does not pass hardware gates.

The private installation inventory must record actual bridge label/hardware/firmware, actual motor model/firmware/family/mode, private identity reference, protocol source/checksum, per-capability tests, polarity/inversion owner, report timing, battery units, independent timer/app owners, tested software versions, operator/date and commissioning epoch. Motor or firmware replacement requires a new epoch. No supplied motor model, WAN independence, observation path, STOP or partial target is verified here.

Vendor questions and an unsent request draft are in [DD7066_VENDOR_REQUEST.md](DD7066_VENDOR_REQUEST.md). Gate A verifies label/network/identity without motion; Gate B verifies supported non-motion observation; supervised movement and automatic release are separate later permissions. Do not provision, pair, reset, calibrate, update firmware or actuate as part of this research.
