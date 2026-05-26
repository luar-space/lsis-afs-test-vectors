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


# ─── `validate` subcommand ───────────────────────────────────────────────
#
# Checks that a card conforms to the standard schema. Adopters who produce
# cards by the fallback path (writing JSON from the written standard
# rather than via `run`) use this to confirm their card is well-formed.

# Required methodology values per the standard.
EXPECTED_METHODOLOGY = {
    "seeds": list(SEEDS),
    "message_ensemble": "uniform_random",
    "ci_method": "wilson_95",
}
EXPECTED_OPERATING_ES_N0_DB = 0.0
EXPECTED_LDPC_VERDICT_CRITERION = (
    f"BER < {LDPC_VERDICT_BAR_BER:g} at Es/N0 >= 0 dB"
)
EXPECTED_SB1_VERDICT_CRITERION = (
    f"FER < {SB1_VERDICT_BAR_FER:g} at Es/N0 >= 0 dB"
)


def _validate_waterfall_row(
    row: dict[str, Any], row_kind: str, errors: list[str], path: str
) -> None:
    """Common required fields for any waterfall entry."""
    required = ["eb_n0_db", "fer", "ci_fer", "frames", "frame_errors"]
    if row_kind == "ldpc":
        # LDPC rows additionally carry bit-level statistics.
        required += ["ber", "bit_errors", "total_bits"]
    for k in required:
        if k not in row:
            errors.append(f"{path}: missing key '{k}'")


def _validate_ldpc_subframe(
    block: dict[str, Any], label: str, errors: list[str]
) -> None:
    path = f"ldpc.subframes.{label}"
    for k in ("code", "frames_per_seed", "eb_n0_grid_db", "waterfall", "verdict"):
        if k not in block:
            errors.append(f"{path}: missing key '{k}'")
    if "eb_n0_grid_db" in block:
        grid = block["eb_n0_grid_db"]
        if list(grid) != list(LDPC_GRID):
            errors.append(
                f"{path}.eb_n0_grid_db: does not match pinned LDPC grid "
                f"({list(LDPC_GRID)})"
            )
    if "waterfall" in block:
        for i, row in enumerate(block["waterfall"]):
            _validate_waterfall_row(row, "ldpc", errors, f"{path}.waterfall[{i}]")
    if "verdict" in block and block["verdict"]:
        v = block["verdict"]
        if v.get("criterion") != EXPECTED_LDPC_VERDICT_CRITERION:
            errors.append(
                f"{path}.verdict.criterion: expected "
                f"'{EXPECTED_LDPC_VERDICT_CRITERION}', got '{v.get('criterion')}'"
            )
        for k in ("at_eb_n0_db", "ber", "pass"):
            if k not in v:
                errors.append(f"{path}.verdict: missing key '{k}'")


def _validate_sb1(block: dict[str, Any], errors: list[str]) -> None:
    path = "sb1"
    for k in (
        "code", "decoder", "frame_error_definition",
        "eb_n0_grid_db", "waterfall", "verdict",
    ):
        if k not in block:
            errors.append(f"{path}: missing key '{k}'")
    if "eb_n0_grid_db" in block:
        grid = block["eb_n0_grid_db"]
        if list(grid) != list(BCH_GRID):
            errors.append(
                f"{path}.eb_n0_grid_db: does not match pinned BCH grid "
                f"({list(BCH_GRID)})"
            )
    if "decoder" in block:
        d = block["decoder"]
        for k in ("name", "class", "algorithm"):
            if k not in d:
                errors.append(f"{path}.decoder: missing key '{k}'")
        if "class" in d and d["class"] not in {"hard_ML", "soft_ML", "BDD", "other"}:
            errors.append(
                f"{path}.decoder.class: '{d['class']}' is not in "
                f"{{hard_ML, soft_ML, BDD, other}}"
            )
    if "waterfall" in block:
        for i, row in enumerate(block["waterfall"]):
            _validate_waterfall_row(row, "sb1", errors, f"{path}.waterfall[{i}]")
    if "verdict" in block and block["verdict"]:
        v = block["verdict"]
        if v.get("criterion") != EXPECTED_SB1_VERDICT_CRITERION:
            errors.append(
                f"{path}.verdict.criterion: expected "
                f"'{EXPECTED_SB1_VERDICT_CRITERION}', got '{v.get('criterion')}'"
            )
        for k in ("at_eb_n0_db", "fer", "pass"):
            if k not in v:
                errors.append(f"{path}.verdict: missing key '{k}'")


def validate_card(card: dict[str, Any]) -> list[str]:
    """Returns a list of validation errors. Empty list means the card is valid."""
    errors: list[str] = []

    # Required top-level fields.
    for k in (
        "schema_version", "tier", "produced_by", "produced_at",
        "reference_anchor", "operating_point", "channel", "methodology",
    ):
        if k not in card:
            errors.append(f"missing top-level key '{k}'")

    if "tier" in card and card["tier"] not in ("core", "extended", "full"):
        errors.append(
            f"tier: '{card['tier']}' not in {{core, extended, full}}"
        )

    if "operating_point" in card:
        op = card["operating_point"]
        if op.get("system_es_n0_db") != EXPECTED_OPERATING_ES_N0_DB:
            errors.append(
                f"operating_point.system_es_n0_db: expected "
                f"{EXPECTED_OPERATING_ES_N0_DB}, got {op.get('system_es_n0_db')}"
            )

    if "channel" in card:
        ch = card["channel"]
        if ch.get("model") != "BPSK-AWGN":
            errors.append(
                f"channel.model: expected 'BPSK-AWGN', got '{ch.get('model')}'"
            )
        for k in ("sigma_formula", "llr_formula"):
            if k not in ch:
                errors.append(f"channel: missing key '{k}'")

    if "methodology" in card:
        m = card["methodology"]
        for k, expected in EXPECTED_METHODOLOGY.items():
            if m.get(k) != expected:
                errors.append(
                    f"methodology.{k}: expected {expected!r}, got {m.get(k)!r}"
                )

    # At least one code block must be present.
    has_ldpc = "ldpc" in card and card["ldpc"]
    has_sb1 = "sb1" in card and card["sb1"]
    if not (has_ldpc or has_sb1):
        errors.append("card has neither 'ldpc' nor 'sb1' block — empty")

    if has_ldpc:
        ldpc = card["ldpc"]
        for k in ("decoder", "algorithm", "max_iterations", "subframes"):
            if k not in ldpc:
                errors.append(f"ldpc: missing key '{k}'")
        if "subframes" in ldpc:
            subs = ldpc["subframes"]
            for label, block in subs.items():
                _validate_ldpc_subframe(block, label, errors)
            if "SF2" not in subs and "SF3_SF4" not in subs:
                errors.append(
                    "ldpc.subframes: at least one of SF2 or SF3_SF4 expected"
                )

    if has_sb1:
        _validate_sb1(card["sb1"], errors)

    return errors


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        card = json.loads(Path(args.card).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{args.card}: cannot load JSON: {exc}", file=sys.stderr)
        return 1
    errors = validate_card(card)
    if not errors:
        print(f"OK — {args.card} conforms to the standard schema.")
        return 0
    print(f"{args.card}: {len(errors)} validation error(s):", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    return 1


# ─── `compare` subcommand ────────────────────────────────────────────────
#
# Statistical comparison between two algo cards on tier-core fields.
# This is NOT diff (equality check) — perf cards from different teams are
# expected to diverge; the question is "by how much, statistically?"

def _ci_overlap(a_fer: float, a_ci: float, b_fer: float, b_ci: float) -> str:
    """Return 'A<' (A strictly better), 'B<' (B strictly better), or '=' (tied within CI)."""
    a_lo, a_hi = a_fer - a_ci, a_fer + a_ci
    b_lo, b_hi = b_fer - b_ci, b_fer + b_ci
    if a_hi < b_lo:
        return "A<"
    if b_hi < a_lo:
        return "B<"
    return "="


def _compare_waterfall(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    label: str,
    verbose: bool,
) -> dict[str, Any]:
    """Compare two waterfalls grid point by grid point."""
    a_by_db = {round(p["eb_n0_db"], 3): p for p in a}
    b_by_db = {round(p["eb_n0_db"], 3): p for p in b}
    shared = sorted(a_by_db.keys() & b_by_db.keys())
    if not shared:
        return {"label": label, "shared_points": 0, "summary": "no shared grid points"}

    a_better = 0
    b_better = 0
    tied = 0
    rows: list[dict[str, Any]] = []
    for db in shared:
        ap = a_by_db[db]
        bp = b_by_db[db]
        verdict = _ci_overlap(ap["fer"], ap["ci_fer"], bp["fer"], bp["ci_fer"])
        if verdict == "A<":
            a_better += 1
        elif verdict == "B<":
            b_better += 1
        else:
            tied += 1
        rows.append({
            "eb_n0_db": db,
            "a_fer": ap["fer"], "a_ci": ap["ci_fer"],
            "b_fer": bp["fer"], "b_ci": bp["ci_fer"],
            "verdict": verdict,
        })

    if verbose:
        print(f"\n{label} waterfall (shared {len(shared)} of "
              f"{len(a_by_db)}/{len(b_by_db)} grid points):")
        print(f"  {'Eb/N0':>6}  {'A FER':>10}  {'± CI':>10}  "
              f"{'B FER':>10}  {'± CI':>10}  verdict")
        for r in rows:
            v = r["verdict"]
            mark = "A wins" if v == "A<" else "B wins" if v == "B<" else "tied"
            print(
                f"  {r['eb_n0_db']:>5.1f}  "
                f"{r['a_fer']:>10.6f}  {r['a_ci']:>10.6f}  "
                f"{r['b_fer']:>10.6f}  {r['b_ci']:>10.6f}  {mark}"
            )

    return {
        "label": label,
        "shared_points": len(shared),
        "a_better": a_better,
        "b_better": b_better,
        "tied": tied,
        "summary": (
            f"A better at {a_better} points, B better at {b_better}, "
            f"tied at {tied}"
        ),
        "rows": rows,
    }


def _compare_verdict(
    av: dict[str, Any] | None, bv: dict[str, Any] | None, label: str
) -> dict[str, Any]:
    if not av and not bv:
        return {"label": label, "summary": "no verdicts present"}
    if not av:
        return {"label": label, "summary": "A missing verdict"}
    if not bv:
        return {"label": label, "summary": "B missing verdict"}
    a_pass = av.get("pass")
    b_pass = bv.get("pass")
    if a_pass == b_pass:
        outcome = "both pass" if a_pass else "both fail"
    elif a_pass:
        outcome = "A passes, B fails"
    else:
        outcome = "B passes, A fails"
    return {
        "label": label,
        "a_pass": a_pass,
        "b_pass": b_pass,
        "a_at_eb_n0_db": av.get("at_eb_n0_db"),
        "b_at_eb_n0_db": bv.get("at_eb_n0_db"),
        "summary": outcome,
    }


def compare_cards(
    a: dict[str, Any], b: dict[str, Any], verbose: bool = False
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "a_identity": {
            "produced_by": a.get("produced_by"),
            "tier": a.get("tier"),
            "ldpc_decoder": a.get("ldpc", {}).get("decoder"),
            "sb1_decoder": a.get("sb1", {}).get("decoder", {}).get("name"),
            "sb1_class": a.get("sb1", {}).get("decoder", {}).get("class"),
        },
        "b_identity": {
            "produced_by": b.get("produced_by"),
            "tier": b.get("tier"),
            "ldpc_decoder": b.get("ldpc", {}).get("decoder"),
            "sb1_decoder": b.get("sb1", {}).get("decoder", {}).get("name"),
            "sb1_class": b.get("sb1", {}).get("decoder", {}).get("class"),
        },
        "anchor_match": (
            a.get("reference_anchor") == b.get("reference_anchor")
        ),
        "waterfalls": [],
        "verdicts": [],
    }

    # LDPC subframe comparison.
    a_ldpc = a.get("ldpc", {}).get("subframes", {})
    b_ldpc = b.get("ldpc", {}).get("subframes", {})
    for sf in ("SF2", "SF3_SF4"):
        if sf in a_ldpc and sf in b_ldpc:
            wf = _compare_waterfall(
                a_ldpc[sf].get("waterfall", []),
                b_ldpc[sf].get("waterfall", []),
                f"ldpc.{sf}",
                verbose,
            )
            result["waterfalls"].append(wf)
            result["verdicts"].append(_compare_verdict(
                a_ldpc[sf].get("verdict"),
                b_ldpc[sf].get("verdict"),
                f"ldpc.{sf}",
            ))

    # SB1 comparison.
    if "sb1" in a and "sb1" in b:
        result["waterfalls"].append(_compare_waterfall(
            a["sb1"].get("waterfall", []),
            b["sb1"].get("waterfall", []),
            "sb1",
            verbose,
        ))
        result["verdicts"].append(_compare_verdict(
            a["sb1"].get("verdict"),
            b["sb1"].get("verdict"),
            "sb1",
        ))

    return result


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        a = json.loads(Path(args.card_a).read_text(encoding="utf-8"))
        b = json.loads(Path(args.card_b).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot load cards: {exc}", file=sys.stderr)
        return 2

    result = compare_cards(a, b, verbose=args.verbose)

    if args.json:
        sys.stdout.write(json.dumps(result, indent=2) + "\n")
        return 0

    # Human-readable summary.
    print(f"A: {args.card_a}")
    print(f"   produced_by:  {result['a_identity']['produced_by']}")
    print(f"   ldpc decoder: {result['a_identity']['ldpc_decoder']}")
    print(f"   sb1 decoder:  {result['a_identity']['sb1_decoder']} "
          f"({result['a_identity']['sb1_class']})")
    print()
    print(f"B: {args.card_b}")
    print(f"   produced_by:  {result['b_identity']['produced_by']}")
    print(f"   ldpc decoder: {result['b_identity']['ldpc_decoder']}")
    print(f"   sb1 decoder:  {result['b_identity']['sb1_decoder']} "
          f"({result['b_identity']['sb1_class']})")
    print()
    if not result["anchor_match"]:
        print("WARNING: cards reference different anchor — comparison may not be fair")
        print(f"  A anchor: {a.get('reference_anchor')}")
        print(f"  B anchor: {b.get('reference_anchor')}")
        print()

    print("Verdicts:")
    for v in result["verdicts"]:
        print(f"  {v['label']:>15}: {v['summary']}")
    print()
    print("Waterfall (per-point CI-overlap):")
    for w in result["waterfalls"]:
        print(f"  {w['label']:>15}: {w['summary']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="perf_card",
        description=(
            "LSIS-AFS Decoder Performance Card harness — run / validate / compare."
        ),
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

    # ── validate ────────────────────────────────────────────────────────
    vp = sub.add_parser(
        "validate",
        help="Check that an algo card conforms to the standard schema.",
    )
    vp.add_argument("card", help="Path to the algo card JSON.")
    vp.set_defaults(func=cmd_validate)

    # ── compare ─────────────────────────────────────────────────────────
    cp = sub.add_parser(
        "compare",
        help=(
            "Statistically compare two algo cards on tier-core fields. "
            "Reports verdict outcomes and per-grid-point CI-overlap "
            "ranking (A better / B better / tied)."
        ),
    )
    cp.add_argument("card_a", help="First algo card JSON.")
    cp.add_argument("card_b", help="Second algo card JSON.")
    cp.add_argument(
        "--verbose", action="store_true",
        help="Print full per-grid-point waterfall table.",
    )
    cp.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of human summary.",
    )
    cp.set_defaults(func=cmd_compare)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
