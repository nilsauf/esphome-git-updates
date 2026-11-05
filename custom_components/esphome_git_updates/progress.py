"""Map a job's own progress onto the one 0-100 bar of an Install (ticket 14)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .device_builder import JobProgress, Stage


@dataclass(frozen=True)
class Band:
    """Where one job's 0-100 progress appears on the Install's bar."""

    start: int
    end: int


# The fixed bands, in one place. Without a Clean Build, compile starts at 0.
BANDS_WITH_CLEAN: Final[Mapping[Stage, Band]] = {
    Stage.CLEAN: Band(0, 5),
    Stage.COMPILE: Band(5, 90),
    Stage.UPLOAD: Band(90, 100),
}
BANDS_WITHOUT_CLEAN: Final[Mapping[Stage, Band]] = {
    Stage.COMPILE: Band(0, 90),
    Stage.UPLOAD: Band(90, 100),
}


class InstallProgress:
    """The Install's bar: one value that only ever moves forward.

    A job's own progress is scaled into its band. `None` holds the last value,
    and the end of a job moves the bar to the end of its band. A job that
    reports less than the bar has already reached does not move it back.
    """

    def __init__(self, clean_build: bool) -> None:
        self._bands = BANDS_WITH_CLEAN if clean_build else BANDS_WITHOUT_CLEAN
        # The bar is 0 until the first progress arrives.
        self.percentage = 0

    def report(self, update: JobProgress) -> bool:
        """Apply *update*; return whether the bar moved."""
        band = self._bands.get(update.stage)
        if band is None:
            return False
        if update.finished:
            value = band.end
        elif update.percent is None:
            return False
        else:
            value = band.start + (band.end - band.start) * update.percent // 100
        value = max(0, min(100, value))
        if value <= self.percentage:
            return False
        self.percentage = value
        return True
