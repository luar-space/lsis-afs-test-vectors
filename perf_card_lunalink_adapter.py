#!/usr/bin/env python3
"""Reference decoder adapter wrapping lunalink's Python bindings.

Implements the perf-card binary stdio protocol from
`shaping/decoder-performance-card.md`. Calls into lunalink's
`ldpc_decode` (sum-product) and `bch_decode_soft` (soft-ML inner-product
BCH) for SF2/SF3 and SB1 respectively.

This is the standard's reference exemplar adapter — the harness paired
with this adapter on the shipped reference vector set produces the
canonical lunalink algo card. Other teams implement their own adapter
in the same protocol and run the harness against it to produce their
own card; `perf-card compare` then ranks them.

Usage:
    python perf_card.py run \\
        --decoder "python perf_card_lunalink_adapter.py" \\
        --codes SB1,SF2,SF3 \\
        --frames-per-seed 5000 \\
        --decoder-name "lunalink ldpc_decode (sum-product, float64)" \\
        --decoder-algorithm "Layered Sum-Product BP (phi-transform)" \\
        --decoder-early-termination "syndrome check every iteration" \\
        --sb1-decoder-name "lunalink bch_decode_soft" \\
        --sb1-decoder-class "soft_ML" \\
        --sb1-decoder-algorithm "exhaustive ML over inner-product LLR" \\
        --out lunalink_algo_card.json
"""

from __future__ import annotations

import struct
import sys

import numpy as np

from lunalink.afs import (  # type: ignore[import-not-found]
    BchStatus,
    LdpcStatus,
    LdpcSubframe,
    bch_decode_soft,
    ldpc_decode,
)


# Wire protocol formats (matches perf_card.py).
REQUEST_HEADER_FMT  = "<BHfI"
REQUEST_HEADER_LEN  = 11
RESPONSE_HEADER_FMT = "<BHI"

# Code metadata keyed by wire code_id.
N_INFO = {0: 9, 1: 1200, 2: 870}
LDPC_TYPE = {1: LdpcSubframe.SF2, 2: LdpcSubframe.SF3}


def pack_sb1_info(fid_val: int, toi_val: int) -> np.ndarray:
    """Must match perf_card.py's pack_sb1_info exactly."""
    info = np.zeros(9, dtype=np.uint8)
    info[0] = (fid_val >> 1) & 1
    info[1] = fid_val & 1
    for i in range(7):
        info[2 + i] = (toi_val >> (6 - i)) & 1
    return info


def decode_sb1(llrs: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Returns (info_bits, status, iters_used). BCH is exhaustive ML — no iters."""
    res = bch_decode_soft(llrs.astype(np.float32))
    info = pack_sb1_info(int(res.fid), int(res.toi))
    status = 0 if res.status == BchStatus.OK else 2
    return info, status, 0


def decode_ldpc(
    code_id: int, llrs: np.ndarray, max_iters: int
) -> tuple[np.ndarray, int, int]:
    """Returns (info_bits, status, iters_used)."""
    subframe = LDPC_TYPE[code_id]
    decoded, status_enum = ldpc_decode(
        subframe, llrs.astype(np.float32), int(max_iters)
    )
    if status_enum == LdpcStatus.OK:
        status = 0
    elif status_enum == LdpcStatus.NOT_CONVERGED:
        status = 1
    else:
        status = 2
    return np.asarray(decoded, dtype=np.uint8), status, int(max_iters)


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        hdr = stdin.read(REQUEST_HEADER_LEN)
        if not hdr:
            return 0
        if len(hdr) < REQUEST_HEADER_LEN:
            print(f"[adapter] short header: {len(hdr)} bytes", file=sys.stderr)
            return 1

        code_id, max_iters, _sigma_sq, n_bits = struct.unpack(
            REQUEST_HEADER_FMT, hdr
        )
        llrs = np.frombuffer(stdin.read(4 * n_bits), dtype=np.float32)

        if code_id == 0:
            info, status, iters = decode_sb1(llrs)
        else:
            info, status, iters = decode_ldpc(code_id, llrs, max_iters)

        n_info = N_INFO[code_id]
        if len(info) != n_info:
            print(
                f"[adapter] wrong n_info: got {len(info)}, expected {n_info}",
                file=sys.stderr,
            )
            return 1

        stdout.write(struct.pack(RESPONSE_HEADER_FMT, status, iters, n_info))
        stdout.write(info.tobytes())
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
