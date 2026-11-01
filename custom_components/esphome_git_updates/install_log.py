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

    def __call__(self, stage: str, line: str) -> None:
        self.stage = stage
        line = _strip_ansi(line)
        self.tail.append(line)
        if line.startswith(_WARNING_PREFIX) and not line.startswith(_ROUTINE_WARNINGS):
            self.warnings[line] = None

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
