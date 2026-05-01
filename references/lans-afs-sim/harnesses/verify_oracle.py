#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 LuarSpace contributors
"""End-to-end verifier for the LANS-AFS-SIM second oracle.

Runs the full reproducibility chain:

    1. Clone LANS-AFS-SIM at the pinned SHA (or use an existing checkout).
    2. Build upstream targets via ``make``.
    3. Compile ``afs_sim_lib.o`` (afs_sim.c with main renamed).
    4. Compile ``dump_lans_codes`` and ``dump_lans_frame`` against it.
    5. Run the L1 dumper for all 210 PRNs and the L2 orchestrator for the
       six prescribed test messages.
    6. ``cmp`` every produced ``.bin`` against the shipped equivalent in
       ``references/lans-afs-sim/{codes,frames}/``.

Exits 0 only if every shipped dump is byte-exactly reproduced from
upstream BSD-2-Clause sources at the pinned commit.  This is the
machine-readable form of the "Verifying the LANS-AFS-SIM oracle from
upstream sources" section in ../../CORRECTNESS.md.

Usage::

    python verify_oracle.py                # clone, build, verify
    python verify_oracle.py --lans-dir DIR # reuse an existing checkout
    python verify_oracle.py --keep-workdir # leave artefacts on success
    python verify_oracle.py --cc PATH      # override compiler discovery

Stdlib-only, no third-party dependencies.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PINNED_SHA = "0578f298ba68d8508ab7d780be843faed3e2b274"
LANS_REPO_URL = "https://github.com/osqzss/LANS-AFS-SIM.git"

HARNESSES_DIR = Path(__file__).resolve().parent
LANS_REF_DIR = HARNESSES_DIR.parent  # references/lans-afs-sim/
SHIPPED_CODES = LANS_REF_DIR / "codes"
SHIPPED_FRAMES = LANS_REF_DIR / "frames"

# Mirror of TEST_MESSAGES in dump_l2_test_vectors.py.
L2_TEST_MESSAGES: list[tuple[str, int, int, str, int | None]] = [
    ("message_1", 0, 0, "zeros", None),
    ("message_2", 0, 0, "ones", None),
    ("message_3", 0, 0, "alternating1", None),
    ("message_4", 0, 0, "marker", None),
    ("message_5", 0, 0, "random", 0xAF52),
    ("boundary", 3, 99, "alternating", None),
]


# ─────────────────────────────── helpers ────────────────────────────────────


def run(cmd: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
    """Run a subprocess; raise SystemExit with stderr on non-zero."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").rstrip()
        sys.exit(
            f"\nERROR: command failed (exit {result.returncode})\n  $ {' '.join(cmd)}\n  {msg}"
        )
    return result.stdout if capture else ""


def step(label: str) -> None:
    print(f"  ▸ {label}", flush=True)


def discover_cc(override: str | None) -> str:
    """Pick a C compiler with -fopenmp support.

    Apple Clang lacks ``-fopenmp``.  On macOS we look for Homebrew GCC.
    On Linux ``cc`` (typically GCC) handles ``-fopenmp`` natively.
    """
    if override:
        return override
    if sys.platform == "darwin":
        try:
            prefix = subprocess.run(
                ["brew", "--prefix", "gcc"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            sys.exit(
                "ERROR: macOS detected but Homebrew GCC not found.\n"
                "       brew install gcc, or pass --cc /path/to/gcc-15"
            )
        for ver in range(20, 11, -1):
            cand = Path(prefix) / "bin" / f"gcc-{ver}"
            if cand.is_file():
                return str(cand)
        sys.exit(f"ERROR: no gcc-NN binary found under {prefix}/bin")
    return "cc"


def cmp_dirs(produced: Path, shipped: Path, label: str) -> tuple[int, int]:
    """Byte-compare every file in ``shipped`` against the matching name in
    ``produced``.  Returns (matched, total)."""
    files = sorted(p for p in shipped.iterdir() if p.is_file() and p.suffix == ".bin")
    total = len(files)
    matched = 0
    mismatches: list[str] = []
    for f in files:
        peer = produced / f.name
        if not peer.is_file():
            mismatches.append(f"{f.name}: missing in rebuild")
        elif filecmp.cmp(f, peer, shallow=False):
            matched += 1
        else:
            mismatches.append(f"{f.name}: BYTE MISMATCH")
    print(f"    {label}: {matched}/{total} bit-exact")
    for msg in mismatches[:5]:
        print(f"      • {msg}", file=sys.stderr)
    if len(mismatches) > 5:
        print(f"      • ({len(mismatches) - 5} more)", file=sys.stderr)
    return matched, total


# ─────────────────────────────── pipeline steps ─────────────────────────────


def clone_or_use(lans_dir: Path | None, workdir: Path) -> Path:
    if lans_dir is not None:
        step(f"using existing LANS-AFS-SIM checkout at {lans_dir}")
        head = run(["git", "rev-parse", "HEAD"], cwd=lans_dir, capture=True).strip()
        if head != PINNED_SHA:
            print(
                f"    WARNING: checkout is at {head[:12]}, "
                f"expected {PINNED_SHA[:12]} — reproduction may differ.",
                file=sys.stderr,
            )
        return lans_dir
    target = workdir / "LANS-AFS-SIM"
    step(f"cloning {LANS_REPO_URL} → {target}")
    run(["git", "clone", "--quiet", LANS_REPO_URL, str(target)])
    step(f"checkout {PINNED_SHA[:12]}…")
    run(["git", "checkout", "--quiet", PINNED_SHA], cwd=target)
    return target


def build_upstream(lans_dir: Path, cc: str) -> None:
    step(f"upstream make (CC={cc})")
    run(["make", f"CC={cc}"], cwd=lans_dir)


def build_harnesses(lans_dir: Path, cc: str) -> None:
    common_includes = [
        f"-I{lans_dir}",
        f"-I{lans_dir}/pocketsdr",
        f"-I{lans_dir}/rtklib",
        f"-I{lans_dir}/ldpc",
    ]
    step("compile afs_sim_lib.o (afs_sim.c with main renamed)")
    run(
        [
            cc,
            "-O2",
            "-fopenmp",
            "-Dmain=afs_sim_main",
            *common_includes,
            "-c",
            "afs_sim.c",
            "-o",
            "afs_sim_lib.o",
        ],
        cwd=lans_dir,
    )
    object_set = [
        "afs_sim_lib.o",
        "afs_nav.o",
        "afs_rand.o",
        "ldpc/alloc.o",
        "ldpc/mod2sparse.o",
        "rtklib/rtkcmn.o",
        "pocketsdr/pocketsdr.o",
    ]
    for harness in ("dump_lans_frame", "dump_lans_codes"):
        step(f"link {harness}")
        run(
            [
                cc,
                "-O2",
                "-fopenmp",
                *common_includes,
                str(HARNESSES_DIR / f"{harness}.c"),
                *object_set,
                "-lm",
                "-o",
                harness,
            ],
            cwd=lans_dir,
        )


def run_dumpers(lans_dir: Path, out_dir: Path) -> tuple[Path, Path]:
    codes_out = out_dir / "codes"
    frames_out = out_dir / "frames"
    codes_out.mkdir(parents=True, exist_ok=True)
    frames_out.mkdir(parents=True, exist_ok=True)

    step("run dump_lans_codes (210 PRNs)")
    run([str(lans_dir / "dump_lans_codes"), str(codes_out), "210"])

    step("run dump_lans_frame for the 6 L2 test messages")
    for name, fid, toi, pattern, seed in L2_TEST_MESSAGES:
        cmd = [
            str(lans_dir / "dump_lans_frame"),
            str(frames_out),
            name,
            str(fid),
            str(toi),
            pattern,
        ]
        if seed is not None:
            cmd.append(f"0x{seed:X}")
        run(cmd)

    return codes_out, frames_out


# ─────────────────────────────── main ───────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "--lans-dir",
        type=Path,
        default=None,
        help="Reuse an existing LANS-AFS-SIM checkout instead of cloning.",
    )
    ap.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="Working directory for clone + outputs (default: fresh tempdir).",
    )
    ap.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Don't delete the work directory on success.",
    )
    ap.add_argument(
        "--cc",
        default=None,
        help="C compiler binary (default: auto-detect Homebrew GCC on macOS, system cc on Linux).",
    )
    args = ap.parse_args(argv)

    if not SHIPPED_CODES.is_dir() or not SHIPPED_FRAMES.is_dir():
        sys.exit(
            f"ERROR: expected shipped reference dirs under {LANS_REF_DIR}, "
            "but codes/ or frames/ is missing."
        )

    cleanup_workdir = False
    if args.workdir is not None:
        args.workdir.mkdir(parents=True, exist_ok=True)
        workdir = args.workdir
    else:
        workdir = Path(tempfile.mkdtemp(prefix="lsis-verify-"))
        cleanup_workdir = not args.keep_workdir

    print(f"verify_oracle: workdir = {workdir}")

    try:
        cc = discover_cc(args.cc)
        print(f"verify_oracle: cc     = {cc}")
        print(f"verify_oracle: pinned = {PINNED_SHA}\n")

        print("[1/5] LANS-AFS-SIM source")
        lans_dir = clone_or_use(args.lans_dir, workdir)

        print("\n[2/5] upstream build")
        build_upstream(lans_dir, cc)

        print("\n[3/5] harness build")
        build_harnesses(lans_dir, cc)

        print("\n[4/5] run dumpers")
        codes_out, frames_out = run_dumpers(lans_dir, workdir)

        print("\n[5/5] compare against shipped reference")
        codes_ok, codes_total = cmp_dirs(codes_out, SHIPPED_CODES, "L1 codes ")
        frames_ok, frames_total = cmp_dirs(frames_out, SHIPPED_FRAMES, "L2 frames")
        all_ok = codes_ok == codes_total and frames_ok == frames_total

        print()
        if all_ok:
            print(
                f"OK — full reproduction: {codes_ok} L1 + {frames_ok} L2 dumps "
                f"bit-exact against upstream {PINNED_SHA[:12]}."
            )
            return 0
        print(
            f"FAIL — {codes_total - codes_ok} L1 + {frames_total - frames_ok} L2 "
            f"dumps did not match the shipped reference.",
            file=sys.stderr,
        )
        return 1
    finally:
        if cleanup_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
