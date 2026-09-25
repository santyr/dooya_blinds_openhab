# WLAN protocol and research references

## Authoritative working reference

[Connector WLAN Integration Protocol - 20240424.pdf](https://github.com/scubamikejax904/Connector-Bridge-Hubitat-direct/blob/main/Connector%20WLAN%20Integration%20Protocol%20-%2020240424.pdf)

Repository blob SHA at review: `973f2d224bb15a40933932085cb28db8d83be12d`. The document says **v1.03**; revision history dates that version to 2022-03-17. The filename is dated 2024. Link to the document rather than assuming permission to redistribute its contents.

## Implemented subset

JSON over UDP; bridge command port 32100; multicast group 238.0.0.18, report/heartbeat port 32101. Unicast replies are correlated by msgID and source address/port. Some firmware may differ; record and explicitly accommodate differences rather than accepting arbitrary replies. Message IDs strictly increase for each process and use a 17-digit timestamp shape.

GetDeviceList obtains bridge/token/child IDs. ReadDevice returns a bridge-cached snapshot; it does not establish fresh RF contact. WriteDevice operation 5 requests motor status. Operations 0, 1, 2 mean close, open, stop; targetPosition is 0=open to 100=closed. Report is an unsolicited motor-status report; the spec describes it after motion stops and for wirelessMode 1. Heartbeats indicate bridge liveness only.

KEY and token are 16-byte UTF-8 strings. AccessToken is the first AES-128 ECB encrypted block of the token, rendered uppercase hexadecimal. The specification supplies a public example key/token/output; tests use that published example only. This is authentication, not encryption of the complete UDP payload. No secret is printed or sent to an online AES calculator.

Useful fields: currentPosition, operation, currentState, batteryLevel, wirelessMode, voltageMode, RSSI, chargingState. batteryLevel represents voltage, not percent; preserve the raw value. Scaling by 100 is used by motionblinds but must be verified for installed hardware; the initial service publishes raw battery units only. Missing fields remain absent. Only a Report carrying valid currentPosition and wirelessMode=1 establishes the service's reported position. Other modes remain a capability gap rather than a silently estimated position.

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
