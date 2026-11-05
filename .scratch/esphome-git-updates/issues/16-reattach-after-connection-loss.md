# 16: Re-attach to an Install after the connection is lost mid-build

**Status:** needs-triage

**Question:** if the Device Builder connection drops while an Install's jobs are running, should the integration keep the active Install and follow it again, rather than clearing it and reporting "Install failed"?

**Context:** a `ConnectionLost` during an Install, for example when the add-on's websocket restarts, currently does three things:
- it clears `active_install`,
- it fails the Install,
- it leaves the Device Builder jobs running.

If the jobs then succeed, nothing records the Installed Commits, so the update is still offered. Pressing Install again cancels the running jobs, because `firmware/install` cancels a configuration's active jobs. The re-attach path from ticket 04 (`resume_install`) already exists for HA restarts.

**Options:**
- Retry `resume` in-process with a backoff.
- Keep `active_install` and re-attach on the next setup only.
- Keep today's behaviour.

Ticket 15 deliberately kept today's behaviour. Once it lands, this belongs in `InstallRunner`.
