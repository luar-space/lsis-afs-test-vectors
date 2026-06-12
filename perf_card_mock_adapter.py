#!/usr/bin/env python3
"""Mock decoder adapter for perf_card harness V1 testing.

Implements the binary stdio protocol from `shaping/decoder-performance-card.md`
but doesn't actually decode anything — returns all-zero info bits for every
request. Useful only as a protocol-mechanics test: when paired with the
V1 harness's all-zero-message scaffold, frame errors should equal zero
regardless of channel quality (because the "true" message and the "decoded"
message are both all-zero).

Not a real decoder. Not a template. Just a test fixture for the harness.

Usage (paired with perf_card.py V1):
    python perf_card.py run \\
        --decoder "python perf_card_mock_adapter.py" \\
        --code SF2 --eb-n0-db 1.4 --frames 100
"""

from __future__ import annotations

import json
import struct
import sys

# Info-bit counts per wire code_id — must match perf_card.py CODES.
N_INFO = {0: 9, 1: 1200, 2: 870}  # SB1, SF2, SF3

REQUEST_HEADER_FMT = "<BHfI"
REQUEST_HEADER_LEN = 11
RESPONSE_HEADER_FMT = "<BHI"
PROTOCOL_VERSION = "1.0"


def do_handshake(stdin, stdout) -> None:
    """Read the harness's HandshakeRequest and reply with HandshakeAck."""
    n = struct.unpack("<I", stdin.read(4))[0]
    _ = json.loads(stdin.read(n))  # we don't act on the request fields
    resp = json.dumps(
        {
            "type": "handshake_ack",
            "protocol_version": PROTOCOL_VERSION,
            "adapter": {
                "name": "perf_card mock_adapter (returns all-zero info bits)",
                "version": "1.0.0",
                "supports_codes": ["SB1", "SF2", "SF3"],
                "ldpc": {
                    "algorithm": "mock (no decoding)",
                    "early_termination": "n/a",
                },
                "sb1": {
                    "name": "mock",
                    "decoder_class": "other",
                    "algorithm": "mock (no decoding)",
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
            # Clean EOF — harness closed stdin, exit cleanly.
            return 0
        if len(hdr) < REQUEST_HEADER_LEN:
            print(
                f"[mock] short header: got {len(hdr)} bytes, expected {REQUEST_HEADER_LEN}",
                file=sys.stderr,
            )
            return 1

        code_id, _max_iters, _sigma_sq, n_bits = struct.unpack(REQUEST_HEADER_FMT, hdr)
        # Consume LLR payload (we don't use it — mock doesn't decode).
        payload = stdin.read(4 * n_bits)
        if len(payload) < 4 * n_bits:
            print(
                f"[mock] short LLR payload: got {len(payload)} bytes, expected {4 * n_bits}",
                file=sys.stderr,
            )
            return 1

        n_info = N_INFO[code_id]
        # status=0 (ok), iters_used=0 (we didn't iterate), n_info_bits, then
        # n_info zero-bytes for the decoded info bits.
        stdout.write(struct.pack(RESPONSE_HEADER_FMT, 0, 0, n_info))
        stdout.write(b"\x00" * n_info)
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
