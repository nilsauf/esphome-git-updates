# 05: Translating every Install refusal and failure

**What to build:** The Gold `exception-translations` rule. Every exception a user can see becomes translatable, with its details kept as placeholders:

- the pre-Install refusal (missing `ota:` platform, or a Git Source without `refresh: always`), one key per reason, naming the platforms and the Git Sources;
- the device-offline refusal;
- the unreadable-YAML refusal;
- the interrupted-Install error;
- the failed Install, with the stage and the error kept;
- the unknown Install outcome.

`Refusal` stops carrying an English sentence. It carries a translation key and placeholders, which keeps `following` free of Home Assistant and lets the coordinator that raises it pick the message. The prose `summary` on the Install endings is removed: an ending is identified by its type, and the raise site chooses the text. Notification headlines and messages are unchanged — they are notifications, not exceptions.

**Blocked by:** 01

**Status:** resolved

- [ ] `Refusal` is a translation key plus placeholders, with no prose; `following` still imports nothing from Home Assistant.
- [ ] The pre-install check returns a refusal for a missing `ota:` platform and for a Git Source without `refresh: always`, and both are raised as a translated error naming the same details as before.
- [ ] Every `HomeAssistantError` raised for an Install carries the integration's translation domain, a key and its placeholders, and no message built from prose.
- [ ] The Install endings no longer carry a `summary`; the raise site switches on the ending's type for the failed, device-offline and unknown-outcome messages.
- [ ] All new keys exist in `strings.json` and `translations/en.json`.
- [ ] Existing tests that asserted refusal prose now assert the key and placeholders; new tests cover each ending type through the install action.
- [ ] Notification text is byte-for-byte unchanged.

## Comments

Implemented and merged into `quality-scale-platinum` (2026-11-26), followed by the code-review fixes merge.
