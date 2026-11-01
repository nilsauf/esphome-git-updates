# Require `refresh: always` instead of cleaning to pick up new commits

ESPHome reuses its cached clone of a Git Source until `refresh` (default `1d`) expires. `firmware/clean` deletes build output and build caches but not the cached clones or their refresh timestamps, and it skips Git Source updates itself, so the original script's clean step never guaranteed a new commit was built. The only other ways to force a fetch are `refresh: always` in the YAML, or `firmware/reset_build_env`, which wipes the whole ESPHome data directory except `storage`: every device's clones, PlatformIO and toolchain caches.

We decided:
- a Tracked Config whose Git Sources lack `refresh: always` (or `0s`) gets a Repair issue;
- Clean Build becomes an opt-in option rather than a standard step.

## Consequences

Checked against ESPHome 2026.10.1 and Device Builder 1.26.0 (ticket 08):
- A changed `ref:` changes the clone's cache key, so it is always cloned fresh, whatever its `refresh`.
- A remote build server fetches Git Sources itself, into its own clone cache with its own refresh timer. The bundle sent to it carries no clones. `refresh: always` covers it too, because it reads the same YAML.
- A clean also deletes caches every config shares (`pio_components` and PlatformIO's download cache), so it slows the next build of every config, not just this one. That is one more reason Clean Build is opt-in.
