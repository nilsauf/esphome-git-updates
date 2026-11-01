"""Config flow: locate the Device Builder, then pick one Tracked Config (ADR-0001)."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .connection import CONNECTION_ERRORS, connect_to_device_builder
from .const import CONF_CONFIGURATION, DOMAIN
from .device_builder import (
    AuthenticationFailed,
    Configuration,
    UnsupportedServerVersion,
)
from .location import CONF_ADDON_SLUG, DEFAULT_ADDON_SLUG, DeviceBuilderNotFound

ADDON_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_ADDON_SLUG, default=DEFAULT_ADDON_SLUG): str}
)

MANUAL_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        probatio.Optional(CONF_USERNAME): str,
        probatio.Optional(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class EsphomeGitUpdatesConfigFlow(ConfigFlow, domain=DOMAIN):
    """One config entry per Tracked Config."""

    VERSION = 1

    def __init__(self) -> None:
        self._location: dict[str, str] = {}
        self._configurations: dict[str, Configuration] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer the ESPHome add-on on a Supervisor install, else ask for a URL."""
        if is_hassio(self.hass):
            return self.async_show_menu(step_id="user", menu_options=["addon", "manual"])
        return await self.async_step_manual()

    async def async_step_addon(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Use the ESPHome add-on, found by its slug through the Supervisor."""
        return await self._async_step_location("addon", ADDON_SCHEMA, user_input)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a manual Device Builder URL and optional credentials."""
        return await self._async_step_location("manual", MANUAL_SCHEMA, user_input)

    async def async_step_configuration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the Tracked Config from the Device Builder's configurations."""
        if user_input is not None:
            chosen = self._configurations[user_input[CONF_CONFIGURATION]]
            await self.async_set_unique_id(chosen.configuration)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=chosen.friendly_name or chosen.name,
                data={**self._location, CONF_CONFIGURATION: chosen.configuration},
            )
        options = [
            SelectOptionDict(value=c.configuration, label=c.friendly_name or c.name)
            for c in self._configurations.values()
        ]
        return self.async_show_form(
            step_id="configuration",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_CONFIGURATION): SelectSelector(SelectSelectorConfig(options=options))}
            ),
        )

    async def _async_step_location(
        self, step_id: str, schema: probatio.Schema, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Show a location form; once it connects, go on to pick the Tracked Config."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                configurations = await self._list_configurations(user_input)
            except DeviceBuilderNotFound:
                errors["base"] = "addon_not_found"
            except AuthenticationFailed:
                errors["base"] = "invalid_auth"
            except UnsupportedServerVersion:
                errors["base"] = "unsupported_version"
            except CONNECTION_ERRORS:
                errors["base"] = "cannot_connect"
            else:
                if not configurations:
                    return self.async_abort(reason="no_configurations")
                self._location = user_input
                self._configurations = {c.configuration: c for c in configurations}
                return await self.async_step_configuration()
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def _list_configurations(self, location: dict[str, Any]) -> list[Configuration]:
        async with connect_to_device_builder(self.hass, location) as client:
            return await client.list_configurations()
