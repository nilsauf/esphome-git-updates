"""HACS packaging: the files HACS reads must agree with each other and with HA."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from awesomeversion import AwesomeVersion
from homeassistant.const import __version__ as HA_VERSION

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = ROOT / "custom_components" / "esphome_git_updates"


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def test_hacs_json_declares_the_integration_with_a_reachable_minimum_ha() -> None:
    hacs = _load(ROOT / "hacs.json")
    assert hacs["name"] == _load(INTEGRATION / "manifest.json")["name"]
    # The tests run against this HA, so it must satisfy the declared minimum.
    assert AwesomeVersion(hacs["homeassistant"]) <= AwesomeVersion(HA_VERSION)


def test_manifest_has_the_keys_hacs_requires_and_matches_its_directory() -> None:
    manifest = _load(INTEGRATION / "manifest.json")
    for key in ("domain", "name", "documentation", "issue_tracker", "codeowners", "version"):
        assert manifest[key], key
    assert manifest["domain"] == INTEGRATION.name


def test_manifest_version_is_semver_and_matches_pyproject() -> None:
    version = _load(INTEGRATION / "manifest.json")["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == version


def test_no_owner_placeholder_remains() -> None:
    # The release workflow refuses to publish while the word OWNER appears in these files.
    for path in (INTEGRATION / "manifest.json", ROOT / "README.md"):
        assert "OWNER" not in path.read_text(), path
