"""What an Install's output tells the user: the log tail and the warnings."""

from __future__ import annotations

import re
from collections import deque

# How many lines of output a failure notification shows.
LOG_TAIL_LINES = 30

# Colour codes arrive both as ESC bytes and, from ESPHome's own logger, as the
# literal text `\033[...m` (ticket 01).
_ANSI = re.compile(r"(?:\x1b|\\033)\[[0-9;]*[A-Za-z]")


# ESPHome's own warnings are log lines at level WARNING. Compiler `warning:`
# lines are not counted: every ESP-IDF build prints dozens from upstream code.
_WARNING_PREFIX = "WARNING "

# WARNING lines ESPHome prints on every Install of a certain kind, which would
# otherwise turn every successful Install into a notification.
_ROUTINE_WARNINGS = (
    # Every compile sent to a remote build server.
    "WARNING Bundle contains secrets",
)


# A Deferred Install, refused (spec step 4).
DEVICE_OFFLINE_TEXT = (
    "The device is offline. The Device Builder would have kept the firmware and "
    "flashed it once the device came back, which ESPHome Git Updates never lets it "
    "do: the queued update was cleared and nothing was installed. "
    "Install again once the device is online."
)


# A re-attached Install whose jobs the Device Builder no longer knows.
OUTCOME_UNKNOWN_TEXT = (
    "Home Assistant restarted while an Install was running, and the Device Builder "
    "no longer knows its jobs, so whether it succeeded is unknown. Nothing was "
    "recorded: if an update is still offered, install it again."
)

# What an over-the-air upload prints when it cannot reach the device. The
# Device Builder only knows a device is offline while its web UI is open, so
# otherwise an offline device compiles and then fails like this (spec step 4).
_UNREACHABLE = re.compile(
    r"connecting to .* failed|error resolving ip address|connection refused"
    r"|no route to host|host is unreachable|network is unreachable|timed out",
    re.IGNORECASE,
)

DEVICE_UNREACHABLE = "device offline / unreachable"


def _strip_ansi(line: str) -> str:
    return _ANSI.sub("", line).rstrip()


class InstallLog:
    """Collects an Install's output lines, as `DeviceBuilderClient.install` reports them."""

    def __init__(self) -> None:
        # The stage the Install has reached, for a failure that is not a job's
        # result, such as a lost connection.
        self.stage = "connect"
        self.tail: deque[str] = deque(maxlen=LOG_TAIL_LINES)
        # The distinct warnings, in the order they first appeared.
        self.warnings: dict[str, None] = {}
        # Whether the upload said it could not reach the device.
        self.device_unreachable = False

    def __call__(self, stage: str, line: str) -> None:
        self.stage = stage
        line = _strip_ansi(line)
        self.tail.append(line)
        if line.startswith(_WARNING_PREFIX) and not line.startswith(_ROUTINE_WARNINGS):
            self.warnings[line] = None
        if stage == "upload" and _UNREACHABLE.search(line):
            self.device_unreachable = True

    def failure_text(self, stage: str, error: str) -> str:
        tail = "\n".join(self.tail)
        return (
            f"Install failed at stage **{stage}**: {error}\n\n"
            f"Last log lines:\n\n```text\n{tail}\n```\n\n"
            "The full output is in the ESPHome Device Builder."
        )

    def warnings_text(self) -> str:
        warnings = "\n".join(self.warnings)
        return f"The Install succeeded, but ESPHome warned:\n\n```text\n{warnings}\n```"
