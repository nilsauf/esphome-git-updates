# Require `refresh: always` instead of cleaning to pick up new commits

ESPHome reuses its cached clone of a Git Source until `refresh` (default `1d`) expires. `firmware/clean` only deletes the build directory and does not re-fetch, so the original script's clean step never guaranteed a new commit was built. The only other ways to force a fetch are `refresh: always` in the YAML, or `firmware/reset_build_env`, which wipes every device's PlatformIO and toolchain caches.

We decided:
- a Tracked Config whose Git Sources lack `refresh: always` (or `0s`) gets a Repair issue;
- Clean Build becomes an opt-in option rather than a standard step.
