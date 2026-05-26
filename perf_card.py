#!/usr/bin/env python3
"""LSIS-AFS perf-card harness — V1 (run + protocol mechanics).

Spawns a decoder adapter as a subprocess, drives it via the binary stdio
protocol defined in `shaping/decoder-performance-card.md`, tallies frame
errors at a single Eb/N0 point.

V1 scope:
  - One subcommand: `run --decoder <cmd> --code <SB1|SF2|SF3> --eb-n0-db <x>`
  - One Eb/N0 point per invocation (no grid sweep yet — that's V2)
  - All-zero codeword scaffold (no encoder integration yet — that's V2;
    the all-zero codeword is the codeword for the all-zero info bits in
    any linear systematic code, so we don't need an encoder to bootstrap)
  - Plain stdout summary (no algo card emission yet — that's V2)

V2+ will add:
  - Real encoder integration (lifts the all-zero scaffold)
  - uniform_random messages per the standard's methodology
  - Full Eb/N0 grid sweep across all codes
  - `sp_results.json` algo card emission matching the unified schema
  - `validate`, `compare`, `--self-test` subcommands

Protocol (per the strawman):
  Request  (11 + 4·n_bits bytes, little-endian):
    u8   code_id      0=SB1, 1=SF2, 2=SF3
    u16  max_iters    0 = adapter default
    f32  sigma_sq     AWGN variance
    u32  n_bits       length of LLR array
    f32 × n_bits      channel LLRs (L = 2y/σ²)
  Response (7 + n_info_bits bytes, little-endian):
    u8   status       0=ok, 1=not_converged, 2=error
    u16  iters_used   informational
    u32  n_info_bits  9 / 1200 / 870 — must match what code_id implies
    u8  × n_info      decoded info bits ∈ {0,1}
"""

from __future__ import annotations

import argparse
import shlex
import struct
import subprocess
import sys
from dataclasses import dataclass

import numpy as np


# Code metadata — keyed by code_id (the wire identifier).
CODES = {
    0: {"name": "SB1", "n_bits": 52,   "n_info_bits": 9,    "rate": 9.0 / 52.0},
    1: {"name": "SF2", "n_bits": 2400, "n_info_bits": 1200, "rate": 0.5},
    2: {"name": "SF3", "n_bits": 1740, "n_info_bits": 870,  "rate": 0.5},
}
CODE_BY_NAME = {meta["name"]: cid for cid, meta in CODES.items()}

# Wire protocol struct formats (little-endian, no padding).
REQUEST_HEADER_FMT  = "<BHfI"   # code_id, max_iters, sigma_sq, n_bits
REQUEST_HEADER_LEN  = struct.calcsize(REQUEST_HEADER_FMT)   # 11
RESPONSE_HEADER_FMT = "<BHI"    # status, iters_used, n_info_bits
RESPONSE_HEADER_LEN = struct.calcsize(RESPONSE_HEADER_FMT)  # 7

assert REQUEST_HEADER_LEN == 11
assert RESPONSE_HEADER_LEN == 7


@dataclass
class FrameResult:
    frame_error: bool
    status: int           # adapter status code: 0=ok, 1=not_converged, 2=error
    iters_used: int
    n_info_recovered: int


def _sigma_for_eb_n0(eb_n0_db: float, rate: float) -> float:
    """σ = 1 / sqrt(2 · R · 10^(Eb_N0_db/10))."""
    eb_n0_lin = 10.0 ** (eb_n0_db / 10.0)
    return 1.0 / np.sqrt(2.0 * rate * eb_n0_lin)


def _one_frame(
    proc: subprocess.Popen[bytes],
    rng: np.random.Generator,
    code_id: int,
    sigma: float,
    sigma_sq: float,
    max_iters: int,
) -> FrameResult:
    """Drive one decode round-trip and return the verdict.

    V1: encodes the all-zero message as the all-zero codeword (valid for
    any linear systematic code over GF(2)). Real encoder integration in V2.
    """
    meta = CODES[code_id]
    n_info_expected = meta["n_info_bits"]
    n_bits = meta["n_bits"]

    # === V1 scaffold: all-zero message and codeword ===
    message = np.zeros(n_info_expected, dtype=np.uint8)
    codeword = np.zeros(n_bits, dtype=np.uint8)
    # === end V1 scaffold ===

    # BPSK: bit 0 → +1, bit 1 → −1.
    bpsk = (1.0 - 2.0 * codeword.astype(np.float32)).astype(np.float32)
    noise = rng.normal(0.0, sigma, size=n_bits).astype(np.float32)
    received = bpsk + noise
    llrs = ((2.0 / sigma_sq) * received).astype(np.float32)

    # Send request.
    request = (
        struct.pack(REQUEST_HEADER_FMT, code_id, max_iters, sigma_sq, n_bits)
        + llrs.tobytes()
    )
    assert proc.stdin is not None
    proc.stdin.write(request)
    proc.stdin.flush()

    # Read response.
    assert proc.stdout is not None
    hdr = proc.stdout.read(RESPONSE_HEADER_LEN)
    if len(hdr) < RESPONSE_HEADER_LEN:
        raise RuntimeError(
            f"adapter closed stdout mid-response (got {len(hdr)} bytes, "
            f"expected {RESPONSE_HEADER_LEN})"
        )
    status, iters_used, n_info_recovered = struct.unpack(RESPONSE_HEADER_FMT, hdr)
    if n_info_recovered != n_info_expected:
        raise RuntimeError(
            f"adapter returned wrong info-bit count for {meta['name']}: "
            f"got {n_info_recovered}, expected {n_info_expected}"
        )
    decoded = np.frombuffer(
        proc.stdout.read(n_info_recovered), dtype=np.uint8
    )
    if len(decoded) < n_info_recovered:
        raise RuntimeError(
            f"adapter closed stdout mid-info-bits "
            f"(got {len(decoded)}, expected {n_info_recovered})"
        )

    frame_error = (status != 0) or (not np.array_equal(message, decoded))
    return FrameResult(
        frame_error=frame_error,
        status=status,
        iters_used=iters_used,
        n_info_recovered=int(n_info_recovered),
    )


def cmd_run(args: argparse.Namespace) -> int:
    code_id = CODE_BY_NAME[args.code]
    meta = CODES[code_id]
    sigma = _sigma_for_eb_n0(args.eb_n0_db, meta["rate"])
    sigma_sq = sigma * sigma

    rng = np.random.default_rng(np.random.SeedSequence(args.seed))

    decoder_cmd = shlex.split(args.decoder)
    print(
        f"[harness] code={meta['name']}  Eb/N0={args.eb_n0_db:.2f} dB  "
        f"σ={sigma:.4f}  σ²={sigma_sq:.4f}  frames={args.frames}",
        file=sys.stderr,
    )
    print(f"[harness] spawning adapter: {decoder_cmd}", file=sys.stderr)

    proc = subprocess.Popen(
        decoder_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # stderr passes through to our stderr (adapter diagnostics visible)
    )

    frame_errors = 0
    status_counts = {0: 0, 1: 0, 2: 0}
    iters_total = 0

    try:
        for _ in range(args.frames):
            r = _one_frame(proc, rng, code_id, sigma, sigma_sq, args.max_iters)
            if r.frame_error:
                frame_errors += 1
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
            iters_total += r.iters_used
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        rc = proc.wait(timeout=5)
        if rc != 0:
            print(f"[harness] adapter exited with code {rc}", file=sys.stderr)

    fer = frame_errors / args.frames if args.frames > 0 else 0.0
    avg_iters = iters_total / args.frames if args.frames > 0 else 0.0

    print()
    print(f"  code:           {meta['name']}")
    print(f"  Eb/N0:          {args.eb_n0_db:.2f} dB")
    print(f"  frames:         {args.frames}")
    print(f"  frame_errors:   {frame_errors}")
    print(f"  FER:            {fer:.6f}")
    print(f"  status_ok:      {status_counts[0]}")
    print(f"  status_nconv:   {status_counts[1]}")
    print(f"  status_error:   {status_counts[2]}")
    print(f"  avg iters_used: {avg_iters:.2f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="perf_card",
        description="LSIS-AFS Decoder Performance Card harness (V1).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser(
        "run", help="Run the adapter at a single Eb/N0 point and report FER."
    )
    rp.add_argument(
        "--decoder",
        required=True,
        help="Adapter executable (shell-quoted; can include args).",
    )
    rp.add_argument(
        "--code", choices=["SB1", "SF2", "SF3"], required=True
    )
    rp.add_argument("--eb-n0-db", type=float, required=True)
    rp.add_argument(
        "--frames", type=int, default=100,
        help="Number of frames per seed (default: 100).",
    )
    rp.add_argument(
        "--max-iters", type=int, default=50,
        help="max_iters passed to the adapter (default: 50).",
    )
    rp.add_argument(
        "--seed", type=int, default=42,
        help="RNG seed for AWGN noise (default: 42).",
    )
    rp.set_defaults(func=cmd_run)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
