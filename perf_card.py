#!/usr/bin/env python3
"""LSIS-AFS perf-card harness — V2 (real encoder + grid sweep + algo card).

Spawns a decoder adapter as a subprocess, drives it via the binary stdio
protocol defined in `shaping/decoder-performance-card.md`, sweeps the
pinned Eb/N0 grid per code with uniform_random messages over 3 fixed
seeds, computes Wilson CI per grid point, and emits a `sp_results.json`
algo card matching the unified schema.

V2 scope:
  - `perf_card run --decoder <cmd> [--codes SF2,SF3,SB1] [--frames-per-seed N]
    [--out FILE]` runs the full characterisation
  - Real codewords via `lunalink.afs.{ldpc_encode, bch_encode}` Python bindings
  - Pinned methodology: seeds {42, 137, 313}, uniform_random messages,
    σ=1/√(2·R·Eb/N0), L=2y/σ², Wilson 95% CI
  - Algo card JSON emission matching the unified schema's `ldpc:` +
    `sb1:` blocks (tier-core fields; extended/full blocks deferred to a
    later slice)

Protocol (binary, little-endian — see shaping doc for full spec):
  Request  (11 + 4·n_bits bytes):
    u8 code_id; u16 max_iters; f32 sigma_sq; u32 n_bits; f32×n_bits llrs
  Response (7 + n_info_bits bytes):
    u8 status; u16 iters_used; u32 n_info_bits; u8×n_info_bits info_bits

V3+ deferred:
  - tier-extended / tier-full blocks (convergence CDF, loss budget,
    error floor, quantisation, etc.)
  - `validate` / `compare` subcommands
  - `--self-test` + reference adapter + adapter stubs (Py/C/Rust)
  - Protocol handshake (adapter declares identity / supported codes)
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# Hard dependency on lunalink Python bindings for encoding. Adopters who
# run this harness need to install lunalink — see README for setup.
from lunalink.afs import (  # type: ignore[import-not-found]
    LdpcSubframe,
    bch_encode,
    ldpc_encode,
)


# ─── Code metadata ───────────────────────────────────────────────────────

# Pinned Eb/N0 grids — matching lunalink's ldpc_characterise / bch_characterise.
LDPC_GRID = (0.2, 0.4, 0.6, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.6, 2.0, 3.0)
BCH_GRID  = (2.0, 3.0, 4.0, 5.0, 6.0, 7.6)

# Per-grid-point frames_per_seed override (BCH bumps at the operating point
# for tighter CI on the verdict row, mirroring lunalink).
BCH_OPERATING_EB_N0 = 7.6
BCH_OPERATING_FRAMES_FACTOR = 10000 // 3000  # = 3.33×; rounded to int

CODES = {
    0: {
        "name": "SB1",
        "n_bits": 52,
        "n_info_bits": 9,
        "rate": 9.0 / 52.0,
        "subframe": None,  # BCH — uses bch_encode, not ldpc_encode
        "grid_db": BCH_GRID,
        "spec_ref": "LSIS V1.0 Tables 13/14 + §2.4.3.1.1",
    },
    1: {
        "name": "SF2",
        "n_bits": 2400,
        "n_info_bits": 1200,
        "rate": 0.5,
        "subframe": LdpcSubframe.SF2,
        "grid_db": LDPC_GRID,
        "spec_ref": "LSIS V1.0 §2.4.3.1.2",
    },
    2: {
        "name": "SF3",
        "n_bits": 1740,
        "n_info_bits": 870,
        "rate": 0.5,
        "subframe": LdpcSubframe.SF3,
        "grid_db": LDPC_GRID,
        "spec_ref": "LSIS V1.0 §2.4.3.1.2",
    },
}
CODE_BY_NAME = {meta["name"]: cid for cid, meta in CODES.items()}

# Standard's pinned methodology.
SEEDS = (42, 137, 313)
DEFAULT_MAX_ITERS = 50
DEFAULT_FRAMES_PER_SEED = 5000  # matches lunalink LDPC characterise

# Wire protocol format strings.
REQUEST_HEADER_FMT  = "<BHfI"
REQUEST_HEADER_LEN  = struct.calcsize(REQUEST_HEADER_FMT)   # 11
RESPONSE_HEADER_FMT = "<BHI"
RESPONSE_HEADER_LEN = struct.calcsize(RESPONSE_HEADER_FMT)  # 7

# Spec-grounded verdict bars.
LDPC_VERDICT_BAR_BER = 1e-5
LDPC_OPERATING_POINT_EB_N0_DB = 0.0  # = Es/N0 0 dB at R=1/2
SB1_VERDICT_BAR_FER = 0.01
SB1_OPERATING_POINT_EB_N0_DB = 7.6   # = Es/N0 0 dB at R=9/52


# ─── BCH (FID, TOI) ↔ 9-bit info packing convention ──────────────────────
#
# The standard packs the 9 SB1 info bits as: FID in bits 0..1 (MSB-first),
# TOI in bits 2..8 (MSB-first). Adapters that recover SB1 (FID, TOI) MUST
# pack them this way in their response. See shaping doc § Schema strawman.

def pack_sb1_info(fid_val: int, toi_val: int) -> np.ndarray:
    info = np.zeros(9, dtype=np.uint8)
    info[0] = (fid_val >> 1) & 1
    info[1] = fid_val & 1
    for i in range(7):
        info[2 + i] = (toi_val >> (6 - i)) & 1
    return info


# ─── Encoding via lunalink bindings ──────────────────────────────────────

def generate_and_encode(
    code_id: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Return (info_bits, codeword) — uniform_random message, real codeword."""
    meta = CODES[code_id]
    if code_id == 0:  # SB1 / BCH — uniform over the 400 valid (FID, TOI) combos
        fid_val = int(rng.integers(0, 4))
        toi_val = int(rng.integers(0, 100))
        info = pack_sb1_info(fid_val, toi_val)
        codeword = np.asarray(bch_encode(fid_val, toi_val), dtype=np.uint8)
    else:  # LDPC — uniform over the 2^k info-bit space
        info = rng.integers(0, 2, meta["n_info_bits"], dtype=np.uint8)
        codeword = np.asarray(ldpc_encode(meta["subframe"], info), dtype=np.uint8)
    return info, codeword


# ─── Wilson 95% CI half-width ────────────────────────────────────────────

def wilson_ci_hw(errors: int, trials: int, z: float = 1.96) -> float:
    if trials == 0:
        return 0.0
    n = float(trials)
    p = errors / n
    denom = 1.0 + z * z / n
    return z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom


# ─── Channel: σ from Eb/N0 ───────────────────────────────────────────────

def sigma_for_eb_n0(eb_n0_db: float, rate: float) -> float:
    """σ = 1 / sqrt(2 · R · 10^(Eb_N0_db/10))."""
    return 1.0 / math.sqrt(2.0 * rate * 10.0 ** (eb_n0_db / 10.0))


# ─── Per-frame round-trip with the adapter ───────────────────────────────

@dataclass
class FramePoint:
    bit_errors: int
    frame_error: bool
    status: int       # 0=ok, 1=not_converged, 2=error
    iters_used: int


def one_frame(
    proc: subprocess.Popen[bytes],
    rng: np.random.Generator,
    code_id: int,
    sigma: float,
    sigma_sq: float,
    max_iters: int,
) -> FramePoint:
    meta = CODES[code_id]
    info, codeword = generate_and_encode(code_id, rng)

    # BPSK: bit 0 → +1, bit 1 → −1.
    bpsk = (1.0 - 2.0 * codeword.astype(np.float32)).astype(np.float32)
    noise = rng.normal(0.0, sigma, size=len(bpsk)).astype(np.float32)
    received = bpsk + noise
    llrs = ((2.0 / sigma_sq) * received).astype(np.float32)

    # Request.
    request = (
        struct.pack(
            REQUEST_HEADER_FMT, code_id, max_iters, sigma_sq, meta["n_bits"]
        )
        + llrs.tobytes()
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(request)
    proc.stdin.flush()

    # Response.
    hdr = proc.stdout.read(RESPONSE_HEADER_LEN)
    if len(hdr) < RESPONSE_HEADER_LEN:
        raise RuntimeError(
            f"adapter closed stdout mid-response (got {len(hdr)} bytes, "
            f"expected {RESPONSE_HEADER_LEN})"
        )
    status, iters_used, n_info_recovered = struct.unpack(RESPONSE_HEADER_FMT, hdr)
    if n_info_recovered != meta["n_info_bits"]:
        raise RuntimeError(
            f"adapter returned wrong info-bit count for {meta['name']}: "
            f"got {n_info_recovered}, expected {meta['n_info_bits']}"
        )
    decoded = np.frombuffer(proc.stdout.read(n_info_recovered), dtype=np.uint8)
    if len(decoded) < n_info_recovered:
        raise RuntimeError("adapter closed stdout mid-info-bits")

    diffs = info != decoded
    bit_errors = int(diffs.sum())
    return FramePoint(
        bit_errors=bit_errors,
        frame_error=(status != 0) or bool(diffs.any()),
        status=int(status),
        iters_used=int(iters_used),
    )


# ─── Grid-point aggregation across seeds ─────────────────────────────────

@dataclass
class GridPoint:
    eb_n0_db: float
    frames: int = 0
    frame_errors: int = 0
    bit_errors: int = 0
    total_bits: int = 0
    not_converged: int = 0  # status == 1
    iters_sum: int = 0      # for avg iters_used

    @property
    def fer(self) -> float:
        return self.frame_errors / self.frames if self.frames else 0.0

    @property
    def ber(self) -> float:
        return self.bit_errors / self.total_bits if self.total_bits else 0.0

    @property
    def ci_fer(self) -> float:
        return wilson_ci_hw(self.frame_errors, self.frames)


def sweep_code(
    proc: subprocess.Popen[bytes],
    code_id: int,
    frames_per_seed: int,
    max_iters: int,
) -> list[GridPoint]:
    meta = CODES[code_id]
    points = []
    for eb_n0_db in meta["grid_db"]:
        sigma = sigma_for_eb_n0(eb_n0_db, meta["rate"])
        sigma_sq = sigma * sigma
        pt = GridPoint(eb_n0_db=eb_n0_db)
        # BCH bumps the operating point's frame count for tighter CI on the verdict.
        if code_id == 0 and abs(eb_n0_db - BCH_OPERATING_EB_N0) < 1e-6:
            this_frames = frames_per_seed * BCH_OPERATING_FRAMES_FACTOR
        else:
            this_frames = frames_per_seed
        for seed in SEEDS:
            ss = np.random.SeedSequence(
                entropy=seed, spawn_key=(code_id, int(eb_n0_db * 1000))
            )
            rng = np.random.default_rng(ss)
            for _ in range(this_frames):
                r = one_frame(
                    proc, rng, code_id, sigma, sigma_sq, max_iters
                )
                pt.frames += 1
                pt.bit_errors += r.bit_errors
                pt.total_bits += meta["n_info_bits"]
                if r.frame_error:
                    pt.frame_errors += 1
                if r.status == 1:
                    pt.not_converged += 1
                pt.iters_sum += r.iters_used
        print(
            f"  [{meta['name']}] Eb/N0={eb_n0_db:>4.1f} dB  "
            f"FER={pt.fer:.6f} ± {pt.ci_fer:.6f}  "
            f"(frames={pt.frames}, frame_errors={pt.frame_errors})",
            file=sys.stderr,
        )
        points.append(pt)
    return points


# ─── Verdict computation per code ────────────────────────────────────────

def ldpc_verdict(points: list[GridPoint]) -> dict[str, Any]:
    """Lowest grid point at Eb/N0 ≥ 0 where BER < 1e-5; else best in-band."""
    in_band = [
        p for p in points if p.eb_n0_db >= LDPC_OPERATING_POINT_EB_N0_DB
    ]
    passing = [p for p in in_band if p.ber < LDPC_VERDICT_BAR_BER]
    if passing:
        anchor = min(passing, key=lambda p: p.eb_n0_db)
        verdict_pass = True
    elif in_band:
        anchor = min(in_band, key=lambda p: p.ber)
        verdict_pass = False
    else:
        return {}
    return {
        "criterion": f"BER < {LDPC_VERDICT_BAR_BER:g} at Es/N0 >= 0 dB",
        "at_eb_n0_db": anchor.eb_n0_db,
        "ber": anchor.ber,
        "pass": verdict_pass,
    }


def sb1_verdict(points: list[GridPoint]) -> dict[str, Any]:
    """FER at the spec operating point (Eb/N0 = 7.6 dB = Es/N0 0 dB at R=9/52)."""
    op = next(
        (p for p in points if abs(p.eb_n0_db - SB1_OPERATING_POINT_EB_N0_DB) < 1e-6),
        None,
    )
    if op is None:
        return {}
    return {
        "criterion": f"FER < {SB1_VERDICT_BAR_FER:g} at Es/N0 >= 0 dB",
        "at_eb_n0_db": SB1_OPERATING_POINT_EB_N0_DB,
        "fer": op.fer,
        "ci_fer": op.ci_fer,
        "pass": op.fer < SB1_VERDICT_BAR_FER,
    }


# ─── Algo card emission (unified schema) ─────────────────────────────────

def waterfall_entry_ldpc(p: GridPoint) -> dict[str, Any]:
    return {
        "eb_n0_db": p.eb_n0_db,
        "fer": p.fer,
        "ber": p.ber,
        "ci_fer": p.ci_fer,
        "frames": p.frames,
        "frame_errors": p.frame_errors,
        "bit_errors": p.bit_errors,
        "total_bits": p.total_bits,
        "not_converged": p.not_converged,
    }


def waterfall_entry_sb1(p: GridPoint) -> dict[str, Any]:
    return {
        "eb_n0_db": p.eb_n0_db,
        "fer": p.fer,
        "ci_fer": p.ci_fer,
        "frame_errors": p.frame_errors,
        "frames": p.frames,
    }


def ldpc_subframe_block(
    code_id: int,
    points: list[GridPoint],
    frames_per_seed: int,
    applies_to: list[str] | None = None,
) -> dict[str, Any]:
    meta = CODES[code_id]
    code_block: dict[str, Any] = {
        "k": meta["n_info_bits"],
        "n": meta["n_bits"],
        "rate": "1/2",
        "spec_ref": meta["spec_ref"],
    }
    if applies_to:
        code_block["applies_to"] = applies_to
    return {
        "code": code_block,
        "frames_per_seed": frames_per_seed,
        "eb_n0_grid_db": list(meta["grid_db"]),
        "waterfall": [waterfall_entry_ldpc(p) for p in points],
        "verdict": ldpc_verdict(points),
    }


def build_algo_card(
    *,
    sweep: dict[int, list[GridPoint]],
    frames_per_seed: int,
    max_iters: int,
    decoder_meta: dict[str, str],
    sb1_decoder_meta: dict[str, str],
    reference_anchor: dict[str, str],
    elapsed_s: float,
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "schema_version": "1.0.0",
        "tier": "core",
        "produced_by": "lsis-afs perf_card V2",
        "produced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference_anchor": reference_anchor,
        "elapsed_seconds": round(elapsed_s, 1),
        "operating_point": {
            "system_es_n0_db": 0.0,
            "notes": "LSIS spec SNR >= 0 dB. Each code's Eb/N0 derives via its rate.",
        },
        "channel": {
            "model": "BPSK-AWGN",
            "sigma_formula": "1 / sqrt(2 * R * 10^(Eb_N0_db/10))",
            "llr_formula": "2 * y / sigma^2",
            "symbol_mapping": "bit 0 -> +1, bit 1 -> -1",
        },
        "methodology": {
            "seeds": list(SEEDS),
            "message_ensemble": "uniform_random",
            "ci_method": "wilson_95",
        },
    }
    # LDPC block (if any LDPC code ran).
    ldpc_codes = {cid: pts for cid, pts in sweep.items() if cid in (1, 2)}
    if ldpc_codes:
        card["ldpc"] = {
            "decoder": decoder_meta.get("name", "unknown"),
            "algorithm": decoder_meta.get("algorithm", "unspecified"),
            "max_iterations": max_iters,
            "early_termination": decoder_meta.get(
                "early_termination", "unspecified"
            ),
            "subframes": {},
        }
        if 1 in ldpc_codes:
            card["ldpc"]["subframes"]["SF2"] = ldpc_subframe_block(
                1, ldpc_codes[1], frames_per_seed
            )
        if 2 in ldpc_codes:
            card["ldpc"]["subframes"]["SF3_SF4"] = ldpc_subframe_block(
                2, ldpc_codes[2], frames_per_seed,
                applies_to=["SF3", "SF4"],
            )
    # SB1 block (if BCH ran).
    if 0 in sweep:
        sb1_points = sweep[0]
        card["sb1"] = {
            "code": {
                "k": 9,
                "n": 52,
                "rate": "9/52",
                "codebook_size": 400,
                "structure": "4 FIDs * 100 TOIs",
                "spec_ref": CODES[0]["spec_ref"],
            },
            "decoder": {
                "name": sb1_decoder_meta.get("name", "unknown"),
                "class": sb1_decoder_meta.get("class", "other"),
                "algorithm": sb1_decoder_meta.get("algorithm", "unspecified"),
            },
            "frame_error_definition":
                "decoded FID != transmitted OR decoded TOI != transmitted",
            "frames_per_seed_default": frames_per_seed,
            "eb_n0_grid_db": list(CODES[0]["grid_db"]),
            "waterfall": [waterfall_entry_sb1(p) for p in sb1_points],
            "verdict": sb1_verdict(sb1_points),
        }
    return card


# ─── CLI ─────────────────────────────────────────────────────────────────

def cmd_run(args: argparse.Namespace) -> int:
    requested = [c.strip() for c in args.codes.split(",") if c.strip()]
    bad = [c for c in requested if c not in CODE_BY_NAME]
    if bad:
        print(f"unknown code(s): {bad}", file=sys.stderr)
        return 2
    code_ids = [CODE_BY_NAME[c] for c in requested]

    decoder_cmd = shlex.split(args.decoder)
    print(
        f"[harness] codes={requested}  "
        f"frames_per_seed={args.frames_per_seed}  seeds={SEEDS}  "
        f"max_iters={args.max_iters}",
        file=sys.stderr,
    )
    print(f"[harness] spawning adapter: {decoder_cmd}", file=sys.stderr)

    proc = subprocess.Popen(
        decoder_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )

    sweep: dict[int, list[GridPoint]] = {}
    t0 = time.time()
    try:
        for cid in code_ids:
            print(f"[harness] sweeping {CODES[cid]['name']}…", file=sys.stderr)
            sweep[cid] = sweep_code(
                proc, cid, args.frames_per_seed, args.max_iters
            )
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        try:
            rc = proc.wait(timeout=10)
            if rc != 0:
                print(f"[harness] adapter exited with code {rc}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            proc.kill()
            print("[harness] adapter wait timeout — killed", file=sys.stderr)
    elapsed_s = time.time() - t0

    decoder_meta = {
        "name": args.decoder_name,
        "algorithm": args.decoder_algorithm,
        "early_termination": args.decoder_early_termination,
    }
    sb1_decoder_meta = {
        "name": args.sb1_decoder_name or args.decoder_name,
        "class": args.sb1_decoder_class,
        "algorithm": args.sb1_decoder_algorithm or args.decoder_algorithm,
    }
    reference_anchor: dict[str, str] = {
        "repo": args.reference_anchor_repo,
    }
    if args.reference_anchor_tag:
        reference_anchor["tag"] = args.reference_anchor_tag
    if args.reference_anchor_commit:
        reference_anchor["commit"] = args.reference_anchor_commit

    card = build_algo_card(
        sweep=sweep,
        frames_per_seed=args.frames_per_seed,
        max_iters=args.max_iters,
        decoder_meta=decoder_meta,
        sb1_decoder_meta=sb1_decoder_meta,
        reference_anchor=reference_anchor,
        elapsed_s=elapsed_s,
    )
    out_text = json.dumps(card, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
        print(f"[harness] wrote {args.out} ({len(out_text):,} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(out_text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="perf_card",
        description="LSIS-AFS Decoder Performance Card harness (V2).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser(
        "run",
        help=(
            "Sweep an adapter over the full pinned Eb/N0 grid per code and "
            "emit an algo card."
        ),
    )
    rp.add_argument("--decoder", required=True, help="Adapter executable.")
    rp.add_argument(
        "--codes", default="SB1,SF2,SF3",
        help="Comma-separated list of codes to characterise (default: all).",
    )
    rp.add_argument(
        "--frames-per-seed", type=int, default=DEFAULT_FRAMES_PER_SEED,
        help="Frames per seed per grid point (default: 5000).",
    )
    rp.add_argument(
        "--max-iters", type=int, default=DEFAULT_MAX_ITERS,
        help="max_iters passed to the adapter (default: 50).",
    )
    rp.add_argument("--out", help="Path to write algo card JSON (default: stdout).")

    # Adapter metadata (passed into the card; harness has no way to detect
    # these from the binary protocol yet — V3 handshake will).
    rp.add_argument(
        "--decoder-name", default="adapter",
        help="LDPC decoder name (recorded in card).",
    )
    rp.add_argument(
        "--decoder-algorithm", default="unspecified",
        help="LDPC decoder algorithm description.",
    )
    rp.add_argument(
        "--decoder-early-termination", default="unspecified",
        help="LDPC decoder early-termination strategy.",
    )
    rp.add_argument("--sb1-decoder-name", default=None)
    rp.add_argument(
        "--sb1-decoder-class", default="other",
        choices=["hard_ML", "soft_ML", "BDD", "other"],
    )
    rp.add_argument("--sb1-decoder-algorithm", default=None)

    # Reference vector anchor (informational; harness doesn't validate against it).
    rp.add_argument(
        "--reference-anchor-repo",
        default="luar-space/lsis-afs-test-vectors",
    )
    rp.add_argument("--reference-anchor-tag", default=None)
    rp.add_argument("--reference-anchor-commit", default=None)

    rp.set_defaults(func=cmd_run)
    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
