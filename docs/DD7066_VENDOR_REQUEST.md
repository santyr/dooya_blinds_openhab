# DD7066 supplier / Dooya request draft

**Draft only — not sent.** Prepared 2026-10-24. No vendor reply, transport contract or physical capability is implied. The owner must choose an already authorized communication channel before sending. Do not attach installation keys, tokens, home addresses or raw captures.

Subject: DD7066 P-Box local LAN/WLAN API, firmware applicability and observation behavior

We are preparing a local integration for a **DD7066 P-Box**, using an existing Ubuntu/openHAB host and MQTT. We need the exact DD7066 local protocol rather than the DD7066M Matter, DD7006, DD7002B or DD1554 contracts. Please confirm the supplied bridge's physical model, hardware revision and firmware; the motor model/firmware will be supplied privately once known. No motor model is assumed.

Please provide:

1. The latest supported DD7066 LAN/WLAN API, full revision history and errata, document identifier/date/checksum and applicable firmware versions. Does the publicly circulated WLAN v1.03 guide (revision row 2022-03-17; filename 20240424) apply? Please explain differences between Ethernet and Wi-Fi, and between DD7066 and DD7066M.
2. The supported identity/version queries, key provisioning/rotation method, token lifetime/change notifications and byte encoding. Are key/token exactly 16 encoded bytes, and is AccessToken a single AES-128 ECB token block rendered as 32 uppercase hexadecimal characters?
3. Required request `msgID` format/timezone, monotonicity scope across clients/reboots, clock rollback handling, collision/replay behavior and whether each acknowledgement echoes it. The v1.03 discovery example has no `msgID`, whereas read/write examples echo it. What discovery correlation contract is supported?
4. Actual request/reply/multicast source/destination ports. The guide's discovery/read prose specifies client port 32101, while its examples reply from bridge port 32100 to the originating client port. Which applies to each supported DD7066 firmware?
5. Whether ReadDevice returns cached bridge state or contacts the motor; cache age, stale/unreachable indicators and exact refusal/error schemas. Does WriteDevice operation 5 safely query the actual motor family without movement, cause fresh RF contact, and produce a new Report? What happens when the motor is unreachable?
6. For the supplied motors: exact family/type/radio-mode identifiers, one-way versus two-way support, top/bottom or other multiple-axis detection, position polarity, endpoints, intermediate positions, STOP, limits-known indicators and optional tilt semantics. Please identify any commands that program/reset rather than operate; those will remain out of scope.
7. What reports follow remote, app, physical or third-party movement, including partial stops? Are reports only final, or also in motion? Are duplicate/order guarantees, sample times, report IDs, boot/session markers or actor/command identifiers available? How can a delayed pre-reboot Report be distinguished from fresh motor state?
8. Optional battery/voltage/charging fields, units/scaling, missing/sentinel values and supported hardware. We will retain raw values until units are established.
9. Maximum devices and datagram sizes, recommended request/poll rates and pacing, in-flight/concurrency limits, group semantics, error/refusal/timeouts and any guarantee against movement duplication on retries. We will not replay uncertain movement automatically.
10. Local behavior with WAN blocked, including bridge reboot, key/token refresh and app provisioning requirements; how to enumerate/disable bridge timers/scenes and identify other autonomous writers without changing pairing, travel limits or firmware.

Please distinguish documented capability from a firmware-specific limitation and provide a safe observation-only validation procedure. Physical movement and calibration will be performed only later with an installer/operator and separate authorization.

## Reply intake and acceptance

Archive the original reply privately; record retrieval time, sender/channel, document origin/identifier/revision and SHA-256. Compare with the pinned artifact in [DD7066_COMPATIBILITY.md](DD7066_COMPATIBILITY.md). Convert changed wire semantics into tests before changing compatibility behavior; a newer filename alone does not supersede a tested contract. Vendor silence does not block offline repairs and does not certify compatibility. Supported behavior may also be established through documented, authorized device tests with the actual firmware, preserving the evidence limits.

Current external dependency: exact-DD7066 firmware applicability and the ten questions above remain unanswered by this audit. Hardware acceptance requires Gate A identity/network, Gate B observation, then separate supervised motion and per-device commissioning; no response authorizes automatic actuation by itself.
