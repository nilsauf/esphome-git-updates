"""Constants for ESPHome Git Updates."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "esphome_git_updates"

CONF_CONFIGURATION: Final = "configuration"

# The integrations whose devices can be a Tracked Config's Linked Device (ADR-0005).
LINKED_DEVICE_DOMAINS: Final = ("esphome", "mqtt")

# How often the Available ESPHome Version is read from the Device Builder.
VERSION_POLL_INTERVAL: Final = timedelta(minutes=5)

# How often Git Sources are re-read from the YAML and their Latest Commits fetched.
GIT_POLL_INTERVAL: Final = timedelta(minutes=30)
