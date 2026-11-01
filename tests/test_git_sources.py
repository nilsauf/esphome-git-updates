"""Discovering a Tracked Config's Git Sources from its raw YAML (ADR-0006)."""

from __future__ import annotations

import pytest

from custom_components.esphome_git_updates.git_sources import (
    Discovery,
    DiscoveryError,
    GitSource,
    OtaStatus,
    discover_git_sources,
)


class Files:
    """The Device Builder's config directory, as `devices/get_config` reads it."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.reads: list[str] = []

    async def read(self, path: str) -> str:
        self.reads.append(path)
        return self.files[path]


async def _discover(main: str, **others: str) -> Discovery:
    return await discover_git_sources("node.yaml", Files({"node.yaml": main, **others}).read)


async def test_a_github_external_component_without_a_ref_is_a_floating_git_source() -> None:
    discovery = await _discover(
        """
external_components:
  - source: github://tomquist/marsrelay
    components: [marsrelay]
"""
    )

    assert discovery.sources == (
        GitSource(name="marsrelay", url="https://github.com/tomquist/marsrelay.git", ref=None),
    )
    assert discovery.untrackable == ()


async def test_a_long_form_git_external_component_keeps_its_url_and_ref() -> None:
    discovery = await _discover(
        """
external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: dev
"""
    )

    assert discovery.sources == (
        GitSource(name="sensors", url="https://gitlab.com/someone/sensors", ref="dev"),
    )


async def test_local_and_path_sources_are_not_git_sources() -> None:
    discovery = await _discover(
        """
external_components:
  - source: my_components
  - source:
      type: local
      path: other_components
"""
    )

    assert discovery.sources == ()


@pytest.mark.parametrize(
    "ref", ["0123456789abcdef0123456789abcdef01234567", "0123abc"]
)
async def test_a_ref_that_is_a_commit_sha_is_pinned_and_ignored(ref: str) -> None:
    """Tags are Pinned Refs too, but only the remote can tell a tag from a branch."""
    discovery = await _discover(
        f"""
external_components:
  - source: github://tomquist/marsrelay@{ref}
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: {ref}
"""
    )

    assert discovery.sources == ()


async def test_substitutions_are_applied_to_urls_and_refs() -> None:
    discovery = await _discover(
        """
substitutions:
  owner: tomquist
  branch: main
  host: gitlab.com
external_components:
  - source: github://${owner}/marsrelay@$branch
  - source:
      type: git
      url: https://${host}/someone/sensors
      ref: ${branch}
"""
    )

    assert discovery.sources == (
        GitSource(name="marsrelay", url="https://github.com/tomquist/marsrelay.git", ref="main"),
        GitSource(name="sensors", url="https://gitlab.com/someone/sensors", ref="main"),
    )


async def test_a_source_that_depends_on_a_secret_is_untrackable_and_secrets_are_never_read() -> None:
    files = Files(
        {
            "node.yaml": """
substitutions:
  private_ref: !secret marsrelay_ref
wifi:
  password: !secret wifi_password
external_components:
  - source:
      type: git
      url: !secret private_repo_url
  - source: github://tomquist/marsrelay@${private_ref}
  - source: github://esphome/esphome-components
""",
            "secrets.yaml": "private_repo_url: https://example.com/x.git\n",
        }
    )

    discovery = await discover_git_sources("node.yaml", files.read)

    assert discovery.sources == (
        GitSource(
            name="esphome-components",
            url="https://github.com/esphome/esphome-components.git",
            ref=None,
        ),
    )
    assert len(discovery.untrackable) == 2
    assert "!secret private_repo_url" in discovery.untrackable[0]
    assert "!secret marsrelay_ref" in discovery.untrackable[1]
    assert files.reads == ["node.yaml"]


async def test_included_files_are_followed_relative_to_the_file_that_includes_them() -> None:
    files = Files(
        {
            "node.yaml": """
wifi: !include common/wifi.yaml
external_components: !include common/external.yaml
""",
            "common/wifi.yaml": "ssid: home\n",
            "common/external.yaml": """
- source: github://tomquist/marsrelay
- !include { file: sensors.yaml, vars: { branch: dev } }
""",
            "common/sensors.yaml": """
source:
  type: git
  url: https://gitlab.com/someone/sensors
  ref: ${branch}
""",
        }
    )

    discovery = await discover_git_sources("node.yaml", files.read)

    assert discovery.sources == (
        GitSource(name="marsrelay", url="https://github.com/tomquist/marsrelay.git", ref=None),
        GitSource(name="sensors", url="https://gitlab.com/someone/sensors", ref="dev"),
    )
    assert sorted(files.reads) == [
        "common/external.yaml",
        "common/sensors.yaml",
        "common/wifi.yaml",
        "node.yaml",
    ]


async def test_local_packages_contribute_their_sources_and_substitutions() -> None:
    discovery = await _discover(
        """
substitutions:
  branch: main
packages:
  base: !include packages/base.yaml
""",
        **{
            "packages/base.yaml": """
substitutions:
  branch: ignored-the-main-config-wins
  sensors_ref: dev
packages:
  - !include nested.yaml
external_components:
  - source: github://tomquist/marsrelay@${branch}
""",
            "packages/nested.yaml": """
external_components:
  - source:
      type: git
      url: https://gitlab.com/someone/sensors
      ref: ${sensors_ref}
""",
        },
    )

    # In the order the YAML reads: base.yaml's packages come before its components.
    assert discovery.sources == (
        GitSource(name="sensors", url="https://gitlab.com/someone/sensors", ref="dev"),
        GitSource(name="marsrelay", url="https://github.com/tomquist/marsrelay.git", ref="main"),
    )


async def test_remote_packages_are_git_sources_but_are_not_read() -> None:
    files = Files(
        {
            "node.yaml": """
packages:
  marsrelay: github://tomquist/marsrelay/esphome/b2500.yaml@main
  sensors:
    url: https://gitlab.com/someone/sensors
    files: [sensors.yaml]
    refresh: always
  pinned:
    url: https://github.com/esphome/firmware
    ref: 0123456789abcdef0123456789abcdef01234567
    files: [base.yaml]
"""
        }
    )

    discovery = await discover_git_sources("node.yaml", files.read)

    assert discovery.sources == (
        GitSource(name="marsrelay", url="https://github.com/tomquist/marsrelay.git", ref="main"),
        GitSource(name="sensors", url="https://gitlab.com/someone/sensors", ref=None),
    )
    assert files.reads == ["node.yaml"]


async def test_other_esphome_tags_are_tolerated_and_directory_includes_are_reported() -> None:
    discovery = await _discover(
        """
packages: !include_dir_named packages
sensor:
  - platform: template
    id: !extend some_sensor
    lambda: !lambda return 1;
external_components:
  - source: github://tomquist/marsrelay
"""
    )

    assert discovery.sources == (
        GitSource(name="marsrelay", url="https://github.com/tomquist/marsrelay.git", ref=None),
    )
    assert len(discovery.untrackable) == 1
    assert "!include_dir_named packages" in discovery.untrackable[0]


async def test_duplicate_sources_are_one_and_same_named_sources_are_told_apart_by_ref() -> None:
    discovery = await _discover(
        """
packages:
  marsrelay: github://tomquist/marsrelay/esphome/b2500.yaml@main
external_components:
  - source: github://tomquist/marsrelay@main
  - source: github://tomquist/marsrelay@dev
"""
    )

    assert discovery.sources == (
        GitSource(
            name="marsrelay@main", url="https://github.com/tomquist/marsrelay.git", ref="main"
        ),
        GitSource(
            name="marsrelay@dev", url="https://github.com/tomquist/marsrelay.git", ref="dev"
        ),
    )


@pytest.mark.parametrize(
    ("files", "error"),
    [
        ({"node.yaml": "a: !include node.yaml\n"}, "nested too deeply"),
        ({"node.yaml": "a: [\n"}, "cannot parse node.yaml"),
    ],
)
async def test_an_include_cycle_or_broken_yaml_is_a_discovery_error(
    files: dict[str, str], error: str
) -> None:
    with pytest.raises(DiscoveryError, match=error):
        await discover_git_sources("node.yaml", Files(files).read)


async def test_an_included_file_merged_with_a_merge_key_is_followed() -> None:
    discovery = await _discover(
        """
<<: !include common.yaml
esphome:
  name: node
""",
        **{"common.yaml": "external_components:\n  - source: github://tomquist/marsrelay\n"},
    )

    assert discovery.sources == (
        GitSource(name="marsrelay", url="https://github.com/tomquist/marsrelay.git", ref=None),
    )


async def test_a_substitution_that_refers_to_another_is_expanded() -> None:
    discovery = await _discover(
        """
substitutions:
  owner: tomquist
  repo_url: https://github.com/${owner}/marsrelay
external_components:
  - source:
      type: git
      url: ${repo_url}
"""
    )

    assert discovery.sources == (
        GitSource(name="marsrelay", url="https://github.com/tomquist/marsrelay", ref=None),
    )


@pytest.mark.parametrize("platform", ["esphome", "web_server"])
async def test_an_ota_entry_with_platform_esphome_or_web_server_is_found(platform: str) -> None:
    discovery = await _discover(f"ota:\n  - platform: http_request\n  - platform: {platform}\n")

    assert discovery.ota is OtaStatus.PRESENT


@pytest.mark.parametrize(
    "main", ["esphome:\n  name: node\n", "ota:\n  - platform: http_request\n"]
)
async def test_a_config_without_an_ota_platform_esphome_or_web_server_misses_ota(main: str) -> None:
    discovery = await _discover(main)

    assert discovery.ota is OtaStatus.MISSING


async def test_ota_from_a_local_package_or_include_with_a_substituted_platform_is_found() -> None:
    discovery = await _discover(
        "substitutions:\n  updater: esphome\npackages:\n  base: !include base.yaml\n",
        **{"base.yaml": "ota: !include ota.yaml\n", "ota.yaml": "- platform: ${updater}\n"},
    )

    assert discovery.ota is OtaStatus.PRESENT


async def test_ota_is_unknown_when_a_remote_package_could_provide_it() -> None:
    discovery = await _discover("packages:\n  remote: github://someone/base/base.yaml@main\n")

    assert discovery.ota is OtaStatus.UNKNOWN


async def test_git_sources_without_refresh_always_or_0s_have_a_stale_refresh_policy() -> None:
    discovery = await _discover(
        """
substitutions:
  never_stale: 0s
external_components:
  - source: github://someone/fresh
    refresh: always
  - source: github://someone/zero
    refresh: ${never_stale}
  - source: github://someone/daily
    refresh: 1d
  - source: github://someone/default
packages:
  shorthand: github://someone/shorthand/base.yaml@main
  fresh_package:
    url: https://gitlab.com/someone/fresh_package
    files: [base.yaml]
    refresh: always
  hourly_package:
    url: https://gitlab.com/someone/hourly_package
    files: [base.yaml]
    refresh: 1h
"""
    )

    assert [source.name for source in discovery.stale_refresh] == [
        "daily",
        "default",
        "shorthand",
        "hourly_package",
    ]


async def test_a_source_declared_twice_is_stale_if_either_declaration_is() -> None:
    discovery = await _discover(
        """
external_components:
  - source: github://someone/shared
    refresh: always
packages:
  base: github://someone/shared/base.yaml
"""
    )

    assert [source.name for source in discovery.stale_refresh] == ["shared"]


async def test_a_single_ota_mapping_rather_than_a_list_is_found() -> None:
    discovery = await _discover("ota:\n  platform: esphome\n  password: hunter2\n")

    assert discovery.ota is OtaStatus.PRESENT


@pytest.mark.parametrize("refresh", ["always", "0s", "0seconds", "0 min", "0us", "0"])
async def test_any_zero_refresh_period_or_always_refreshes_on_every_build(refresh: str) -> None:
    discovery = await _discover(
        f"external_components:\n  - source: github://someone/fresh\n    refresh: {refresh}\n"
    )

    assert discovery.stale_refresh == ()
