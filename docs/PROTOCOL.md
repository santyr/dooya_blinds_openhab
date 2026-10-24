# WLAN protocol and research references

## Authoritative working reference

[Connector WLAN Integration Protocol - 20240424.pdf](https://github.com/scubamikejax904/Connector-Bridge-Hubitat-direct/blob/main/Connector%20WLAN%20Integration%20Protocol%20-%2020240424.pdf)

Repository blob SHA at review: `973f2d224bb15a40933932085cb28db8d83be12d`. The document says **v1.03**; revision history dates that version to 2022-03-17. The filename is dated 2024. Link to the document rather than assuming permission to redistribute its contents.

## Implemented subset

JSON over UDP; bridge command port 32100; multicast group 238.0.0.18, report/heartbeat port 32101. The default `bridge.discovery_correlation = "echoed_id"` requires exact request/Ack type and `msgID`, source IP/port, and device MAC/type for device requests. Wrong, missing, null, or empty IDs do not trigger a fallback. Each request uses a fresh dedicated UDP socket and one monotonic timeout budget; rejected packets do not extend it.

Message IDs use the calendar-safe 17-digit UTC-shaped representation inferred from the public examples. The PDF calls them increasing timestamps but does not explicitly specify this format or timezone. A locked, fsynced private high-water file provides uniqueness across processes/restarts; without it the guarantee is process-only. Movement requires the configured durable file. A local adapter session UUID is separate from the wire ID and is not bridge authentication.

### Explicit discovery compatibility profile

The directly read v1.03 page 7 `GetDeviceListAck` example and table omit `msgID`. This is a documented public response shape, while the default remains the stronger echoed-ID contract. An installation may explicitly select:

```toml
[bridge]
mac = "AABBCCDDEEFF" # synthetic example; replace from physical/read-only acceptance
# Other bridge settings remain required.
discovery_correlation = "pinned_discovery_without_id"
```

This selection requires a configured bridge MAC before discovery. Only a `GetDeviceList` → `GetDeviceListAck` response with the `msgID` field completely absent may use the exception. It must come from the configured source IP/port, match the pinned bridge MAC and exact bridge `deviceType = "02000001"`, and contain a valid 16-byte UTF-8 token and completely validated inventory. Duplicate identities, invalid child fields, foreign bridge identity/type, malformed tokens, and resource-limit violations are rejected before any authentication/inventory update. A normally echoed, exactly matching ID is also supported in this profile, subject to its pinned bridge/type/schema checks. A present wrong, null, or empty ID is refused.

ReadDevice/WriteDevice correlation is unchanged: this exception never applies to their acknowledgements. There is no automatic profile switch after a timeout. The selected profile is diagnostic metadata in `service/state`, not motor observation evidence or actuation permission.

Without an echoed discovery ID, a dedicated fresh socket and pin reduce ambiguity but cannot exclude an unknown late discovery Ack from an earlier request or a forged permitted-LAN sender. Discovery state is therefore not evidence of current motor contact. The physical DD7066 model/firmware applicability and chosen interface/profile still require read-only Gate A acceptance. The optional software profile does not certify DD7066 compatibility or release automatic actuation. See [DD7066 evidence and commissioning limits](DD7066_COMPATIBILITY.md).

GetDeviceList obtains bridge/token/child IDs transactionally. The adapter treats ReadDevice/Ack values as cached evidence; the public document does not establish a fresh RF-contact guarantee. WriteDevice operation 5 requests motor status without promising a new Report. Operations 0, 1, 2 mean close, open, stop; targetPosition is 0=open to 100=closed. Report is an unsolicited motor-status report; the spec describes it after motion stops and for wirelessMode 1. Heartbeats indicate bridge liveness only.

KEY and token are 16-byte UTF-8 strings. AccessToken is the first AES-128 ECB encrypted block of the token, rendered uppercase hexadecimal. The specification supplies a public example key/token/output; tests use that published example only. This is authentication, not encryption of the complete UDP payload. No secret is printed or sent to an online AES calculator.

Useful fields: currentPosition, operation, currentState, batteryLevel, wirelessMode, voltageMode, RSSI, chargingState. batteryLevel represents voltage, not percent; preserve the raw value. Scaling by 100 is used by motionblinds but must be verified for installed hardware; the initial service publishes raw battery units only. Missing fields remain absent. Only a validated, uniquely ordered Report bound to the current source/session and a supported profile can qualify the service's reported position. The implemented profile requires exact integer wirelessMode=1 and type=1 (roller) or type=4 (honeycomb), family 10000000, with no contradictory dual-axis fields. TDBU/unknown families and unidirectional/unknown modes remain diagnostic inventory and cannot receive writes, including operation 5. These software profile choices do not assert the actual supplied motor type or commissioned capabilities.

The app performs pairing and add/edit/delete operations. Speed, travel-limit changes, and favorites are outside this implementation until their exact commands are verified. Persist motor IDs in local configuration, not remote channel numbers.

## Sources

- [Hubitat driver](https://github.com/scubamikejax904/Connector-Bridge-Hubitat-direct): DD7006/DD7002B implementation; reviewed parent blob `485f9fab1fb0c3cfe3d14555bf482dd9e959527b`. Its battery percentage formula is not adopted. Hubitat's position convention differs from openHAB's.
- [motionblinds](https://github.com/starkillerOG/motion-blinds): useful independent implementation; source reviewed at version 0.6.30.
- [Home Assistant supported bridges/key retrieval](https://www.home-assistant.io/integrations/motion_blinds/): D1554 family listed, not explicit DD1554E certification.
- [Dooya products/API availability](https://dooya.in/home-automation-for-curtains-and-blinds/): DV24WE/S Bi-RF and Mini/P-Box capabilities.
- [Connector on Google Play](https://play.google.com/store/apps/details?id=com.smarthome.app.connector): developer@dooya.com and support@shadeconnector.com published contacts. info@dooya.com bounced for this project. API request has been sent; reply pending.
- [HT](https://www.htwfonline.com/): sales@richview.com; 800-879-9512.
- [RF alternative article](https://drkhsh.at/posts/2026/05/decoding-dooya-40-bits-between-you-and-your-curtains/).
- [openHAB MQTT](https://www.openhab.org/addons/bindings/mqtt/).
