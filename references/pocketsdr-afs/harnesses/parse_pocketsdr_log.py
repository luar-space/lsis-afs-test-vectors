#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 LuarSpace contributors
"""Extract AFS-D frame-decode status from a pocket_trk -log file.

PocketSDR-AFS emits per-event log lines via ``sdr_log()`` to the ``-log``
file.  For AFS-D frames, the relevant prefixes are:

- ``$SB2,...`` — Subframe 2 LDPC + CRC pass (decoded data follows)
- ``$SB3,...`` — Subframe 3 LDPC + CRC pass
- ``$SB4,...`` — Subframe 4 LDPC + CRC pass
- ``$LOG,...,AFSD SB{2,3,4} FRAME ERROR,...`` — SB LDPC/CRC fail
- ``$LOG,...,TOI NOT FOUND`` — SB1 BCH did not match any TOI 0..99

This module returns a small structured tuple summarising those counts
per channel, used by verify_pocketsdr_decode.py as a corroborating
("CRC validation passes") signal alongside the load-bearing bytewise
symbol-stream comparator.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SB_OK_RE = re.compile(r"^\$(SB[234]),")
_SB_ERR_RE = re.compile(r"AFSD (SB[234]) FRAME ERROR")
_TOI_NOT_FOUND_RE = re.compile(r"TOI NOT FOUND")


@dataclass
class FrameDecodeStats:
    sb2_pass: int = 0
    sb3_pass: int = 0
    sb4_pass: int = 0
    sb2_fail: int = 0
    sb3_fail: int = 0
    sb4_fail: int = 0
    toi_not_found: int = 0
    raw_lines: list[str] = field(default_factory=list)

    @property
    def all_subframes_pass_at_least_once(self) -> bool:
        return self.sb2_pass > 0 and self.sb3_pass > 0 and self.sb4_pass > 0

    def summary(self) -> str:
        return (
            f"SB2 {self.sb2_pass}/{self.sb2_pass + self.sb2_fail}, "
            f"SB3 {self.sb3_pass}/{self.sb3_pass + self.sb3_fail}, "
            f"SB4 {self.sb4_pass}/{self.sb4_pass + self.sb4_fail}"
            + (f", TOI not found ×{self.toi_not_found}" if self.toi_not_found else "")
        )


def parse_log(log_path: Path) -> FrameDecodeStats:
    stats = FrameDecodeStats()
    if not log_path.exists():
        return stats
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip()
            ok = _SB_OK_RE.match(line)
            if ok:
                which = ok.group(1)
                if which == "SB2":
                    stats.sb2_pass += 1
                elif which == "SB3":
                    stats.sb3_pass += 1
                elif which == "SB4":
                    stats.sb4_pass += 1
                continue
            err = _SB_ERR_RE.search(line)
            if err:
                which = err.group(1)
                if which == "SB2":
                    stats.sb2_fail += 1
                elif which == "SB3":
                    stats.sb3_fail += 1
                elif which == "SB4":
                    stats.sb4_fail += 1
                continue
            if _TOI_NOT_FOUND_RE.search(line):
                stats.toi_not_found += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    p.add_argument("log", type=Path, help="pocket_trk -log output file")
    args = p.parse_args(argv)
    stats = parse_log(args.log)
    print(stats.summary())
    return 0 if stats.all_subframes_pass_at_least_once else 1


if __name__ == "__main__":
    sys.exit(main())
