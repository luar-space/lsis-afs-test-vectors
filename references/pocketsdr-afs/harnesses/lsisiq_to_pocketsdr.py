#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 LuarSpace contributors
"""LSISIQ → PocketSDR-AFS INT8X2 signal converter.

Streams a shipped ``signals/signal_*_12s.iq.gz`` file (128-byte LSISIQ
header + interleaved float32 I/Q at 10.23 MHz × 12 s, BPSK ±1.0) into
the headerless interleaved int8 I/Q format that ``pocket_trk -fmt
INT8X2`` consumes.

Conversion pipeline:

    gunzip → strip 128B LSISIQ header → parse header for sample_rate / PRN
           → cast float32 → int8 with scale 100 (BPSK ±1.0 → ±100, well
             below int8 max ±127, lossless given the strict ±1.0 range
             enforced by validate.py check-signals)
           → write interleaved int8 pairs to disk

Optionally inject deterministic AWGN (``--awgn-cn0``) to nudge a
noiseless input into the SNR band PocketSDR-AFS's tracking loops were
tuned for (35 dB-Hz lock threshold, ~45 dB-Hz typical real-world).
The seed is per-signal-derived so re-runs are byte-stable.

Stdlib-only: numpy is used if available for speed, but the function
falls back to a pure-stdlib path that processes 1 MiB at a time.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import random as _random
import struct
import sys
from pathlib import Path

try:
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None

# LSIS V1.0 §4 — 128-byte LSISIQ header (per interoperability.pdf "Signal Export Format").
LSISIQ_MAGIC = b"LSISIQ\x00\x00"
LSISIQ_HEADER_LEN = 128
LSISIQ_SAMPLE_RATE = 10_230_000.0
LSISIQ_DURATION_S = 12.0

# Scale chosen so BPSK ±1.0 maps to ±100, well clear of int8 saturation
# (±127) and well above the LSB so quantisation noise is negligible.
# PocketSDR-AFS internal SDR_CSCALE = 1/24, so int8 ±100 enters the
# correlator as ~±4.17, comfortably within the dynamic range.
INT8_SCALE = 100.0


def _parse_lsisiq_header(header: bytes) -> tuple[float, int]:
    """Return (sample_rate_hz, prn) from a 128-byte LSISIQ header."""
    if len(header) != LSISIQ_HEADER_LEN:
        raise ValueError(f"header is {len(header)} bytes, expected {LSISIQ_HEADER_LEN}")
    if header[:8] != LSISIQ_MAGIC:
        raise ValueError(f"magic {header[:8]!r}, expected {LSISIQ_MAGIC!r}")
    (version,) = struct.unpack("<I", header[8:12])
    (sample_rate,) = struct.unpack("<d", header[12:20])
    (_duration,) = struct.unpack("<d", header[20:28])
    (prn,) = struct.unpack("<I", header[28:32])
    if version != 1:
        raise ValueError(f"version {version}, expected 1")
    if sample_rate != LSISIQ_SAMPLE_RATE:
        raise ValueError(f"sample_rate {sample_rate}, expected {LSISIQ_SAMPLE_RATE}")
    return sample_rate, prn


def _signal_seed(input_path: Path) -> int:
    """Per-signal deterministic seed derived from the filename."""
    h = hashlib.sha256(input_path.name.encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def _sigma_for_cn0(cn0_dbhz: float, sample_rate_hz: float) -> float:
    """Per-component noise stddev to hit a target C/N0 in dB-Hz.

    Signal power (post-de-spread) is 1.0 (BPSK ±1.0).  For a given C/N0
    in dB-Hz, the noise PSD N0 = signal_power / (10**(cn0_dbhz/10)).
    Per-component noise variance = N0 · BW_one_sided = N0 · (fs/2),
    so the per-sample stddev for I and Q each is sqrt(N0 · fs / 2).
    """
    n0 = 1.0 / (10 ** (cn0_dbhz / 10.0))
    return (n0 * sample_rate_hz / 2.0) ** 0.5


def convert(
    input_path: Path,
    output_path: Path,
    *,
    awgn_cn0_dbhz: float | None = None,
    seed: int | None = None,
) -> tuple[float, int]:
    """Convert one shipped LSISIQ signal to a headerless INT8X2 file.

    Parameters
    ----------
    input_path: shipped ``signals/signal_*_12s.iq.gz`` (or .iq).
    output_path: written as raw int8 interleaved I/Q, no header.
    awgn_cn0_dbhz: if given, inject AWGN at this target C/N0.
    seed: explicit RNG seed; defaults to a per-signal-derived value.

    Returns (sample_rate_hz, prn).
    """
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    opener = gzip.open if input_path.suffix == ".gz" else open
    with opener(input_path, "rb") as fin:
        header = fin.read(LSISIQ_HEADER_LEN)
        sample_rate, prn = _parse_lsisiq_header(header)

        rng_seed = seed if seed is not None else _signal_seed(input_path)
        sigma = _sigma_for_cn0(awgn_cn0_dbhz, sample_rate) if awgn_cn0_dbhz is not None else 0.0
        rng_np = _np.random.default_rng(rng_seed) if (_np is not None and sigma > 0) else None
        rng_py = _random.Random(rng_seed) if (sigma > 0 and _np is None) else None

        with output_path.open("wb") as fout:
            chunk_floats = 1 << 18  # 256K floats = 1 MiB float32
            while True:
                buf = fin.read(chunk_floats * 4)
                if not buf:
                    break
                if _np is not None:
                    samples = _np.frombuffer(buf, dtype="<f4")
                    if sigma > 0 and rng_np is not None:
                        samples = samples + rng_np.normal(0.0, sigma, samples.shape).astype("<f4")
                    scaled = _np.round(samples * INT8_SCALE)
                    scaled = _np.clip(scaled, -127, 127).astype("<i1")
                    fout.write(scaled.tobytes())
                else:
                    n = len(buf) // 4
                    floats = struct.unpack(f"<{n}f", buf)
                    if sigma > 0 and rng_py is not None:  # pragma: no cover
                        floats = tuple(f + rng_py.gauss(0.0, sigma) for f in floats)
                    out = bytearray(n)
                    for i, f in enumerate(floats):
                        v = round(f * INT8_SCALE)
                        if v > 127:
                            v = 127
                        elif v < -127:
                            v = -127
                        out[i] = v & 0xFF
                    fout.write(bytes(out))
    return sample_rate, prn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    p.add_argument("input", type=Path, help="shipped signal_*_12s.iq.gz file")
    p.add_argument("output", type=Path, help="output INT8X2 .bin file")
    p.add_argument(
        "--awgn-cn0",
        type=float,
        default=None,
        metavar="DBHZ",
        help="inject deterministic AWGN at target C/N0 in dB-Hz (default: off)",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="explicit RNG seed (default: derived from input filename)",
    )
    args = p.parse_args(argv)
    rate, prn = convert(
        args.input,
        args.output,
        awgn_cn0_dbhz=args.awgn_cn0,
        seed=args.seed,
    )
    awgn_note = f" (AWGN C/N0={args.awgn_cn0:.1f} dB-Hz)" if args.awgn_cn0 is not None else ""
    print(
        f"OK — {args.input.name} → {args.output.name}: "
        f"sample_rate={rate / 1e6:.3f} MHz, PRN={prn}{awgn_note}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
