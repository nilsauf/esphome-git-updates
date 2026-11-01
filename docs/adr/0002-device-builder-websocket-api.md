# Build through the Device Builder `/ws` API, in-process

Installs are driven from inside Home Assistant as background tasks talking to the Device Builder's multiplexed `/ws` API (`firmware/clean`, `firmware/install`, `firmware/follow_job`). The add-on is located through the Supervisor's ingress port, with a manual URL plus credentials as an alternative for dev setups and non-Supervisor installs. If the Supervisor can't find the add-on, the integration falls back to the Device Builder URL that core's `esphome` integration discovered.

## Considered Options

- **Legacy REST/`spawn` endpoints, as HA core's `esphome` integration uses.** Rejected: Device Builder marks them deprecated.
- **A detached subprocess reporting back through an HA event, as the original script did.** Rejected: it needed a double-fork to escape `shell_command`'s 60 s kill, plus an automation to receive the result.
- **The add-on's MCP endpoint.** Rejected: it is aimed at LLM tooling, not this use.

## Consequences

Device Builder describes `/ws` as unstable ("undocumented breaking changes until stable"). The protocol is kept behind one client module, checked against `server_version` on connect, and unexpected frames raise a Repair issue. The client is proven by a CLI spike against a real add-on before any integration code is written, because the original script was never run.

Jobs persist on the Device Builder across disconnects. The integration therefore stores active job ids and re-attaches with `follow_job` after an HA restart, instead of declaring the build "interrupted".
