"""Checks that a Tracked Config can take its latest Git Source commits over the air.

Each failed check refuses the Install and raises its own Repair issue, which
clears once the YAML is fixed and read again (ADR-0004).
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_MISSING_OTA = "missing_ota"
_REFRESH_POLICY = "refresh_policy"


def _issue_id(kind: str, entry_id: str) -> str:
    return f"{kind}_{entry_id}"


@dataclass(frozen=True)
class PreInstallCheck:
    """What the YAML, as last read, says about installing over the air."""

    # No `ota:` entry with platform `esphome` or `web_server`.
    missing_ota: bool
    # The names of the tracked Git Sources without `refresh: always` or `0s`.
    stale_refresh: tuple[str, ...]

    def refusal(self, title: str) -> str | None:
        """Why an Install of *title* is refused, or None if it may go ahead."""
        reasons = []
        if self.missing_ota:
            reasons.append(
                "its YAML has no `ota:` platform `esphome` or `web_server`, so it cannot be "
                "updated over the air; flash it once over USB with OTA enabled"
            )
        if self.stale_refresh:
            reasons.append(
                f"the Git Sources {', '.join(self.stale_refresh)} lack `refresh: always`, "
                "so ESPHome may build a stale cached copy"
            )
        if not reasons:
            return None
        return f"Install of {title} refused: {'; and '.join(reasons)}. See Repairs."

    def update_issues(self, hass: HomeAssistant, entry_id: str, title: str) -> None:
        """Raise the Repair issue of each failed check, and clear the others."""
        _set_issue(hass, _MISSING_OTA, entry_id, self.missing_ota, {"title": title})
        _set_issue(
            hass,
            _REFRESH_POLICY,
            entry_id,
            bool(self.stale_refresh),
            {"title": title, "sources": ", ".join(self.stale_refresh)},
        )


def delete_pre_install_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Drop the Repair issues of a removed Tracked Config."""
    for kind in (_MISSING_OTA, _REFRESH_POLICY):
        ir.async_delete_issue(hass, DOMAIN, _issue_id(kind, entry_id))


def _set_issue(
    hass: HomeAssistant, kind: str, entry_id: str, failed: bool, placeholders: dict[str, str]
) -> None:
    if not failed:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(kind, entry_id))
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(kind, entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=kind,
        translation_placeholders=placeholders,
    )
