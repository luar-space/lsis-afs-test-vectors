#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 LuarSpace contributors
"""Per-signal cross-decode driver: shipped LSISIQ signal → frame[64:6064] match.

For one entry in ``SIGNAL_TEST_VECTORS``:

    1. Convert the .iq.gz to headerless INT8X2 (lsisiq_to_pocketsdr).
    2. Run the patched pocket_trk against it with -dump-symbols.
    3. Read the first 6000 dumped symbols.
    4. Compare bytewise against frames/frame_<source>.bin[64:6064].
    5. Optionally cross-check $SB2/$SB3/$SB4 CRC-pass via parse_pocketsdr_log.

Returns a ``DecodeResult`` with the recovered symbols, CRC-pass stats,
and a localised mismatch report on failure.

This module is consumed by verify_pocketsdr_decode.py (sweeps all 10
signals); standalone CLI invocation is also supported for debugging.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HARNESSES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESSES_DIR))

from lsisiq_to_pocketsdr import convert as lsisiq_convert  # noqa: E402
from parse_pocketsdr_log import FrameDecodeStats, parse_log  # noqa: E402

FRAME_HEADER_LEN = 64
FRAME_PAYLOAD_LEN = 6000

# Post-LDPC, post-CRC subframe data-bit lengths (LSIS V1.0 §2.4):
#   SB2 = 1176 bits, SB3 = 846 bits, SB4 = 846 bits.
# Concatenated, this is exactly the layout shipped at inputs/frame_*_input.bin.
SB2_FEC_LEN = 1176
SB3_FEC_LEN = 846
SB4_FEC_LEN = 846
FEC_TOTAL_LEN = SB2_FEC_LEN + SB3_FEC_LEN + SB4_FEC_LEN  # 2868

# pocket_trk runs in pseudo-real-time; with -tscale we can speed it up,
# but for safety we cap the per-signal wall-clock at this many seconds.
DEFAULT_POCKET_TRK_TIMEOUT_S = 90.0

# PocketSDR-AFS's decode_AFSD attempts initial frame sync via
# sync_frame() (sdr_nav.c) which requires TWO preamble matches at
# buffer offsets 0 and 6000 (sdr_nav.c::sync_frame line 206-213).
# Combined with the +500 pull-in margin in decode_AFSD, the first
# successful decode_AFSD_frame call lands at ch->lock = 12068
# (= 24.14 s of locked tracking at 500 sym/s).  Subsequent decodes
# fire whenever ch->lock == fsync + 6000 — i.e. every 12 s after
# the first.
#
# Our shipped signals are 12 s = 6000 symbols.  Tile counts and the
# resulting frame-dumps per signal:
#
#     tile_count   signal len   ch->lock max   #frame-dumps
#     ─────────────────────────────────────────────────────
#     3 (≈36 s)    18000        17 996         1   (just misses 18068)
#     4 (≈48 s)    24000        23 996         2   (12068, 18068)
#     5 (≈60 s)    30000        29 996         3   (12068, 18068, 24068)
#
# Default 3: gives one decode per signal, which is sufficient given
# all tiles are byte-identical (replicated content; bumping
# tile_count would just re-decode the same frame).  Real multi-frame
# coverage requires *different* content across frames (advancing
# ITOW etc.) — that's L5 territory with realistic ephemeris, not a
# tile_count knob.  The verifier infrastructure walks all dumped
# frames regardless of count, so a future bump to ≥4 transparently
# upgrades the coverage with no validator changes.
DEFAULT_TILE_COUNT = 3


@dataclass
class DecodeResult:
    signal: str
    prn: int
    source_frame: str
    # Channel-symbol oracle (always evaluated):
    # all_chan_bytes is the FULL multi-frame symbol dump (typically
    # tile_count × FRAME_PAYLOAD_LEN; may be empty on failure).
    all_chan_bytes: bytes
    expected_bytes: bytes  # always 6000 — frames/<source>.bin[64:6064]
    # Post-FEC oracle:
    # all_fec_bytes is the FULL multi-frame post-FEC dump (typically
    # tile_count × FEC_TOTAL_LEN; may be empty on failure).
    all_fec_bytes: bytes
    expected_fec: bytes  # 2868 — inputs/<source-stem>_input.bin
    fec_evaluated: bool  # always True under v0.4.0+'s patch (the FID-bypass hook
    # in decode_AFSD_frame enables LDPC+CRC for FID>0 frames too).  Retained as a
    # field so a future upstream re-pin that drops the bypass can disable it
    # cleanly without changing the data class shape.
    # Bypass tracking (#2): True iff the bundled FID-bypass code-path fired
    # in the receiver (i.e. SB1 BCH search failed and we proceeded with a
    # placeholder TOI).  Derived from log lines emitted by our patch.
    bypass_fired: bool
    crc_stats: FrameDecodeStats
    pocket_trk_returncode: int
    error: str | None = None  # populated on failure

    @property
    def bytes_recovered(self) -> bytes:
        """First-frame channel-symbol output (the canonical shipped artefact)."""
        return self.all_chan_bytes[:FRAME_PAYLOAD_LEN]

    @property
    def fec_recovered(self) -> bytes:
        """First-frame post-FEC output (the canonical shipped artefact)."""
        return self.all_fec_bytes[:FEC_TOTAL_LEN]

    @property
    def chan_frames_count(self) -> int:
        """Number of full 6000-byte frame dumps produced (typically == tile_count)."""
        return len(self.all_chan_bytes) // FRAME_PAYLOAD_LEN

    @property
    def fec_frames_count(self) -> int:
        """Number of full 2868-byte post-FEC dumps produced."""
        return len(self.all_fec_bytes) // FEC_TOTAL_LEN

    @property
    def byte_exact(self) -> bool:
        """Channel-symbol oracle: every recovered frame matches frames/[64:6064].

        Stronger than "first frame matches" — verifies that consecutive
        frame decodes across the tiled signal are byte-stable.  Catches
        clock-tracking drift, late-cycle PLL slip, and any divergence
        between the first and Nth recovered frame.
        """
        n = self.chan_frames_count
        if n == 0 or len(self.all_chan_bytes) != n * FRAME_PAYLOAD_LEN:
            return False
        for i in range(n):
            chunk = self.all_chan_bytes[i * FRAME_PAYLOAD_LEN : (i + 1) * FRAME_PAYLOAD_LEN]
            if chunk != self.expected_bytes:
                return False
        return True

    @property
    def fec_byte_exact(self) -> bool:
        """Post-FEC oracle: every recovered subframe-bit dump matches inputs/.

        Always True when ``fec_evaluated`` is False (the FID-bypass hook
        in our patch makes this always True under v0.4.0+ — but we keep
        the gate so a future de-bundling of the bypass remains a 1-line
        change).
        """
        if not self.fec_evaluated:
            return True
        n = self.fec_frames_count
        if n == 0 or len(self.all_fec_bytes) != n * FEC_TOTAL_LEN:
            return False
        for i in range(n):
            chunk = self.all_fec_bytes[i * FEC_TOTAL_LEN : (i + 1) * FEC_TOTAL_LEN]
            if chunk != self.expected_fec:
                return False
        return True

    def first_mismatch(self) -> tuple[int, int, int, int] | None:
        """Return (frame_index, byte_index, expected, got) for the first
        differing channel-symbol byte across all dumped frames, or None."""
        return _first_chunk_mismatch(self.all_chan_bytes, self.expected_bytes, FRAME_PAYLOAD_LEN)

    def first_fec_mismatch(self) -> tuple[int, int, int, int] | None:
        """Return (frame_index, byte_index, expected, got) for the first
        differing post-FEC byte across all dumped frames, or None."""
        if not self.fec_evaluated or self.fec_byte_exact:
            return None
        return _first_chunk_mismatch(self.all_fec_bytes, self.expected_fec, FEC_TOTAL_LEN)


def _first_chunk_mismatch(
    blob: bytes, expected_chunk: bytes, chunk_len: int
) -> tuple[int, int, int, int] | None:
    """Walk ``blob`` in chunk_len-sized chunks; return (frame_index, byte_in_chunk,
    expected, got) for the first differing byte across all chunks, or None.

    A short final chunk (len % chunk_len != 0) is reported as a mismatch
    at (frame_index, len(short_chunk), -1, -1).
    """
    if not blob:
        return (0, 0, expected_chunk[0] if expected_chunk else -1, -1)
    n = len(blob) // chunk_len
    for i in range(n):
        chunk = blob[i * chunk_len : (i + 1) * chunk_len]
        if chunk == expected_chunk:
            continue
        for j in range(chunk_len):
            if chunk[j] != expected_chunk[j]:
                return (i, j, expected_chunk[j], chunk[j])
        return (i, chunk_len, -1, -1)  # unreachable in well-formed cases
    if n * chunk_len != len(blob):
        # Trailing partial chunk after a full chunk — flag the boundary.
        return (n, len(blob) - n * chunk_len, -1, -1)
    return None


def _expected_payload(repo_root: Path, source_frame: str) -> bytes:
    frame_path = repo_root / "frames" / source_frame
    raw = frame_path.read_bytes()
    if len(raw) != FRAME_HEADER_LEN + FRAME_PAYLOAD_LEN:
        raise ValueError(
            f"{frame_path}: expected {FRAME_HEADER_LEN + FRAME_PAYLOAD_LEN} bytes, got {len(raw)}"
        )
    return raw[FRAME_HEADER_LEN : FRAME_HEADER_LEN + FRAME_PAYLOAD_LEN]


def _expected_fec(repo_root: Path, source_frame: str) -> bytes:
    """Return the 2868-byte canonical pre-encode SB2+SB3+SB4 input for a frame.

    The input filename mirrors frame_X.bin → frame_X_input.bin (v0.2.1 layout).
    """
    stem = source_frame.removesuffix(".bin")
    input_path = repo_root / "inputs" / f"{stem}_input.bin"
    raw = input_path.read_bytes()
    if len(raw) != FEC_TOTAL_LEN:
        raise ValueError(f"{input_path}: expected {FEC_TOTAL_LEN} bytes, got {len(raw)}")
    return raw


def _tile_in_place(path: Path, tile_count: int) -> None:
    """Replicate a binary file's contents in-place ``tile_count`` times.

    Streams 16 MiB at a time so the operation is O(1) memory.
    """
    if tile_count <= 1:
        return
    tmp = path.with_suffix(path.suffix + ".tiled")
    with tmp.open("wb") as out:
        for _ in range(tile_count):
            with path.open("rb") as src:
                shutil.copyfileobj(src, out, length=1 << 24)
    path.unlink()
    tmp.rename(path)


def decode_one(
    *,
    pocket_trk: Path,
    signal_path: Path,
    prn: int,
    source_frame: str,
    repo_root: Path,
    workdir: Path,
    awgn_cn0_dbhz: float | None = None,
    tile_count: int = DEFAULT_TILE_COUNT,
    timeout_s: float = DEFAULT_POCKET_TRK_TIMEOUT_S,
    extra_pocket_trk_args: list[str] | None = None,
) -> DecodeResult:
    """Decode a single signal; return ``DecodeResult``."""
    workdir.mkdir(parents=True, exist_ok=True)
    iq_bin = workdir / (signal_path.stem.replace(".iq", "") + ".int8x2.bin")
    syms_bin = workdir / (signal_path.stem.replace(".iq", "") + ".syms.bin")
    fec_bin = workdir / (signal_path.stem.replace(".iq", "") + ".fec.bin")
    log_path = workdir / (signal_path.stem.replace(".iq", "") + ".log")

    expected = _expected_payload(repo_root, source_frame)
    # v0.4.0+: the bundled FID-bypass patch makes the FEC oracle apply to all
    # FIDs (including the FID=3 boundary frames).  See dump-symbols.patch.
    fec_evaluated = True
    expected_fec = _expected_fec(repo_root, source_frame)

    # 1. Convert to INT8X2.
    rate, hdr_prn = lsisiq_convert(
        signal_path,
        iq_bin,
        awgn_cn0_dbhz=awgn_cn0_dbhz,
    )
    # 1b. Tile to give pocket_trk's tracker enough runway (see DEFAULT_TILE_COUNT).
    _tile_in_place(iq_bin, tile_count)
    if hdr_prn != prn:
        return DecodeResult(
            signal=signal_path.name,
            prn=prn,
            source_frame=source_frame,
            all_chan_bytes=b"",
            expected_bytes=expected,
            all_fec_bytes=b"",
            expected_fec=expected_fec,
            fec_evaluated=fec_evaluated,
            bypass_fired=False,
            crc_stats=FrameDecodeStats(),
            pocket_trk_returncode=-1,
            error=f"signal header PRN {hdr_prn} != expected {prn}",
        )

    # 2. Run pocket_trk.
    cmd = [
        str(pocket_trk),
        "-sig",
        "AFSD",
        "-prn",
        str(prn),
        "-fmt",
        "INT8X2",
        "-f",
        f"{rate / 1e6:.6f}",
        "-ti",
        "0",
        "-log",
        str(log_path),
        "-dump-symbols",
        str(syms_bin),
        "-dump-fec",
        str(fec_bin),
    ]
    if extra_pocket_trk_args:
        cmd.extend(extra_pocket_trk_args)
    cmd.append(str(iq_bin))

    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        rc = proc.returncode
        run_err = proc.stderr or proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        rc = -2
        run_err = f"timeout after {timeout_s}s"
        if exc.stderr:
            run_err += f" — stderr: {exc.stderr.decode('utf-8', errors='replace')[:500]}"

    # 3. Read FULL multi-frame symbol + FEC dumps (no truncation).
    all_chan_bytes = syms_bin.read_bytes() if syms_bin.exists() else b""
    all_fec_bytes = fec_bin.read_bytes() if fec_bin.exists() else b""

    # 4. Parse log for $SB CRC stats AND for the bundled FID-bypass marker.
    crc_stats = parse_log(log_path)
    bypass_fired = _detect_bypass(log_path)

    err: str | None = None
    if rc != 0:
        err = f"pocket_trk exit {rc}: {run_err.strip()[:300]}"
    elif len(all_chan_bytes) == 0:
        err = "pocket_trk produced no symbol dump (failed to acquire/sync)"
    elif len(all_chan_bytes) < FRAME_PAYLOAD_LEN:
        err = (
            f"pocket_trk dumped {len(all_chan_bytes)} symbol bytes "
            f"({len(all_chan_bytes) / FRAME_PAYLOAD_LEN:.2f} frames); "
            "expected at least 1 full frame (6000 bytes)"
        )
    elif len(all_chan_bytes) % FRAME_PAYLOAD_LEN != 0:
        err = (
            f"pocket_trk produced {len(all_chan_bytes)} symbol bytes — "
            f"not a whole multiple of {FRAME_PAYLOAD_LEN}; the dump is misaligned"
        )
    elif fec_evaluated and len(all_fec_bytes) < FEC_TOTAL_LEN:
        err = (
            f"pocket_trk produced {len(all_fec_bytes)} FEC bytes "
            f"({len(all_fec_bytes) / FEC_TOTAL_LEN:.2f} frames); "
            f"expected at least 1 full frame ({FEC_TOTAL_LEN} bytes; "
            "SB2 + SB3 + SB4 LDPC+CRC must all succeed)"
        )
    elif fec_evaluated and len(all_fec_bytes) % FEC_TOTAL_LEN != 0:
        err = (
            f"pocket_trk produced {len(all_fec_bytes)} FEC bytes — "
            f"not a whole multiple of {FEC_TOTAL_LEN}; the dump is misaligned"
        )

    return DecodeResult(
        signal=signal_path.name,
        prn=prn,
        source_frame=source_frame,
        all_chan_bytes=all_chan_bytes,
        expected_bytes=expected,
        all_fec_bytes=all_fec_bytes,
        expected_fec=expected_fec,
        fec_evaluated=fec_evaluated,
        bypass_fired=bypass_fired,
        crc_stats=crc_stats,
        pocket_trk_returncode=rc,
        error=err,
    )


# Marker line emitted by the bundled FID-bypass when sync_AFS_SF1_FID0 fails.
# See references/pocketsdr-afs/harnesses/patches/dump-symbols.patch — the patch
# adds: sdr_log(3, "$LOG,...,SB1 FID mismatch — proceeding for FEC dump", ...)
_BYPASS_MARKER = "SB1 FID mismatch"


def _detect_bypass(log_path: Path) -> bool:
    """Did the bundled FID-bypass code-path fire during this signal's decode?

    The patch emits a distinctive log line whenever it activates; this lets
    the verifier separately count "decoded natively" (FID=0) from "decoded
    with bypass" (FID>0) results.  Returns False if the log is absent.
    """
    if not log_path.exists():
        return False
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        return any(_BYPASS_MARKER in line for line in f)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    p.add_argument("--pocket-trk", type=Path, required=True, help="path to pocket_trk binary")
    p.add_argument("--signal", type=Path, required=True, help="signals/signal_*_12s.iq.gz")
    p.add_argument("--prn", type=int, required=True, help="expected PRN")
    p.add_argument("--source-frame", required=True, help="frames/frame_*.bin filename")
    p.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="LSIS-AFS test-vector repo root",
    )
    p.add_argument("--workdir", type=Path, default=None)
    p.add_argument("--awgn-cn0", type=float, default=None, metavar="DBHZ")
    p.add_argument("--tile-count", type=int, default=DEFAULT_TILE_COUNT)
    p.add_argument("--timeout", type=float, default=DEFAULT_POCKET_TRK_TIMEOUT_S)
    args = p.parse_args(argv)

    cleanup = False
    if args.workdir is None:
        args.workdir = Path(tempfile.mkdtemp(prefix="lsis-afs-l4-"))
        cleanup = True

    try:
        result = decode_one(
            pocket_trk=args.pocket_trk,
            signal_path=args.signal,
            prn=args.prn,
            source_frame=args.source_frame,
            repo_root=args.repo_root,
            workdir=args.workdir,
            awgn_cn0_dbhz=args.awgn_cn0,
            tile_count=args.tile_count,
            timeout_s=args.timeout,
        )
    finally:
        if cleanup and not os.environ.get("KEEP_WORKDIR"):
            shutil.rmtree(args.workdir, ignore_errors=True)

    print(f"  CRC: {result.crc_stats.summary()}")
    bypass_note = " [BYPASS]" if result.bypass_fired else ""
    if result.error:
        print(f"FAIL — {result.signal}: {result.error}", file=sys.stderr)
        return 1
    if result.byte_exact and result.fec_byte_exact:
        print(
            f"OK — {result.signal}: chan {result.chan_frames_count}/{result.chan_frames_count} "
            f"frames byte-exact vs frames/{result.source_frame}; "
            f"FEC {result.fec_frames_count}/{result.fec_frames_count} byte-exact vs inputs/"
            f"{result.source_frame.removesuffix('.bin')}_input.bin{bypass_note}"
        )
        return 0
    miss = result.first_mismatch()
    if miss is not None:
        frame_idx, byte_idx, exp, got = miss
        print(
            f"FAIL — {result.signal}: first symbol mismatch at frame {frame_idx} "
            f"byte {byte_idx} (expected {exp}, got {got})",
            file=sys.stderr,
        )
    fec_miss = result.first_fec_mismatch()
    if fec_miss is not None:
        frame_idx, byte_idx, exp, got = fec_miss
        print(
            f"FAIL — {result.signal}: first FEC byte mismatch at frame {frame_idx} "
            f"byte {byte_idx} (expected {exp}, got {got})",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
