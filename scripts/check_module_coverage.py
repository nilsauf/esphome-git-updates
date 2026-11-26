#!/usr/bin/env python3
"""Fail unless every module of the integration is above the coverage floor.

A total can hide one weak module, so this reads each file's own percentage
from a `coverage json` report. Every `.py` file in the package counts, so a
module the report does not list fails too.

    pytest --cov=custom_components/esphome_git_updates --cov-report=json
    check_module_coverage.py coverage.json custom_components/esphome_git_updates
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The Silver `test-coverage` rule: every module above 95%.
FLOOR = 95.0


def main(argv: list[str]) -> int:
    report_path, package = Path(argv[0]), Path(argv[1])
    files = json.loads(report_path.read_text())["files"]
    covered = {
        Path(name).resolve(): data["summary"]["percent_covered"] for name, data in files.items()
    }
    failures = []
    for module in sorted(package.rglob("*.py")):
        percent = covered.get(module.resolve())
        if percent is None:
            failures.append(f"{module.as_posix()}: not in the coverage report")
        elif percent <= FLOOR:
            failures.append(f"{module.as_posix()}: {percent:.2f}%")
    if failures:
        print(f"Modules at or below {FLOOR:g}% coverage:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print(f"Every module of {package.as_posix()} is above {FLOOR:g}% coverage.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
