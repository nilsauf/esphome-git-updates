"""Checks that a Tracked Config can take its latest Git Source commits over the air.

Each failed check refuses the Install and raises its own Repair issue, which
clears once the YAML is fixed and read again (ADR-0004).
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .git_sources import OTA_PLATFORMS, GitSource, OtaStatus

_MISSING_OTA = "missing_ota"
_REFRESH_POLICY = "refresh_policy"


def missing_ota_issue_id(entry_id: str) -> str:
    """The Repair issue raised when an entry's YAML cannot be updated over the air."""
    return f"{_MISSING_OTA}_{entry_id}"


def refresh_policy_issue_id(entry_id: str) -> str:
    """The Repair issue raised when a tracked Git Source's Refresh Policy is not `always`."""
    return f"{_REFRESH_POLICY}_{entry_id}"


@dataclass(frozen=True)
class PreInstallCheck:
    """What the YAML, as last read, says about installing over the air."""

    ota: OtaStatus
    # The tracked Git Sources whose Refresh Policy is not `always` or `0s`.
    wrong_refresh_policy: tuple[GitSource, ...]

    @property
    def ota_missing(self) -> bool:
        """Whether OTA is known to be missing; a remote package may add it, so unknown is not."""
        return self.ota is OtaStatus.MISSING

    @property
    def wrong_refresh_policy_names(self) -> str:
        return ", ".join(source.name for source in self.wrong_refresh_policy)

    def refusal(self, title: str) -> str | None:
        """Why an Install of *title* is refused, or None if it may go ahead."""
        reasons = []
        if self.ota_missing:
            platforms = " or ".join(f"`{platform}`" for platform in OTA_PLATFORMS)
            reasons.append(
                f"its YAML has no `ota:` platform {platforms}, so it cannot be "
                "updated over the air; flash it once over USB with OTA enabled"
            )
        if self.wrong_refresh_policy:
            reasons.append(
                f"the Git Sources {self.wrong_refresh_policy_names} lack `refresh: always`, "
                "so ESPHome may build a stale cached clone"
            )
        if not reasons:
            return None
        return f"Install of {title} refused: {'; and '.join(reasons)}. See Repairs."


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
