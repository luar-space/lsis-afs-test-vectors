#!/usr/bin/env python3
"""Adapter template for the LSIS-AFS perf-card harness — Python.

Replace the three TODO blocks (handshake identity + decoder bodies) with
your decoder's specifics. Everything else (protocol I/O, byte packing,
SB1 info-bit convention) is boilerplate and shouldn't need changes.

Run via the harness:

    python ../perf_card.py run --decoder "python my_adapter.py"

To test your decoder in isolation without the harness, you can pipe a
recorded LLR stream in:

    python my_adapter.py < recorded_requests.bin > my_responses.bin
"""

from __future__ import annotations

import json
import struct
import sys

import numpy as np

# Wire protocol — must match perf_card.py exactly. Do not change.
REQUEST_HEADER_FMT = "<BHfI"
REQUEST_HEADER_LEN = 11
RESPONSE_HEADER_FMT = "<BHI"
PROTOCOL_VERSION = "1.0"

# Code identifiers (wire encoding) and their info-bit counts.
N_INFO = {0: 9, 1: 1200, 2: 870}  # SB1 / SF2 / SF3


# ─── TODO 1: adapter identity ────────────────────────────────────────────
# Edit these strings to describe your decoder. They land in the algo card's
# identity block and are what `perf-card compare` reports.
ADAPTER_NAME = "my-decoder"
ADAPTER_VERSION = "0.1.0"
SUPPORTS_CODES = ["SB1", "SF2", "SF3"]  # remove any you don't implement
LDPC_ALGORITHM = "TODO: e.g., min-sum, sum-product BP, layered LP, …"
LDPC_EARLY_TERM = "TODO: e.g., syndrome check every iter, fixed iters, …"
SB1_DECODER_NAME = "my-bch-decoder"
SB1_DECODER_CLASS = "soft_ML"  # hard_ML | soft_ML | BDD | other
SB1_ALGORITHM = "TODO: e.g., exhaustive ML over LLR, BMA, …"


# ─── TODO 2: decoder implementations ─────────────────────────────────────
# Replace these stubs with your real decoder calls.


def decode_sb1(llrs: np.ndarray, max_iters: int) -> tuple[np.ndarray, int, int]:
    """52 channel LLRs → 9 info bits (FID:2 MSB-first | TOI:7 MSB-first).

    Return: (info_bits, status, iters_used).
      status: 0 = ok, 1 = not_converged, 2 = error
      iters_used: informational; pass 0 if not iterative.
    """
    # TODO: implement. Below is a placeholder that always returns all-zero
    # info bits — useful only as a protocol smoke test.
    return np.zeros(9, dtype=np.uint8), 0, 0


def decode_sf2(llrs: np.ndarray, max_iters: int) -> tuple[np.ndarray, int, int]:
    """2400 channel LLRs → 1200 info bits. Return (info_bits, status, iters_used)."""
    # TODO: implement.
    return np.zeros(1200, dtype=np.uint8), 0, max_iters


def decode_sf3(llrs: np.ndarray, max_iters: int) -> tuple[np.ndarray, int, int]:
    """1740 channel LLRs → 870 info bits. Return (info_bits, status, iters_used)."""
    # TODO: implement.
    return np.zeros(870, dtype=np.uint8), 0, max_iters


# ─── TODO 3 (optional): pack_sb1_info convention ─────────────────────────
# The standard pins this bit-layout for SB1 — DO NOT change. It's included
# here so your decoder can call it if it recovers (FID, TOI) directly
# rather than as 9 raw bits.


def pack_sb1_info(fid_val: int, toi_val: int) -> np.ndarray:
    """SB1 info-bit packing convention. 9 bits = FID (2, MSB-first) |
    TOI (7, MSB-first)."""
    info = np.zeros(9, dtype=np.uint8)
    info[0] = (fid_val >> 1) & 1
    info[1] = fid_val & 1
    for i in range(7):
        info[2 + i] = (toi_val >> (6 - i)) & 1
    return info


# ─── Protocol I/O — boilerplate, no edits needed below ───────────────────

CODE_HANDLERS = {0: decode_sb1, 1: decode_sf2, 2: decode_sf3}


def do_handshake(stdin, stdout) -> None:
    n = struct.unpack("<I", stdin.read(4))[0]
    _ = json.loads(stdin.read(n))
    resp = json.dumps(
        {
            "type": "handshake_ack",
            "protocol_version": PROTOCOL_VERSION,
            "adapter": {
                "name": ADAPTER_NAME,
                "version": ADAPTER_VERSION,
                "supports_codes": SUPPORTS_CODES,
                "ldpc": {
                    "algorithm": LDPC_ALGORITHM,
                    "early_termination": LDPC_EARLY_TERM,
                },
                "sb1": {
                    "name": SB1_DECODER_NAME,
                    "decoder_class": SB1_DECODER_CLASS,
                    "algorithm": SB1_ALGORITHM,
                },
            },
        }
    ).encode("utf-8")
    stdout.write(struct.pack("<I", len(resp)))
    stdout.write(resp)
    stdout.flush()


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    do_handshake(stdin, stdout)

    while True:
        hdr = stdin.read(REQUEST_HEADER_LEN)
        if not hdr:
            return 0
        code_id, max_iters, _sigma_sq, n_bits = struct.unpack(REQUEST_HEADER_FMT, hdr)
        llrs = np.frombuffer(stdin.read(4 * n_bits), dtype=np.float32)
        handler = CODE_HANDLERS[code_id]
        info, status, iters = handler(llrs, max_iters)
        n_info = N_INFO[code_id]
        if len(info) != n_info:
            print(
                f"[adapter] wrong n_info for code {code_id}: got {len(info)}, expected {n_info}",
                file=sys.stderr,
            )
            return 1
        stdout.write(struct.pack(RESPONSE_HEADER_FMT, status, iters, n_info))
        stdout.write(np.asarray(info, dtype=np.uint8).tobytes())
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
