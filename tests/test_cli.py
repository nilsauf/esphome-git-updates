"""The spike CLI, run against replayed Device Builder transcripts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import device_builder_cli
from fake_device_builder import load_fixture, serve


async def _run(fixture: str, *argv: str) -> int:
    async with serve(load_fixture(fixture)) as (url, fake):
        code = await device_builder_cli.run(device_builder_cli.parse_args(["--url", url, *argv]))
    assert fake.mismatches == []
    return code


async def test_list_prints_one_configuration_per_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert await _run("list", "list") == 0
    assert capsys.readouterr().out.splitlines() == [
        "broken-node.yaml\tbroken-node\tbroken-node",
        "host-node.yaml\thost-node\thost-node",
        "offline-node.yaml\toffline-node\toffline-node",
    ]


async def test_install_exits_3_when_the_install_is_deferred(capsys: pytest.CaptureFixture[str]) -> None:
    assert await _run("install_deferred_at_enqueue", "install", "offline-node.yaml") == 3
    assert "deferred at compile: device offline; refused" in capsys.readouterr().err


async def test_install_exits_1_when_the_compile_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert await _run("install_compile_failed", "install", "broken-node.yaml") == 1
    assert "failed at compile: compile failed (exit code 1)" in capsys.readouterr().err


async def test_record_writes_a_replayable_transcript_without_credentials(tmp_path: Path) -> None:
    record = tmp_path / "t.json"
    code = await _run(
        "auth_login_list", "--username", "admin", "--password", "hunter2",
        "--record", str(record), "list",
    )
    assert code == 0
    text = record.read_text()
    assert "hunter2" not in text
    assert json.loads(text)["frames"] == load_fixture("auth_login_list")
