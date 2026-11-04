"""Ask for new Device Builder credentials when the stored ones are rejected."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .device_builder import AuthenticationFailed
from .location import is_manual_location

_LOGGER = logging.getLogger(__name__)


class Reauthentication:
    """The entry's login state after the Device Builder rejects its credentials.

    Manual entries ask the user for new credentials. Pollers ask `required`
    before connecting, so once the credentials are rejected they are not tried
    again: the Device Builder answers repeated failed logins with `rate_limited`
    (ticket 13). A successful reauth reloads the entry, which builds a fresh one
    of these.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._required = False
        self._reported_addon = False

    @property
    def required(self) -> bool:
        """Whether the stored credentials were rejected, so polling must pause."""
        return self._required

    @callback
    def async_require(self, err: AuthenticationFailed) -> None:
        """Stop connecting and ask the user for new credentials, once."""
        if self._required:
            return
        if not is_manual_location(self._entry.data):
            # An add-on entry has no credentials to re-enter: its ingress port
            # trusts the caller, so a rejected login means its auth settings changed.
            # Keep polling, but do not latch: there is no reauth flow that could
            # unpause it.
            if not self._reported_addon:
                _LOGGER.error(
                    "%s: the Device Builder rejected the login, but this entry uses "
                    "the add-on, which has no credentials to update: %s",
                    self._entry.title,
                    err,
                )
                self._reported_addon = True
            return
        self._required = True
        _LOGGER.warning(
            "%s: the Device Builder rejected the stored username or password; "
            "asking for new ones and pausing polls until then: %s",
            self._entry.title,
            err,
        )
        self._entry.async_start_reauth(self._hass)
