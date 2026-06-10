#!/usr/bin/env python3
"""Maintainer tool — generates the shipped reference codeword pool.

NOT PART OF THE HARNESS RUNTIME. This script imports lunalink to use its
C++ encoder bindings and produces `perf_card_reference_codewords.npz`,
which the harness then loads at runtime. The harness itself does not
import lunalink.

Run once per spec/encoder change:

    python tools/generate_perf_card_reference_codewords.py

The codeword pool is bit-packed (np.packbits) and compressed; expected
file size ~700 KB for 1000 LDPC pairs/code + 100 BCH pairs.

Adopters of the standard NEVER need to run this — the .npz is shipped
in the repo. They get codewords by loading the shipped file.

Pool size rationale (1000 LDPC pairs, 100 BCH pairs):
  - LDPC tier-core sweep: 5000 frames/seed × 3 seeds × 12 grid pts × 2 codes
    = 360k frames against 1000 pairs → each codeword used ~360× across
    a full sweep. Plenty for FER averaging.
  - BCH: smaller (only 400 valid (FID, TOI) combos exist) so 100 random
    pairs is essentially full coverage of the codebook.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# Generator imports lunalink — NOT in runtime path.
from lunalink.afs import (  # type: ignore[import-not-found]
    LdpcSubframe,
    bch_encode,
    ldpc_encode,
)

# Pool sizes. Generation is fast (encoders are C++), so generous pools
# are cheap. Shipped file size is dominated by the packed-bit payload.
N_BCH_PAIRS = 100  # ~26% of the 400-entry valid codebook
N_LDPC_PAIRS = 1000

# Deterministic seed for generation — anyone regenerating the pool with
# this script gets byte-identical codewords (info bits) and codewords
# (encoded bits) as the shipped file.
GENERATION_SEED = 0xC0DE_BEEF


OUT_PATH = Path(__file__).resolve().parent.parent / "perf_card_reference_codewords.npz"


def generate_bch_pairs(rng: np.random.Generator, n: int):
    """Return (info, codeword) for N BCH pairs, sampled uniformly over
    the 400 valid (FID, TOI) combinations."""
    info_bits = np.zeros((n, 9), dtype=np.uint8)
    codewords = np.zeros((n, 52), dtype=np.uint8)
    for i in range(n):
        fid_val = int(rng.integers(0, 4))
        toi_val = int(rng.integers(0, 100))
        # Pack (FID, TOI) into 9 info bits per the standard's convention.
        info_bits[i, 0] = (fid_val >> 1) & 1
        info_bits[i, 1] = fid_val & 1
        for j in range(7):
            info_bits[i, 2 + j] = (toi_val >> (6 - j)) & 1
        codewords[i] = np.asarray(bch_encode(fid_val, toi_val), dtype=np.uint8)
    return info_bits, codewords


def generate_ldpc_pairs(
    rng: np.random.Generator, n: int, subframe: LdpcSubframe, k: int, n_bits: int
):
    info_bits = np.zeros((n, k), dtype=np.uint8)
    codewords = np.zeros((n, n_bits), dtype=np.uint8)
    for i in range(n):
        info = rng.integers(0, 2, k, dtype=np.uint8)
        info_bits[i] = info
        codewords[i] = np.asarray(ldpc_encode(subframe, info), dtype=np.uint8)
    return info_bits, codewords


def main() -> int:
    rng = np.random.default_rng(np.random.SeedSequence(GENERATION_SEED))

    print(f"Generating SB1 / BCH ({N_BCH_PAIRS} pairs)...", flush=True)
    sb1_info, sb1_cw = generate_bch_pairs(rng, N_BCH_PAIRS)

    print(f"Generating SF2 ({N_LDPC_PAIRS} pairs)...", flush=True)
    sf2_info, sf2_cw = generate_ldpc_pairs(rng, N_LDPC_PAIRS, LdpcSubframe.SF2, 1200, 2400)

    print(f"Generating SF3 ({N_LDPC_PAIRS} pairs)...", flush=True)
    sf3_info, sf3_cw = generate_ldpc_pairs(rng, N_LDPC_PAIRS, LdpcSubframe.SF3, 870, 1740)

    # Bit-pack for compact storage.
    print(f"Bit-packing and writing {OUT_PATH.name}...", flush=True)
    np.savez_compressed(
        OUT_PATH,
        # Metadata (so the harness can sanity-check shapes at load time).
        format_version=np.array([1], dtype=np.uint32),
        generation_seed=np.array([GENERATION_SEED], dtype=np.uint64),
        n_bch_pairs=np.array([N_BCH_PAIRS], dtype=np.uint32),
        n_ldpc_pairs=np.array([N_LDPC_PAIRS], dtype=np.uint32),
        # Pairs (bit-packed; harness unpacks with np.unpackbits).
        sb1_info_packed=np.packbits(sb1_info, axis=1),
        sb1_codeword_packed=np.packbits(sb1_cw, axis=1),
        sf2_info_packed=np.packbits(sf2_info, axis=1),
        sf2_codeword_packed=np.packbits(sf2_cw, axis=1),
        sf3_info_packed=np.packbits(sf3_info, axis=1),
        sf3_codeword_packed=np.packbits(sf3_cw, axis=1),
    )
    size = OUT_PATH.stat().st_size
    print(f"Wrote {OUT_PATH} ({size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
