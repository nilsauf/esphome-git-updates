# 13: Re-authenticate when Device Builder credentials stop working

**What to build:** In "manual" mode, if the Device Builder starts rejecting the stored username and password, the user is asked to re-enter them. HA's standard reauth flow shows this. At the moment the entity just becomes unavailable with no explanation. Once valid credentials are entered, the entry reloads and the entity recovers without the entry being removed.

**Context from 02** (see the `## Comments` on 02): during polling, `AuthenticationFailed` currently only makes the entity unavailable. Repeated failed logins make the Device Builder answer `rate_limited`, so the integration must stop retrying the bad credentials once reauth has started.

**Blocked by:** 02 (Tracer bullet: manual-URL entry → update entity showing ESPHome version)

**Status:** closed

- [x] An authentication failure while polling or installing starts HA's reauth flow for the entry.
- [x] Polling stops logging in with the rejected credentials until reauth completes, so it doesn't trip `rate_limited`.
- [x] The reauth flow validates the new credentials against the Device Builder and rejects wrong ones.
- [x] After a successful reauth the entry reloads and the entity becomes available again.
- [x] Tests cover the start of reauth, a rejected reauth, and a successful reauth.

**Implementation notes:**

- `reauth.py` holds `Reauthentication`: whether the stored credentials were rejected, and starting HA's reauth flow once. Both coordinators and the Install path report `AuthenticationFailed` to it.
- Once required, the version and Git coordinators raise before connecting, so the rejected credentials are never sent again. A successful reauth reloads the entry, which starts a fresh `Reauthentication`.
- `config_flow.py` adds `async_step_reauth`/`async_step_reauth_confirm`. The new credentials are validated by logging in, then saved with `async_update_reload_and_abort`, which reloads the entry.
- Add-on entries have no credentials to re-enter, so they only log the rejection and are not latched off; they keep polling in case the Device Builder's auth settings change.
- New tests in `tests/test_reauth.py` cover a rejected poll, polling staying paused, a rejected reauth, a successful reauth (including that the new credentials were sent and polling resumes), and a rejected login during an Install. The fake Device Builder now records connections and login arguments, and can queue a transcript per connection.
