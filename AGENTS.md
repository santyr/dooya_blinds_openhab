# Instructions for coding agents

Read README.md and docs/CODEX_HANDOFF.md before changing this project.
- Preserve 0=open, 100=closed across MQTT and openHAB; any per-shade inversion is applied exactly once.
- Never substitute zero for missing position or claim an acknowledgement proves motion completed.
- Never commit keys, passwords, actual home IPs, or unredacted packet captures.
- Keep movement disabled by default. Do not deploy to household systems, reset limits, or transmit movement during diagnostics without task authorization.
- Keep raw protocol behavior separate from household thermal policy.
- Test against the documented AES vector and simulated UDP traffic; hardware acceptance remains distinct.
- Document implementation gaps candidly. Update the handoff after meaningful changes.
- Do not add library copies of this Git-backed project.
