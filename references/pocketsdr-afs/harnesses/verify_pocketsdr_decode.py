#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 LuarSpace contributors
"""End-to-end verifier for the PocketSDR-AFS decode oracle (Level 4).

Runs the full reproducibility chain:

    1. Check Homebrew deps (fftw, libusb on macOS).
    2. Clone PocketSDR-AFS at the pinned SHA (or use an existing
       checkout via --upstream-dir).
    3. Run upstream lib/clone_lib.sh to fetch libfec + LDPC-codes.
    4. Apply the bundled dump-symbols.patch (idempotent).
    5. Build pocket_trk.
    6. For each entry in SIGNAL_TEST_VECTORS:
         - Convert signals/signal_*_12s.iq.gz → headerless INT8X2.
         - Run pocket_trk -dump-symbols.
         - Compare 6000 recovered symbols against
           frames/frame_<source>.bin[64:6064] byte-for-byte.
    7. (Optional) regenerate references/pocketsdr-afs/decoded/*.bin.

Exits 0 only if all 10 signals are byte-exactly recovered.

Usage::

    python verify_pocketsdr_decode.py
    python verify_pocketsdr_decode.py --upstream-dir DIR --keep-workdir
    python verify_pocketsdr_decode.py --signal signal_message_1_12s.iq.gz
    python verify_pocketsdr_decode.py --awgn-cn0 50.0  # disclosed normalisation
    python verify_pocketsdr_decode.py --regenerate-decoded

Stdlib-only orchestration; numpy is consumed by the inner converter
when present (recommended).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HARNESSES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESSES_DIR))

from decode_signal import DecodeResult, decode_one  # noqa: E402

# ─────────────────────────────── pinning ───────────────────────────────────

PINNED_SHA = "5b23809f30d68518b7fad7a564fd0fac57cc497d"
POCKETSDR_AFS_REPO_URL = "https://github.com/osqzss/PocketSDR-AFS.git"

PATCHES_DIR = HARNESSES_DIR / "patches"
# Patches are applied in order.  Each must be idempotent.  Order matters
# only insofar as later patches may reference text the earlier ones added.
LOCAL_PATCHES: list[str] = [
    # Adds -dump-symbols flag to pocket_trk for byte-exact frame recovery.
    "dump-symbols.patch",
    # Renames a static satpos() in sdr_pvt_afs.c that conflicts with
    # RTKLIB's exported satpos under Apple Clang 17 strict-declaration
    # checking.  No semantic change.
    "clang17-build-fix.patch",
]

POCKETSDR_AFS_REF_DIR = HARNESSES_DIR.parent  # references/pocketsdr-afs/
DECODED_DIR = POCKETSDR_AFS_REF_DIR / "decoded"
REPO_ROOT = POCKETSDR_AFS_REF_DIR.parent.parent  # ../../

SIGNALS_DIR = REPO_ROOT / "signals"

# Mirror of validate.py SIGNAL_TEST_VECTORS (kept duplicated for harness
# self-containment; the validate.py tests assert these stay in sync).
SIGNAL_TEST_VECTORS: list[tuple[str, int, str]] = [
    ("signal_message_1_12s.iq.gz", 1, "frame_message_1.bin"),
    ("signal_message_2_12s.iq.gz", 1, "frame_message_2.bin"),
    ("signal_message_3_12s.iq.gz", 1, "frame_message_3.bin"),
    ("signal_message_4_12s.iq.gz", 1, "frame_message_4.bin"),
    ("signal_message_5_12s.iq.gz", 1, "frame_message_5.bin"),
    ("signal_prn2_baseline_12s.iq.gz", 2, "frame_message_1.bin"),
    ("signal_prn3_baseline_12s.iq.gz", 3, "frame_message_1.bin"),
    ("signal_prn12_baseline_12s.iq.gz", 12, "frame_message_1.bin"),
    ("signal_boundary_at_prn12_12s.iq.gz", 12, "frame_boundary.bin"),
    (
        "signal_boundary_max_fields_at_prn12_12s.iq.gz",
        12,
        "frame_boundary_max_fields.bin",
    ),
]


def _decoded_filename(signal_name: str) -> str:
    """signal_*_12s.iq.gz → decoded_signal_*_12s.bin"""
    stem = signal_name.removesuffix(".gz").removesuffix(".iq")
    return f"decoded_{stem}.bin"


def _decoded_fec_filename(signal_name: str) -> str:
    """signal_*_12s.iq.gz → decoded_fec_signal_*_12s.bin"""
    stem = signal_name.removesuffix(".gz").removesuffix(".iq")
    return f"decoded_fec_{stem}.bin"


# ─────────────────────────────── helpers ────────────────────────────────────


def run(cmd: list[str], *, cwd: Path | None = None, capture: bool = False) -> str:
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


# ─────────────────────────────── deps ───────────────────────────────────────


def check_deps(cc: str) -> list[str]:
    """Return list of human-readable missing-dep messages (empty = all OK)."""
    missing: list[str] = []

    # Compiler.
    if shutil.which(cc) is None:
        missing.append(f"compiler '{cc}' not found in PATH")

    # On macOS, fftw3f and libusb are typically Homebrew packages.
    if sys.platform == "darwin":
        for pkg, hint in (("fftw", "brew install fftw"), ("libusb", "brew install libusb")):
            try:
                subprocess.run(
                    ["brew", "--prefix", pkg],
                    capture_output=True,
                    check=True,
                )
            except (FileNotFoundError, subprocess.CalledProcessError):
                missing.append(f"missing Homebrew package '{pkg}' — install: {hint}")

    return missing


# ─────────────────────────────── upstream ─────────────────────────────────


def ensure_upstream(workdir: Path, upstream_dir: Path | None, *, no_sha_check: bool) -> Path:
    """Clone PocketSDR-AFS at PINNED_SHA into workdir/upstream, or reuse one.

    Returns the path to the upstream tree.
    """
    if upstream_dir is not None:
        if not (upstream_dir / ".git").is_dir():
            sys.exit(f"--upstream-dir {upstream_dir} is not a git checkout")
        if not no_sha_check:
            head = run(
                ["git", "rev-parse", "HEAD"],
                cwd=upstream_dir,
                capture=True,
            ).strip()
            if head != PINNED_SHA:
                sys.exit(
                    f"--upstream-dir HEAD ({head}) != PINNED_SHA ({PINNED_SHA}); "
                    "pass --no-sha-check to override."
                )
        step(f"reusing upstream checkout: {upstream_dir}")
        return upstream_dir

    target = workdir / "PocketSDR-AFS"
    if target.exists():
        shutil.rmtree(target)
    step(f"cloning {POCKETSDR_AFS_REPO_URL} (depth 50) into {target}")
    run(
        ["git", "clone", "--depth", "50", POCKETSDR_AFS_REPO_URL, str(target)],
        capture=True,
    )
    run(["git", "checkout", PINNED_SHA], cwd=target, capture=True)
    return target


def apply_patches(upstream: Path) -> None:
    """Apply each local patch idempotently, in order.

    For each patch: try --check --reverse first; if it succeeds, the patch
    is already applied and we skip.  Otherwise --check forward; abort on
    failure.  Then apply.
    """
    for name in LOCAL_PATCHES:
        patch = PATCHES_DIR / name
        if not patch.exists():
            sys.exit(f"missing patch: {patch}")

        check_applied = subprocess.run(
            ["git", "apply", "--check", "--reverse", str(patch)],
            cwd=upstream,
            capture_output=True,
            text=True,
            check=False,
        )
        if check_applied.returncode == 0:
            step(f"{name} already applied; skipping")
            continue

        check_fresh = subprocess.run(
            ["git", "apply", "--check", str(patch)],
            cwd=upstream,
            capture_output=True,
            text=True,
            check=False,
        )
        if check_fresh.returncode != 0:
            sys.exit(
                f"{name} does not apply cleanly:\n{check_fresh.stderr}\n"
                "Has the upstream SHA drifted?  Re-pin or refresh the patch."
            )
        step(f"applying {name}")
        run(["git", "apply", str(patch)], cwd=upstream, capture=True)


def fetch_sub_libs(upstream: Path) -> None:
    """Run upstream's lib/clone_lib.sh to fetch libfec + LDPC-codes.

    Idempotent: skips if both are already present.
    """
    lib_dir = upstream / "lib"
    libfec = lib_dir / "libfec"
    ldpc = lib_dir / "LDPC-codes"
    if libfec.is_dir() and ldpc.is_dir():
        step("libfec + LDPC-codes already cloned")
        return
    script = lib_dir / "clone_lib.sh"
    if not script.exists():
        sys.exit(f"missing upstream sub-lib script: {script}")
    step("running upstream lib/clone_lib.sh")
    run(["bash", str(script)], cwd=lib_dir, capture=True)


def build_pocket_trk(upstream: Path, *, jobs: int) -> Path:
    """Build pocket_trk via upstream's documented sequence; return binary path.

    Sequence (per upstream README + lib/build/makefile):
      1. make -C lib/build all       — build librtk.a, libldpc.a, libfec.a, libsdr.a
      2. make -C lib/build install   — copy .a files into lib/{macos,linux,win32}/
      3. make -C app/pocket_trk      — link pocket_trk against the installed .a files

    The upstream makefiles pick the right compiler per platform (clang on
    macOS arm64, g++ on Windows + Linux), so we do not override CC.
    """
    step(f"building lib/ static archives (-j{jobs})")
    run(["make", f"-j{jobs}", "all"], cwd=upstream / "lib" / "build", capture=True)
    step("installing lib/ static archives")
    run(["make", "install"], cwd=upstream / "lib" / "build", capture=True)
    step(f"building app/pocket_trk (-j{jobs})")
    run(["make", f"-j{jobs}"], cwd=upstream / "app" / "pocket_trk", capture=True)

    binary = upstream / "app" / "pocket_trk" / "pocket_trk"
    if not binary.exists():
        sys.exit(f"build did not produce {binary}")
    return binary


# ─────────────────────────────── decode sweep ──────────────────────────────


def decode_all(
    *,
    pocket_trk: Path,
    workdir: Path,
    selected_signal: str | None,
    awgn_cn0: float | None,
    timeout_s: float,
) -> list[DecodeResult]:
    out: list[DecodeResult] = []
    vectors = SIGNAL_TEST_VECTORS
    if selected_signal:
        vectors = [v for v in vectors if v[0] == selected_signal]
        if not vectors:
            sys.exit(f"--signal {selected_signal!r} not in SIGNAL_TEST_VECTORS")
    for filename, prn, source_frame in vectors:
        signal_path = SIGNALS_DIR / filename
        if not signal_path.exists():
            sys.exit(f"missing signal file: {signal_path}")
        step(f"decoding {filename} (PRN {prn}, source {source_frame})")
        result = decode_one(
            pocket_trk=pocket_trk,
            signal_path=signal_path,
            prn=prn,
            source_frame=source_frame,
            repo_root=REPO_ROOT,
            workdir=workdir / "per-signal" / filename.removesuffix(".iq.gz"),
            awgn_cn0_dbhz=awgn_cn0,
            timeout_s=timeout_s,
        )
        out.append(result)
    return out


def write_decoded_outputs(results: list[DecodeResult]) -> None:
    DECODED_DIR.mkdir(parents=True, exist_ok=True)
    for r in results:
        # Channel-symbol oracle output (every signal, 6000 bytes).
        if r.byte_exact:
            out_path = DECODED_DIR / _decoded_filename(r.signal)
            out_path.write_bytes(r.bytes_recovered)
            step(f"wrote {out_path.relative_to(REPO_ROOT)}")
        # Post-FEC oracle output (FID=0 frames only, 2868 bytes).
        if r.fec_evaluated and r.fec_byte_exact and len(r.fec_recovered) == len(r.expected_fec):
            out_path = DECODED_DIR / _decoded_fec_filename(r.signal)
            out_path.write_bytes(r.fec_recovered)
            step(f"wrote {out_path.relative_to(REPO_ROOT)}")


# ─────────────────────────────── main ───────────────────────────────────────


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0912, PLR0915
    # Argparse-driven entrypoints are inherently long.  We keep the linear
    # flow (parse → deps → clone → patch → build → decode → report) in
    # one function so the verifier reads top-to-bottom.
    p = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--upstream-dir", type=Path, default=None)
    p.add_argument("--workdir", type=Path, default=None)
    p.add_argument("--keep-workdir", action="store_true")
    p.add_argument(
        "--cc",
        default=None,
        help="compiler override; default lets upstream makefiles pick "
        "(clang on macOS arm64, g++ elsewhere)",
    )
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    p.add_argument("--check-deps", action="store_true", help="only check deps and exit")
    p.add_argument("--no-sha-check", action="store_true", help="allow non-pinned upstream HEAD")
    p.add_argument(
        "--awgn-cn0",
        type=float,
        default=None,
        metavar="DBHZ",
        help="inject deterministic AWGN at target C/N0 in dB-Hz "
        "(disclosed normalisation; documented in CORRECTNESS.md)",
    )
    p.add_argument(
        "--signal",
        default=None,
        help="run only one signal by name (default: all 10 SIGNAL_TEST_VECTORS)",
    )
    p.add_argument(
        "--regenerate-decoded",
        action="store_true",
        help="overwrite references/pocketsdr-afs/decoded/*.bin with successful results",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="per-signal pocket_trk timeout in seconds",
    )
    p.add_argument(
        "--check-determinism",
        action="store_true",
        help="after the main sweep, re-decode signal_message_1_12s.iq.gz and "
        "assert both channel-symbol and post-FEC dumps are byte-identical "
        "across runs.  Catches non-determinism in acquisition / tracking / "
        "LDPC that would surface as flaky CI under noise or threading.",
    )
    args = p.parse_args(argv)

    print("LSIS-AFS Test Vectors v0.4.0 — PocketSDR-AFS decode oracle verifier")
    print(f"  pinned SHA: {PINNED_SHA}")

    # Upstream picks the compiler per-platform; only honour an explicit override.
    cc_for_dep_check = args.cc or ("clang" if sys.platform == "darwin" else "g++")
    missing = check_deps(cc_for_dep_check)
    if missing:
        print("Missing build dependencies:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 2
    step("dependencies OK")
    if args.check_deps:
        return 0

    cleanup_workdir = False
    if args.workdir is None:
        args.workdir = Path(tempfile.mkdtemp(prefix="lsis-afs-l4-"))
        cleanup_workdir = not args.keep_workdir
    args.workdir.mkdir(parents=True, exist_ok=True)
    print(f"  workdir: {args.workdir}")

    try:
        upstream = ensure_upstream(args.workdir, args.upstream_dir, no_sha_check=args.no_sha_check)
        fetch_sub_libs(upstream)
        apply_patches(upstream)
        pocket_trk = build_pocket_trk(upstream, jobs=args.jobs)
        step(f"pocket_trk built: {pocket_trk}")

        print("\nDecoding…")
        results = decode_all(
            pocket_trk=pocket_trk,
            workdir=args.workdir,
            selected_signal=args.signal,
            awgn_cn0=args.awgn_cn0,
            timeout_s=args.timeout,
        )

        # Determinism check re-invokes the freshly built pocket_trk, so it
        # must run before the finally below tears down the workdir (and the
        # binary inside it).  It is independent of the oracle aggregation.
        determinism_ok = True
        if args.check_determinism:
            determinism_ok = _run_determinism_check(
                pocket_trk=pocket_trk,
                workdir=args.workdir,
                awgn_cn0=args.awgn_cn0,
                timeout_s=args.timeout,
            )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    finally:
        if cleanup_workdir:
            shutil.rmtree(args.workdir, ignore_errors=True)

    print("\nResults:")
    passed_chan = 0
    passed_fec_native = 0  # decoded with bypass dormant (FID=0 frames)
    passed_fec_bypass = 0  # decoded with bypass active (FID>0 frames)
    fec_native_total = 0
    fec_bypass_total = 0
    chan_frames_total = 0
    fec_frames_total = 0
    bypass_consistency_failures: list[str] = []
    for r in results:
        chan_ok = r.byte_exact
        fec_ok = r.fec_byte_exact
        marker = "OK  " if (chan_ok and fec_ok) else "FAIL"
        bypass_tag = "BYPASS" if r.bypass_fired else "native"
        print(
            f"  [{marker}] {r.signal:55s} PRN={r.prn:3d}  "
            f"chan {'OK' if chan_ok else 'FAIL'} ({r.chan_frames_count}f)  "
            f"FEC  {'OK' if fec_ok else 'FAIL'} ({r.fec_frames_count}f, {bypass_tag})  "
            f"CRC: {r.crc_stats.summary()}"
        )
        if r.error:
            print(f"          error: {r.error}")
        if not chan_ok:
            miss = r.first_mismatch()
            if miss is not None:
                fr, by, exp, got = miss
                print(
                    f"          first symbol mismatch at frame {fr} byte {by}: "
                    f"expected {exp}, got {got}"
                )
        if not fec_ok:
            fec_miss = r.first_fec_mismatch()
            if fec_miss is not None:
                fr, by, exp, got = fec_miss
                print(
                    f"          first FEC byte mismatch at frame {fr} byte {by}: "
                    f"expected {exp}, got {got}"
                )

        # Cross-check that bypass fired iff source frame is FID=3 (boundary).
        # The structural expectation is encoded in source_frame's stem;
        # frame_boundary*.bin carry FID=3, all other frames carry FID=0.
        is_boundary = r.source_frame.startswith("frame_boundary")
        if is_boundary != r.bypass_fired:
            bypass_consistency_failures.append(
                f"{r.signal}: bypass_fired={r.bypass_fired} but "
                f"is_boundary(FID=3)={is_boundary} — patch behaviour drift"
            )

        if chan_ok:
            passed_chan += 1
            chan_frames_total += r.chan_frames_count
        if r.bypass_fired:
            fec_bypass_total += 1
            if fec_ok:
                passed_fec_bypass += 1
        else:
            fec_native_total += 1
            if fec_ok:
                passed_fec_native += 1
        if fec_ok:
            fec_frames_total += r.fec_frames_count

    print(
        f"\n  Channel-symbol oracle: {passed_chan}/{len(results)} signals byte-exactly recovered "
        f"({chan_frames_total} frame-dumps total — typically tile_count × signals)"
    )
    passed_fec_total = passed_fec_native + passed_fec_bypass
    fec_eligible = fec_native_total + fec_bypass_total
    print(
        f"  Post-FEC oracle:       {passed_fec_total}/{fec_eligible} signals byte-exactly recovered"
    )
    print(f"    native (no bypass):  {passed_fec_native}/{fec_native_total} FID=0 signals")
    print(f"    with FID-bypass:     {passed_fec_bypass}/{fec_bypass_total} FID=3 boundary frames")
    print(f"    ({fec_frames_total} frame-dumps total across both)")

    if bypass_consistency_failures:
        print(
            "\n  BYPASS CONSISTENCY FAILURES (patch behaviour drift):",
            file=sys.stderr,
        )
        for msg in bypass_consistency_failures:
            print(f"    {msg}", file=sys.stderr)

    if args.regenerate_decoded:
        print("\nRegenerating references/pocketsdr-afs/decoded/…")
        write_decoded_outputs(results)

    chan_pass = passed_chan == len(results)
    fec_pass = passed_fec_total == fec_eligible
    bypass_pass = not bypass_consistency_failures
    return 0 if (chan_pass and fec_pass and bypass_pass and determinism_ok) else 1


def _run_determinism_check(
    *, pocket_trk: Path, workdir: Path, awgn_cn0: float | None, timeout_s: float
) -> bool:
    """Re-decode a fixed signal twice; assert both runs produce identical bytes.

    Catches non-determinism in acquisition (FFT plan order), tracking-loop
    convergence (PLL initial conditions), or LDPC iteration scheduling that
    would surface as flaky CI once we add Doppler/noise.
    """
    print("\nDeterminism check:")
    # Pick a quick, FID=0 signal: signal_message_1_12s.iq.gz (PRN 1).
    target_signal_name = "signal_message_1_12s.iq.gz"
    target = next(
        (
            (fname, prn, frame)
            for fname, prn, frame in SIGNAL_TEST_VECTORS
            if fname == target_signal_name
        ),
        None,
    )
    if target is None:
        print(
            f"  WARN: {target_signal_name} not in SIGNAL_TEST_VECTORS; skipping",
            file=sys.stderr,
        )
        return True
    fname, prn, frame = target

    runs: list[bytes] = []
    fec_runs: list[bytes] = []
    for run_idx in (1, 2):
        step(f"determinism run #{run_idx} on {fname}")
        result = decode_one(
            pocket_trk=pocket_trk,
            signal_path=SIGNALS_DIR / fname,
            prn=prn,
            source_frame=frame,
            repo_root=REPO_ROOT,
            workdir=workdir / "determinism" / f"run{run_idx}" / fname.removesuffix(".iq.gz"),
            awgn_cn0_dbhz=awgn_cn0,
            timeout_s=timeout_s,
        )
        if result.error:
            print(f"  FAIL: run #{run_idx} errored: {result.error}", file=sys.stderr)
            return False
        runs.append(result.all_chan_bytes)
        fec_runs.append(result.all_fec_bytes)

    chan_ok = runs[0] == runs[1]
    fec_ok = fec_runs[0] == fec_runs[1]
    if chan_ok and fec_ok:
        print(
            f"  OK — {fname} produced byte-identical "
            f"channel ({len(runs[0])} bytes) + FEC ({len(fec_runs[0])} bytes) "
            "dumps across both runs"
        )
        return True
    if not chan_ok:
        print(
            f"  FAIL: channel-symbol dump differed across runs "
            f"(run1={len(runs[0])}B, run2={len(runs[1])}B)",
            file=sys.stderr,
        )
    if not fec_ok:
        print(
            f"  FAIL: post-FEC dump differed across runs "
            f"(run1={len(fec_runs[0])}B, run2={len(fec_runs[1])}B)",
            file=sys.stderr,
        )
    return False


if __name__ == "__main__":
    sys.exit(main())
