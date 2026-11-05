"""Config flow: locate the Device Builder, pick one Tracked Config (ADR-0001), then
optionally its Linked Device (ADR-0005)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_DEVICE_ID, CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers.selector import (
    DeviceFilterSelectorConfig,
    DeviceSelector,
    DeviceSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import slugify

from .connection import connect_to_device_builder
from .const import CONF_CONFIGURATION, DOMAIN, LINKED_DEVICE_DOMAINS
from .device_builder import (
    CONNECTION_ERRORS,
    AuthenticationFailed,
    Configuration,
    UnsupportedServerVersion,
)
from .location import CONF_ADDON_SLUG, DEFAULT_ADDON_SLUG, DeviceBuilderNotFound
from .options import (
    CONF_CLEAN_BUILD,
    CONF_GITHUB_TOKEN,
    CONF_GIT_POLL_INTERVAL,
    CONF_UPLOAD_ADDRESS,
    DEFAULT_CLEAN_BUILD,
    DEFAULT_GIT_POLL_INTERVAL,
    DEFAULT_UPLOAD_ADDRESS,
)

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

REAUTH_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_USERNAME): str,
        probatio.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_CLEAN_BUILD, default=DEFAULT_CLEAN_BUILD): bool,
        probatio.Required(CONF_GIT_POLL_INTERVAL, default=DEFAULT_GIT_POLL_INTERVAL): NumberSelector(
            NumberSelectorConfig(
                min=1, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX
            )
        ),
        probatio.Required(CONF_UPLOAD_ADDRESS, default=DEFAULT_UPLOAD_ADDRESS): str,
        probatio.Optional(CONF_GITHUB_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class EsphomeGitUpdatesConfigFlow(ConfigFlow, domain=DOMAIN):
    """One config entry per Tracked Config."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> EsphomeGitUpdatesOptionsFlow:
        """The options of a Tracked Config."""
        return EsphomeGitUpdatesOptionsFlow()

    def __init__(self) -> None:
        self._location: dict[str, str] = {}
        self._configurations: dict[str, Configuration] = {}
        self._chosen: Configuration | None = None

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

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The Device Builder rejected the stored credentials: ask for new ones."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check new credentials against the Device Builder before saving them."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            location = {
                **entry.data,
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                # Logging in is the check. A command would also depend on the
                # Device Builder still holding the configuration.
                async with connect_to_device_builder(self.hass, location):
                    pass
            except AuthenticationFailed:
                errors["base"] = "invalid_auth"
            except UnsupportedServerVersion:
                errors["base"] = "unsupported_version"
            except CONNECTION_ERRORS:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                REAUTH_SCHEMA, {CONF_USERNAME: entry.data.get(CONF_USERNAME, "")}
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_configuration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the Tracked Config from the Device Builder's configurations."""
        if user_input is not None:
            chosen = self._configurations[user_input[CONF_CONFIGURATION]]
            await self.async_set_unique_id(chosen.configuration)
            self._abort_if_unique_id_configured()
            self._chosen = chosen
            return await self.async_step_device()
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

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Optionally pick the Linked Device: the ESPHome or MQTT device the
        Tracked Config already has in Home Assistant."""
        chosen = self._chosen
        assert chosen is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = user_input.get(CONF_DEVICE_ID)
            if device_id is None or self._is_linkable(device_id):
                linked = {} if device_id is None else {CONF_DEVICE_ID: device_id}
                return self.async_create_entry(
                    title=chosen.friendly_name or chosen.name,
                    data={**self._location, CONF_CONFIGURATION: chosen.configuration, **linked},
                )
            errors[CONF_DEVICE_ID] = "not_linkable_device"
        schema = probatio.Schema(
            {
                probatio.Optional(CONF_DEVICE_ID): DeviceSelector(
                    DeviceSelectorConfig(
                        filter=[
                            DeviceFilterSelectorConfig(integration=domain)
                            for domain in LINKED_DEVICE_DOMAINS
                        ]
                    )
                )
            }
        )
        if user_input is None and (match := self._matching_device(chosen)) is not None:
            user_input = {CONF_DEVICE_ID: match}
        return self.async_show_form(
            step_id="device",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders={"name": chosen.name},
        )

    def _linkable_devices(self) -> list[dr.DeviceEntry]:
        """The devices of the ESPHome and MQTT integrations."""
        registry = dr.async_get(self.hass)
        return [
            device
            for domain in LINKED_DEVICE_DOMAINS
            for entry in self.hass.config_entries.async_entries(domain)
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        ]

    def _is_linkable(self, device_id: str) -> bool:
        return any(device.id == device_id for device in self._linkable_devices())

    def _matching_device(self, chosen: Configuration) -> str | None:
        """The one linkable device named like the YAML's `esphome: name:`, if any.

        The Device Builder's list gives that name and the friendly name; the
        registry holds the friendly name when the device has one.
        """
        names = {slugify(name) for name in (chosen.name, chosen.friendly_name) if name}
        matches = {
            device.id
            for device in self._linkable_devices()
            if device.name and slugify(device.name) in names
        }
        return next(iter(matches)) if len(matches) == 1 else None

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


class EsphomeGitUpdatesOptionsFlow(OptionsFlow):
    """How a Tracked Config is installed and polled, and the GitHub token for release notes."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options, filled in with the ones set."""
        if user_input is not None:
            token = user_input.pop(CONF_GITHUB_TOKEN, "").strip()
            return self.async_create_entry(
                data={
                    **user_input,
                    **({CONF_GITHUB_TOKEN: token} if token else {}),
                    # The number selector gives a float; the interval is whole minutes.
                    CONF_GIT_POLL_INTERVAL: int(user_input[CONF_GIT_POLL_INTERVAL]),
                    CONF_UPLOAD_ADDRESS: user_input[CONF_UPLOAD_ADDRESS].strip()
                    or DEFAULT_UPLOAD_ADDRESS,
                }
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
