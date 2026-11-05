#!/usr/bin/env python3
"""Talk to an ESPHome Device Builder through the integration's `/ws` client.

Spike CLI for ticket 01 (ADR-0002): proves the client against a real
Device Builder and captures `/ws` transcripts as test fixtures.

    # The add-on, from the Home Assistant host (ingress port, no auth):
    device_builder_cli.py --addon-port 63412 list
    # A Device Builder with a password:
    DEVICE_BUILDER_PASSWORD=... device_builder_cli.py --url http://host:6052 \\
        --username admin version
    # Capture fixtures:
    device_builder_cli.py --addon-port 63412 --record success.json \\
        install b2500.yaml --clean

Exit codes: 0 success, 1 failure, 2 usage, 3 Install deferred (device offline).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "esphome_git_updates"))

import device_builder

EXIT_FAILED = 1
EXIT_DEFERRED = 3


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--url", help="Device Builder base URL, e.g. http://host:6052")
    where.add_argument(
        "--addon-port", type=int, help="the ESPHome add-on's ingress port on 127.0.0.1 (no auth)"
    )
    parser.add_argument("--username")
    parser.add_argument(
        "--password",
        default=os.environ.get("DEVICE_BUILDER_PASSWORD"),
        help="defaults to $DEVICE_BUILDER_PASSWORD",
    )
    parser.add_argument("--record", type=Path, help="write the /ws transcript to this JSON file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list the Device Builder's configurations")
    commands.add_parser("version", help="print the Available ESPHome Version")
    get_config = commands.add_parser("get-config", help="print a configuration's raw YAML")
    get_config.add_argument("configuration")
    install = commands.add_parser("install", help="clean (optional), compile and upload")
    install.add_argument("configuration")
    install.add_argument("--clean", action="store_true", help="run a Clean Build first")
    install.add_argument("--port", default="OTA", help='upload target (default "OTA")')
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    url = args.url or f"http://127.0.0.1:{args.addon_port}"
    frames: list[dict[str, Any]] = []
    try:
        async with (
            aiohttp.ClientSession() as session,
            device_builder.connect(
                session,
                url,
                username=args.username,
                password=args.password,
                recorder=lambda direction, frame: frames.append({"dir": direction, "frame": frame}),
            ) as client,
        ):
            return await _command(client, args)
    except (device_builder.DeviceBuilderError, aiohttp.ClientError) as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_FAILED
    finally:
        if args.record:
            _write_transcript(args.record, frames)


class _PrintOutput:
    """Prints an Install's output as it arrives."""

    def output(self, stage: device_builder.Stage, line: str) -> None:
        print(f"[{stage}] {line}", flush=True)

    async def jobs(self, jobs: tuple[device_builder.ActiveJob, ...]) -> None:
        pass

    def progress(self, update: device_builder.JobProgress) -> None:
        pass


async def _command(client: device_builder.DeviceBuilderClient, args: argparse.Namespace) -> int:
    print(
        f"== Device Builder {client.server_version}, ESPHome {client.esphome_version}",
        file=sys.stderr,
    )
    if args.command == "list":
        for config in await client.list_configurations():
            print(f"{config.configuration}\t{config.name}\t{config.friendly_name}")
    elif args.command == "version":
        print(client.esphome_version)
    elif args.command == "get-config":
        sys.stdout.write(await client.get_config(args.configuration))
    elif args.command == "install":
        result = await client.install(
            args.configuration,
            port=args.port,
            clean_build=args.clean,
            observer=_PrintOutput(),
        )
        for job in result.jobs:
            print(
                f"== {job.stage}: {job.status} (exit {job.exit_code}, job {job.job_id})",
                file=sys.stderr,
            )
        if result.outcome is device_builder.InstallOutcome.DEFERRED:
            print(f"deferred at {result.stage}: {result.error}; refused", file=sys.stderr)
            return EXIT_DEFERRED
        if result.outcome is device_builder.InstallOutcome.FAILED:
            print(f"failed at {result.stage}: {result.error}", file=sys.stderr)
            return EXIT_FAILED
        print("install completed", file=sys.stderr)
    return 0


def _write_transcript(path: Path, frames: list[dict[str, Any]]) -> None:
    # Passwords and session tokens never go into a fixture.
    for entry in frames:
        frame = entry["frame"]
        if frame.get("command") == "auth/login":
            frame["args"] = {k: "REDACTED" for k in frame["args"]}
        if isinstance(frame.get("result"), dict) and "token" in frame["result"]:
            frame["result"] = {**frame["result"], "token": "REDACTED"}
    path.write_text(json.dumps({"frames": frames}, indent=1) + "\n")
    print(f"== recorded {len(frames)} frames to {path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(parse_args(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    sys.exit(main())
