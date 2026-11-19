"""The Repair issues of the checks that a Tracked Config can take its latest Git
Source commits over the air.

Each failed check of a poll's `PreInstallCheck` refuses the Install and raises
its own Repair issue, which clears once the YAML is fixed and read again
(ADR-0004).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .following import PreInstallCheck

_MISSING_OTA = "missing_ota"
_REFRESH_POLICY = "refresh_policy"


def missing_ota_issue_id(entry_id: str) -> str:
    """The Repair issue raised when an entry's YAML cannot be updated over the air."""
    return f"{_MISSING_OTA}_{entry_id}"


def refresh_policy_issue_id(entry_id: str) -> str:
    """The Repair issue raised when a tracked Git Source's Refresh Policy is not `always`."""
    return f"{_REFRESH_POLICY}_{entry_id}"


class PreInstallIssues:
    """The Repair issues of an entry's pre-install checks, one per check.

    Each issue follows its check as last read: raised while it fails, cleared
    once it passes.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._title = entry.title
        self._entry_id = entry.entry_id

    def report(self, check: PreInstallCheck) -> None:
        """Raise the Repair issue of each failed check, and clear the others."""
        self._set(missing_ota_issue_id(self._entry_id), _MISSING_OTA, check.ota_missing, {})
        self._set(
            refresh_policy_issue_id(self._entry_id),
            _REFRESH_POLICY,
            bool(check.wrong_refresh_policy),
            {"sources": check.wrong_refresh_policy_names},
        )

    def _set(
        self, issue_id: str, translation_key: str, failed: bool, placeholders: dict[str, str]
    ) -> None:
        if not failed:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=translation_key,
            translation_placeholders={"title": self._title, **placeholders},
        )
