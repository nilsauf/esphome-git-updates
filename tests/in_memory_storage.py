"""Storage for an Installed Record, held in memory in place of Home Assistant's store."""

from __future__ import annotations

import json
from typing import Any


class InMemoryStorage:
    """Keeps what was last saved, as JSON would, and counts the saves."""

    def __init__(self) -> None:
        self._saved: str | None = None
        self.saves = 0

    async def async_load(self) -> Any:
        return None if self._saved is None else json.loads(self._saved)

    async def async_save(self, data: Any) -> None:
        self._saved = json.dumps(data)
        self.saves += 1

    async def async_remove(self) -> None:
        self._saved = None
