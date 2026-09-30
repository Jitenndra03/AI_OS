"""Run with ``python -m sandbox.cli``. Inspection never executes artifacts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .core import SandboxError, doctor, inspect_artifact, quarantine_artifact, run_artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect, quarantine, or explicitly isolate a local artifact.")
    actions = parser.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser("inspect", help="Static inspection only (no execution)")
    inspect.add_argument("file")
    inspect.add_argument("--output", type=Path, help="Create a new JSON report (never overwrite)")
    quarantine = actions.add_parser("quarantine", help="Copy inert bytes into a private quarantine directory")
    quarantine.add_argument("file")
    quarantine.add_argument("--directory", type=Path, required=True)
    quarantine.add_argument("--output", type=Path)
    check = actions.add_parser("doctor", help="Check actual bubblewrap isolation with a built-in benign probe")
    check.add_argument("--output", type=Path)
    run = actions.add_parser("run", help="Explicitly execute a Python/shell artifact only inside verified isolation")
    run.add_argument("file")
    run.add_argument("--timeout", type=float, default=5.0, help="Wall time in seconds, >0 and <=30 (default: 5)")
    run.add_argument("--report", type=Path, dest="output")
    args = parser.parse_args(argv)
    output_stream = None
    try:
        # Reserve a report before any execution; never follow symlinks or overwrite artifacts.
        if args.output:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            output_stream = os.fdopen(fd, "w", encoding="utf-8")
        try:
            if args.action == "inspect":
                report = inspect_artifact(args.file)
            elif args.action == "quarantine":
                report = quarantine_artifact(args.file, args.directory)
            elif args.action == "doctor":
                report = doctor()
            else:
                report = run_artifact(args.file, args.timeout)
        except (SandboxError, OSError) as exc:
            report = {"schema_version": 1, "action": args.action, "status": "error", "executed": False,
                      "reason": str(exc)}
        rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
        if output_stream:
            output_stream.write(rendered)
            output_stream.flush()
        sys.stdout.write(rendered)
        return 0 if report["status"] in ("inspected", "quarantined", "available", "completed") else 2
    except OSError as exc:
        sys.stderr.write(f"Cannot create report: {exc}\n")
        return 2
    finally:
        if output_stream:
            output_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
