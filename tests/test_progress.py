"""Mapping a job's own progress onto the Install's one 0-100 bar (ticket 14)."""

from __future__ import annotations

import pytest

from custom_components.esphome_git_updates.device_builder import JobProgress
from custom_components.esphome_git_updates.progress import InstallProgress


def _report(progress: InstallProgress, stage: str, percent: int | None) -> bool:
    return progress.report(JobProgress(stage, percent))


def test_with_clean_build_each_stage_is_scaled_into_its_band() -> None:
    progress = InstallProgress(clean_build=True)

    assert progress.percentage == 0
    # A clean reports no progress until it ends, at the end of its band.
    assert not _report(progress, "clean", None)
    assert progress.percentage == 0
    assert progress.report(JobProgress("clean", None, finished=True))
    assert progress.percentage == 5

    assert _report(progress, "compile", 50)
    assert progress.percentage == 47  # 5 + 85 * 50 // 100
    # A remote compile tops out at 99, then its end fills the band.
    assert _report(progress, "compile", 99)
    assert progress.percentage == 89
    assert progress.report(JobProgress("compile", 99, finished=True))
    assert progress.percentage == 90

    assert _report(progress, "upload", 50)
    assert progress.percentage == 95
    assert _report(progress, "upload", 100)
    assert progress.percentage == 100
    assert not progress.report(JobProgress("upload", 100, finished=True))
    assert progress.percentage == 100


def test_without_clean_build_compile_starts_at_zero() -> None:
    progress = InstallProgress(clean_build=False)

    assert _report(progress, "compile", 50)
    assert progress.percentage == 45  # 90 * 50 // 100
    assert _report(progress, "compile", 99)
    assert progress.percentage == 89
    assert progress.report(JobProgress("compile", 99, finished=True))
    assert progress.percentage == 90

    assert _report(progress, "upload", 50)
    assert progress.percentage == 95
    assert progress.report(JobProgress("upload", 50, finished=True))
    assert progress.percentage == 100


@pytest.mark.parametrize("percent", [None, 10, 40])
def test_the_bar_never_goes_backwards(percent: int | None) -> None:
    progress = InstallProgress(clean_build=False)
    assert _report(progress, "compile", 60)
    before = progress.percentage

    moved = _report(progress, "compile", percent)
    assert not moved
    assert progress.percentage == before


def test_a_job_that_is_not_part_of_the_install_is_ignored() -> None:
    progress = InstallProgress(clean_build=False)

    assert not _report(progress, "connect", 80)
    assert progress.percentage == 0


def test_a_finished_job_moves_to_the_end_of_its_band() -> None:
    progress = InstallProgress(clean_build=True)

    assert _report(progress, "clean", None) is False
    assert progress.report(JobProgress("clean", None, finished=True))
    assert progress.percentage == 5
    assert progress.report(JobProgress("upload", 30, finished=True))
    assert progress.percentage == 100
