"""The automation examples in the README must be automations Home Assistant accepts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.components.update import UpdateEntityStateAttribute
from homeassistant.core import HomeAssistant

README = Path(__file__).resolve().parent.parent / "README.md"


def _examples() -> list[dict[str, Any]]:
    """Every automation in a YAML block of the README's Examples section."""
    section = README.read_text().split("\n## Examples\n", 1)[1].split("\n## ", 1)[0]
    blocks = re.findall(r"```yaml\n(.*?)```", section, re.DOTALL)
    return [yaml.safe_load(block) for block in blocks]


async def test_each_example_is_a_valid_automation(hass: HomeAssistant) -> None:
    examples = _examples()
    assert len(examples) >= 2
    for example in examples:
        assert await async_validate_config_item(hass, example["alias"], example) is not None


def test_examples_read_only_attributes_an_update_entity_has() -> None:
    attributes = {attribute.value for attribute in UpdateEntityStateAttribute}
    text = "\n".join(yaml.safe_dump(example) for example in _examples())
    read = set(re.findall(r"state_attr\([^,]+,\s*'([a-z_]+)'\)", text))
    assert read, "the examples read no attribute"
    assert read <= attributes


def test_an_example_installs_with_the_update_install_action() -> None:
    text = "\n".join(yaml.safe_dump(example) for example in _examples())
    assert "action: update.install" in text
