#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 LuarSpace contributors
"""Orchestrator: produce all 6 Level-2 LANS-AFS-SIM frame dumps.

Invokes the ``dump_lans_frame`` harness once per test message defined in
the competition interoperability plan, plus the boundary frame from Test
Case 4. Output filenames match the public ``lsis-afs-test-vectors``
``frame_message_*.bin`` set so that bit-exact comparison is one ``cmp``
away (skipping the 64-byte header on the LuarSpace side).

Usage:
    python dump_l2_test_vectors.py <output_dir> [--harness PATH]

The harness binary defaults to ``./dump_lans_frame`` next to this script.
See README.md in this directory for build instructions.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_HARNESS = SCRIPT_DIR / "dump_lans_frame"

# (name, fid, toi, pattern, seed_or_None) — must match the LuarSpace
# generator-side export script that produced the public frame_*.bin set.
TEST_MESSAGES: list[tuple[str, int, int, str, int | None]] = [
    ("message_1", 0, 0, "zeros", None),
    ("message_2", 0, 0, "ones", None),
    ("message_3", 0, 0, "alternating1", None),
    ("message_4", 0, 0, "marker", None),
    ("message_5", 0, 0, "random", 0xAF52),
    ("boundary", 3, 99, "alternating", None),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "output_dir",
        type=Path,
        help="Target directory for the lans_frame_*.bin dumps.",
    )
    ap.add_argument(
        "--harness",
        type=Path,
        default=DEFAULT_HARNESS,
        help=f"Path to the dump_lans_frame binary (default: {DEFAULT_HARNESS}).",
    )
    args = ap.parse_args(argv)

    # Resolve the harness path explicitly so relative paths like
    # "./dump_lans_frame" work — Path() drops the leading "./" which
    # causes subprocess.run to search $PATH and fail.
    harness = args.harness.resolve()
    if not harness.is_file():
        print(
            f"error: harness not found at {harness}\n"
            f"       see README.md in this directory for build instructions",
            file=sys.stderr,
        )
        return 2

    out_dir: Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, fid, toi, pattern, seed in TEST_MESSAGES:
        cmd = [str(harness), str(out_dir), name, str(fid), str(toi), pattern]
        if seed is not None:
            cmd.append(f"0x{seed:X}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(
                f"error: harness failed for {name}: {result.stderr}",
                file=sys.stderr,
            )
            return result.returncode
        for line in result.stdout.splitlines():
            print(f"  {line}", file=sys.stderr)

    print(f"OK: {len(TEST_MESSAGES)} LANS-AFS-SIM dumps in {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
