"""The declared quality scale tier, kept honest.

hassfest validates `quality_scale.yaml` for core integrations only, so this
repository checks it: every rule is listed once with a status, every exemption
says why, the manifest declares the tier the file earns, and the evidence each
locally checkable rule needs is still there. Documentation rules are reviewed
by a human, not asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.esphome_git_updates import diagnostics, update
from custom_components.esphome_git_updates.config_flow import EsphomeGitUpdatesConfigFlow
from fake_device_builder import serve, server_info
from tracked_config import CONFIGURATION, ENTITY_ID, OTA, set_up

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = ROOT / "custom_components" / "esphome_git_updates"

TIERS = ("bronze", "silver", "gold", "platinum")

# Home Assistant's rule list (script/hassfest/quality_scale.py in core), by tier.
RULES: dict[str, str] = {
    **dict.fromkeys(
        (
            "action-setup",
            "appropriate-polling",
            "brands",
            "common-modules",
            "config-flow",
            "config-flow-test-coverage",
            "dependency-transparency",
            "docs-actions",
            "docs-conditions",
            "docs-high-level-description",
            "docs-installation-instructions",
            "docs-removal-instructions",
            "docs-triggers",
            "entity-event-setup",
            "entity-unique-id",
            "has-entity-name",
            "runtime-data",
            "test-before-configure",
            "test-before-setup",
            "unique-config-entry",
        ),
        "bronze",
    ),
    **dict.fromkeys(
        (
            "action-exceptions",
            "config-entry-unloading",
            "docs-configuration-parameters",
            "docs-installation-parameters",
            "entity-unavailable",
            "integration-owner",
            "log-when-unavailable",
            "parallel-updates",
            "reauthentication-flow",
            "test-coverage",
        ),
        "silver",
    ),
    **dict.fromkeys(
        (
            "devices",
            "diagnostics",
            "discovery",
            "discovery-update-info",
            "docs-data-update",
            "docs-examples",
            "docs-known-limitations",
            "docs-supported-devices",
            "docs-supported-functions",
            "docs-troubleshooting",
            "docs-use-cases",
            "dynamic-devices",
            "entity-category",
            "entity-device-class",
            "entity-disabled-by-default",
            "entity-translations",
            "exception-translations",
            "icon-translations",
            "reconfiguration-flow",
            "repair-issues",
            "stale-devices",
        ),
        "gold",
    ),
    **dict.fromkeys(("async-dependency", "inject-websession", "strict-typing"), "platinum"),
}


class _UniqueKeyLoader(yaml.SafeLoader):
    """A YAML loader that refuses a key given twice, which PyYAML would keep silently."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        assert not duplicates, f"listed more than once: {duplicates}"
        return super().construct_mapping(node, deep=deep)


def _statuses() -> dict[str, dict[str, str]]:
    """Each listed rule's status and comment, in the file's two spellings."""
    text = (INTEGRATION / "quality_scale.yaml").read_text()
    rules = yaml.load(text, Loader=_UniqueKeyLoader)["rules"]
    return {
        rule: {"status": entry} if isinstance(entry, str) else dict(entry)
        for rule, entry in rules.items()
    }


def _earned_tier(statuses: dict[str, dict[str, str]]) -> str | None:
    """The highest tier whose rules, and every lower tier's, are done or exempt."""
    earned = None
    for tier in TIERS:
        if any(
            statuses[rule]["status"] not in ("done", "exempt")
            for rule, rule_tier in RULES.items()
            if rule_tier == tier
        ):
            break
        earned = tier
    return earned


def _manifest() -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((INTEGRATION / "manifest.json").read_text())
    return manifest


def test_every_rule_is_listed_once_and_no_unknown_rule_is() -> None:
    assert len(RULES) == 54
    assert sorted(_statuses()) == sorted(RULES)


def test_every_status_is_one_the_quality_scale_knows() -> None:
    for rule, entry in _statuses().items():
        assert entry["status"] in ("todo", "done", "exempt"), rule


def test_every_exemption_says_why() -> None:
    for rule, entry in _statuses().items():
        if entry["status"] == "exempt":
            assert str(entry.get("comment", "")).strip(), rule


def test_the_manifest_declares_platinum_and_keeps_the_code_owner() -> None:
    manifest = _manifest()
    assert manifest["quality_scale"] == "platinum"
    assert manifest["codeowners"] == ["@nilsauf"]


def test_every_rule_up_to_the_declared_tier_is_done_or_exempt() -> None:
    declared = TIERS.index(_manifest()["quality_scale"])
    statuses = _statuses()
    for rule, tier in RULES.items():
        if TIERS.index(tier) <= declared:
            assert statuses[rule]["status"] in ("done", "exempt"), rule


def test_the_manifest_declares_the_tier_the_file_earns() -> None:
    assert _manifest()["quality_scale"] == _earned_tier(_statuses())


# The evidence for the rules a file can show. Removing it must fail here.


def test_diagnostics_evidence_the_entry_has_a_diagnostics_platform() -> None:
    assert callable(getattr(diagnostics, "async_get_config_entry_diagnostics", None))


def test_reconfiguration_flow_evidence_the_config_flow_has_a_reconfigure_step() -> None:
    assert callable(getattr(EsphomeGitUpdatesConfigFlow, "async_step_reconfigure", None))


def test_parallel_updates_evidence_the_update_platform_declares_its_limit() -> None:
    assert isinstance(getattr(update, "PARALLEL_UPDATES", None), int)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_icon_translations_evidence_the_entity_key_has_icon_definitions(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    async with serve(
        [server_info(esphome_version="2026.10.1")],
        {CONFIGURATION: f"esphome:\n  name: host-node\n{OTA}"},
    ) as (_, fake):
        await set_up(hass, fake)
        entity = entity_registry.async_get(ENTITY_ID)
        assert entity is not None
        assert entity.translation_key, "the update entity sets no translation key"
        icons = json.loads((INTEGRATION / "icons.json").read_text())
        assert entity.translation_key in icons["entity"]["update"]


def test_strict_typing_evidence_ci_type_checks_the_standalone_cli_strictly() -> None:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    runs = [
        " ".join(str(step.get("run", "")).split())
        for job in ci["jobs"].values()
        for step in job.get("steps", [])
    ]
    assert "mypy" in runs
    assert any(
        run.startswith("mypy ") and "--strict" in run and "scripts/device_builder_cli.py" in run
        for run in runs
    )


def test_translation_evidence_strings_and_the_runtime_copy_agree() -> None:
    strings = json.loads((INTEGRATION / "strings.json").read_text())
    runtime_copy = json.loads((INTEGRATION / "translations" / "en.json").read_text())
    assert strings == runtime_copy
