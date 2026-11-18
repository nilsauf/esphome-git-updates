"""Where discovery finds a Tag Ref's text written, so an Install can rewrite it (ADR-0007),
and which Tag Refs it reports untrackable because their text cannot be rewritten safely."""

from __future__ import annotations

import pytest

from custom_components.esphome_git_updates.git_sources import (
    Discovery,
    discover_git_sources,
    rewritten,
)


async def _discover(files: dict[str, str]) -> Discovery:
    async def read(path: str) -> str:
        return files[path]

    return await discover_git_sources("node.yaml", read)


async def _moved(tag: str, main: str, **others: str) -> dict[str, str]:
    """Each file of the config, after moving its one Git Source's ref to *tag*."""
    files = {"node.yaml": main, **others}
    discovery = await _discover(files)
    assert discovery.untrackable == ()
    [source] = discovery.sources
    assert source.written_at is not None
    location = source.written_at
    return {**files, location.file: rewritten(files[location.file], [(location, tag)])}


async def test_a_tag_ref_in_an_included_file_is_rewritten_there() -> None:
    common = """external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: v1.4.0  # v1.4.0
"""
    main = "packages:\n  common: !include common/sensors.yaml\n"

    moved = await _moved("v1.5.0", main, **{"common/sensors.yaml": common})

    assert moved == {
        "node.yaml": main,
        "common/sensors.yaml": common.replace("ref: v1.4.0", "ref: v1.5.0"),
    }


async def test_a_tag_ref_in_a_local_package_is_rewritten_in_place() -> None:
    main = """packages:
  sensors:
    external_components:
      - source:
          type: git
          url: https://gitlab.com/someone/sensors
          ref: v1.4.0
"""

    moved = await _moved("v1.5.0", main)

    assert moved == {"node.yaml": main.replace("v1.4.0", "v1.5.0")}


@pytest.mark.parametrize(
    "written",
    [
        "github://someone/sensors@v1.4.0",
        "'github://someone/sensors@v1.4.0'",
        "github://someone/sensors/components@v1.4.0",
    ],
)
async def test_the_tag_part_of_the_github_shorthand_is_rewritten(written: str) -> None:
    main = f"external_components:\n  - source: {written}  # @v1.4.0\n"

    moved = await _moved("v1.10.0", main)

    assert moved == {
        "node.yaml": main.replace(written, written.replace("@v1.4.0", "@v1.10.0"))
    }


async def test_the_ref_of_a_remote_package_is_rewritten() -> None:
    main = """packages:
  base:
    url: https://github.com/someone/shared
    ref: v1.4.0
    files: [base.yaml]
"""

    moved = await _moved("v1.5.0", main)

    assert moved == {"node.yaml": main.replace("v1.4.0", "v1.5.0")}


async def test_the_github_shorthand_of_a_remote_package_is_rewritten() -> None:
    main = "packages:\n  base: github://someone/shared/base.yaml@v1.4.0\n"

    moved = await _moved("v1.5.0", main)

    assert moved == {"node.yaml": main.replace("v1.4.0", "v1.5.0")}


@pytest.mark.parametrize("written", ["1.10", "2", "1.10.0"])
async def test_a_number_like_ref_keeps_its_text(written: str) -> None:
    main = f"""external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: {written}
"""

    discovery = await _discover({"node.yaml": main})

    [source] = discovery.sources
    assert source.ref == written
    assert source.written_at is not None
    assert rewritten(main, [(source.written_at, "1.11")]) == main.replace(written, "1.11")


@pytest.mark.parametrize("used", ["${fw_ver}", "$fw_ver"])
async def test_a_substitution_used_only_for_the_ref_has_its_value_rewritten(used: str) -> None:
    main = f"""substitutions:
  fw_ver: "v1.4.0"  # the sensors' version
external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: {used}
"""

    moved = await _moved("v1.5.0", main)

    assert moved == {"node.yaml": main.replace('"v1.4.0"', '"v1.5.0"')}


async def test_a_substitution_used_only_as_the_shorthands_tag_has_its_value_rewritten() -> None:
    main = """substitutions:
  fw_ver: v1.4.0
external_components:
  - source: github://someone/sensors@${fw_ver}
"""

    moved = await _moved("v1.5.0", main)

    assert moved == {"node.yaml": main.replace("v1.4.0", "v1.5.0")}


async def test_a_substitution_defined_in_an_included_package_is_rewritten_there() -> None:
    common = "substitutions:\n  fw_ver: v1.4.0\n"
    main = """packages:
  common: !include common.yaml
external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: ${fw_ver}
"""

    moved = await _moved("v1.5.0", main, **{"common.yaml": common})

    assert moved == {"node.yaml": main, "common.yaml": common.replace("v1.4.0", "v1.5.0")}


async def _untrackable(main: str, **others: str) -> str:
    """Why the one Tag Ref of the config cannot be tracked."""
    discovery = await _discover({"node.yaml": main, **others})
    assert discovery.sources == ()
    [reason] = discovery.untrackable
    return reason


async def test_a_substitution_also_used_elsewhere_is_untrackable() -> None:
    reason = await _untrackable(
        """substitutions:
  fw_ver: v1.4.0
esphome:
  name: node
  project:
    name: someone.sensors
    version: ${fw_ver}
external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: ${fw_ver}
"""
    )

    assert "substitution fw_ver is also used elsewhere" in reason


async def test_a_substitution_defined_more_than_once_is_untrackable() -> None:
    reason = await _untrackable(
        """substitutions:
  fw_ver: v1.4.0
packages:
  common:
    substitutions:
      fw_ver: v1.3.0
external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: ${fw_ver}
""",
    )

    assert "substitution fw_ver is defined more than once" in reason


async def test_a_ref_from_an_includes_vars_is_untrackable() -> None:
    reason = await _untrackable(
        """packages:
  sensors: !include
    file: sensors.yaml
    vars:
      fw_ver: v1.4.0
""",
        **{
            "sensors.yaml": """external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: ${fw_ver}
"""
        },
    )

    assert "ref comes from an include's vars" in reason


@pytest.mark.parametrize(
    "source",
    [
        "{type: git, url: 'https://gitlab.com/someone/sensors', ref: 'v${major}.4.0'}",
        "github://someone/sensors@v${major}.4.0",
    ],
)
async def test_a_ref_built_from_several_parts_is_untrackable(source: str) -> None:
    reason = await _untrackable(
        f"""substitutions:
  major: "1"
  fw_ver: v1.4.0
external_components:
  - source: {source}
"""
    )

    assert "ref is built from several parts" in reason


async def test_the_same_repository_at_two_refs_is_untrackable() -> None:
    discovery = await _discover(
        {
            "node.yaml": """external_components:
  - source: github://someone/sensors@v1.4.0
  - source: github://someone/sensors@v1.5.0
  - source: github://someone/relays@main
  - source: github://someone/relays@v2.0.0
"""
        }
    )

    assert [source.ref for source in discovery.sources] == ["main"]
    assert len(discovery.untrackable) == 3
    assert all("referred to at several refs" in reason for reason in discovery.untrackable)


async def test_the_same_tag_ref_written_twice_is_untrackable() -> None:
    """Both places would have to be rewritten together."""
    discovery = await _discover(
        {
            "node.yaml": """packages:
  sensors: github://someone/sensors/sensors.yaml@v1.4.0
external_components:
  - source: github://someone/sensors@v1.4.0
"""
        }
    )

    assert discovery.sources == ()
    assert len(discovery.untrackable) == 2
    assert all("written in several places" in reason for reason in discovery.untrackable)


@pytest.mark.parametrize("other", ["github://someone/sensors", "github://someone/sensors@main"])
async def test_an_unset_ref_whose_repository_is_referred_to_twice_follows_commits(
    other: str,
) -> None:
    """A ref inserted in one place only would leave the repository at two refs."""
    discovery = await _discover(
        {
            "node.yaml": f"""packages:
  sensors: github://someone/sensors/sensors.yaml
external_components:
  - source: {other}
"""
        }
    )

    unset = [source for source in discovery.sources if source.ref is None]
    assert unset != []
    assert all(source.written_at is None for source in unset)
    [reason] = discovery.untrackable
    assert "referred to in several places" in reason
