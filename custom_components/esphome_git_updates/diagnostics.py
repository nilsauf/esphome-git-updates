"""The diagnostics download of a Tracked Config.

It reports what the integration sees, never what it could be used to impersonate:
credentials are redacted, a manual Device Builder URL is cut down to scheme, host and
port, and no YAML is included. Everything is read from memory, so the download works
while the Device Builder is unreachable.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from yarl import URL

from .coordinator import EsphomeGitUpdatesConfigEntry
from .location import CONF_ADDON_SLUG, is_manual_location
from .options import CONF_GITHUB_TOKEN

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, CONF_GITHUB_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EsphomeGitUpdatesConfigEntry
) -> dict[str, Any]:
    """The diagnostics of the Tracked Config *entry*."""
    runtime = entry.runtime_data
    data = dict(entry.data)
    if CONF_URL in data:
        data[CONF_URL] = _address(data[CONF_URL])
    manual = is_manual_location(entry.data)
    return {
        "entry": {
            "title": entry.title,
            "unique_id": entry.unique_id,
            "data": async_redact_data(data, TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "device_builder_location": {
            "kind": "manual" if manual else "add-on",
            "address": data[CONF_URL] if manual else entry.data[CONF_ADDON_SLUG],
        },
        "esphome_version": {
            "available": runtime.coordinator.data,
            "last_update_success": runtime.coordinator.last_update_success,
        },
        "git_sources": {
            "last_update_success": runtime.git_coordinator.last_update_success,
            "shown": [
                {
                    "label": source.name,
                    "installed": None
                    if source.installed is None
                    else source.label(source.installed),
                    "latest": source.label(source.latest),
                    "commits": None
                    if source.comparison is None
                    else source.comparison.total_commits,
                }
                for source in runtime.git_coordinator.shown_sources()
            ],
        },
        "installed_record": runtime.record.diagnostics(),
        "linked_device_id": None
        if runtime.linked_device is None
        else runtime.linked_device.device_id,
    }


def _address(url: str) -> str:
    """*url* reduced to its scheme, host and port: no userinfo, path or query."""
    parsed = URL(url)
    return str(URL.build(scheme=parsed.scheme, host=parsed.host or "", port=parsed.explicit_port))
