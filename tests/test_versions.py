"""What the update entity shows: composite versions and the release summary."""

from __future__ import annotations

import pytest

from custom_components.esphome_git_updates.github import Commit, Comparison
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


def _commit(sha: str, message: str) -> Commit:
    return Commit(sha, message, f"https://github.com/nilsauf/marsrelay/commit/{sha}")


THREE = Comparison(
    total_commits=3,
    commits=(
        _commit("1111111" + "a" * 33, "Add relay timer\n\nLonger body."),
        _commit("2222222" + "a" * 33, "Fix debounce"),
        _commit(NEW, "Bump version"),
    ),
)


def test_a_compared_github_source_is_summarised_with_its_commit_count() -> None:
    versions = Versions(
        "2026.10.1",
        "2026.10.1",
        (
            SourceCommits("marsrelay", OLD, NEW, THREE),
            SourceCommits("sensors", SENSORS, NEW, Comparison(1, (_commit(NEW, "One"),))),
        ),
    )

    assert versions.release_summary == (
        "marsrelay abc1234 → def5678 (3 commits); sensors 1234567 → def5678 (1 commit)"
    )


def test_the_release_notes_list_a_github_sources_commits_after_the_esphome_change() -> None:
    versions = Versions("2026.10.1", "2026.10.2", (SourceCommits("marsrelay", OLD, NEW, THREE),))

    assert versions.release_notes == (
        "### ESPHome\n"
        "2026.10.1 → 2026.10.2\n"
        "\n"
        "### marsrelay\n"
        "abc1234 → def5678 (3 commits)\n"
        "\n"
        "- [`1111111`](https://github.com/nilsauf/marsrelay/commit/1111111" + "a" * 33 + ") "
        "Add relay timer\n"
        "- [`2222222`](https://github.com/nilsauf/marsrelay/commit/2222222" + "a" * 33 + ") "
        "Fix debounce\n"
        f"- [`def5678`](https://github.com/nilsauf/marsrelay/commit/{NEW}) Bump version"
    )


def test_the_release_notes_show_only_the_sha_change_of_an_uncompared_source() -> None:
    versions = Versions("2026.10.1", "2026.10.1", (SourceCommits("marsrelay", OLD, NEW),))

    assert versions.release_notes == "### marsrelay\nabc1234 → def5678"


def test_the_release_notes_show_only_the_latest_commit_when_the_installed_one_is_unknown() -> None:
    versions = Versions("2026.10.1", "2026.10.1", (SourceCommits("marsrelay", None, NEW),))

    assert versions.release_notes == "### marsrelay\nLatest commit def5678"


def test_unchanged_sources_have_no_release_notes() -> None:
    versions = Versions(
        "2026.10.1", "2026.10.1", (SourceCommits("marsrelay", OLD, OLD), SourceCommits("s", NEW, NEW))
    )

    assert versions.release_notes is None


def test_commits_github_did_not_list_are_counted_at_the_end() -> None:
    comparison = Comparison(300, (_commit(NEW, "First listed"),))
    versions = Versions("2026.10.1", "2026.10.1", (SourceCommits("marsrelay", OLD, NEW, comparison),))

    assert versions.release_notes is not None
    assert versions.release_notes.endswith(") First listed\n- … and 299 more")


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
