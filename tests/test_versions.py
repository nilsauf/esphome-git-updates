"""What the update entity shows: composite versions and the release summary."""

from __future__ import annotations

import pytest

from custom_components.esphome_git_updates.versions import SourceCommits, Versions, parse_sw_version

OLD = "abc1234aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW = "def5678bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
SENSORS = "1234567ccccccccccccccccccccccccccccccccc"


def test_the_composite_version_is_the_esphome_version_and_each_sources_short_sha() -> None:
    versions = Versions(
        installed_esphome="2026.10.1",
        available_esphome="2026.10.2",
        git_sources=(
            SourceCommits("marsrelay", installed=OLD, latest=NEW),
            SourceCommits("sensors", installed=SENSORS, latest=SENSORS),
        ),
    )

    assert versions.installed == "2026.10.1 (marsrelay abc1234, sensors 1234567)"
    assert versions.latest == "2026.10.2 (marsrelay def5678, sensors 1234567)"


def test_without_git_sources_the_version_is_the_esphome_version() -> None:
    versions = Versions("2026.10.1", "2026.10.1", ())

    assert (versions.installed, versions.latest) == ("2026.10.1", "2026.10.1")
    assert versions.release_summary is None


def test_an_unknown_installed_esphome_version_makes_the_installed_version_unknown() -> None:
    versions = Versions(None, "2026.10.1", (SourceCommits("marsrelay", OLD, OLD),))

    assert versions.installed is None
    assert versions.latest == "2026.10.1 (marsrelay abc1234)"


def test_the_release_summary_has_one_line_per_changed_source() -> None:
    versions = Versions(
        "2026.10.1",
        "2026.10.2",
        (
            SourceCommits("marsrelay", OLD, NEW),
            SourceCommits("sensors", SENSORS, SENSORS),
        ),
    )

    assert versions.release_summary == "ESPHome 2026.10.1 → 2026.10.2; marsrelay abc1234 → def5678"


def test_a_changed_git_source_alone_is_summarised_without_esphome() -> None:
    versions = Versions("2026.10.1", "2026.10.1", (SourceCommits("marsrelay", OLD, NEW),))

    assert versions.release_summary == "marsrelay abc1234 → def5678"


@pytest.mark.parametrize(
    ("sw_version", "esphome_version"),
    [
        # ESPHome over MQTT discovery.
        ("2026.10.1 (config hash 0x1a2b3c4d)", "2026.10.1"),
        # ESPHome over the native API.
        ("2026.10.1 (Oct 31 2026, 12:34:56)", "2026.10.1"),
        # Either, with `esphome: project:` set.
        ("1.4.0 (ESPHome 2026.10.1)", "2026.10.1"),
        ("v2-beta (ESPHome 2026.11.0b2)", "2026.11.0b2"),
        # The native API without a compilation time.
        ("2026.10.1", "2026.10.1"),
        ("2026.11.0-dev (config hash 0x00000001)", "2026.11.0-dev"),
    ],
)
def test_the_esphome_version_is_parsed_from_sw_version(
    sw_version: str, esphome_version: str
) -> None:
    assert parse_sw_version(sw_version) == esphome_version


@pytest.mark.parametrize(
    "sw_version",
    [None, "", "unknown", "1.4.0", "firmware (2026.10.1)", "1.4.0 (ESPHome )", "(ESPHome 2026.10.1)"],
)
def test_a_sw_version_that_names_no_esphome_version_is_not_parsed(sw_version: str | None) -> None:
    assert parse_sw_version(sw_version) is None
