# Validation record

The 42-test result below is the unchanged 88e09b0 baseline only. Final DD7066
software verification belongs to the new exact revision pair and is recorded
separately; it is not hardware qualification. Actual disposable OpenHAB/Mosquitto
and signed Node→Python→MQTT→UDP-fake integrations are now maintained under
`tests/integration/`. Run their documented opt-in profiles as well as pytest.

Hardware remains not installed; physical gates in DEPLOYMENT.md are pending.


2026-09-25: **42 automated tests passed** on the development workspace. Python package installed in a virtual environment; CLI help and compileall completed. `git diff --check` passed.

Coverage includes the published AES vector, invalid key lengths/JSON, strictly increasing IDs, UDP reply correlation and timeout using a real loopback socket, discovery/command encoding, zero position and inversion, cached-vs-reported state, missing/stale values, bounded STOP-priority queue, MQTT callback rejection/reconnect behavior, token rotation, sanitized errors, and discarded pending moves after a failure.

Runtime dependency versions tested: paho-mqtt 2.1.0, pycryptodomex 3.23.0. Use requirements-tested.txt as install constraints for reproduction.

Not performed: physical bridge/motor tests, actual MQTT broker integration, real openHAB config loading, systemd installation, RF reachability, battery/solar validation, and WAN isolation/reboots. The tests use fake MQTT callbacks and simulated bridge responses; passing them does not qualify hardware. No household deployment or actuation occurred.

Repeat `.venv/bin/pytest` after changes. Fill the compatibility table in DEPLOYMENT.md during supervised hardware commissioning. Record new test counts and observed firmware behavior rather than retaining this record as proof for changed code.
