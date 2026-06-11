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
import concurrent.futures
import json
import math
import os
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

# NOTE: this harness does NOT import lunalink. Codewords come from a
# shipped reference pool (`perf_card_reference_codewords.npz`) generated
# once by the maintainer via `tools/generate_perf_card_reference_codewords.py`.
# Lunalink is treated as just another decoder implementation — the same
# adopter contract any team would use.


# ─── Code metadata ───────────────────────────────────────────────────────

# Pinned Eb/N0 grids — matching lunalink's ldpc_characterise / bch_characterise.
LDPC_GRID = (0.2, 0.4, 0.6, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.6, 2.0, 3.0)
BCH_GRID = (2.0, 3.0, 4.0, 5.0, 6.0, 7.6)

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
        "grid_db": BCH_GRID,
        "spec_ref": "LSIS V1.0 Tables 13/14 + §2.4.3.1.1",
    },
    1: {
        "name": "SF2",
        "n_bits": 2400,
        "n_info_bits": 1200,
        "rate": 0.5,
        "grid_db": LDPC_GRID,
        "spec_ref": "LSIS V1.0 §2.4.3.1.2",
    },
    2: {
        "name": "SF3",
        "n_bits": 1740,
        "n_info_bits": 870,
        "rate": 0.5,
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
REQUEST_HEADER_FMT = "<BHfI"
REQUEST_HEADER_LEN = struct.calcsize(REQUEST_HEADER_FMT)  # 11
RESPONSE_HEADER_FMT = "<BHI"
RESPONSE_HEADER_LEN = struct.calcsize(RESPONSE_HEADER_FMT)  # 7

# Handshake protocol version. Adapters must reply with the same version.
PROTOCOL_VERSION = "1.0"
HARNESS_NAME = "lsis-afs perf_card"
HARNESS_VERSION = "1.0.0"

# Spec-grounded verdict bars.
LDPC_VERDICT_BAR_BER = 1e-5
# Spec operating point converted into the code's Eb/N0 axis:
#   Es/N0 [dB] = Eb/N0 [dB] + 10·log10(R)
# so Es/N0 = 0 dB  ⟺  Eb/N0 = 10·log10(1/R).
# For LDPC R = 1/2 → Eb/N0 = 10·log10(2) ≈ 3.01 dB.
# (The 3.0 dB grid point is the boundary of the spec operating region.)
LDPC_OPERATING_POINT_EB_N0_DB = 3.0  # = Es/N0 0 dB at R=1/2
SB1_VERDICT_BAR_FER = 0.01
SB1_OPERATING_POINT_EB_N0_DB = 7.6  # = Es/N0 0 dB at R=9/52


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


# ─── Shipped reference codeword pool ─────────────────────────────────────
#
# The harness samples codewords from a shipped pool of (info, codeword)
# pairs — bit-packed and compressed in perf_card_reference_codewords.npz.
# The pool was generated once by the maintainer via lunalink's encoder
# (see tools/generate_perf_card_reference_codewords.py); from then on
# the harness needs no encoder dependency.
#
# The harness picks pool indices via the seeded RNG, so:
#   - Two harnesses run with the same seed sequence see identical
#     (info, codeword) pairs at identical positions in the frame stream.
#   - Two adopters' algo cards are therefore methodologically aligned
#     down to byte-identical channel realisations modulo the RNG (numpy
#     `default_rng` for AWGN noise samples).
#
# Pool size: 100 BCH pairs + 1000 LDPC pairs per LDPC code.

REFERENCE_CODEWORDS_BASENAME = "perf_card_reference_codewords.npz"


@dataclass
class CodewordPool:
    sb1_info: np.ndarray  # (n_bch, 9)  uint8 {0,1}
    sb1_codeword: np.ndarray  # (n_bch, 52)
    sf2_info: np.ndarray  # (n_ldpc, 1200)
    sf2_codeword: np.ndarray  # (n_ldpc, 2400)
    sf3_info: np.ndarray  # (n_ldpc, 870)
    sf3_codeword: np.ndarray  # (n_ldpc, 1740)

    def pair(self, code_id: int, index: int) -> tuple[np.ndarray, np.ndarray]:
        """Return (info_bits, codeword) for `code_id` at `index` modulo pool size."""
        if code_id == 0:
            i = index % self.sb1_info.shape[0]
            return self.sb1_info[i], self.sb1_codeword[i]
        if code_id == 1:
            i = index % self.sf2_info.shape[0]
            return self.sf2_info[i], self.sf2_codeword[i]
        if code_id == 2:
            i = index % self.sf3_info.shape[0]
            return self.sf3_info[i], self.sf3_codeword[i]
        raise ValueError(f"unknown code_id: {code_id}")


def load_codeword_pool() -> CodewordPool:
    """Load the shipped reference codeword pool. Lazy-loaded once."""
    here = Path(__file__).resolve().parent
    path = here / REFERENCE_CODEWORDS_BASENAME
    if not path.exists():
        raise FileNotFoundError(
            f"Reference codeword pool not found at {path}. "
            f"(Run tools/generate_perf_card_reference_codewords.py — "
            f"maintainer task, requires lunalink — to regenerate.)"
        )
    with np.load(path) as z:

        def unpack(name: str, n_bits: int) -> np.ndarray:
            return np.unpackbits(z[name], axis=1)[:, :n_bits]

        pool = CodewordPool(
            sb1_info=unpack("sb1_info_packed", 9),
            sb1_codeword=unpack("sb1_codeword_packed", 52),
            sf2_info=unpack("sf2_info_packed", 1200),
            sf2_codeword=unpack("sf2_codeword_packed", 2400),
            sf3_info=unpack("sf3_info_packed", 870),
            sf3_codeword=unpack("sf3_codeword_packed", 1740),
        )
    return pool


_pool_singleton: CodewordPool | None = None


def get_pool() -> CodewordPool:
    """Lazy-load the codeword pool once per process."""
    global _pool_singleton  # noqa: PLW0603 — singleton lazy-init pattern
    if _pool_singleton is None:
        _pool_singleton = load_codeword_pool()
    return _pool_singleton


def generate_and_encode(code_id: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Return (info_bits, codeword) — sampled from the shipped pool via
    a seeded random index, so two harnesses with the same seed see the
    same pairs in the same positions."""
    pool = get_pool()
    # Sample a pool index. Modulo-cycle is fine — pool size is large enough
    # that across a full sweep each codeword is reused only tens of times.
    if code_id == 0:
        idx = int(rng.integers(0, pool.sb1_info.shape[0]))
    elif code_id == 1:
        idx = int(rng.integers(0, pool.sf2_info.shape[0]))
    else:
        idx = int(rng.integers(0, pool.sf3_info.shape[0]))
    return pool.pair(code_id, idx)


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


# ─── Handshake: adapter self-declares identity at session start ──────────
#
# The handshake happens once per session, before any binary frame request.
# Framing: length-prefixed JSON in each direction.
#
#   harness → adapter   <u32 LE n_bytes><n_bytes JSON HandshakeRequest>
#   adapter → harness   <u32 LE n_bytes><n_bytes JSON HandshakeAck>
#
# After the ack, the protocol switches to the binary per-frame format
# described above.


def _send_length_prefixed_json(stream, obj: dict[str, Any]) -> None:
    payload = json.dumps(obj).encode("utf-8")
    stream.write(struct.pack("<I", len(payload)))
    stream.write(payload)
    stream.flush()


def _recv_length_prefixed_json(stream) -> dict[str, Any]:
    n_bytes = stream.read(4)
    if len(n_bytes) < 4:
        raise RuntimeError("counterparty closed stream during handshake")
    n = struct.unpack("<I", n_bytes)[0]
    payload = stream.read(n)
    if len(payload) < n:
        raise RuntimeError(
            f"counterparty closed stream mid-handshake (got {len(payload)} bytes, expected {n})"
        )
    return json.loads(payload)


def handshake_with_adapter(proc: subprocess.Popen[bytes]) -> dict[str, Any]:
    """Run the protocol handshake; return the adapter's self-description."""
    assert proc.stdin is not None and proc.stdout is not None
    _send_length_prefixed_json(
        proc.stdin,
        {
            "type": "handshake",
            "protocol_version": PROTOCOL_VERSION,
            "harness": {"name": HARNESS_NAME, "version": HARNESS_VERSION},
        },
    )
    resp = _recv_length_prefixed_json(proc.stdout)
    if resp.get("type") != "handshake_ack":
        raise RuntimeError(f"adapter returned wrong handshake type: {resp.get('type')!r}")
    if resp.get("protocol_version") != PROTOCOL_VERSION:
        raise RuntimeError(
            f"adapter protocol_version mismatch: "
            f"got {resp.get('protocol_version')!r}, "
            f"expected {PROTOCOL_VERSION!r}"
        )
    adapter = resp.get("adapter")
    if not isinstance(adapter, dict):
        raise RuntimeError(f"adapter block missing or malformed: {resp}")
    return adapter


def adapter_decoder_meta(adapter_info: dict[str, Any]) -> dict[str, str]:
    """Extract LDPC decoder_meta from a handshake response."""
    ldpc = adapter_info.get("ldpc", {})
    return {
        "name": adapter_info.get("name", "adapter"),
        "algorithm": ldpc.get("algorithm", "unspecified"),
        "early_termination": ldpc.get("early_termination", "unspecified"),
    }


def adapter_sb1_meta(adapter_info: dict[str, Any]) -> dict[str, str]:
    """Extract SB1 decoder_meta from a handshake response."""
    sb1 = adapter_info.get("sb1", {})
    return {
        "name": sb1.get("name", adapter_info.get("name", "adapter")),
        "class": sb1.get("decoder_class", "other"),
        "algorithm": sb1.get("algorithm", "unspecified"),
    }


# ─── Per-frame round-trip with the adapter ───────────────────────────────


@dataclass
class FramePoint:
    bit_errors: int
    frame_error: bool
    status: int  # 0=ok, 1=not_converged, 2=error
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
        struct.pack(REQUEST_HEADER_FMT, code_id, max_iters, sigma_sq, meta["n_bits"])
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
    iters_sum: int = 0  # for avg iters_used

    @property
    def fer(self) -> float:
        return self.frame_errors / self.frames if self.frames else 0.0

    @property
    def ber(self) -> float:
        return self.bit_errors / self.total_bits if self.total_bits else 0.0

    @property
    def ci_fer(self) -> float:
        return wilson_ci_hw(self.frame_errors, self.frames)


# Extended-tier convergence_cdf probe parameters (matches lunalink's
# ldpc_characterise.cpp Step 5). Sweeps max_iters at the cliff Eb/N0 to
# expose how many iterations the decoder actually needs to clear the floor.
CONVERGENCE_PROBE_EB_N0_DB = 1.5  # cliff vicinity for R=1/2 LDPC
CONVERGENCE_PROBE_ITERS = (1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50)
CONVERGENCE_PROBE_FRAMES_PER_SEED = 2000

# Full-tier probes — match lunalink ldpc_characterise.cpp.
ERROR_FLOOR_PROBE_EB_N0_DB = (2.5, 3.0)
ERROR_FLOOR_PROBE_FRAMES_PER_SEED = 20000  # 60k per point — for upper-CI tightness
ERROR_PATTERN_PROBE_EB_N0_DB = (1.0, 1.2, 1.4)
ERROR_PATTERN_PROBE_FRAMES = 5000  # single-seed run; per-frame bit-error counts
ERROR_PATTERN_HISTOGRAM_BINS = ((1, 5), (6, 20), (21, 100), (101, 300), (301, None))
SATURATION_STRESS_EB_N0_DB = (0.1, 10.0)
SATURATION_STRESS_FRAMES_PER_SEED = 1000  # sanity check at the extremes


def sweep_one_grid_point(
    proc: subprocess.Popen[bytes],
    code_id: int,
    eb_n0_db: float,
    frames_per_seed: int,
    max_iters: int,
) -> GridPoint:
    """Run the Monte Carlo for one (code, Eb/N0) point against `proc`.

    Independent of all other grid points — drives parallelism: each
    worker can claim one of these and run to completion against its own
    adapter subprocess. RNG seeding is purely a function of (seed,
    code_id, eb_n0_db), so two workers running the same grid point
    against the same decoder produce identical results.
    """
    meta = CODES[code_id]
    sigma = sigma_for_eb_n0(eb_n0_db, meta["rate"])
    sigma_sq = sigma * sigma
    pt = GridPoint(eb_n0_db=eb_n0_db)
    # BCH bumps the operating point's frame count for tighter CI on the verdict.
    if code_id == 0 and abs(eb_n0_db - BCH_OPERATING_EB_N0) < 1e-6:
        this_frames = frames_per_seed * BCH_OPERATING_FRAMES_FACTOR
    else:
        this_frames = frames_per_seed
    for seed in SEEDS:
        ss = np.random.SeedSequence(entropy=seed, spawn_key=(code_id, int(eb_n0_db * 1000)))
        rng = np.random.default_rng(ss)
        for _ in range(this_frames):
            r = one_frame(proc, rng, code_id, sigma, sigma_sq, max_iters)
            pt.frames += 1
            pt.bit_errors += r.bit_errors
            pt.total_bits += meta["n_info_bits"]
            if r.frame_error:
                pt.frame_errors += 1
            if r.status == 1:
                pt.not_converged += 1
            pt.iters_sum += r.iters_used
    return pt


def sweep_code(
    proc: subprocess.Popen[bytes],
    code_id: int,
    frames_per_seed: int,
    max_iters: int,
) -> list[GridPoint]:
    """Serial sweep across all grid points of one code. Used by the
    single-worker path. Multi-worker uses `sweep_one_grid_point` directly
    via the worker pool below."""
    meta = CODES[code_id]
    points = []
    for eb_n0_db in meta["grid_db"]:
        pt = sweep_one_grid_point(proc, code_id, eb_n0_db, frames_per_seed, max_iters)
        print(
            f"  [{meta['name']}] Eb/N0={eb_n0_db:>4.1f} dB  "
            f"FER={pt.fer:.6f} ± {pt.ci_fer:.6f}  "
            f"(frames={pt.frames}, frame_errors={pt.frame_errors})",
            file=sys.stderr,
        )
        points.append(pt)
    return points


# ─── Multiprocessing: per-worker adapter + grid-point dispatcher ─────────
#
# Each worker process spawns its own adapter subprocess at init time and
# keeps it alive for the worker's lifetime. The parent dispatches grid
# points across the pool. Adapter handshake costs are paid N times (one
# per worker) instead of 1 — trivial against the cost of a full sweep.

_worker_proc: subprocess.Popen[bytes] | None = None


def _worker_init(decoder_cmd: list[str]) -> None:
    """Called once per worker process. Spawns the adapter and handshakes."""
    global _worker_proc  # noqa: PLW0603 — per-worker singleton, set once at init
    _worker_proc = subprocess.Popen(
        decoder_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    handshake_with_adapter(_worker_proc)


def _worker_run_point(
    task: tuple[int, float, int, int],
) -> tuple[int, float, GridPoint]:
    """Run one grid point against this worker's adapter."""
    code_id, eb_n0_db, frames_per_seed, max_iters = task
    assert _worker_proc is not None
    pt = sweep_one_grid_point(_worker_proc, code_id, eb_n0_db, frames_per_seed, max_iters)
    return code_id, eb_n0_db, pt


def _worker_handshake_only(decoder_cmd: list[str]) -> dict[str, Any]:
    """Used by the parent to do one handshake (just to capture adapter
    identity for the algo card) without keeping the subprocess alive."""
    proc = subprocess.Popen(
        decoder_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        info = handshake_with_adapter(proc)
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return info


def parallel_sweep(
    decoder_cmd: list[str],
    code_ids: list[int],
    frames_per_seed: int,
    max_iters: int,
    n_workers: int,
) -> tuple[dict[int, list[GridPoint]], dict[str, Any]]:
    """Distribute the full grid sweep across `n_workers` adapter
    subprocesses. Returns (sweep_dict, adapter_info_from_handshake).
    """
    # One quick handshake to capture adapter identity for the card.
    adapter_info = _worker_handshake_only(decoder_cmd)

    # Build the task list (one entry per grid point).
    tasks: list[tuple[int, float, int, int]] = []
    for cid in code_ids:
        for eb_n0_db in CODES[cid]["grid_db"]:
            tasks.append((cid, eb_n0_db, frames_per_seed, max_iters))

    sweep: dict[int, dict[float, GridPoint]] = {cid: {} for cid in code_ids}

    n_threads = n_workers
    if n_threads <= 0:
        n_threads = max(1, (os.cpu_count() or 1) - 1)
    print(
        f"[harness] parallel sweep — {n_threads} worker(s), {len(tasks)} grid points",
        file=sys.stderr,
    )
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=n_threads,
        initializer=_worker_init,
        initargs=(decoder_cmd,),
    ) as pool:
        for i, (cid_out, eb_n0_out, pt) in enumerate(pool.map(_worker_run_point, tasks), start=1):
            sweep[cid_out][eb_n0_out] = pt
            print(
                f"  [{CODES[cid_out]['name']}] Eb/N0={eb_n0_out:>4.1f} dB  "
                f"FER={pt.fer:.6f} ± {pt.ci_fer:.6f}  "
                f"(frames={pt.frames}, frame_errors={pt.frame_errors}) "
                f"[{i}/{len(tasks)}]",
                file=sys.stderr,
            )

    # Reassemble: per code, points sorted by Eb/N0 in the original order.
    out: dict[int, list[GridPoint]] = {}
    for cid in code_ids:
        ordered = [sweep[cid][db] for db in CODES[cid]["grid_db"]]
        out[cid] = ordered
    return out, adapter_info


# ─── Extended-tier: convergence CDF probe ────────────────────────────────


@dataclass
class ConvergencePoint:
    max_iters: int
    frames: int = 0
    frame_errors: int = 0
    not_converged: int = 0

    @property
    def fer(self) -> float:
        return self.frame_errors / self.frames if self.frames else 0.0

    @property
    def ci_fer(self) -> float:
        return wilson_ci_hw(self.frame_errors, self.frames)


def probe_convergence_cdf(
    proc: subprocess.Popen[bytes],
    code_id: int,
    frames_per_seed: int = CONVERGENCE_PROBE_FRAMES_PER_SEED,
    iters_list: tuple[int, ...] = CONVERGENCE_PROBE_ITERS,
    eb_n0_db: float = CONVERGENCE_PROBE_EB_N0_DB,
) -> list[ConvergencePoint]:
    """For LDPC: sweep max_iters at one Eb/N0 point, return per-iter FER.

    Exposes the convergence curve (how many BP iterations the decoder
    actually needs to drop FER to its floor). At max_iters values below
    convergence, almost every frame returns status=not_converged.
    """
    meta = CODES[code_id]
    sigma = sigma_for_eb_n0(eb_n0_db, meta["rate"])
    sigma_sq = sigma * sigma

    print(
        f"[harness] convergence-cdf probe ({meta['name']} @ {eb_n0_db} dB)…",
        file=sys.stderr,
    )
    results: list[ConvergencePoint] = []
    for max_iters in iters_list:
        pt = ConvergencePoint(max_iters=max_iters)
        for seed in SEEDS:
            ss = np.random.SeedSequence(
                entropy=seed,
                spawn_key=(code_id, int(eb_n0_db * 1000), max_iters, 0xC0FE),
            )
            rng = np.random.default_rng(ss)
            for _ in range(frames_per_seed):
                r = one_frame(proc, rng, code_id, sigma, sigma_sq, max_iters)
                pt.frames += 1
                if r.frame_error:
                    pt.frame_errors += 1
                if r.status == 1:
                    pt.not_converged += 1
        print(
            f"  [{meta['name']}] max_iters={max_iters:>3}  "
            f"FER={pt.fer:.6f} ± {pt.ci_fer:.6f}  "
            f"not_converged={pt.not_converged}/{pt.frames}",
            file=sys.stderr,
        )
        results.append(pt)
    return results


# ─── Full-tier: error_floor probe (deep characterisation at high Eb/N0) ──


def probe_error_floor(
    proc: subprocess.Popen[bytes],
    code_id: int,
    eb_n0_list: tuple[float, ...] = ERROR_FLOOR_PROBE_EB_N0_DB,
    frames_per_seed: int = ERROR_FLOOR_PROBE_FRAMES_PER_SEED,
) -> dict[str, dict[str, Any]]:
    """At each of a few high Eb/N0 points, run many frames so the CI upper
    bound is small even when zero errors are observed — characterises
    where the curve flattens out (decoder's error floor)."""
    meta = CODES[code_id]
    print(
        f"[harness] error-floor probe ({meta['name']} @ {eb_n0_list} dB, "
        f"{frames_per_seed} frames/seed)…",
        file=sys.stderr,
    )
    results: dict[str, dict[str, Any]] = {}
    for eb_n0_db in eb_n0_list:
        sigma = sigma_for_eb_n0(eb_n0_db, meta["rate"])
        sigma_sq = sigma * sigma
        pt = GridPoint(eb_n0_db=eb_n0_db)
        for seed in SEEDS:
            ss = np.random.SeedSequence(
                entropy=seed,
                spawn_key=(code_id, int(eb_n0_db * 1000), 0xF100),
            )
            rng = np.random.default_rng(ss)
            for _ in range(frames_per_seed):
                r = one_frame(proc, rng, code_id, sigma, sigma_sq, DEFAULT_MAX_ITERS)
                pt.frames += 1
                pt.bit_errors += r.bit_errors
                pt.total_bits += meta["n_info_bits"]
                if r.frame_error:
                    pt.frame_errors += 1
                if r.status == 1:
                    pt.not_converged += 1
        key = f"floor_{meta['name'].lower()}_{eb_n0_db}"
        ci_upper = pt.fer + pt.ci_fer if pt.frame_errors else wilson_ci_hw(0, pt.frames)
        results[key] = {
            "fer": pt.fer,
            "ber": pt.ber,
            "ci_fer": pt.ci_fer,
            "frames": pt.frames,
            "frame_errors": pt.frame_errors,
            "total_bits": pt.total_bits,
            "ci_fer_upper": ci_upper,
        }
        print(
            f"  [{meta['name']}] Eb/N0={eb_n0_db} dB  "
            f"FER={pt.fer:.2e}  ({pt.frame_errors}/{pt.frames})  "
            f"CI upper={ci_upper:.2e}",
            file=sys.stderr,
        )
    return results


# ─── Full-tier: error_patterns probe (per-frame bit-error histograms) ────


def probe_error_patterns(
    proc: subprocess.Popen[bytes],
    code_id: int,
    eb_n0_list: tuple[float, ...] = ERROR_PATTERN_PROBE_EB_N0_DB,
    frames: int = ERROR_PATTERN_PROBE_FRAMES,
) -> list[dict[str, Any]]:
    """Single-seed, per-frame error-count histogram. Captures the
    DISTRIBUTION of bit errors per frame (not just FER), revealing
    whether errors cluster or spread."""
    meta = CODES[code_id]
    print(
        f"[harness] error-pattern probe ({meta['name']} @ {eb_n0_list} dB, {frames} frames/point)…",
        file=sys.stderr,
    )
    results: list[dict[str, Any]] = []
    for eb_n0_db in eb_n0_list:
        sigma = sigma_for_eb_n0(eb_n0_db, meta["rate"])
        sigma_sq = sigma * sigma
        # Single seed by convention (matches lunalink).
        ss = np.random.SeedSequence(
            entropy=SEEDS[0],
            spawn_key=(code_id, int(eb_n0_db * 1000), 0xE7E7),
        )
        rng = np.random.default_rng(ss)
        per_frame_errs: list[int] = []
        max_errs = 0
        for _ in range(frames):
            r = one_frame(proc, rng, code_id, sigma, sigma_sq, DEFAULT_MAX_ITERS)
            if r.bit_errors > 0:
                per_frame_errs.append(r.bit_errors)
                max_errs = max(max_errs, r.bit_errors)
        # Histogram with the same bins lunalink uses.
        histogram = []
        for lo, hi in ERROR_PATTERN_HISTOGRAM_BINS:
            if hi is None:
                count = sum(1 for e in per_frame_errs if e >= lo)
                label = f"{lo}+"
            else:
                count = sum(1 for e in per_frame_errs if lo <= e <= hi)
                label = f"{lo}-{hi}"
            histogram.append({"range": label, "count": count})
        results.append(
            {
                "eb_n0_db": eb_n0_db,
                "total_frames": frames,
                "frame_errors": len(per_frame_errs),
                "max_bit_errors": max_errs,
                "k_nominal": meta["n_info_bits"],
                "histogram": histogram,
                "raw_errors": per_frame_errs,
            }
        )
        print(
            f"  [{meta['name']}] Eb/N0={eb_n0_db} dB  "
            f"frame_errors={len(per_frame_errs)}/{frames}  "
            f"max_bit_errors={max_errs}",
            file=sys.stderr,
        )
    return results


# ─── Full-tier: saturation_stress probe (extreme-SNR sanity) ─────────────


def probe_saturation_stress(
    proc: subprocess.Popen[bytes],
    code_id: int,
    eb_n0_list: tuple[float, ...] = SATURATION_STRESS_EB_N0_DB,
    frames_per_seed: int = SATURATION_STRESS_FRAMES_PER_SEED,
) -> dict[str, dict[str, Any]]:
    """Smoke-test at very low and very high SNR: the decoder shouldn't
    crash on either extreme. Low: ~all-errors expected. High: ~no-errors."""
    meta = CODES[code_id]
    print(
        f"[harness] saturation-stress probe ({meta['name']} @ {eb_n0_list} dB)…",
        file=sys.stderr,
    )
    results: dict[str, dict[str, Any]] = {}
    for eb_n0_db in eb_n0_list:
        sigma = sigma_for_eb_n0(eb_n0_db, meta["rate"])
        sigma_sq = sigma * sigma
        pt = GridPoint(eb_n0_db=eb_n0_db)
        for seed in SEEDS:
            ss = np.random.SeedSequence(
                entropy=seed,
                spawn_key=(code_id, int(eb_n0_db * 1000), 0x5A57),
            )
            rng = np.random.default_rng(ss)
            for _ in range(frames_per_seed):
                r = one_frame(proc, rng, code_id, sigma, sigma_sq, DEFAULT_MAX_ITERS)
                pt.frames += 1
                pt.bit_errors += r.bit_errors
                pt.total_bits += meta["n_info_bits"]
                if r.frame_error:
                    pt.frame_errors += 1
                if r.status == 1:
                    pt.not_converged += 1
        label = "low_snr" if eb_n0_db < 1.0 else "high_snr"
        results[label] = {
            "eb_n0_db": eb_n0_db,
            "fer": pt.fer,
            "ber": pt.ber,
            "frames": pt.frames,
            "not_converged": pt.not_converged,
        }
        print(
            f"  [{meta['name']}] {label}  Eb/N0={eb_n0_db} dB  "
            f"FER={pt.fer:.4f}  not_converged={pt.not_converged}",
            file=sys.stderr,
        )
    return results


# ─── Verdict computation per code ────────────────────────────────────────


def ldpc_verdict(points: list[GridPoint]) -> dict[str, Any]:
    """LDPC verdict block.

    The verdict carries two distinct signals:

    1. **Spec compliance** (`pass`, `at_eb_n0_db`, `ber`) — does the
       decoder meet `BER < 1e-5` at the spec operating point
       (Es/N0 ≥ 0 dB, i.e. Eb/N0 ≥ 3 dB for R=1/2)? Computed at the
       lowest in-band grid point where the bar holds; binary outcome.

    2. **Cross-team comparison** (`first_bar_crossing_eb_n0_db`,
       `margin_below_spec_db`) — at what Eb/N0 does the decoder first
       achieve the bar, *regardless* of whether that's in the spec's
       operating region? This is what differentiates implementations:
       two cards that both PASS can sit several dB apart on this axis,
       which is what `perf-card leaderboard` ranks on.
    """
    in_band = [p for p in points if p.eb_n0_db >= LDPC_OPERATING_POINT_EB_N0_DB]
    in_band_passing = [p for p in in_band if p.ber < LDPC_VERDICT_BAR_BER]
    if in_band_passing:
        anchor = min(in_band_passing, key=lambda p: p.eb_n0_db)
        verdict_pass = True
    elif in_band:
        anchor = min(in_band, key=lambda p: p.ber)
        verdict_pass = False
    else:
        return {}

    # Comparison metric: lowest Eb/N0 anywhere on the waterfall where
    # the decoder achieves the bar. May be lower than at_eb_n0_db.
    all_passing = [p for p in points if p.ber < LDPC_VERDICT_BAR_BER]
    if all_passing:
        first = min(all_passing, key=lambda p: p.eb_n0_db)
        first_eb_n0 = first.eb_n0_db
        margin = anchor.eb_n0_db - first_eb_n0
    else:
        first_eb_n0 = None
        margin = None

    return {
        "criterion": f"BER < {LDPC_VERDICT_BAR_BER:g} at Es/N0 >= 0 dB",
        "at_eb_n0_db": anchor.eb_n0_db,
        "ber": anchor.ber,
        "pass": verdict_pass,
        # Comparison fields — used by leaderboard / compare ranking.
        "first_bar_crossing_eb_n0_db": first_eb_n0,
        "margin_below_spec_db": margin,
    }


def sb1_verdict(points: list[GridPoint]) -> dict[str, Any]:
    """SB1 verdict block.

    Same two-signal structure as the LDPC verdict — spec compliance at
    the operating point (Eb/N0 = 7.6 dB = Es/N0 0 dB at R=9/52), plus
    comparison-only fields for ranking decoders that all PASS.
    """
    op = next(
        (p for p in points if abs(p.eb_n0_db - SB1_OPERATING_POINT_EB_N0_DB) < 1e-6),
        None,
    )
    if op is None:
        return {}

    all_passing = [p for p in points if p.fer < SB1_VERDICT_BAR_FER]
    if all_passing:
        first = min(all_passing, key=lambda p: p.eb_n0_db)
        first_eb_n0 = first.eb_n0_db
        margin = SB1_OPERATING_POINT_EB_N0_DB - first_eb_n0
    else:
        first_eb_n0 = None
        margin = None

    return {
        "criterion": f"FER < {SB1_VERDICT_BAR_FER:g} at Es/N0 >= 0 dB",
        "at_eb_n0_db": SB1_OPERATING_POINT_EB_N0_DB,
        "fer": op.fer,
        "ci_fer": op.ci_fer,
        "pass": op.fer < SB1_VERDICT_BAR_FER,
        # Comparison fields — used by leaderboard / compare ranking.
        "first_bar_crossing_eb_n0_db": first_eb_n0,
        "margin_below_spec_db": margin,
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
    tier: str = "core",
    convergence_cdf: list[ConvergencePoint] | None = None,
    error_floor: dict[str, dict[str, Any]] | None = None,
    error_patterns: list[dict[str, Any]] | None = None,
    saturation_stress: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "schema_version": "1.0.0",
        "tier": tier,
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
            "early_termination": decoder_meta.get("early_termination", "unspecified"),
            "subframes": {},
        }
        if 1 in ldpc_codes:
            card["ldpc"]["subframes"]["SF2"] = ldpc_subframe_block(
                1, ldpc_codes[1], frames_per_seed
            )
        if 2 in ldpc_codes:
            card["ldpc"]["subframes"]["SF3_SF4"] = ldpc_subframe_block(
                2,
                ldpc_codes[2],
                frames_per_seed,
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
            "frame_error_definition": "decoded FID != transmitted OR decoded TOI != transmitted",
            "frames_per_seed_default": frames_per_seed,
            "eb_n0_grid_db": list(CODES[0]["grid_db"]),
            "waterfall": [waterfall_entry_sb1(p) for p in sb1_points],
            "verdict": sb1_verdict(sb1_points),
        }

    # ── Extended-tier blocks (present iff tier == "extended" or "full") ──
    if tier in ("extended", "full") and convergence_cdf:
        card["ldpc_extended"] = {
            "convergence_cdf": [
                {
                    "max_iters": p.max_iters,
                    "fer": p.fer,
                    "ci_fer": p.ci_fer,
                    "not_converged": p.not_converged,
                    "frames": p.frames,
                }
                for p in convergence_cdf
            ],
        }

    # ── Full-tier blocks (present iff tier == "full") ────────────────────
    if tier == "full":
        full_block: dict[str, Any] = {}
        if error_floor:
            full_block["error_floor"] = error_floor
        if error_patterns:
            full_block["error_patterns"] = error_patterns
        if saturation_stress:
            full_block["saturation_stress"] = saturation_stress
        if full_block:
            card["ldpc_full"] = full_block
    return card


# ─── CLI ─────────────────────────────────────────────────────────────────


def run_harness(  # noqa: PLR0912, PLR0915 — orchestrator: tier/probes/finally cleanup
    *,
    decoder_cmd: list[str],
    code_ids: list[int],
    frames_per_seed: int,
    max_iters: int,
    reference_anchor: dict[str, str],
    tier: str = "core",
    n_workers: int = 1,
) -> dict[str, Any]:
    """Run the handshake + main sweep (+ tier-extended/full probes where
    applicable) and return the algo card dict.

    When n_workers > 1 the main grid sweep runs in parallel across N
    adapter subprocesses (each worker spawns one). Tier-extended/full
    probes always run serially against a final adapter — they're smaller
    by an order of magnitude and the implementation complexity isn't
    worth the savings.

    Shared core between `run` and `self-test`. Adapter identity (decoder
    name / algorithm / class) is taken from the handshake response.
    """
    print(
        f"[harness] tier={tier}  codes={[CODES[c]['name'] for c in code_ids]}  "
        f"frames_per_seed={frames_per_seed}  seeds={SEEDS}  "
        f"max_iters={max_iters}  workers={n_workers}",
        file=sys.stderr,
    )

    sweep: dict[int, list[GridPoint]] = {}
    convergence_cdf: list[ConvergencePoint] | None = None
    error_floor: dict[str, dict[str, Any]] | None = None
    error_patterns: list[dict[str, Any]] | None = None
    saturation_stress: dict[str, dict[str, Any]] | None = None
    adapter_info: dict[str, Any] = {}
    t0 = time.time()

    if n_workers > 1:
        # ── Parallel grid sweep ──
        sweep, adapter_info = parallel_sweep(
            decoder_cmd=decoder_cmd,
            code_ids=code_ids,
            frames_per_seed=frames_per_seed,
            max_iters=max_iters,
            n_workers=n_workers,
        )
        # Extended/full probes run serially against a fresh adapter.
        if tier in ("extended", "full") and 1 in code_ids:
            proc = subprocess.Popen(
                decoder_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            handshake_with_adapter(proc)
            try:
                convergence_cdf = probe_convergence_cdf(proc, code_id=1)
                if tier == "full":
                    error_floor = {}
                    error_floor.update(probe_error_floor(proc, code_id=1))
                    if 2 in code_ids:
                        error_floor.update(probe_error_floor(proc, code_id=2))
                    error_patterns = probe_error_patterns(proc, code_id=1)
                    saturation_stress = probe_saturation_stress(proc, code_id=1)
            finally:
                assert proc.stdin is not None
                proc.stdin.close()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    else:
        # ── Serial path (single adapter, every probe) ──
        print(f"[harness] spawning adapter: {decoder_cmd}", file=sys.stderr)
        proc = subprocess.Popen(
            decoder_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        adapter_info = handshake_with_adapter(proc)
        print(
            f"[harness] handshake ok — adapter: {adapter_info.get('name', '?')} "
            f"(supports: {adapter_info.get('supports_codes', '?')})",
            file=sys.stderr,
        )
        try:
            for cid in code_ids:
                print(
                    f"[harness] sweeping {CODES[cid]['name']}…",
                    file=sys.stderr,
                )
                sweep[cid] = sweep_code(proc, cid, frames_per_seed, max_iters)
            if tier in ("extended", "full") and 1 in code_ids:
                convergence_cdf = probe_convergence_cdf(proc, code_id=1)
            if tier == "full" and 1 in code_ids:
                error_floor = {}
                error_floor.update(probe_error_floor(proc, code_id=1))
                if 2 in code_ids:
                    error_floor.update(probe_error_floor(proc, code_id=2))
                error_patterns = probe_error_patterns(proc, code_id=1)
                saturation_stress = probe_saturation_stress(proc, code_id=1)
        finally:
            assert proc.stdin is not None
            proc.stdin.close()
            try:
                rc = proc.wait(timeout=10)
                if rc != 0:
                    print(
                        f"[harness] adapter exited with code {rc}",
                        file=sys.stderr,
                    )
            except subprocess.TimeoutExpired:
                proc.kill()
                print(
                    "[harness] adapter wait timeout — killed",
                    file=sys.stderr,
                )
    elapsed_s = time.time() - t0

    return build_algo_card(
        sweep=sweep,
        frames_per_seed=frames_per_seed,
        max_iters=max_iters,
        decoder_meta=adapter_decoder_meta(adapter_info),
        sb1_decoder_meta=adapter_sb1_meta(adapter_info),
        reference_anchor=reference_anchor,
        elapsed_s=elapsed_s,
        tier=tier,
        convergence_cdf=convergence_cdf,
        error_floor=error_floor,
        error_patterns=error_patterns,
        saturation_stress=saturation_stress,
    )


def cmd_run(args: argparse.Namespace) -> int:
    requested = [c.strip() for c in args.codes.split(",") if c.strip()]
    bad = [c for c in requested if c not in CODE_BY_NAME]
    if bad:
        print(f"unknown code(s): {bad}", file=sys.stderr)
        return 2
    code_ids = [CODE_BY_NAME[c] for c in requested]

    reference_anchor: dict[str, str] = {"repo": args.reference_anchor_repo}
    if args.reference_anchor_tag:
        reference_anchor["tag"] = args.reference_anchor_tag
    if args.reference_anchor_commit:
        reference_anchor["commit"] = args.reference_anchor_commit

    card = run_harness(
        decoder_cmd=shlex.split(args.decoder),
        code_ids=code_ids,
        frames_per_seed=args.frames_per_seed,
        max_iters=args.max_iters,
        reference_anchor=reference_anchor,
        tier=args.tier,
        n_workers=args.workers,
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
EXPECTED_LDPC_VERDICT_CRITERION = f"BER < {LDPC_VERDICT_BAR_BER:g} at Es/N0 >= 0 dB"
EXPECTED_SB1_VERDICT_CRITERION = f"FER < {SB1_VERDICT_BAR_FER:g} at Es/N0 >= 0 dB"


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


def _validate_ldpc_subframe(block: dict[str, Any], label: str, errors: list[str]) -> None:
    path = f"ldpc.subframes.{label}"
    for k in ("code", "frames_per_seed", "eb_n0_grid_db", "waterfall", "verdict"):
        if k not in block:
            errors.append(f"{path}: missing key '{k}'")
    if "eb_n0_grid_db" in block:
        grid = block["eb_n0_grid_db"]
        if list(grid) != list(LDPC_GRID):
            errors.append(
                f"{path}.eb_n0_grid_db: does not match pinned LDPC grid ({list(LDPC_GRID)})"
            )
    if "waterfall" in block:
        for i, row in enumerate(block["waterfall"]):
            _validate_waterfall_row(row, "ldpc", errors, f"{path}.waterfall[{i}]")
    if block.get("verdict"):
        v = block["verdict"]
        if v.get("criterion") != EXPECTED_LDPC_VERDICT_CRITERION:
            errors.append(
                f"{path}.verdict.criterion: expected "
                f"'{EXPECTED_LDPC_VERDICT_CRITERION}', got '{v.get('criterion')}'"
            )
        for k in ("at_eb_n0_db", "ber", "pass"):
            if k not in v:
                errors.append(f"{path}.verdict: missing key '{k}'")


def _validate_sb1(block: dict[str, Any], errors: list[str]) -> None:  # noqa: PLR0912
    path = "sb1"
    for k in (
        "code",
        "decoder",
        "frame_error_definition",
        "eb_n0_grid_db",
        "waterfall",
        "verdict",
    ):
        if k not in block:
            errors.append(f"{path}: missing key '{k}'")
    if "eb_n0_grid_db" in block:
        grid = block["eb_n0_grid_db"]
        if list(grid) != list(BCH_GRID):
            errors.append(
                f"{path}.eb_n0_grid_db: does not match pinned BCH grid ({list(BCH_GRID)})"
            )
    if "decoder" in block:
        d = block["decoder"]
        for k in ("name", "class", "algorithm"):
            if k not in d:
                errors.append(f"{path}.decoder: missing key '{k}'")
        if "class" in d and d["class"] not in {"hard_ML", "soft_ML", "BDD", "other"}:
            errors.append(
                f"{path}.decoder.class: '{d['class']}' is not in {{hard_ML, soft_ML, BDD, other}}"
            )
    if "waterfall" in block:
        for i, row in enumerate(block["waterfall"]):
            _validate_waterfall_row(row, "sb1", errors, f"{path}.waterfall[{i}]")
    if block.get("verdict"):
        v = block["verdict"]
        if v.get("criterion") != EXPECTED_SB1_VERDICT_CRITERION:
            errors.append(
                f"{path}.verdict.criterion: expected "
                f"'{EXPECTED_SB1_VERDICT_CRITERION}', got '{v.get('criterion')}'"
            )
        for k in ("at_eb_n0_db", "fer", "pass"):
            if k not in v:
                errors.append(f"{path}.verdict: missing key '{k}'")


def validate_card(card: dict[str, Any]) -> list[str]:  # noqa: PLR0912
    """Returns a list of validation errors. Empty list means the card is valid."""
    errors: list[str] = []

    # Required top-level fields.
    for k in (
        "schema_version",
        "tier",
        "produced_by",
        "produced_at",
        "reference_anchor",
        "operating_point",
        "channel",
        "methodology",
    ):
        if k not in card:
            errors.append(f"missing top-level key '{k}'")

    if "tier" in card and card["tier"] not in ("core", "extended", "full"):
        errors.append(f"tier: '{card['tier']}' not in {{core, extended, full}}")

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
            errors.append(f"channel.model: expected 'BPSK-AWGN', got '{ch.get('model')}'")
        for k in ("sigma_formula", "llr_formula"):
            if k not in ch:
                errors.append(f"channel: missing key '{k}'")

    if "methodology" in card:
        m = card["methodology"]
        for k, expected in EXPECTED_METHODOLOGY.items():
            if m.get(k) != expected:
                errors.append(f"methodology.{k}: expected {expected!r}, got {m.get(k)!r}")

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
                errors.append("ldpc.subframes: at least one of SF2 or SF3_SF4 expected")

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
        rows.append(
            {
                "eb_n0_db": db,
                "a_fer": ap["fer"],
                "a_ci": ap["ci_fer"],
                "b_fer": bp["fer"],
                "b_ci": bp["ci_fer"],
                "verdict": verdict,
            }
        )

    if verbose:
        print(
            f"\n{label} waterfall (shared {len(shared)} of "
            f"{len(a_by_db)}/{len(b_by_db)} grid points):"
        )
        print(f"  {'Eb/N0':>6}  {'A FER':>10}  {'± CI':>10}  {'B FER':>10}  {'± CI':>10}  verdict")
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
        "summary": (f"A better at {a_better} points, B better at {b_better}, tied at {tied}"),
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


def compare_cards(a: dict[str, Any], b: dict[str, Any], verbose: bool = False) -> dict[str, Any]:
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
        "anchor_match": (a.get("reference_anchor") == b.get("reference_anchor")),
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
            result["verdicts"].append(
                _compare_verdict(
                    a_ldpc[sf].get("verdict"),
                    b_ldpc[sf].get("verdict"),
                    f"ldpc.{sf}",
                )
            )

    # SB1 comparison.
    if "sb1" in a and "sb1" in b:
        result["waterfalls"].append(
            _compare_waterfall(
                a["sb1"].get("waterfall", []),
                b["sb1"].get("waterfall", []),
                "sb1",
                verbose,
            )
        )
        result["verdicts"].append(
            _compare_verdict(
                a["sb1"].get("verdict"),
                b["sb1"].get("verdict"),
                "sb1",
            )
        )

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
    print(
        f"   sb1 decoder:  {result['a_identity']['sb1_decoder']} "
        f"({result['a_identity']['sb1_class']})"
    )
    print()
    print(f"B: {args.card_b}")
    print(f"   produced_by:  {result['b_identity']['produced_by']}")
    print(f"   ldpc decoder: {result['b_identity']['ldpc_decoder']}")
    print(
        f"   sb1 decoder:  {result['b_identity']['sb1_decoder']} "
        f"({result['b_identity']['sb1_class']})"
    )
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


# ─── `self-test` subcommand ──────────────────────────────────────────────
#
# Runs the user's adapter at a small frame count and CI-overlap-compares
# against the shipped reference card. Used as a regression test for the
# harness mechanics (protocol, channel, statistical aggregation) and as
# a smoke test for anyone setting up the harness for the first time.
#
# Layout assumption: the shipped reference card lives next to
# perf_card.py. The adapter is supplied by the user (--decoder) — no
# vendor-specific adapter is bundled.

SHIPPED_REFERENCE_BASENAME = "perf_card_reference_card.json"
SELF_TEST_FRAMES_PER_SEED = 50


def cmd_self_test(args: argparse.Namespace) -> int:
    here = Path(__file__).resolve().parent
    reference = here / SHIPPED_REFERENCE_BASENAME

    if not reference.exists():
        print(f"shipped reference card not found: {reference}", file=sys.stderr)
        print(
            "(Maintainer task: generate by running the reference adapter "
            "at production frame counts and writing the result to "
            "perf_card_reference_card.json — see README.)",
            file=sys.stderr,
        )
        return 2

    ref_card = json.loads(reference.read_text(encoding="utf-8"))
    reference_anchor = ref_card.get("reference_anchor", {})

    decoder_cmd = shlex.split(args.decoder)
    print(f"[self-test] reference: {reference}", file=sys.stderr)
    print(
        f"[self-test] running adapter {decoder_cmd} at "
        f"frames_per_seed={args.frames_per_seed} "
        f"(reference card frames_per_seed may differ — comparison is CI-overlap)…",
        file=sys.stderr,
    )

    code_ids = [CODE_BY_NAME[c.strip()] for c in args.codes.split(",")]
    fresh = run_harness(
        decoder_cmd=decoder_cmd,
        code_ids=code_ids,
        frames_per_seed=args.frames_per_seed,
        max_iters=DEFAULT_MAX_ITERS,
        reference_anchor=reference_anchor,
    )

    # Validate the freshly-produced card first.
    errors = validate_card(fresh)
    if errors:
        print(
            f"[self-test] FRESH CARD INVALID — {len(errors)} schema error(s):",
            file=sys.stderr,
        )
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # Compare fresh vs shipped reference.
    result = compare_cards(fresh, ref_card, verbose=args.verbose)

    drift_points = sum(w.get("a_better", 0) + w.get("b_better", 0) for w in result["waterfalls"])
    verdict_mismatch = any(
        v.get("a_pass") is not None and v.get("b_pass") is not None and v["a_pass"] != v["b_pass"]
        for v in result["verdicts"]
    )

    print()
    print("=== self-test result ===")
    print("Verdicts:")
    for v in result["verdicts"]:
        print(f"  {v['label']:>15}: {v['summary']}")
    print("Waterfall (per-point CI-overlap):")
    for w in result["waterfalls"]:
        print(f"  {w['label']:>15}: {w['summary']}")

    if drift_points == 0 and not verdict_mismatch:
        print()
        print("PASS — fresh card statistically equivalent to shipped reference.")
        return 0
    print()
    print(
        f"FAIL — {drift_points} grid point(s) outside CI overlap"
        + (" + verdict mismatch" if verdict_mismatch else "")
        + ". The fresh card and shipped reference disagree beyond statistical noise."
    )
    print(
        "(If this is expected — e.g., lunalink decoder was deliberately changed — "
        "regenerate the reference card.)"
    )
    return 1


# ─── `leaderboard` subcommand ────────────────────────────────────────────
#
# N-way ranking across multiple algo cards. Each code (SB1, SF2, SF3) is
# ranked independently. Cards that pass the verdict are ranked above
# cards that fail; within passes, lower achievement Eb/N0 (LDPC) or
# lower FER (SB1) is better. Statistical ties are grouped via CI overlap.


def _card_label(path: Path, card: dict[str, Any]) -> str:
    """Display label: filename stem + decoder identity hint."""
    return path.stem


def _load_card(p: str) -> tuple[Path, dict[str, Any]]:
    path = Path(p)
    return path, json.loads(path.read_text(encoding="utf-8"))


def _ldpc_subframe_entry(
    label: str,
    path: Path,
    card: dict[str, Any],
    sf_key: str,
) -> dict[str, Any] | None:
    """Pull SF2 or SF3_SF4 ranking info out of one card."""
    sub = card.get("ldpc", {}).get("subframes", {}).get(sf_key)
    if not sub:
        return None
    v = sub.get("verdict") or {}
    if "pass" not in v:
        return None
    # Find the waterfall row matching the verdict point (for CI lookup).
    wf = sub.get("waterfall", [])
    op_row = next(
        (r for r in wf if abs(r["eb_n0_db"] - v.get("at_eb_n0_db", -1)) < 1e-6),
        None,
    )
    # Ranking metric: lowest Eb/N0 where bar is met (anywhere on the
    # waterfall). Falls back to verdict.at_eb_n0_db for older cards that
    # don't carry the comparison field.
    first_eb_n0 = v.get("first_bar_crossing_eb_n0_db", v["at_eb_n0_db"])
    margin = v.get("margin_below_spec_db")
    return {
        "label": label,
        "path": str(path),
        "decoder": card.get("ldpc", {}).get("decoder", "?"),
        "pass": bool(v["pass"]),
        "at_eb_n0_db": v["at_eb_n0_db"],
        "ber": v.get("ber", 0.0),
        "fer": op_row["fer"] if op_row else 0.0,
        "ci_fer": op_row["ci_fer"] if op_row else 0.0,
        "frames": op_row["frames"] if op_row else 0,
        "first_bar_crossing_eb_n0_db": first_eb_n0,
        "margin_below_spec_db": margin,
    }


def _sb1_entry(label: str, path: Path, card: dict[str, Any]) -> dict[str, Any] | None:
    sb1 = card.get("sb1")
    if not sb1:
        return None
    v = sb1.get("verdict") or {}
    if "pass" not in v:
        return None
    wf = sb1.get("waterfall", [])
    op_row = next(
        (r for r in wf if abs(r["eb_n0_db"] - v.get("at_eb_n0_db", -1)) < 1e-6),
        None,
    )
    first_eb_n0 = v.get("first_bar_crossing_eb_n0_db", v["at_eb_n0_db"])
    margin = v.get("margin_below_spec_db")
    return {
        "label": label,
        "path": str(path),
        "decoder": sb1.get("decoder", {}).get("name", "?"),
        "decoder_class": sb1.get("decoder", {}).get("class", "?"),
        "pass": bool(v["pass"]),
        "at_eb_n0_db": v["at_eb_n0_db"],
        "fer": v.get("fer", op_row["fer"] if op_row else 0.0),
        "ci_fer": v.get("ci_fer", op_row["ci_fer"] if op_row else 0.0),
        "frames": op_row["frames"] if op_row else 0,
        "first_bar_crossing_eb_n0_db": first_eb_n0,
        "margin_below_spec_db": margin,
    }


def _rank_with_ties(
    entries: list[dict[str, Any]],
    *,
    sort_key: str,
    secondary_key: str,
) -> list[dict[str, Any]]:
    """Sort by sort_key ascending, then group adjacent entries whose CIs
    overlap at the comparison point. Returns entries annotated with `rank`
    (1-indexed) and `tied_with_prev` (bool).

    Sort_key is the primary metric (e.g., 'at_eb_n0_db' or 'fer').
    Secondary_key is the FER at the comparison point used for CI overlap.
    """
    if not entries:
        return entries
    # Passing entries first (by sort_key asc), then failing.
    passing = sorted(
        [e for e in entries if e["pass"]],
        key=lambda e: (e[sort_key], e[secondary_key]),
    )
    failing = sorted(
        [e for e in entries if not e["pass"]],
        key=lambda e: e.get("ber", e.get("fer", float("inf"))),
    )
    # Assign ranks within passing, group adjacent on CI overlap at
    # secondary_key.
    rank = 1
    for i, e in enumerate(passing):
        if i == 0:
            e["rank"] = rank
            e["tied_with_prev"] = False
            continue
        prev = passing[i - 1]
        # Tied iff same achievement bucket AND CI overlap at the
        # operating row.
        same_bucket = abs(e[sort_key] - prev[sort_key]) < 1e-9
        overlap = (
            _ci_overlap(
                e[secondary_key],
                e.get("ci_fer", 0.0),
                prev[secondary_key],
                prev.get("ci_fer", 0.0),
            )
            == "="
        )
        if same_bucket and overlap:
            e["rank"] = prev["rank"]
            e["tied_with_prev"] = True
        else:
            rank = i + 1
            e["rank"] = rank
            e["tied_with_prev"] = False
    for e in failing:
        e["rank"] = None  # FAIL — below all passing
        e["tied_with_prev"] = False
    return passing + failing


def _fmt_eb_n0(v: float | None) -> str:
    return f"{v:>4.1f} dB" if v is not None else "  — "


def _fmt_margin(v: float | None) -> str:
    return f"{v:>+4.1f} dB" if v is not None else "   — "


def _render_ldpc_table(title: str, entries: list[dict[str, Any]]) -> None:
    print(title)
    print("─" * len(title))
    print(f"  {'RANK':<6}{'CARD':<32}{'DECODER':<38}{'bar @':>7}  {'spec @':>7}  {'margin':>8}")
    print("  " + "─" * 108)
    for e in entries:
        if e["pass"]:
            rank_str = f"{e['rank']:>3}" + ("=" if e["tied_with_prev"] else " ")
            print(
                f"  {rank_str:<6}"
                f"{e['label'][:30]:<32}{e['decoder'][:36]:<38}"
                f"{_fmt_eb_n0(e.get('first_bar_crossing_eb_n0_db')):>9}  "
                f"{_fmt_eb_n0(e['at_eb_n0_db']):>9}  "
                f"{_fmt_margin(e.get('margin_below_spec_db')):>9}"
            )
        else:
            print(
                f"  {'FAIL':<6}"
                f"{e['label'][:30]:<32}{e['decoder'][:36]:<38}"
                f"   best BER {e.get('ber', 0.0):.2e}"
            )
    print()


def _render_sb1_table(entries: list[dict[str, Any]]) -> None:
    title = "SB1 — verdict: FER < 0.01 at Es/N0 >= 0 dB"
    print(title)
    print("─" * len(title))
    print(f"  {'RANK':<6}{'CARD':<32}{'DECODER':<38}{'bar @':>7}  {'spec @':>7}  {'margin':>8}")
    print("  " + "─" * 108)
    for e in entries:
        if e["pass"]:
            rank_str = f"{e['rank']:>3}" + ("=" if e["tied_with_prev"] else " ")
            decoder_with_class = f"{e['decoder'][:28]} ({e['decoder_class']})"
            print(
                f"  {rank_str:<6}"
                f"{e['label'][:30]:<32}{decoder_with_class[:36]:<38}"
                f"{_fmt_eb_n0(e.get('first_bar_crossing_eb_n0_db')):>9}  "
                f"{_fmt_eb_n0(e['at_eb_n0_db']):>9}  "
                f"{_fmt_margin(e.get('margin_below_spec_db')):>9}"
            )
        else:
            print(f"  {'FAIL':<6}{e['label'][:30]:<32}{e['decoder'][:36]:<38}{e['fer']:>10.6f}")
    print()


def cmd_leaderboard(args: argparse.Namespace) -> int:
    cards: list[tuple[Path, dict[str, Any]]] = []
    for p in args.cards:
        try:
            cards.append(_load_card(p))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot load {p}: {exc}", file=sys.stderr)
            return 2

    # Anchor consistency check.
    anchors = {json.dumps(c.get("reference_anchor", {}), sort_keys=True) for _, c in cards}
    if len(anchors) > 1:
        print(
            "WARNING: cards reference different anchors — ranking may not be fair.",
            file=sys.stderr,
        )
        for path, c in cards:
            print(
                f"  {path.name}: {c.get('reference_anchor', {})}",
                file=sys.stderr,
            )

    # Build per-code entries.
    sf2: list[dict[str, Any]] = []
    sf3: list[dict[str, Any]] = []
    sb1: list[dict[str, Any]] = []
    for path, card in cards:
        label = _card_label(path, card)
        if e := _ldpc_subframe_entry(label, path, card, "SF2"):
            sf2.append(e)
        if e := _ldpc_subframe_entry(label, path, card, "SF3_SF4"):
            sf3.append(e)
        if e := _sb1_entry(label, path, card):
            sb1.append(e)

    # Rank (sort by achievement Eb/N0 for LDPC, by op-point FER for SB1).
    # Rank by `first_bar_crossing_eb_n0_db` — the cliff position. Lower
    # = better implementation. `at_eb_n0_db` (the spec-compliance point)
    # is constant for all PASSing decoders so doesn't discriminate.
    sf2 = _rank_with_ties(sf2, sort_key="first_bar_crossing_eb_n0_db", secondary_key="fer")
    sf3 = _rank_with_ties(sf3, sort_key="first_bar_crossing_eb_n0_db", secondary_key="fer")
    # SB1 has so much margin (FER=0 at any reasonable Eb/N0 ≥ 5 dB) that
    # ranking by the cliff position is more informative than ranking by
    # the operating-point FER (which is ~0 for every passing decoder).
    sb1 = _rank_with_ties(sb1, sort_key="first_bar_crossing_eb_n0_db", secondary_key="fer")

    if args.json:
        sys.stdout.write(
            json.dumps(
                {
                    "n_cards": len(cards),
                    "anchor_consistent": (len(anchors) == 1),
                    "ldpc_SF2": sf2,
                    "ldpc_SF3_SF4": sf3,
                    "sb1": sb1,
                },
                indent=2,
            )
            + "\n"
        )
        return 0

    # Human-readable.
    print(f"\nLeaderboard — {len(cards)} card(s)\n")
    if sf2:
        _render_ldpc_table("LDPC SF2 — verdict: BER < 1e-05 at Es/N0 >= 0 dB", sf2)
    if sf3:
        _render_ldpc_table("LDPC SF3/SF4 — verdict: BER < 1e-05 at Es/N0 >= 0 dB", sf3)
    if sb1:
        _render_sb1_table(sb1)
    return 0


# ─── `render` subcommand ─────────────────────────────────────────────────
#
# Pretty rendering of an algo card to PNG / PDF / SVG — for showing
# numbers to humans (judges, slide decks, README badges). Optional
# dependency on matplotlib (pip install 'lsis-afs-test-vectors[render]').

# Visual style — print-friendly, high-contrast, single-color-blind-safe.
RENDER_PALETTE = {
    "fer": "#2563eb",  # blue
    "ber": "#ea580c",  # orange
    "ci_band": "#93c5fd",  # light blue
    "spec_bar": "#dc2626",  # red
    "verdict_pass": "#16a34a",  # green
    "verdict_fail": "#dc2626",  # red
    "grid": "#e5e7eb",  # light gray
    "text_dim": "#6b7280",
}


def _floor_for_log(x: float) -> float:
    """Clip a value above zero so it plots on a log axis."""
    return max(x, 1e-7)


def _wilson_bounds(p: float, ci: float, frames: int) -> tuple[float, float]:
    """Return CI lower / upper bounds suitable for log-axis plotting.

    Wilson's half-width `ci` is centred on the empirical proportion `p`,
    which goes to zero when no errors are observed. Naive `p - ci` is
    then negative and gets clipped to the log floor, producing a
    misleading "cliff" in the CI band. Instead we report the Wilson-style
    upper bound at p=0 (1 − (alpha/2)^(1/n) ≈ z²/(n+z²)) for the floor,
    and the usual p+ci on the high side.
    """
    if frames <= 0:
        return 1e-7, 1.5
    if p == 0.0:
        # Tight one-sided upper bound when zero errors observed.
        upper = ci  # Wilson half-width at p=0 IS the upper bound
        return 1e-7, max(upper, 1e-7)
    return max(p - ci, 1e-7), min(p + ci, 1.5)


def _ci_band_arrays(wf: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    """Vector form of _wilson_bounds across a waterfall."""
    los: list[float] = []
    his: list[float] = []
    for p in wf:
        lo, hi = _wilson_bounds(p["fer"], p["ci_fer"], p.get("frames", 0))
        los.append(lo)
        his.append(hi)
    return los, his


def _plot_ldpc_waterfall(ax, sub: dict[str, Any], title: str) -> None:
    """Plot one LDPC subframe's FER + BER waterfall."""
    wf = sub["waterfall"]
    ebs = [p["eb_n0_db"] for p in wf]
    fers = [_floor_for_log(p["fer"]) for p in wf]
    bers = [_floor_for_log(p.get("ber", 0.0)) for p in wf]
    fers_lo, fers_hi = _ci_band_arrays(wf)

    ax.fill_between(
        ebs,
        fers_lo,
        fers_hi,
        color=RENDER_PALETTE["ci_band"],
        alpha=0.35,
        label="FER 95% CI",
    )
    ax.semilogy(
        ebs, fers, "o-", color=RENDER_PALETTE["fer"], linewidth=2.5, markersize=7, label="FER"
    )
    ax.semilogy(
        ebs, bers, "s--", color=RENDER_PALETTE["ber"], linewidth=1.5, markersize=5, label="BER"
    )

    ax.axhline(
        LDPC_VERDICT_BAR_BER,
        color=RENDER_PALETTE["spec_bar"],
        linestyle=":",
        linewidth=1.5,
        alpha=0.7,
    )
    ax.text(
        ebs[0],
        LDPC_VERDICT_BAR_BER * 1.6,
        "spec: BER < 1e-5",
        color=RENDER_PALETTE["spec_bar"],
        fontsize=8,
        va="bottom",
        ha="left",
    )

    # Two distinct verticals: the cliff (where the decoder first meets the
    # bar) and the spec point (where the spec evaluates). The gap between
    # them is the decoder's headroom — load-bearing for cross-team
    # comparison. Cliff gets the prominent green styling; spec is subtle.
    v = sub.get("verdict") or {}
    cliff = v.get("first_bar_crossing_eb_n0_db")
    spec = v.get("at_eb_n0_db")
    if v.get("pass") and cliff is not None and spec is not None:
        if spec > cliff:
            ax.axvspan(
                cliff,
                spec,
                color=RENDER_PALETTE["verdict_pass"],
                alpha=0.10,
            )
        # Cliff: prominent — the implementation's actual achievement.
        ax.axvline(
            cliff,
            color=RENDER_PALETTE["verdict_pass"],
            linewidth=2.0,
            alpha=0.75,
        )
        ax.annotate(
            f"cliff\n{cliff:.1f} dB",
            xy=(cliff, 1.0),
            xytext=(cliff - 0.05, 0.6),
            fontsize=9,
            fontweight="bold",
            color=RENDER_PALETTE["verdict_pass"],
            ha="right",
            va="top",
        )
        # Spec: subtle dashed — the bar's evaluation point. Annotation
        # placed in the MIDDLE of the panel (below the cliff annotation
        # which sits at the top) so they don't horizontally crowd.
        ax.axvline(
            spec,
            color=RENDER_PALETTE["text_dim"],
            linewidth=1.2,
            alpha=0.65,
            linestyle="--",
        )
        ax.annotate(
            f"spec {spec:.1f} dB",
            xy=(spec, 1e-2),
            xytext=(spec + 0.05, 1e-3),
            fontsize=9,
            fontweight="bold",
            color=RENDER_PALETTE["text_dim"],
            ha="left",
            va="center",
        )

    code = sub.get("code", {})
    ax.set_title(
        f"LDPC {title}  (k={code.get('k', '?')}, n={code.get('n', '?')}, "
        f"R={code.get('rate', '?')})",
        fontsize=12,
        fontweight="bold",
    )
    ax.set_xlabel("Eb/N0 (dB)")
    ax.set_ylabel("error rate (log)")
    ax.grid(True, which="both", color=RENDER_PALETTE["grid"], linewidth=0.5)
    ax.set_ylim(1e-6, 1.5)
    ax.legend(loc="lower left", fontsize=9, framealpha=0.95)


def _plot_sb1_waterfall(ax, sb1: dict[str, Any]) -> None:
    """Plot SB1/BCH FER waterfall."""
    wf = sb1["waterfall"]
    ebs = [p["eb_n0_db"] for p in wf]
    fers = [_floor_for_log(p["fer"]) for p in wf]
    fers_lo, fers_hi = _ci_band_arrays(wf)

    ax.fill_between(
        ebs, fers_lo, fers_hi, color=RENDER_PALETTE["ci_band"], alpha=0.35, label="FER 95% CI"
    )
    ax.semilogy(
        ebs, fers, "o-", color=RENDER_PALETTE["fer"], linewidth=2.5, markersize=7, label="FER"
    )

    ax.axhline(
        SB1_VERDICT_BAR_FER,
        color=RENDER_PALETTE["spec_bar"],
        linestyle=":",
        linewidth=1.5,
        alpha=0.7,
    )
    ax.text(
        ebs[0],
        SB1_VERDICT_BAR_FER * 1.6,
        "spec: FER < 0.01 (Frame-Detection > 99%)",
        color=RENDER_PALETTE["spec_bar"],
        fontsize=8,
        va="bottom",
        ha="left",
    )

    # Cliff + spec verticals — same pattern as LDPC. For BCH soft-ML
    # the gap is dramatic (3.0 dB → 7.6 dB), so the margin region
    # paints a clear picture of headroom.
    v = sb1.get("verdict") or {}
    cliff = v.get("first_bar_crossing_eb_n0_db")
    spec = v.get("at_eb_n0_db")
    if v.get("pass") and cliff is not None and spec is not None:
        if spec > cliff:
            ax.axvspan(
                cliff,
                spec,
                color=RENDER_PALETTE["verdict_pass"],
                alpha=0.10,
            )
        ax.axvline(
            cliff,
            color=RENDER_PALETTE["verdict_pass"],
            linewidth=2.0,
            alpha=0.75,
        )
        ax.annotate(
            f"cliff\n{cliff:.1f} dB",
            xy=(cliff, 1.0),
            xytext=(cliff - 0.1, 0.6),
            fontsize=9,
            fontweight="bold",
            color=RENDER_PALETTE["verdict_pass"],
            ha="right",
            va="top",
        )
        ax.axvline(
            spec,
            color=RENDER_PALETTE["text_dim"],
            linewidth=1.0,
            alpha=0.55,
            linestyle="--",
        )
        ax.annotate(
            f"spec\n{spec:.1f} dB",
            xy=(spec, 1.0),
            xytext=(spec + 0.1, 0.6),
            fontsize=8,
            color=RENDER_PALETTE["text_dim"],
            ha="left",
            va="top",
        )

    decoder = sb1.get("decoder", {})
    code = sb1.get("code", {})
    ax.set_title(
        f"SB1 / BCH  (k={code.get('k', '?')}, n={code.get('n', '?')}, "
        f"R={code.get('rate', '?')}, {decoder.get('class', '?')})",
        fontsize=12,
        fontweight="bold",
    )
    ax.set_xlabel("Eb/N0 (dB)")
    ax.set_ylabel("FER (log)")
    ax.grid(True, which="both", color=RENDER_PALETTE["grid"], linewidth=0.5)
    ax.set_ylim(1e-5, 1.5)
    ax.legend(loc="lower left", fontsize=9, framealpha=0.95)


def _draw_verdict_panel(ax, card: dict[str, Any]) -> None:
    """Verdict summary panel.

    Each row carries TWO numbers:
      - 'spec @ X dB' — verdict point (Es/N0=0 boundary in per-code Eb/N0)
      - 'bar @ Y dB' — comparison metric: first Eb/N0 where bar is met
                       anywhere on the waterfall (the cliff position)
    The margin (X − Y) is what distinguishes implementations that all PASS.
    """
    ax.axis("off")
    rows = []
    if "ldpc" in card:
        for sf_name, sub in card["ldpc"].get("subframes", {}).items():
            v = sub.get("verdict") or {}
            label = "LDPC " + ("SF3/SF4" if sf_name == "SF3_SF4" else sf_name)
            rows.append(
                (
                    label,
                    v.get("criterion", "?"),
                    v.get("at_eb_n0_db"),
                    v.get("first_bar_crossing_eb_n0_db"),
                    v.get("margin_below_spec_db"),
                    bool(v.get("pass")),
                )
            )
    if "sb1" in card:
        v = card["sb1"].get("verdict") or {}
        rows.append(
            (
                "SB1 (BCH)",
                v.get("criterion", "?"),
                v.get("at_eb_n0_db"),
                v.get("first_bar_crossing_eb_n0_db"),
                v.get("margin_below_spec_db"),
                bool(v.get("pass")),
            )
        )

    ax.text(
        0.02, 0.95, "Verdicts", fontsize=14, fontweight="bold", transform=ax.transAxes, va="top"
    )
    ax.text(
        0.02,
        0.88,
        "spec @ = boundary point   ·   bar @ = cliff position   ·   "
        "margin = spec − cliff (more is better)",
        fontsize=7,
        color=RENDER_PALETTE["text_dim"],
        transform=ax.transAxes,
        va="top",
    )

    if not rows:
        return
    row_h = 0.72 / len(rows)
    for i, (label, criterion, spec_db, cliff_db, margin_db, passed) in enumerate(rows):
        y = 0.78 - (i + 0.5) * row_h
        color = RENDER_PALETTE["verdict_pass"] if passed else RENDER_PALETTE["verdict_fail"]
        status = "PASS" if passed else "FAIL"

        ax.text(
            0.02,
            y,
            "  " + status + "  ",
            fontsize=11,
            fontweight="bold",
            color="white",
            bbox={"facecolor": color, "edgecolor": "none", "boxstyle": "round,pad=0.4"},
            transform=ax.transAxes,
            va="center",
        )
        ax.text(
            0.20,
            y + row_h * 0.18,
            label,
            fontsize=11,
            fontweight="bold",
            transform=ax.transAxes,
            va="center",
        )
        ax.text(
            0.20,
            y - row_h * 0.20,
            criterion,
            fontsize=8,
            color=RENDER_PALETTE["text_dim"],
            transform=ax.transAxes,
            va="center",
        )
        spec_str = f"{spec_db:.1f} dB" if spec_db is not None else "—"
        cliff_str = f"{cliff_db:.1f} dB" if cliff_db is not None else "—"
        margin_str = f"+{margin_db:.1f} dB" if margin_db is not None else "—"
        ax.text(
            0.98,
            y + row_h * 0.18,
            f"spec @ {spec_str}   ·   bar @ {cliff_str}",
            fontsize=10,
            color=color,
            fontweight="bold",
            transform=ax.transAxes,
            va="center",
            ha="right",
        )
        ax.text(
            0.98,
            y - row_h * 0.20,
            f"margin: {margin_str}",
            fontsize=9,
            color=RENDER_PALETTE["text_dim"],
            transform=ax.transAxes,
            va="center",
            ha="right",
        )


def _draw_header_footer(fig, card: dict[str, Any]) -> None:
    ldpc = card.get("ldpc", {})
    sb1_dec = card.get("sb1", {}).get("decoder", {})
    anchor = card.get("reference_anchor", {})
    methodology = card.get("methodology", {})
    channel = card.get("channel", {})

    fig.suptitle("LSIS-AFS — Decoder Performance Card", fontsize=18, fontweight="bold", y=0.97)
    subtitle = (
        f"LDPC: {ldpc.get('decoder', '?')}   ·   "
        f"SB1: {sb1_dec.get('name', '?')} ({sb1_dec.get('class', '?')})"
    )
    fig.text(0.5, 0.935, subtitle, ha="center", fontsize=11, color=RENDER_PALETTE["text_dim"])

    anchor_text = (
        f"reference: {anchor.get('repo', '?')}@{anchor.get('tag', anchor.get('commit', '?'))}"
    )
    fig.text(0.99, 0.97, anchor_text, ha="right", fontsize=8, color=RENDER_PALETTE["text_dim"])

    footer = (
        f"{channel.get('model', '?')}   ·   "
        f"σ = {channel.get('sigma_formula', '?')}   ·   "
        f"seeds: {methodology.get('seeds', '?')}   ·   "
        f"messages: {methodology.get('message_ensemble', '?')}   ·   "
        f"CI: {methodology.get('ci_method', '?')}   ·   "
        f"tier: {card.get('tier', '?')}   ·   "
        f"{card.get('produced_at', '?')}"
    )
    fig.text(0.5, 0.012, footer, ha="center", fontsize=7, color=RENDER_PALETTE["text_dim"])


def render_card(card: dict[str, Any], out_path: Path) -> None:
    """Render an algo card to PNG / PDF / SVG (matplotlib infers from suffix)."""
    import matplotlib  # type: ignore[import-not-found]  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # type: ignore[import-not-found]  # noqa: PLC0415

    fig = plt.figure(figsize=(15, 10), dpi=120)
    gs = fig.add_gridspec(
        2,
        2,
        height_ratios=[3, 3],
        hspace=0.40,
        wspace=0.22,
        left=0.06,
        right=0.97,
        top=0.88,
        bottom=0.05,
    )

    _draw_header_footer(fig, card)

    sf2 = card.get("ldpc", {}).get("subframes", {}).get("SF2")
    sf3 = card.get("ldpc", {}).get("subframes", {}).get("SF3_SF4")
    if sf2:
        _plot_ldpc_waterfall(fig.add_subplot(gs[0, 0]), sf2, "SF2")
    if sf3:
        _plot_ldpc_waterfall(fig.add_subplot(gs[0, 1]), sf3, "SF3/SF4")
    if "sb1" in card:
        _plot_sb1_waterfall(fig.add_subplot(gs[1, 0]), card["sb1"])
    _draw_verdict_panel(fig.add_subplot(gs[1, 1]), card)

    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


def cmd_render(args: argparse.Namespace) -> int:
    try:
        import matplotlib  # noqa: F401, PLC0415 — probe optional dep
    except ImportError:
        print(
            "render requires matplotlib. Install with: pip install 'lsis-afs-test-vectors[render]'",
            file=sys.stderr,
        )
        return 2
    try:
        card = json.loads(Path(args.card).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot load {args.card}: {exc}", file=sys.stderr)
        return 2
    out_path = Path(args.out)
    render_card(card, out_path)
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="perf_card",
        description=("LSIS-AFS Decoder Performance Card harness — run / validate / compare."),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser(
        "run",
        help=("Sweep an adapter over the full pinned Eb/N0 grid per code and emit an algo card."),
    )
    rp.add_argument("--decoder", required=True, help="Adapter executable.")
    rp.add_argument(
        "--codes",
        default="SB1,SF2,SF3",
        help="Comma-separated list of codes to characterise (default: all).",
    )
    rp.add_argument(
        "--frames-per-seed",
        type=int,
        default=DEFAULT_FRAMES_PER_SEED,
        help="Frames per seed per grid point (default: 5000).",
    )
    rp.add_argument(
        "--max-iters",
        type=int,
        default=DEFAULT_MAX_ITERS,
        help="max_iters passed to the adapter (default: 50).",
    )
    rp.add_argument(
        "--tier",
        default="core",
        choices=["core", "extended", "full"],
        help=(
            "Tier of probes to run (default: core). 'extended' adds an "
            "LDPC convergence-CDF probe (max_iters sweep) on SF2 at 1.5 dB."
        ),
    )
    _default_workers = max(1, (os.cpu_count() or 1) - 1)
    rp.add_argument(
        "--workers",
        type=int,
        default=_default_workers,
        help=(
            f"Number of parallel adapter subprocesses to run the grid "
            f"sweep across (default: {_default_workers} = cpu_count - 1). "
            f"Pass 1 for the serial path."
        ),
    )
    rp.add_argument("--out", help="Path to write algo card JSON (default: stdout).")

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
        "--verbose",
        action="store_true",
        help="Print full per-grid-point waterfall table.",
    )
    cp.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human summary.",
    )
    cp.set_defaults(func=cmd_compare)

    # ── self-test ───────────────────────────────────────────────────────
    stp = sub.add_parser(
        "self-test",
        help=(
            "Run an adapter against the shipped reference card; PASS iff "
            "fresh and reference are CI-overlap-tied at every grid point "
            "and verdicts agree. Useful as a smoke-test for a new adapter "
            "or as a regression check that the harness still talks to the "
            "reference implementation."
        ),
    )
    stp.add_argument(
        "--decoder",
        required=True,
        help="Adapter executable (shell-quoted; can include args).",
    )
    stp.add_argument(
        "--codes",
        default="SB1,SF2,SF3",
        help="Codes to run during self-test (default: all).",
    )
    stp.add_argument(
        "--frames-per-seed",
        type=int,
        default=SELF_TEST_FRAMES_PER_SEED,
        help=(
            f"Frames per seed (default: {SELF_TEST_FRAMES_PER_SEED} — faster than `run`'s default)."
        ),
    )
    stp.add_argument(
        "--verbose",
        action="store_true",
        help="Print the per-grid-point CI-overlap table.",
    )
    stp.set_defaults(func=cmd_self_test)

    # ── leaderboard ─────────────────────────────────────────────────────
    lbp = sub.add_parser(
        "leaderboard",
        help=(
            "N-way ranking across multiple algo cards. Per code: cards "
            "that pass verdict are ranked ahead of failures; within "
            "passes, lower achievement Eb/N0 (LDPC) or lower FER (SB1) "
            "is better; statistical ties grouped via CI overlap."
        ),
    )
    lbp.add_argument(
        "cards",
        nargs="+",
        help="Two or more algo card JSON files to rank.",
    )
    lbp.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable table.",
    )
    lbp.set_defaults(func=cmd_leaderboard)

    # ── render ──────────────────────────────────────────────────────────
    rdp = sub.add_parser(
        "render",
        help=(
            "Render an algo card to a figure file (PNG / PDF / SVG inferred "
            "from --out suffix). Single-page layout: LDPC waterfalls, SB1 "
            "FER, verdict pills. Pretty enough for a slide. "
            "Requires matplotlib (pip install 'lsis-afs-test-vectors[render]')."
        ),
    )
    rdp.add_argument("card", help="Algo card JSON to render.")
    rdp.add_argument(
        "--out",
        required=True,
        help="Output path. Format inferred from extension: .png / .pdf / .svg.",
    )
    rdp.set_defaults(func=cmd_render)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
