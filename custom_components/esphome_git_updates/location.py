"""Where the Device Builder runs: a manual URL, or the ESPHome add-on (ADR-0002).

An add-on location stores only the add-on's slug. Its URL is resolved on
every connection, so an add-on restart that moves its ingress port does not
break the entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.hassio import is_hassio

from .device_builder import DeviceBuilderError

CONF_ADDON_SLUG: Final = "addon_slug"

# The slug of the ESPHome add-on from the official ESPHome add-on repository.
DEFAULT_ADDON_SLUG: Final = "5c53de3b_esphome"

# Where core's esphome integration keeps the Device Builder it discovered
# through the Supervisor (core calls it the dashboard). Read by key: importing
# core's esphome would pull in aioesphomeapi, which this integration does not
# otherwise need.
_ESPHOME_DASHBOARD_MANAGER: Final = "esphome_dashboard_manager"


class DeviceBuilderNotFound(DeviceBuilderError):
    """Neither the add-on nor core's esphome integration gives a Device Builder URL."""


@dataclass(frozen=True)
class ResolvedLocation:
    """The URL and credentials to connect to, worked out from a stored location."""

    url: str
    username: str | None = None
    password: str | None = None


async def async_resolve_location(
    hass: HomeAssistant, location: Mapping[str, Any]
) -> ResolvedLocation:
    """Turn a stored location into the URL and credentials to connect with."""
    if CONF_ADDON_SLUG not in location:
        return ResolvedLocation(
            location[CONF_URL], location.get(CONF_USERNAME), location.get(CONF_PASSWORD)
        )
    slug = location[CONF_ADDON_SLUG]
    # The add-on trusts its ingress port, so no credentials (ADR-0002).
    if (port := await _async_addon_ingress_port(hass, slug)) is not None:
        return ResolvedLocation(f"http://127.0.0.1:{port}")
    if (url := _core_esphome_device_builder_url(hass)) is not None:
        return ResolvedLocation(url)
    raise DeviceBuilderNotFound(
        f"add-on {slug} not found, and core's esphome integration knows no Device Builder"
    )


async def _async_addon_ingress_port(hass: HomeAssistant, slug: str) -> int | None:
    if not is_hassio(hass):
        return None
    # Imported here: hassio is only an after_dependency, absent without Supervisor.
    from aiohasupervisor import SupervisorError  # noqa: PLC0415
    from homeassistant.components.hassio import get_supervisor_client  # noqa: PLC0415

    try:
        info = await get_supervisor_client(hass).addons.addon_info(slug)
    except SupervisorError:
        return None
    port: int | None = info.ingress_port
    return port


def _core_esphome_device_builder_url(hass: HomeAssistant) -> str | None:
    manager = hass.data.get(_ESPHOME_DASHBOARD_MANAGER)
    # While core's esphome sets up, the slot holds an asyncio.Event instead.
    if (get_dashboard := getattr(manager, "async_get", None)) is None:
        return None
    dashboard = get_dashboard()
    url: str | None = getattr(dashboard, "url", None)
    return url
