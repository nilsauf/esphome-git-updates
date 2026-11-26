"""The CI check that every module of the integration is above the coverage floor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from check_module_coverage import main


def _package(root: Path, *modules: str) -> Path:
    package = root / "custom_components" / "demo"
    package.mkdir(parents=True)
    for module in modules:
        (package / module).write_text("")
    return package


def _report(root: Path, percents: dict[str, float]) -> Path:
    """A `coverage json` report, with each file's percentage covered."""
    report = root / "coverage.json"
    files = {
        f"custom_components/demo/{module}": {"summary": {"percent_covered": percent}}
        for module, percent in percents.items()
    }
    report.write_text(json.dumps({"files": files, "totals": {"percent_covered": 99.0}}))
    return report


def _check(root: Path, report: Path, package: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.chdir(root)
    return main([str(report), str(package.relative_to(root))])


def test_every_module_above_the_floor_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path, "__init__.py", "update.py")
    report = _report(tmp_path, {"__init__.py": 100.0, "update.py": 95.1})

    assert _check(tmp_path, report, package, monkeypatch) == 0
    assert "update.py" not in capsys.readouterr().out


def test_one_module_at_the_floor_fails_even_when_the_total_is_high(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path, "__init__.py", "update.py", "diagnostics.py")
    report = _report(tmp_path, {"__init__.py": 100.0, "update.py": 100.0, "diagnostics.py": 95.0})

    assert _check(tmp_path, report, package, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "custom_components/demo/diagnostics.py: 95.00%" in out
    assert "update.py" not in out


def test_a_module_missing_from_the_report_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    package = _package(tmp_path, "__init__.py", "diagnostics.py")
    report = _report(tmp_path, {"__init__.py": 100.0})

    assert _check(tmp_path, report, package, monkeypatch) == 1
    assert "custom_components/demo/diagnostics.py: not in the coverage report" in (
        capsys.readouterr().out
    )
