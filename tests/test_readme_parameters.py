"""The README documents every field the setup and options flows show, by the label shown."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.esphome_git_updates.location import DEFAULT_ADDON_SLUG
from custom_components.esphome_git_updates.options import DEFAULT_GIT_POLL_INTERVAL

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
STRINGS = json.loads((ROOT / "custom_components/esphome_git_updates/strings.json").read_text())

SETUP_STEPS = ("user", "addon", "manual", "configuration", "device")


def _section(heading: str) -> str:
    """The text under the README's level-2 *heading*, up to the next one."""
    return README.read_text().split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_the_setup_section_names_every_setup_field_and_choice() -> None:
    section = _section("Setup")
    steps = STRINGS["config"]["step"]
    labels = [
        label
        for step in SETUP_STEPS
        for key in ("data", "menu_options")
        for label in steps[step].get(key, {}).values()
    ]
    assert labels
    missing = [label for label in labels if f"**{label}**" not in section]
    assert missing == []


def test_the_setup_section_gives_the_default_add_on_slug() -> None:
    assert f"`{DEFAULT_ADDON_SLUG}`" in _section("Setup")


def test_the_options_section_names_every_option() -> None:
    section = _section("Options")
    labels = list(STRINGS["options"]["step"]["init"]["data"].values())
    missing = [label for label in labels if f"**{label}**" not in section]
    assert missing == []


def test_the_options_section_gives_the_default_git_poll_interval() -> None:
    assert f"{DEFAULT_GIT_POLL_INTERVAL} minutes" in _section("Options")


def test_reconfigure_and_removal_have_their_own_sections() -> None:
    assert "Reconfigure" in _section("Reconfigure")
    assert "HACS" in _section("Removal")
