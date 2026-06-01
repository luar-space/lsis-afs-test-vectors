#!/usr/bin/env python3
"""Lunalink decode adapter — MAINTAINER TOOL, NOT RUNTIME.

This file imports lunalink. It is used by the maintainer to regenerate
the shipped reference card (perf_card_reference_card.json). The harness
itself does NOT import lunalink; codewords come from the shipped
perf_card_reference_codewords.npz pool, and adapters are provided by
each team independently.

Long-term plan (V8-b): this adapter moves into the lunalink repository,
exposed as a console-script entry point (e.g., `lunalink-perf-card-adapter`).
After that move, test-vectors will carry no lunalink-dependent code.

Maintainer usage:

    python perf_card.py self-test --decoder "python tools/lunalink_adapter.py"

Or to regenerate the shipped reference card:

    python perf_card.py run \\
        --decoder "python tools/lunalink_adapter.py" \\
        --frames-per-seed 200 \\
        --reference-anchor-tag v0.6.0 \\
        --out perf_card_reference_card.json
"""

from __future__ import annotations

import json
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


# Wire protocol formats — must match perf_card.py.
REQUEST_HEADER_FMT  = "<BHfI"
REQUEST_HEADER_LEN  = 11
RESPONSE_HEADER_FMT = "<BHI"
PROTOCOL_VERSION    = "1.0"

N_INFO    = {0: 9, 1: 1200, 2: 870}
LDPC_TYPE = {1: LdpcSubframe.SF2, 2: LdpcSubframe.SF3}


def pack_sb1_info(fid_val: int, toi_val: int) -> np.ndarray:
    info = np.zeros(9, dtype=np.uint8)
    info[0] = (fid_val >> 1) & 1
    info[1] = fid_val & 1
    for i in range(7):
        info[2 + i] = (toi_val >> (6 - i)) & 1
    return info


def do_handshake(stdin, stdout) -> None:
    n = struct.unpack("<I", stdin.read(4))[0]
    _ = json.loads(stdin.read(n))
    resp = json.dumps({
        "type": "handshake_ack",
        "protocol_version": PROTOCOL_VERSION,
        "adapter": {
            "name": "lunalink ldpc_decode (sum-product, float64)",
            "version": "1.0.0",
            "supports_codes": ["SB1", "SF2", "SF3"],
            "ldpc": {
                "algorithm": "Layered Sum-Product BP (phi-transform)",
                "early_termination": "syndrome check every iteration",
            },
            "sb1": {
                "name": "lunalink bch_decode_soft",
                "decoder_class": "soft_ML",
                "algorithm": "exhaustive ML over inner-product LLR",
            },
        },
    }).encode("utf-8")
    stdout.write(struct.pack("<I", len(resp)))
    stdout.write(resp)
    stdout.flush()


def decode_sb1(llrs: np.ndarray) -> tuple[np.ndarray, int, int]:
    res = bch_decode_soft(llrs.astype(np.float32))
    info = pack_sb1_info(int(res.fid), int(res.toi))
    status = 0 if res.status == BchStatus.OK else 2
    return info, status, 0


def decode_ldpc(
    code_id: int, llrs: np.ndarray, max_iters: int
) -> tuple[np.ndarray, int, int]:
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
    do_handshake(stdin, stdout)

    while True:
        hdr = stdin.read(REQUEST_HEADER_LEN)
        if not hdr:
            return 0
        code_id, max_iters, _sigma_sq, n_bits = struct.unpack(
            REQUEST_HEADER_FMT, hdr
        )
        llrs = np.frombuffer(stdin.read(4 * n_bits), dtype=np.float32)

        if code_id == 0:
            info, status, iters = decode_sb1(llrs)
        else:
            info, status, iters = decode_ldpc(code_id, llrs, max_iters)

        n_info = N_INFO[code_id]
        stdout.write(struct.pack(RESPONSE_HEADER_FMT, status, iters, n_info))
        stdout.write(info.tobytes())
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
