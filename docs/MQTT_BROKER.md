# MQTT broker on the openHAB Ubuntu host

The owner has authorized Codex to check the existing Ubuntu Server host and set up an MQTT broker if needed. The adapter runs on that same host as openHAB; no extra Raspberry Pi is needed. This is a deployment runbook, not a record that the household host has been inspected or changed.

## Check before installing

On the actual host, inspect openHAB's MQTT binding and existing broker Things (UI or `/etc/openhab/things/`), the existing installation's documented broker endpoint, and the local services/listeners:

```sh
systemctl status mosquitto --no-pager
ss -ltn '( sport = :1883 or sport = :8883 )'
command -v mosquitto_sub
```

Also check any broker running in a container, on another LAN machine, or on a nonstandard port; openHAB's MQTT binding is a client and does not itself provide a broker. Confirm an existing broker with a real authenticated publish/subscribe test before reusing it. Preserve its listeners, credentials, other clients and retained topics. Record broker address, port, TLS mode, authentication method and openHAB broker Thing UID. If an appropriate reachable broker exists, use it and skip installation. If one is absent, install Mosquitto locally as below.

## Install only if absent

On Ubuntu, use the distribution packages (`sudo apt update && sudo apt install mosquitto mosquitto-clients`); check the installed version and the current `/etc/mosquitto/mosquitto.conf` and `conf.d/` before editing anything. For a new broker used only by local openHAB and this adapter, configure a **loopback-only** listener on `127.0.0.1:1883`, disable anonymous access and use a password file. In a new dedicated `/etc/mosquitto/conf.d/dooya-local.conf` (provided the base config actually loads `conf.d`), for example:

```conf
listener 1883 127.0.0.1
allow_anonymous false
password_file /etc/mosquitto/passwd
```

Create the password file and separate credentials interactively, without putting passwords in shell arguments or the repository. `sudo mosquitto_passwd -c /etc/mosquitto/passwd dooya` creates the file and prompts for the adapter password; use `sudo mosquitto_passwd /etc/mosquitto/passwd openhab` for a separate openHAB account (no `-c`, which would erase the first account). Restrict the file so the Mosquitto process can read it and other users cannot; verify the service's actual user/group before setting ownership and mode. Start or restart the service and inspect `systemctl status mosquitto` and `journalctl -u mosquitto` for configuration errors. If `1883` is already in use, investigate rather than replace the listener. A loopback listener will not serve remote MQTT clients; if the household has remote MQTT devices, integrate with their existing broker/listener and access policy instead of applying this example unchanged.

Keep the adapter's password in `/etc/dooya-blinds/mqtt.password` with ownership restricted to its service account, and set `mqtt.host = "127.0.0.1"`, `mqtt.port = 1883`, `mqtt.username = "dooya"`, `mqtt.tls = false` in its private config. Set up or reuse an openHAB MQTT broker Thing pointed at the *same* broker, with the separate openHAB account. Import `examples/openhab.things` after adjusting its parent broker Thing UID and shade IDs; import matching Items. Avoid creating a duplicate Thing with an existing UID. If reusing a LAN/TLS broker, match its host/port/TLS/auth in both clients and use a CA trusted by the adapter; the current adapter's `tls=true` uses system trust and does not expose a custom CA or client-certificate setting.

## Verify without moving shades

With the broker running, subscribe from one terminal and publish a temporary, non-command topic from another using authenticated `mosquitto_sub`/`mosquitto_pub`; supply passwords via a protected client config or interactive means, not command-line `-P` or shell history. Check that openHAB's broker Thing is ONLINE, start the adapter with `allow_commands = false`, and observe `earthship/shades/v1/service/availability` plus a read-only state query. Verify the openHAB shade Thing and Items receive state and offline/stale transitions; do not publish anything to `+/command` as a connectivity test. Restart Mosquitto and the adapter independently to check reconnection and no retained movement. Record versions, listener/broker location, sanitized results and any site-specific config in the deployment record. Test shade motion later under supervised commissioning.

Primary references: [openHAB MQTT binding](https://www.openhab.org/addons/bindings/mqtt/), [Mosquitto configuration](https://mosquitto.org/man/mosquitto-conf-5.html), [mosquitto_passwd](https://mosquitto.org/man/mosquitto_passwd-1.html).
