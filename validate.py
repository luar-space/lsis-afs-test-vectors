"""LSIS-AFS interoperability test-vector validator (Levels 1–4).

Subcommands
-----------
check-annex3
    Confirm every code in ``codes/`` matches the corresponding entry in the
    Annex 3 reference files in ``references/``.  L1 normative oracle:
    210 PRNs × 3 code types (Gold, Weil-10230, Weil-1500).

check-lans-afs-sim
    Confirm every code in ``codes/`` matches chip-for-chip against the
    LANS-AFS-SIM reference dumps in ``references/lans-afs-sim/codes/``.
    L1 second oracle: 210 PRNs × 2 code families (Gold, Weil-10230).

check-frames
    Validate every ``frames/frame_*.bin`` against the structural rules
    derived from the LSIS-AFS spec and the Gateway 3 deliverables checklist:
    header magic, version, frame length, PRN, symbol-domain values, and the
    68-symbol sync pattern (0xCC63F74536F49E04A).  L2 structural oracle.

check-lans-afs-sim-frames
    Compare every ``frames/frame_*.bin`` (header stripped) byte-for-byte
    against the corresponding ``references/lans-afs-sim/frames/lans_frame_*.bin``.
    L2 second oracle.

check-signals
    Validate every ``signals/signal_*_12s.iq.gz`` structurally per the interop
    document's Signal Export Format (LSISIQ\\0\\0 magic + 128-byte header +
    interleaved float32 I/Q at 10.23 MHz × 12 s) and chain L1+L2 oracles into
    L3 by checking the first-chip I- and Q-channel polarity against the
    Annex-3-verified Gold/Weil/Tertiary chips and the FAQ-Q17-pinned sync
    prefix.  L3 structural + first-chip polarity oracle.

diff
    Compare a directory of code vectors (codes_prnNNN.hex) against ours.

diff-frames
    Compare a directory of frame vectors (frame_*.bin) against ours.

diff-signals
    Compare a directory of L3 signal vectors (signal_*_12s.iq[.gz]) against ours.

check-decode
    Verify every shipped ``references/pocketsdr-afs/decoded/decoded_signal_*.bin``
    is exactly 6000 bytes of {0,1} symbols and byte-equal to the corresponding
    ``frames/frame_*.bin[64:6064]``.  L4 cheap oracle: confirms the bundled
    PocketSDR-AFS cross-decode outputs round-trip to the shipped L2 frames.
    Re-running the decode end-to-end (clone + build + decode 10 signals)
    requires the maintainer command
    ``references/pocketsdr-afs/harnesses/verify_pocketsdr_decode.py``.

diff-decode
    Compare a directory of decoded outputs (decoded_signal_*.bin) against ours.

check-canonical-inputs
    Verify the canonical pre-encode input files in ``inputs/`` reproduce
    from the documented patterns (zeros / ones / alternating0 / alternating1
    / marker / xorshift32, with FAQ Q21 spare-bit normalisation applied).

diff-inputs
    Compare a directory of canonical-input files (frame_*_input.bin) against ours.

build-canonical-inputs
    Regenerate ``inputs/`` from the documented patterns.  Maintainer command.

verify-manifest
    Re-compute SHA256 for every file listed in ``manifest.json``.

rebuild-manifest
    Regenerate ``manifest.json`` from the contents of ``codes/``, ``frames/``,
    ``inputs/``, ``signals/``, and ``references/``.  Maintainer command.

refresh
    Download Annex 3 reference files from a user-supplied URL and re-hash.

Stdlib-only — no third-party dependencies required to run this tool.
"""

from __future__ import annotations

import argparse
import array
import functools
import gzip
import hashlib
import json
import re
import struct
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

# Optional speedup for the L3 full-range scan: numpy reads a 982 MB float32
# payload as a uint32 view in microseconds (zero-copy) and runs a vectorised
# set-membership check ~12× faster than bytes.count.  The validator is
# stdlib-only by design — if numpy is not installed we fall back to the
# slower path.  Both paths are functionally equivalent.
try:
    import numpy as _np
except ImportError:  # pragma: no cover - exercised only on stdlib-only installs
    _np = None

REPO_ROOT = Path(__file__).resolve().parent
CODES_DIR = REPO_ROOT / "codes"
FRAMES_DIR = REPO_ROOT / "frames"
SIGNALS_DIR = REPO_ROOT / "signals"
REFERENCES_DIR = REPO_ROOT / "references"
ANNEX3_DIR = REFERENCES_DIR / "annex-3"
LANS_DIR = REFERENCES_DIR / "lans-afs-sim"
LANS_CODES_DIR = LANS_DIR / "codes"
LANS_FRAMES_DIR = LANS_DIR / "frames"
POCKETSDR_DIR = REFERENCES_DIR / "pocketsdr-afs"
POCKETSDR_DECODED_DIR = POCKETSDR_DIR / "decoded"
MANIFEST_PATH = REPO_ROOT / "manifest.json"

ANNEX3_FILES = {
    "GOLD_CODE": "006_GoldCode2046hex210prns.txt",
    "WEIL_PRIMARY": "007_l1cp_hex210prns.txt",
    "WEIL_TERTIARY": "008_Weil1500hex210prns.txt",
}

# For LANS-AFS-SIM cross-check: section → (bin prefix, chip count)
LANS_FILES = {
    "GOLD_CODE": ("gold", 2046),
    "WEIL_PRIMARY": ("weil", 10230),
}

SECTION_LENGTHS = {
    "GOLD_CODE": 512,
    "WEIL_PRIMARY": 2558,
    "WEIL_TERTIARY": 375,
    "SECONDARY_S0": 1,
    "SECONDARY_S1": 1,
    "SECONDARY_S2": 1,
    "SECONDARY_S3": 1,
}

# ─────────────────────────────── Level 2 constants ─────────────────────────

FRAME_MAGIC = b"LSISAFS\x00"
FRAME_VERSION = 1
FRAME_PAYLOAD_LEN = 6000  # symbols
FRAME_HEADER_LEN = 64
FRAME_FILE_LEN = FRAME_HEADER_LEN + FRAME_PAYLOAD_LEN  # 6064 bytes

# Sync pattern (LSIS V1.0 §2.4.1, Table 12; FAQ Q17): 17 nibbles = 68 bits MSB-first
SYNC_PATTERN_HEX = "CC63F74536F49E04A"
EXPECTED_SYNC_BITS = bytes(int(b) for b in "".join(f"{int(c, 16):04b}" for c in SYNC_PATTERN_HEX))
assert len(EXPECTED_SYNC_BITS) == 68

# (filename, expected_prn, expected_fid, expected_toi).  PRN is checked
# structurally by check-frames (it lives in the 64-byte header).  FID and
# TOI are encoded into the BCH(51,8)-protected SB1 (52 bits at payload
# offset 68); they are verified bit-for-bit by check-lans-afs-sim-frames,
# whose LANS reference dump was produced by upstream
# generate_BCH_AFS_SF1(sb1, fid, toi) at the values listed below.  Any
# disagreement on FID/TOI in our frame surfaces as an SB1 payload diff.
# The per-file (FID, TOI) inputs are also pinned in
# references/lans-afs-sim/harnesses/dump_l2_test_vectors.py.
FRAME_TEST_VECTORS: list[tuple[str, int, int, int]] = [
    ("frame_message_1.bin", 1, 0, 0),
    ("frame_message_2.bin", 1, 0, 0),
    ("frame_message_3.bin", 1, 0, 0),
    ("frame_message_4.bin", 1, 0, 0),
    ("frame_message_5.bin", 1, 0, 0),
    ("frame_boundary.bin", 210, 3, 99),
    # v0.2.2 — covers TC4 max-field dimensions the original boundary frame
    # does NOT exercise (WN=8191 in SB2[0..12], ITOW=503 in SB2[13..21]).
    # Same FID/TOI/PRN as frame_boundary.bin (max field maxima); SB2/SB3/SB4
    # = all-ones EXCEPT the 9-bit ITOW field clamped to its spec maximum 503
    # (bits SB2[13..21] = 0b111110111 MSB-first; raw 9-bit max would be 511,
    # which is invalid per LSIS V1.0 §2.4.3.1.6 — TC5 territory, not TC4).
    ("frame_boundary_max_fields.bin", 210, 3, 99),
]


def _lans_frame_name(frame_filename: str) -> str:
    """Map our ``frame_xxx.bin`` to the LANS dump ``lans_frame_xxx.bin``."""
    assert frame_filename.startswith("frame_") and frame_filename.endswith(".bin")
    return "lans_" + frame_filename


# ─────────────────────────────── Canonical inputs (L2 pre-encode) ──────────
#
# Per LSIS V1.0 §2.4: subframe data-bit counts (the bits the encoder consumes
# before CRC-24Q + LDPC).  Canonical input files in inputs/ ship these bits
# in unpacked form (1 byte per bit, value 0x00 or 0x01) so any contestant
# can read them, feed them into their encoder, and bit-compare the output
# against frames/frame_*.bin via diff-frames.  The 6 input files map 1:1
# to the 6 frame files in FRAME_TEST_VECTORS.
#
# FAQ Q21 / LSIS-300: SB2 bits 1150..1175 carry the spec-mandated alternating
# 0/1 pattern starting with 0.  This is applied in the canonical input bytes
# (post-normalisation) so the file is self-describing ground truth: a
# contestant whose encoder consumes the file produces our frame regardless
# of whether their encoder applies Q21 internally.

SB2_BITS = 1176
SB3_BITS = 846
SB4_BITS = 846
INPUT_BYTE_COUNT = SB2_BITS + SB3_BITS + SB4_BITS  # 2868

INPUTS_DIR = REPO_ROOT / "inputs"

SB2_SPARE_BITS_OFFSET = 1150
SB2_SPARE_BITS_LENGTH = 26

# (filename, pattern_name).  Pattern names are documented in CORRECTNESS.md.
INPUT_TEST_VECTORS: list[tuple[str, str]] = [
    ("frame_message_1_input.bin", "zeros"),
    ("frame_message_2_input.bin", "ones"),
    ("frame_message_3_input.bin", "alternating1"),
    ("frame_message_4_input.bin", "marker"),
    ("frame_message_5_input.bin", "xorshift32"),
    ("frame_boundary_input.bin", "alternating0"),
    ("frame_boundary_max_fields_input.bin", "max_fields"),
]


# Spec-defined SB2 field positions (LSIS V1.0 §2.4.3.1.6 / LSIS-FID0-520):
# WN occupies bits 0..12 (13 bits, MSB-first); ITOW occupies bits 13..21
# (9 bits, MSB-first).  ITOW's spec maximum is 503, not the 9-bit raw 511.
SB2_WN_OFFSET = 0
SB2_WN_BITS = 13
SB2_ITOW_OFFSET = 13
SB2_ITOW_BITS = 9
SB2_ITOW_SPEC_MAX = 503


def _xorshift32_bits(seed: int, count: int) -> list[int]:
    """xorshift32 PRNG; bit i = state & 1 after iteration i+1.  See CORRECTNESS.md TM5."""
    state = seed & 0xFFFFFFFF
    out: list[int] = []
    for _ in range(count):
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= state >> 17
        state ^= (state << 5) & 0xFFFFFFFF
        out.append(state & 1)
    return out


def _marker_bits(bit_count: int) -> list[int]:
    """Bytewise marker: bit i is the MSB-first bit of byte (i // 8) mod 256."""
    out: list[int] = []
    for i in range(bit_count):
        byte_val = (i // 8) % 256
        bit_pos = i % 8  # 0 = MSB
        out.append((byte_val >> (7 - bit_pos)) & 1)
    return out


def _build_canonical_input(name: str) -> bytes:
    """Return the 2868-byte canonical input (SB2 || SB3 || SB4) for a pattern.

    SB2 includes the FAQ Q21 spare-bit normalisation at bits 1150..1175.
    """
    if name == "zeros":
        sb2 = [0] * SB2_BITS
        sb3 = [0] * SB3_BITS
        sb4 = [0] * SB4_BITS
    elif name == "ones":
        sb2 = [1] * SB2_BITS
        sb3 = [1] * SB3_BITS
        sb4 = [1] * SB4_BITS
    elif name == "alternating0":
        # bit_i = i mod 2 → first packed byte 0x55
        sb2 = [i % 2 for i in range(SB2_BITS)]
        sb3 = [i % 2 for i in range(SB3_BITS)]
        sb4 = [i % 2 for i in range(SB4_BITS)]
    elif name == "alternating1":
        # bit_i = (i + 1) mod 2 → first packed byte 0xAA, matches interop-doc TM3
        sb2 = [(i + 1) % 2 for i in range(SB2_BITS)]
        sb3 = [(i + 1) % 2 for i in range(SB3_BITS)]
        sb4 = [(i + 1) % 2 for i in range(SB4_BITS)]
    elif name == "marker":
        sb2 = _marker_bits(SB2_BITS)
        sb3 = _marker_bits(SB3_BITS)
        sb4 = _marker_bits(SB4_BITS)
    elif name == "xorshift32":
        # Single stream consumed across SB2 → SB3 → SB4 (matches dump_lans_frame.c)
        all_bits = _xorshift32_bits(0xAF52, INPUT_BYTE_COUNT)
        sb2 = all_bits[:SB2_BITS]
        sb3 = all_bits[SB2_BITS : SB2_BITS + SB3_BITS]
        sb4 = all_bits[SB2_BITS + SB3_BITS :]
    elif name == "max_fields":
        # All-ones in every SB EXCEPT the 9-bit ITOW field (SB2[13..21]) which
        # is clamped to ITOW=503 (the spec maximum, MSB-first 0b111110111).
        # The 9-bit raw maximum 511 is invalid per LSIS V1.0 §2.4.3.1.6 and
        # would land in TC5 territory (out-of-range), not TC4 (boundary).
        # WN (SB2[0..12]) stays at its 13-bit raw max 8191; all other SB2
        # fields (Health, CED, time-conv) are at all-ones; SB3 + SB4 are at
        # all-ones too.
        sb2 = [1] * SB2_BITS
        sb3 = [1] * SB3_BITS
        sb4 = [1] * SB4_BITS
        for i in range(SB2_ITOW_BITS):
            sb2[SB2_ITOW_OFFSET + i] = (SB2_ITOW_SPEC_MAX >> (SB2_ITOW_BITS - 1 - i)) & 1
    else:
        raise ValueError(f"Unknown canonical-input pattern: {name!r}")

    # FAQ Q21 spare-bit normalisation on SB2[1150:1176].
    for i in range(SB2_SPARE_BITS_LENGTH):
        sb2[SB2_SPARE_BITS_OFFSET + i] = i % 2

    return bytes(sb2) + bytes(sb3) + bytes(sb4)


# ─────────────────────────────── parsing helpers ────────────────────────────

_SECTION_RE = re.compile(
    r"\[(?P<name>[A-Z0-9_]+)\]\s*(?:length:\s*\d+\s*)?hex:\s*(?P<hex>[0-9A-Fa-f]+)",
)


@functools.cache
def parse_codes_hex(path: Path) -> dict[str, str]:
    """Parse a ``codes_prnNNN.hex`` file into a dict of section → uppercase hex.

    Cached: codes/ is read-only across a single CLI invocation, and the L3
    polarity helpers fan out into 4 calls per signal × 10 signals × 4 PRNs.
    """
    text = path.read_text()
    out: dict[str, str] = {}
    for m in _SECTION_RE.finditer(text):
        out[m.group("name")] = m.group("hex").upper()
    return out


def parse_annex3(path: Path) -> list[str]:
    """Parse an Annex 3 reference file into a 210-element list of uppercase hex."""
    txt = path.read_text()
    quoted = re.findall(r'"([0-9A-Fa-f]+)"', txt)
    if quoted:
        return [s.upper() for s in quoted]
    return [line.strip().upper() for line in txt.splitlines() if line.strip()]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────── check-annex3 ───────────────────────────────


def cmd_check_annex3(_args: argparse.Namespace | None = None) -> int:
    del _args
    refs: dict[str, list[str]] = {}
    for section, filename in ANNEX3_FILES.items():
        ref_path = ANNEX3_DIR / filename
        if not ref_path.exists():
            print(f"ERROR: missing reference file {ref_path}", file=sys.stderr)
            return 2
        refs[section] = parse_annex3(ref_path)

    totals = dict.fromkeys(ANNEX3_FILES, 0)
    failures: list[str] = []
    for prn in range(1, 211):
        path = CODES_DIR / f"codes_prn{prn:03d}.hex"
        if not path.exists():
            failures.append(f"PRN {prn}: file missing ({path.name})")
            continue
        sections = parse_codes_hex(path)
        for section in ANNEX3_FILES:
            got = sections.get(section, "")
            want = refs[section][prn - 1]
            if got == want:
                totals[section] += 1
            else:
                failures.append(f"PRN {prn} {section}: mismatch")

    print(
        f"  GOLD_CODE      : {totals['GOLD_CODE']:>3}/210",
        f"  WEIL_PRIMARY   : {totals['WEIL_PRIMARY']:>3}/210",
        f"  WEIL_TERTIARY  : {totals['WEIL_TERTIARY']:>3}/210",
        sep="\n",
    )
    if failures:
        print(f"\nFAIL: {len(failures)} mismatches", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        if len(failures) > 10:
            print(f"  … ({len(failures) - 10} more)", file=sys.stderr)
        return 1
    print("\nOK — all 630 codes bit-exact against Annex 3.")
    return 0


# ─────────────────────────────── check-lans-afs-sim ────────────────────────


def _hex_to_chips(hex_str: str, prepend_zeros: int, chip_count: int) -> bytes:
    """Decode an Annex-3-style hex string back to raw chips (1 byte per chip).

    The encoding rule: chips are packed MSB-first in 4-bit nibbles; for codes
    whose chip count is not a multiple of 4, ``prepend_zeros`` zero bits are
    padded on the MSB side.  This reverses that transformation.
    """
    # Hex → bit string
    bit_str = "".join(f"{int(c, 16):04b}" for c in hex_str)
    total_bits = len(bit_str)
    expected = chip_count + prepend_zeros
    if total_bits != expected:
        msg = (
            f"hex decodes to {total_bits} bits, expected {expected} "
            f"({chip_count} chips + {prepend_zeros} pad)"
        )
        raise ValueError(msg)
    chip_bits = bit_str[prepend_zeros:]
    return bytes(int(b) for b in chip_bits)


def cmd_check_lans_afs_sim(_args: argparse.Namespace | None = None) -> int:
    del _args
    if not LANS_CODES_DIR.is_dir():
        print(
            f"ERROR: {LANS_CODES_DIR} not found. This oracle is optional; "
            f"run 'python validate.py check-annex3' for the normative check.",
            file=sys.stderr,
        )
        return 2

    totals = dict.fromkeys(LANS_FILES, 0)
    failures: list[str] = []
    for prn in range(1, 211):
        path = CODES_DIR / f"codes_prn{prn:03d}.hex"
        if not path.exists():
            failures.append(f"PRN {prn}: {path.name} missing")
            continue
        sections = parse_codes_hex(path)
        for section, (prefix, chip_count) in LANS_FILES.items():
            bin_path = LANS_CODES_DIR / f"{prefix}_prn_{prn:03d}.bin"
            if not bin_path.exists():
                failures.append(f"PRN {prn} {section}: {bin_path.name} missing")
                continue
            hex_str = sections.get(section, "")
            try:
                chips = _hex_to_chips(hex_str, prepend_zeros=2, chip_count=chip_count)
            except ValueError as exc:
                failures.append(f"PRN {prn} {section}: {exc}")
                continue
            ref = bin_path.read_bytes()
            if len(ref) != chip_count:
                failures.append(
                    f"PRN {prn} {section}: {bin_path.name} has {len(ref)} bytes, "
                    f"expected {chip_count}"
                )
                continue
            if chips == ref:
                totals[section] += 1
            else:
                mismatches = sum(1 for a, b in zip(chips, ref, strict=True) if a != b)
                failures.append(f"PRN {prn} {section}: {mismatches}/{chip_count} chip mismatches")

    print(
        f"  Gold (2046 chips) : {totals['GOLD_CODE']:>3}/210",
        f"  Weil (10230 chips): {totals['WEIL_PRIMARY']:>3}/210",
        sep="\n",
    )
    if failures:
        print(f"\nFAIL: {len(failures)} problems", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        if len(failures) > 10:
            print(f"  … ({len(failures) - 10} more)", file=sys.stderr)
        return 1
    print("\nOK — all 420 code dumps bit-exact against LANS-AFS-SIM reference.")
    return 0


# ─────────────────────────────── diff ───────────────────────────────────────


def cmd_diff(args: argparse.Namespace) -> int:
    other = Path(args.other_dir).resolve()
    if not other.is_dir():
        print(f"ERROR: {other} is not a directory", file=sys.stderr)
        return 2

    sections = list(SECTION_LENGTHS)
    match = {s: 0 for s in sections}
    missing_files: list[int] = []
    diffs: list[str] = []

    for prn in range(1, 211):
        name = f"codes_prn{prn:03d}.hex"
        ours = parse_codes_hex(CODES_DIR / name)
        other_path = other / name
        if not other_path.exists():
            missing_files.append(prn)
            continue
        theirs = parse_codes_hex(other_path)
        for section in sections:
            a = ours.get(section, "")
            b = theirs.get(section, "")
            if a and b and a == b:
                match[section] += 1
            elif a != b:
                diffs.append(f"PRN {prn} {section}: ours={a[:16]}… theirs={b[:16]}…")

    total = 210 - len(missing_files)
    print(f"Compared {total}/210 PRNs (missing: {len(missing_files)})")
    for s in sections:
        print(f"  {s:<14}: {match[s]:>3}/{total}")
    if diffs:
        print(f"\n{len(diffs)} section-level differences (first 10):", file=sys.stderr)
        for d in diffs[:10]:
            print(f"  {d}", file=sys.stderr)
        return 1
    if missing_files:
        return 1
    print("\nOK — bit-exact match.")
    return 0


# ─────────────────────────────── frame helpers ─────────────────────────────


def _parse_frame_header(data: bytes, source: str) -> tuple[dict[str, object], list[str]]:
    """Parse the 64-byte frame header. Returns (fields, errors)."""
    errors: list[str] = []
    if len(data) < FRAME_HEADER_LEN:
        errors.append(f"{source}: file shorter than 64-byte header")
        return {}, errors

    magic = data[0:8]
    version = int.from_bytes(data[8:12], "little")
    frame_length = int.from_bytes(data[12:16], "little")
    prn = int.from_bytes(data[16:20], "little")
    timestamp = int.from_bytes(data[20:28], "little", signed=True)

    fields: dict[str, object] = {
        "magic": magic,
        "version": version,
        "frame_length": frame_length,
        "prn": prn,
        "timestamp": timestamp,
    }

    if magic != FRAME_MAGIC:
        errors.append(f"{source}: magic={magic!r}, expected {FRAME_MAGIC!r}")
    if version != FRAME_VERSION:
        errors.append(f"{source}: version={version}, expected {FRAME_VERSION}")
    if frame_length != FRAME_PAYLOAD_LEN:
        errors.append(f"{source}: frame_length={frame_length}, expected {FRAME_PAYLOAD_LEN}")
    return fields, errors


def _check_frame_payload(payload: bytes, source: str) -> list[str]:
    """Validate the 6000-symbol payload structurally. Returns list of errors."""
    errors: list[str] = []
    if len(payload) != FRAME_PAYLOAD_LEN:
        errors.append(f"{source}: payload is {len(payload)} bytes, expected {FRAME_PAYLOAD_LEN}")
        return errors
    # Symbol-domain values must be {0, 1}
    bad = sum(1 for b in payload if b not in (0, 1))
    if bad:
        errors.append(f"{source}: {bad} symbols are not 0/1")
    # Sync prefix
    if payload[:68] != EXPECTED_SYNC_BITS:
        errors.append(
            f"{source}: first 68 symbols do not match sync pattern "
            f"0x{SYNC_PATTERN_HEX} (LSIS V1.0 §2.4.1)"
        )
    return errors


# ─────────────────────────────── check-frames ──────────────────────────────


def cmd_check_frames(_args: argparse.Namespace | None = None) -> int:
    del _args
    if not FRAMES_DIR.is_dir():
        print(f"ERROR: {FRAMES_DIR} not found", file=sys.stderr)
        return 2

    failures: list[str] = []
    passed = 0
    for filename, expected_prn, *_ in FRAME_TEST_VECTORS:
        path = FRAMES_DIR / filename
        if not path.exists():
            failures.append(f"{filename}: missing")
            continue
        data = path.read_bytes()
        if len(data) != FRAME_FILE_LEN:
            failures.append(
                f"{filename}: file is {len(data)} bytes, expected {FRAME_FILE_LEN} "
                f"(64 header + {FRAME_PAYLOAD_LEN} payload)"
            )
            continue
        fields, header_errors = _parse_frame_header(data[:FRAME_HEADER_LEN], filename)
        frame_errors = list(header_errors)
        if fields.get("prn") != expected_prn:
            frame_errors.append(
                f"{filename}: header PRN={fields.get('prn')}, expected {expected_prn}"
            )
        frame_errors.extend(_check_frame_payload(data[FRAME_HEADER_LEN:], filename))
        failures.extend(frame_errors)
        if not frame_errors:
            passed += 1

    total = len(FRAME_TEST_VECTORS)
    print(f"  Structural checks: {passed:>2}/{total}")
    if failures:
        print(f"\nFAIL: {len(failures)} problems", file=sys.stderr)
        for msg in failures[:20]:
            print(f"  {msg}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  … ({len(failures) - 20} more)", file=sys.stderr)
        return 1
    print(f"\nOK — all {total} frames pass spec structural checks.")
    return 0


# ─────────────────────────────── check-lans-afs-sim-frames ─────────────────


def cmd_check_lans_afs_sim_frames(_args: argparse.Namespace | None = None) -> int:
    del _args
    if not FRAMES_DIR.is_dir():
        print(f"ERROR: {FRAMES_DIR} not found", file=sys.stderr)
        return 2
    if not LANS_FRAMES_DIR.is_dir():
        print(
            f"ERROR: {LANS_FRAMES_DIR} not found. This oracle is optional; "
            f"run 'python validate.py check-frames' for the structural check.",
            file=sys.stderr,
        )
        return 2

    failures: list[str] = []
    passed = 0
    for filename, *_ in FRAME_TEST_VECTORS:
        ours_path = FRAMES_DIR / filename
        lans_path = LANS_FRAMES_DIR / _lans_frame_name(filename)
        if not ours_path.exists():
            failures.append(f"{filename}: missing on our side")
            continue
        if not lans_path.exists():
            failures.append(f"{lans_path.name}: missing")
            continue
        ours_data = ours_path.read_bytes()
        if len(ours_data) != FRAME_FILE_LEN:
            failures.append(
                f"{filename}: file is {len(ours_data)} bytes, expected {FRAME_FILE_LEN}"
            )
            continue
        ours_payload = ours_data[FRAME_HEADER_LEN:]
        lans_payload = lans_path.read_bytes()
        if len(lans_payload) != FRAME_PAYLOAD_LEN:
            failures.append(
                f"{lans_path.name}: {len(lans_payload)} bytes, expected {FRAME_PAYLOAD_LEN}"
            )
            continue
        if ours_payload == lans_payload:
            passed += 1
        else:
            mismatches = sum(1 for a, b in zip(ours_payload, lans_payload, strict=True) if a != b)
            failures.append(f"{filename}: {mismatches}/{FRAME_PAYLOAD_LEN} symbol mismatches")

    total = len(FRAME_TEST_VECTORS)
    print(f"  Bit-exact vs LANS-AFS-SIM: {passed:>2}/{total}")
    if failures:
        print(f"\nFAIL: {len(failures)} problems", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        return 1
    print(f"\nOK — all {total} frames bit-exact against LANS-AFS-SIM reference.")
    return 0


# ─────────────────────────────── diff-frames ───────────────────────────────


def cmd_diff_frames(args: argparse.Namespace) -> int:
    other = Path(args.other_dir).resolve()
    if not other.is_dir():
        print(f"ERROR: {other} is not a directory", file=sys.stderr)
        return 2

    failures: list[str] = []
    matches = 0
    missing = 0
    for filename, expected_prn, *_ in FRAME_TEST_VECTORS:
        ours = (FRAMES_DIR / filename).read_bytes()[FRAME_HEADER_LEN:]
        their_path = other / filename
        if not their_path.exists():
            missing += 1
            failures.append(f"{filename}: missing in {other}")
            continue
        their_data = their_path.read_bytes()
        # Accept either (a) full 6064-byte file with header, or (b) raw 6000-byte payload.
        if len(their_data) == FRAME_FILE_LEN:
            their_fields, header_errors = _parse_frame_header(
                their_data[:FRAME_HEADER_LEN], filename
            )
            if their_fields.get("prn") != expected_prn:
                header_errors.append(
                    f"{filename}: prn={their_fields.get('prn')}, expected {expected_prn}"
                )
            if header_errors:
                failures.extend(header_errors)
                continue
            their_payload = their_data[FRAME_HEADER_LEN:]
        elif len(their_data) == FRAME_PAYLOAD_LEN:
            their_payload = their_data
        else:
            failures.append(
                f"{filename}: their file is {len(their_data)} bytes, "
                f"expected {FRAME_FILE_LEN} or {FRAME_PAYLOAD_LEN}"
            )
            continue
        if ours == their_payload:
            matches += 1
        else:
            mismatches = sum(1 for a, b in zip(ours, their_payload, strict=True) if a != b)
            failures.append(f"{filename}: {mismatches}/{FRAME_PAYLOAD_LEN} symbol mismatches")

    total = len(FRAME_TEST_VECTORS)
    print(f"Compared {total - missing}/{total} frames (missing: {missing})")
    print(f"  Bit-exact: {matches:>2}/{total}")
    if failures:
        print(f"\n{len(failures)} differences (first 10):", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        return 1
    print("\nOK — bit-exact match.")
    return 0


# ─────────────────────────────── check-canonical-inputs ────────────────────


def _locate_first_diff(diff_offset: int) -> str:
    """Return a 'SB{n} bit {k}' label for a byte offset in the SB2||SB3||SB4 stream."""
    if diff_offset < SB2_BITS:
        return f"SB2 bit {diff_offset}"
    if diff_offset < SB2_BITS + SB3_BITS:
        return f"SB3 bit {diff_offset - SB2_BITS}"
    return f"SB4 bit {diff_offset - SB2_BITS - SB3_BITS}"


def cmd_check_canonical_inputs(_args: argparse.Namespace | None = None) -> int:
    """Verify shipped canonical-input files reproduce from the documented patterns."""
    del _args
    if not INPUTS_DIR.is_dir():
        print(f"ERROR: {INPUTS_DIR} not found", file=sys.stderr)
        return 2

    failures: list[str] = []
    passed = 0
    for filename, pattern in INPUT_TEST_VECTORS:
        path = INPUTS_DIR / filename
        if not path.exists():
            failures.append(f"{filename}: missing")
            continue
        actual = path.read_bytes()
        expected = _build_canonical_input(pattern)
        if actual == expected:
            passed += 1
            continue
        if len(actual) != INPUT_BYTE_COUNT:
            failures.append(f"{filename}: file is {len(actual)} bytes, expected {INPUT_BYTE_COUNT}")
            continue
        mismatches = sum(1 for a, b in zip(actual, expected, strict=True) if a != b)
        first = next(i for i, (a, b) in enumerate(zip(actual, expected, strict=True)) if a != b)
        failures.append(
            f"{filename}: {mismatches}/{INPUT_BYTE_COUNT} bit mismatches "
            f"vs {pattern!r} reference (first at {_locate_first_diff(first)})"
        )

    total = len(INPUT_TEST_VECTORS)
    print(f"  Canonical inputs: {passed:>2}/{total}")
    if failures:
        print(f"\nFAIL: {len(failures)} mismatches", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        return 1
    print(f"\nOK — all {total} canonical input files reproduce from documented patterns.")
    return 0


# ─────────────────────────────── diff-inputs ───────────────────────────────


def cmd_diff_inputs(args: argparse.Namespace) -> int:
    """Compare a directory of canonical-input files against ours."""
    other = Path(args.other_dir).resolve()
    if not other.is_dir():
        print(f"ERROR: {other} is not a directory", file=sys.stderr)
        return 2

    failures: list[str] = []
    matches = 0
    missing = 0
    for filename, _pattern in INPUT_TEST_VECTORS:
        ours = (INPUTS_DIR / filename).read_bytes()
        their_path = other / filename
        if not their_path.exists():
            missing += 1
            failures.append(f"{filename}: missing in {other}")
            continue
        their_data = their_path.read_bytes()
        if len(their_data) != INPUT_BYTE_COUNT:
            failures.append(
                f"{filename}: their file is {len(their_data)} bytes, expected {INPUT_BYTE_COUNT}"
            )
            continue
        if ours == their_data:
            matches += 1
            continue
        mismatches = sum(1 for a, b in zip(ours, their_data, strict=True) if a != b)
        first = next(i for i, (a, b) in enumerate(zip(ours, their_data, strict=True)) if a != b)
        failures.append(
            f"{filename}: {mismatches}/{INPUT_BYTE_COUNT} bit mismatches "
            f"(first at {_locate_first_diff(first)})"
        )

    total = len(INPUT_TEST_VECTORS)
    print(f"Compared {total - missing}/{total} canonical inputs (missing: {missing})")
    print(f"  Bit-exact: {matches:>2}/{total}")
    if failures:
        print(f"\n{len(failures)} differences (first 10):", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        return 1
    print("\nOK — bit-exact match.")
    return 0


# ─────────────────────────────── build-canonical-inputs ────────────────────


def cmd_build_canonical_inputs(_args: argparse.Namespace | None = None) -> int:
    """Maintainer command: regenerate inputs/ from the documented patterns."""
    del _args
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    for filename, pattern in INPUT_TEST_VECTORS:
        data = _build_canonical_input(pattern)
        assert len(data) == INPUT_BYTE_COUNT
        (INPUTS_DIR / filename).write_bytes(data)
    print(f"Wrote {len(INPUT_TEST_VECTORS)} canonical input files to {INPUTS_DIR}.")
    return 0


# ─────────────────────────────── Level 3 constants ─────────────────────────
#
# Per references/interoperability.pdf, Signal Export Format:
#   Header (128 bytes):
#     Magic:       "LSISIQ\0\0" (8 bytes)
#     Version:     uint32 LE = 1 (4 bytes)
#     Sample rate: float64 LE  (8 bytes)
#     Duration:    float64 LE seconds (8 bytes)
#     PRN:         uint32 LE  (4 bytes)
#     Format:      "float32" zero-padded to 16 bytes
#     Reserved:    80 zero bytes
#   Data: float32 I/Q interleaved [I0, Q0, I1, Q1, …]
#
# Spec baseline (LSIS V1.0 §4): sample_rate = 10.23 MHz, duration = 12 s.
# Total file size = 128 + 12 × 10 230 000 × 2 × 4 = 982 080 128 bytes.

SIGNAL_MAGIC = b"LSISIQ\x00\x00"
SIGNAL_VERSION = 1
SIGNAL_HEADER_LEN = 128
SIGNAL_SAMPLE_RATE = 10_230_000.0
SIGNAL_DURATION_S = 12.0
SIGNAL_FORMAT = b"float32"  # padded with NULs to 16 bytes in the header
SIGNAL_FORMAT_FIELD_LEN = 16
SIGNAL_FORMAT_OFFSET = 32  # bytes 32:48
SIGNAL_RESERVED_OFFSET = 48
SIGNAL_RESERVED_LEN = 80
SIGNAL_BYTES_PER_SAMPLE_PAIR = 8  # float32 × 2 (I+Q)
SIGNAL_TOTAL_FILE_LEN = (
    SIGNAL_HEADER_LEN + int(SIGNAL_SAMPLE_RATE * SIGNAL_DURATION_S) * SIGNAL_BYTES_PER_SAMPLE_PAIR
)

# I-channel chip rate is 1.023 Mchip/s (LSIS V1.0 §4); at 10.23 MHz sample rate,
# each I-chip spans 10 consecutive samples by nearest-neighbour upsampling.
SIGNAL_I_SAMPLES_PER_CHIP = 10

# (filename, expected_prn).  The 6 entries map 1:1 to FRAME_TEST_VECTORS — each
# signal is generated from the matching L2 frame (same PRN, same nav data).
#
# Each entry is (signal_filename, expected_prn, source_frame_filename).  The
# 5 standard Test Messages (TM1–TM5) all use PRN 1; the additional
# ``signal_prn12_baseline_12s.iq.gz`` covers TC2's "high end of the legal
# PRN range" using the same nav data as TM1 (frame_message_1.bin) modulated
# at PRN 12 — the largest PRN with a defined AFS-Q matched-code phase
# assignment per LSIS V1.0 Annex 3 Table 11.
#
# The L2 ``frame_boundary.bin`` (PRN=210) has no L3 counterpart.  PRN 13–210
# are reserved for the future LunaNet operational deployment and have no
# defined matched-code assignment yet, so the interop doc's Test Case 2
# itself scopes L3 PRN coverage to "PRN: 1-12 (Table 11)".
SIGNAL_TEST_VECTORS: list[tuple[str, int, str]] = [
    ("signal_message_1_12s.iq.gz", 1, "frame_message_1.bin"),
    ("signal_message_2_12s.iq.gz", 1, "frame_message_2.bin"),
    ("signal_message_3_12s.iq.gz", 1, "frame_message_3.bin"),
    ("signal_message_4_12s.iq.gz", 1, "frame_message_4.bin"),
    ("signal_message_5_12s.iq.gz", 1, "frame_message_5.bin"),
    # Mid-range PRNs covering the two AFS-Q secondary indices not exercised
    # by PRN 1 (S0) or PRN 12 (S3): PRN 2 → S1, PRN 3 → S2.  Together with
    # PRN 1 and PRN 12, all four secondary codes from LSIS V1.0 §4.4.2 are
    # exercised at L3.
    ("signal_prn2_baseline_12s.iq.gz", 2, "frame_message_1.bin"),
    ("signal_prn3_baseline_12s.iq.gz", 3, "frame_message_1.bin"),
    ("signal_prn12_baseline_12s.iq.gz", 12, "frame_message_1.bin"),
    # TC4 boundary frame (FID=3, TOI=99) modulated at PRN 12 — exercises
    # the max-BCH-SB1-codeword corner of the spec at L3.  The L2
    # ``frame_boundary.bin`` itself uses PRN=210 (no defined matched-code
    # phase), so we substitute PRN 12 — the highest legal interim PRN —
    # while keeping the boundary FID/TOI bits in SB1 and the
    # alternating-start-with-0 pattern in SB2/SB3/SB4.  A clean L4
    # cross-decode of this signal at PRN 12 must recover the SB1 BCH
    # codeword for (FID=3, TOI=99), proving the encoder's behaviour at
    # field maxima end-to-end.
    ("signal_boundary_at_prn12_12s.iq.gz", 12, "frame_boundary.bin"),
    # TC4 SB2-field maxima (WN=8191 in SB2[0..12], ITOW=503 in SB2[13..21])
    # modulated at PRN 12.  Pairs with the v0.2.2
    # ``frame_boundary_max_fields.bin`` to propagate the SB2-field maxima
    # coverage through to L3.  A clean L4 cross-decode must recover those
    # maxima alongside the FID=3 / TOI=99 from BCH SB1.
    ("signal_boundary_max_fields_at_prn12_12s.iq.gz", 12, "frame_boundary_max_fields.bin"),
]


def _read_signal_bytes(path: Path) -> bytes:
    """Read a possibly-gzipped signal file into memory (gunzipped if .gz)."""
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as f:
            return f.read()
    return path.read_bytes()


# ─────────────────────────────── Level 4 constants ─────────────────────────
#
# Per references/interoperability.pdf "Level 4: Decoding Interoperability",
# the pass criterion is "Decoded data matches original input exactly".  Our
# bundled cross-decode oracle is PocketSDR-AFS @ pinned SHA, with a small
# bundled patch that emits the raw 6000 hard-decision symbols per detected
# AFS-D frame (sync prefix + SB1 + interleaved SB2/SB3/SB4) before
# deinterleaving.  Those 6000 symbols are the exact byte sequence shipped
# at frames/frame_*.bin[64:6064] (after the 64-byte LSISAFS header).
#
# This module's check-decode subcommand verifies the shipped decoded
# outputs round-trip; the upstream rebuild + cross-decode is in
# references/pocketsdr-afs/harnesses/verify_pocketsdr_decode.py.

DECODED_FILE_LEN = FRAME_PAYLOAD_LEN  # 6000 — one byte per symbol

# Post-FEC oracle: each frame's SB2 + SB3 + SB4 LDPC-decoded data bits
# (no CRC trailer) concatenated.  Same layout as inputs/*_input.bin.
DECODED_FEC_FILE_LEN = INPUT_BYTE_COUNT  # 2868 = 1176 + 846 + 846


def _decoded_filename_for(signal_filename: str) -> str:
    """signal_*_12s.iq.gz → decoded_signal_*_12s.bin (channel-symbol oracle)"""
    stem = signal_filename.removesuffix(".gz").removesuffix(".iq")
    return f"decoded_{stem}.bin"


def _decoded_fec_filename_for(signal_filename: str) -> str:
    """signal_*_12s.iq.gz → decoded_fec_signal_*_12s.bin (post-FEC oracle)"""
    stem = signal_filename.removesuffix(".gz").removesuffix(".iq")
    return f"decoded_fec_{stem}.bin"


def _input_filename_for(source_frame: str) -> str:
    """frame_*.bin → frame_*_input.bin (canonical pre-encode bytes)."""
    return source_frame.removesuffix(".bin") + "_input.bin"


# (signal, prn, source_frame) is reused from SIGNAL_TEST_VECTORS — the L4
# decoded outputs are 1:1 with the L3 signals.  We do not duplicate the
# table; instead, we derive the (decoded_filename, source_frame) pairs
# at use sites.  This guarantees the L3 signal coverage and L4 decode
# coverage stay in lock-step automatically.


def _parse_iq_header(data: bytes, source: str) -> tuple[dict[str, object], list[str]]:
    """Parse the 128-byte LSISIQ header. Returns (fields, errors)."""
    errors: list[str] = []
    if len(data) < SIGNAL_HEADER_LEN:
        errors.append(f"{source}: file shorter than 128-byte header")
        return {}, errors

    magic = data[0:8]
    version = int.from_bytes(data[8:12], "little")
    sample_rate = struct.unpack("<d", data[12:20])[0]
    duration = struct.unpack("<d", data[20:28])[0]
    prn = int.from_bytes(data[28:32], "little")
    fmt_field = data[SIGNAL_FORMAT_OFFSET : SIGNAL_FORMAT_OFFSET + SIGNAL_FORMAT_FIELD_LEN]
    reserved = data[SIGNAL_RESERVED_OFFSET : SIGNAL_RESERVED_OFFSET + SIGNAL_RESERVED_LEN]

    fields: dict[str, object] = {
        "magic": magic,
        "version": version,
        "sample_rate": sample_rate,
        "duration": duration,
        "prn": prn,
        "format": fmt_field,
        "reserved": reserved,
    }

    if magic != SIGNAL_MAGIC:
        errors.append(f"{source}: magic={magic!r}, expected {SIGNAL_MAGIC!r}")
    if version != SIGNAL_VERSION:
        errors.append(f"{source}: version={version}, expected {SIGNAL_VERSION}")
    if sample_rate != SIGNAL_SAMPLE_RATE:
        errors.append(f"{source}: sample_rate={sample_rate!r}, expected {SIGNAL_SAMPLE_RATE!r}")
    if duration != SIGNAL_DURATION_S:
        errors.append(f"{source}: duration={duration!r}, expected {SIGNAL_DURATION_S!r}")
    # Format field must start with b"float32" and the remaining bytes must be NUL.
    expected_fmt = SIGNAL_FORMAT + b"\x00" * (SIGNAL_FORMAT_FIELD_LEN - len(SIGNAL_FORMAT))
    if fmt_field != expected_fmt:
        errors.append(f"{source}: format field={fmt_field!r}, expected {expected_fmt!r}")
    if reserved != b"\x00" * SIGNAL_RESERVED_LEN:
        errors.append(f"{source}: reserved bytes are not all zero")
    return fields, errors


def _decode_chip_bits(hex_str: str, prepend_zeros: int, chip_count: int) -> list[int]:
    """Decode the Annex-3-style hex string back to a list of {0,1} chips.

    Identical algorithm to ``_hex_to_chips`` but returns a list-of-int (avoids
    re-allocation for callers that only need the first few chips).
    """
    bit_str = "".join(f"{int(c, 16):04b}" for c in hex_str)
    expected = chip_count + prepend_zeros
    if len(bit_str) != expected:
        msg = f"hex decodes to {len(bit_str)} bits, expected {expected}"
        raise ValueError(msg)
    return [int(b) for b in bit_str[prepend_zeros:]]


def _gold_chips_for_prn(prn: int, n: int) -> list[int]:
    """Return the first n Gold-code chips for a PRN (from codes/codes_prnNNN.hex)."""
    sections = parse_codes_hex(CODES_DIR / f"codes_prn{prn:03d}.hex")
    return _decode_chip_bits(sections["GOLD_CODE"], prepend_zeros=2, chip_count=2046)[:n]


def _weil_primary_chip_0(prn: int) -> int:
    """Return the first chip of the AFS-Q Weil-10230 primary code for a PRN."""
    sections = parse_codes_hex(CODES_DIR / f"codes_prn{prn:03d}.hex")
    return _decode_chip_bits(sections["WEIL_PRIMARY"], prepend_zeros=2, chip_count=10230)[0]


def _weil_tertiary_chip_0(prn: int) -> int:
    """Return the first chip of the AFS-Q Weil-1500 tertiary code for a PRN."""
    sections = parse_codes_hex(CODES_DIR / f"codes_prn{prn:03d}.hex")
    return _decode_chip_bits(sections["WEIL_TERTIARY"], prepend_zeros=0, chip_count=1500)[0]


def _secondary_chip_0(prn: int) -> int:
    """Return the first chip of the AFS-Q secondary code assigned to a PRN.

    Per LSIS V1.0 §4.4.2 / Annex 3 Table 2: PRN-to-secondary index assignment
    is k = (prn − 1) mod 4 (linear for PRN 1–4, periodic for higher PRNs).
    Each secondary code is 4 chips, MSB-first in the single-nibble hex value.
    """
    sec_idx = (prn - 1) % 4
    sections = parse_codes_hex(CODES_DIR / f"codes_prn{prn:03d}.hex")
    sec_hex = sections[f"SECONDARY_S{sec_idx}"]  # one hex digit
    sec_bits = _decode_chip_bits(sec_hex, prepend_zeros=0, chip_count=4)
    return sec_bits[0]


def _frame_symbol(filename: str, symbol_idx: int) -> int:
    """Return frame symbol ``symbol_idx`` (0..5999) of a shipped frame file."""
    data = (FRAMES_DIR / filename).read_bytes()
    assert len(data) == FRAME_FILE_LEN
    return data[FRAME_HEADER_LEN + symbol_idx]


# I-channel sample rate / symbol rate = 10.23 MHz / 500 sym/s = 20460 samples/symbol.
SIGNAL_I_SAMPLES_PER_SYMBOL = 20460

# First frame symbol that distinguishes the 5 standard test messages: symbols
# 0..67 are the spec sync prefix (identical across all frames) and 68..119 are
# BCH(51,8) of (FID=0, TOI=0) (identical across all 5 message frames since
# they share FID/TOI).  Symbol 120 is the first interleaved SB2/SB3/SB4 LDPC
# symbol — it differs across the 5 messages.  Probing the polarity at the
# start of symbol 120 catches interleaver / LDPC bit-ordering errors that the
# sync-prefix probe at sample 0 cannot.
SIGNAL_DISTINGUISHING_SYMBOL_IDX = 120


def _expected_i_sample(frame_sym: int, gold_chip: int) -> float:
    """BPSK polarity: I = (1 − 2·sym) · (1 − 2·chip) per FAQ Q19."""
    return float((1 - 2 * frame_sym) * (1 - 2 * gold_chip))


def _expected_q_sample(weil_chip: int, tert_chip: int, sec_chip: int) -> float:
    """Q-channel BPSK polarity: matched code = Weil ⊕ Tert ⊕ Sec, mapped per FAQ Q19."""
    return float((1 - 2 * weil_chip) * (1 - 2 * tert_chip) * (1 - 2 * sec_chip))


def _read_iq_pair(data: bytes, sample_idx: int) -> tuple[float, float]:
    """Read one (I, Q) pair from the float32 payload of a parsed signal blob."""
    offset = SIGNAL_HEADER_LEN + sample_idx * SIGNAL_BYTES_PER_SAMPLE_PAIR
    i_val, q_val = struct.unpack("<ff", data[offset : offset + 8])
    return i_val, q_val


def _check_signal_payload(data: bytes, prn: int, frame_filename: str, source: str) -> list[str]:
    """Validate sample-domain rules: range, file size, first-chip polarity."""
    errors: list[str] = []
    if len(data) != SIGNAL_TOTAL_FILE_LEN:
        errors.append(
            f"{source}: file is {len(data)} bytes, expected {SIGNAL_TOTAL_FILE_LEN} "
            f"(128 header + 12 s × 10.23 MHz × 8 B/sample-pair)"
        )
        return errors

    frame_sym_0 = _frame_symbol(frame_filename, 0)
    frame_sym_dist = _frame_symbol(frame_filename, SIGNAL_DISTINGUISHING_SYMBOL_IDX)
    gold_chips = _gold_chips_for_prn(prn, 4)
    weil0 = _weil_primary_chip_0(prn)
    tert0 = _weil_tertiary_chip_0(prn)
    sec0 = _secondary_chip_0(prn)

    expected_q0 = _expected_q_sample(weil0, tert0, sec0)
    # Probe samples within symbol 0 at chip-rate boundaries (samples 0/10/20/30)
    # — exercises Gold[0..3] and confirms the 10-samples-per-chip upsampling at
    # 10.23 MHz.  This part of the I-channel is dominated by the sync prefix
    # (symbol 0 = sync_bit_0 = 1 for every shipped frame), so it cannot
    # distinguish the 5 messages from each other.
    sym0_probe_indices = [k * SIGNAL_I_SAMPLES_PER_CHIP for k in range(4)]

    # Probe within symbol 120 — the first interleaver-output symbol, which
    # differs across the 5 standard test messages.  This catches interleaver
    # / LDPC bit-ordering errors that pass the sync-prefix probe.  Sample
    # index 120 × 20460 = 2_455_200; we re-use Gold[0..3] because the AFS-I
    # Gold code repeats every symbol (one full 2046-chip period per epoch).
    sym_dist_base = SIGNAL_DISTINGUISHING_SYMBOL_IDX * SIGNAL_I_SAMPLES_PER_SYMBOL
    sym_dist_probe_indices = [sym_dist_base + k * SIGNAL_I_SAMPLES_PER_CHIP for k in range(4)]

    probes = [
        (sym0_probe_indices, frame_sym_0, "sync_bit_0", 0),
        (sym_dist_probe_indices, frame_sym_dist, "sym_120", SIGNAL_DISTINGUISHING_SYMBOL_IDX),
    ]
    for indices, frame_sym, label, sym_idx in probes:
        for k, sample_idx in enumerate(indices):
            i_got, q_got = _read_iq_pair(data, sample_idx)
            # Range check (strict ±1.0 BPSK).  Run only on the probe samples
            # so this function is O(1) per file; full-stream range is the
            # caller's concern.
            for axis, val in (("I", i_got), ("Q", q_got)):
                if val not in (1.0, -1.0):
                    errors.append(f"{source}: sample {sample_idx} {axis}={val!r}, expected ±1.0")
            gold_chip = gold_chips[k]
            expected_i = _expected_i_sample(frame_sym, gold_chip)
            if i_got != expected_i:
                errors.append(
                    f"{source}: I[{sample_idx}]={i_got!r}, expected {expected_i!r} "
                    f"(symbol {sym_idx}={frame_sym} [{label}], Gold[{k}]={gold_chip})"
                )
            if sample_idx == 0 and q_got != expected_q0:
                errors.append(
                    f"{source}: Q[0]={q_got!r}, expected {expected_q0!r} "
                    f"(Weil[0]={weil0}, Tert[0]={tert0}, Sec[0]={sec0}, "
                    f"sec_idx={(prn - 1) % 4})"
                )
    return errors


# float32 ±1.0 in little-endian byte form: +1.0 = 00 00 80 3F, -1.0 = 00 00 80 BF.
# These patterns cannot match at unaligned 4-byte offsets when the surrounding
# samples are also ±1.0 (the prev sample ends with 0x3F or 0xBF and the next
# starts with 0x00, so any 4-byte window straddling the boundary contains a
# 0x3F|0xBF byte where the pattern requires 0x00).  We exploit that to scan a
# 982 MB payload via bytes.count (C-level Boyer-Moore) in well under a second
# instead of unpacking 245M floats.
_FLOAT32_PLUS_ONE_LE = b"\x00\x00\x80\x3f"
_FLOAT32_MINUS_ONE_LE = b"\x00\x00\x80\xbf"


def _check_signal_full_range(data: bytes, source: str) -> list[str]:
    """Walk every float32 sample and confirm strict ±1.0 BPSK.

    Fast path (numpy present): zero-copy uint32 view + vectorised mask;
    ~0.3 s per 982 MB payload.

    Fallback path (stdlib only): byte-level Boyer-Moore count of the two
    valid 4-byte little-endian ±1.0 patterns; ~3 s per 982 MB payload.
    Cross-boundary false matches cannot occur because ±1.0 always begins
    with two zero bytes, so any 4-byte window straddling a sample
    boundary contains a 0x3F or 0xBF byte where the pattern requires
    0x00.

    Both paths return identical error reports on failure (count + first
    bad sample localised to ``(sample, channel, value)``).
    """
    errors: list[str] = []
    payload = data[SIGNAL_HEADER_LEN:]
    n_floats = len(payload) // 4
    if not n_floats:
        return errors

    plus_u32 = int.from_bytes(_FLOAT32_PLUS_ONE_LE, "little")
    minus_u32 = int.from_bytes(_FLOAT32_MINUS_ONE_LE, "little")

    if _np is not None:
        arr_np = _np.frombuffer(payload, dtype="<u4")
        bad_mask = (arr_np != plus_u32) & (arr_np != minus_u32)
        bad = int(bad_mask.sum())
        if bad == 0:
            return errors
        first_bad_idx = int(_np.argmax(bad_mask))
    else:
        n_pos = payload.count(_FLOAT32_PLUS_ONE_LE)
        n_neg = payload.count(_FLOAT32_MINUS_ONE_LE)
        bad = n_floats - n_pos - n_neg
        if bad == 0:
            return errors
        # array("I") is the unsigned-int typecode; Python guarantees ≥2 bytes
        # but the float32 view requires exactly 4.  True on every supported
        # 64-bit platform (CPython on x86_64 / ARM64 macOS / Linux / Windows).
        assert array.array("I").itemsize == 4
        arr = array.array("I")
        arr.frombytes(payload)
        first_bad_idx = next(i for i, w in enumerate(arr) if w not in (plus_u32, minus_u32))

    bad_bytes = payload[first_bad_idx * 4 : first_bad_idx * 4 + 4]
    (bad_val,) = struct.unpack("<f", bad_bytes)
    errors.append(
        f"{source}: {bad} of {n_floats} samples not ±1.0 "
        f"(first at sample {first_bad_idx // 2} "
        f"{'I' if first_bad_idx % 2 == 0 else 'Q'}={bad_val!r})"
    )
    return errors


# ─────────────────────────────── check-signals ─────────────────────────────


def cmd_check_signals(_args: argparse.Namespace | None = None) -> int:
    """Validate every shipped signal file structurally + first-chip polarity."""
    del _args
    if not SIGNALS_DIR.is_dir():
        print(f"ERROR: {SIGNALS_DIR} not found", file=sys.stderr)
        return 2

    failures: list[str] = []
    passed = 0
    for filename, expected_prn, source_frame in SIGNAL_TEST_VECTORS:
        path = SIGNALS_DIR / filename
        if not path.exists():
            failures.append(f"{filename}: missing")
            continue
        try:
            data = _read_signal_bytes(path)
        except OSError as exc:
            failures.append(f"{filename}: read error ({exc})")
            continue
        fields, header_errors = _parse_iq_header(data[:SIGNAL_HEADER_LEN], filename)
        signal_errors = list(header_errors)
        if fields.get("prn") != expected_prn:
            signal_errors.append(
                f"{filename}: header PRN={fields.get('prn')}, expected {expected_prn}"
            )
        signal_errors.extend(_check_signal_payload(data, expected_prn, source_frame, filename))
        if len(data) == SIGNAL_TOTAL_FILE_LEN:
            signal_errors.extend(_check_signal_full_range(data, filename))
        failures.extend(signal_errors)
        if not signal_errors:
            passed += 1

    total = len(SIGNAL_TEST_VECTORS)
    print(f"  Structural + first-chip polarity: {passed:>2}/{total}")
    if failures:
        print(f"\nFAIL: {len(failures)} problems", file=sys.stderr)
        for msg in failures[:20]:
            print(f"  {msg}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  … ({len(failures) - 20} more)", file=sys.stderr)
        return 1
    print(f"\nOK — all {total} signals pass structural and first-chip polarity checks.")
    return 0


# ─────────────────────────────── diff-signals ──────────────────────────────


def cmd_diff_signals(args: argparse.Namespace) -> int:
    """Compare a directory of L3 signal vectors against ours, byte-by-byte."""
    other = Path(args.other_dir).resolve()
    if not other.is_dir():
        print(f"ERROR: {other} is not a directory", file=sys.stderr)
        return 2

    failures: list[str] = []
    matches = 0
    missing = 0
    for filename, _expected_prn, _source_frame in SIGNAL_TEST_VECTORS:
        ours_path = SIGNALS_DIR / filename
        # Accept either .iq.gz or .iq on the user's side.
        candidates = [other / filename, other / filename.removesuffix(".gz")]
        their_path = next((p for p in candidates if p.exists()), None)
        if their_path is None:
            missing += 1
            failures.append(f"{filename}: missing in {other}")
            continue
        try:
            ours_bytes = _read_signal_bytes(ours_path)
            their_bytes = _read_signal_bytes(their_path)
        except OSError as exc:
            failures.append(f"{filename}: read error ({exc})")
            continue
        if len(their_bytes) != SIGNAL_TOTAL_FILE_LEN:
            failures.append(
                f"{filename}: their file is {len(their_bytes)} bytes, "
                f"expected {SIGNAL_TOTAL_FILE_LEN}"
            )
            continue
        if ours_bytes == their_bytes:
            matches += 1
            continue
        # Find the first differing byte; map to a sample index for the report.
        first = next(
            i for i, (a, b) in enumerate(zip(ours_bytes, their_bytes, strict=True)) if a != b
        )
        if first < SIGNAL_HEADER_LEN:
            location = f"header byte {first}"
        else:
            sample_idx = (first - SIGNAL_HEADER_LEN) // 4 // 2
            channel = "I" if ((first - SIGNAL_HEADER_LEN) // 4) % 2 == 0 else "Q"
            location = f"sample {sample_idx} {channel}-byte"
        failures.append(f"{filename}: first byte mismatch at {location} (offset {first})")

    total = len(SIGNAL_TEST_VECTORS)
    print(f"Compared {total - missing}/{total} signals (missing: {missing})")
    print(f"  Bit-exact: {matches:>2}/{total}")
    if failures:
        print(f"\n{len(failures)} differences (first 10):", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        return 1
    print("\nOK — bit-exact match.")
    return 0


# ─────────────────────────────── check-decode ──────────────────────────────


def cmd_check_decode(_args: argparse.Namespace | None = None) -> int:
    """Round-trip-verify the bundled PocketSDR-AFS decoded outputs.

    Two oracles run end-to-end:

    1. **Channel-symbol oracle** — every signal in SIGNAL_TEST_VECTORS:
       ``references/pocketsdr-afs/decoded/decoded_signal_*.bin`` is
       exactly ``DECODED_FILE_LEN`` (6000) bytes of {0, 1} symbols and
       byte-equal to ``frames/<source_frame>[64:6064]``.  Demonstrates
       that the receiver's demodulator recovers the on-air channel bits.

    2. **Post-FEC oracle** — every signal in SIGNAL_TEST_VECTORS:
       ``references/pocketsdr-afs/decoded/decoded_fec_signal_*.bin`` is
       exactly ``DECODED_FEC_FILE_LEN`` (2868) bytes of {0, 1} bits and
       byte-equal to ``inputs/<source_frame_stem>_input.bin``.
       Demonstrates that the receiver's deinterleave + LDPC + CRC
       pipeline recovers the canonical pre-encode bits.

    Both oracles cover all 10 signals (including the FID=3 boundary
    frames) under v0.4.0+'s bundled FID-bypass patch — the LDPC + CRC
    stages are FID-agnostic, so SB2/SB3/SB4 decode correctly even when
    upstream's FID=0-only SB1 BCH search fails.

    This is the cheap CI-friendly form of the L4 oracle.  The expensive
    rebuild+cross-decode form is
    ``references/pocketsdr-afs/harnesses/verify_pocketsdr_decode.py``.
    """
    del _args
    if not POCKETSDR_DECODED_DIR.is_dir():
        print(f"ERROR: {POCKETSDR_DECODED_DIR} not found", file=sys.stderr)
        return 2

    failures: list[str] = []
    passed_chan = 0
    passed_fec = 0
    for signal_filename, _prn, source_frame in SIGNAL_TEST_VECTORS:
        # ── channel-symbol oracle ──────────────────────────────────────
        decoded_name = _decoded_filename_for(signal_filename)
        decoded_path = POCKETSDR_DECODED_DIR / decoded_name
        if not decoded_path.exists():
            failures.append(f"{decoded_name}: missing")
        else:
            decoded_bytes = decoded_path.read_bytes()
            chan_err = _verify_decoded_chan(decoded_name, decoded_bytes, source_frame)
            if chan_err is None:
                passed_chan += 1
            else:
                failures.append(chan_err)

        # ── post-FEC oracle ────────────────────────────────────────────
        fec_name = _decoded_fec_filename_for(signal_filename)
        fec_path = POCKETSDR_DECODED_DIR / fec_name
        if not fec_path.exists():
            failures.append(f"{fec_name}: missing")
            continue
        fec_bytes = fec_path.read_bytes()
        fec_err = _verify_decoded_fec(fec_name, fec_bytes, source_frame)
        if fec_err is None:
            passed_fec += 1
        else:
            failures.append(fec_err)

    total = len(SIGNAL_TEST_VECTORS)
    print(f"  Channel-symbol oracle: {passed_chan:>2}/{total}")
    print(f"  Post-FEC oracle:       {passed_fec:>2}/{total}")
    if failures:
        print(f"\nFAIL: {len(failures)} problems", file=sys.stderr)
        for msg in failures[:20]:
            print(f"  {msg}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  … ({len(failures) - 20} more)", file=sys.stderr)
        return 1
    print(
        f"\nOK — all {total} channel-symbol outputs match frames/*.bin payloads "
        f"and all {total} post-FEC outputs match inputs/*_input.bin."
    )
    return 0


def _verify_decoded_chan(
    decoded_name: str,
    decoded_bytes: bytes,
    source_frame: str,
    frames_dir: Path = FRAMES_DIR,
) -> str | None:
    """Validate one decoded_signal_*.bin file. Returns error str or None.

    ``frames_dir`` defaults to the bundled ``frames/``; ``diff-decode
    --reference`` overrides it so the channel truth can live in an agreed
    external reference set (check-decode keeps the default).
    """
    if len(decoded_bytes) != DECODED_FILE_LEN:
        return f"{decoded_name}: {len(decoded_bytes)} bytes, expected {DECODED_FILE_LEN}"
    if any(b not in (0, 1) for b in decoded_bytes):
        bad_idx = next(i for i, b in enumerate(decoded_bytes) if b not in (0, 1))
        return (
            f"{decoded_name}: non-{{0,1}} byte at offset {bad_idx} (value {decoded_bytes[bad_idx]})"
        )
    frame_bytes = (frames_dir / source_frame).read_bytes()
    if len(frame_bytes) != FRAME_FILE_LEN:
        return (
            f"{decoded_name}: companion {source_frame} is "
            f"{len(frame_bytes)} bytes, expected {FRAME_FILE_LEN}"
        )
    expected = frame_bytes[FRAME_HEADER_LEN : FRAME_HEADER_LEN + FRAME_PAYLOAD_LEN]
    if decoded_bytes == expected:
        return None
    first = next(i for i, (a, b) in enumerate(zip(decoded_bytes, expected, strict=True)) if a != b)
    return (
        f"{decoded_name}: first symbol mismatch at index {first} "
        f"(expected {expected[first]}, got {decoded_bytes[first]})"
    )


def _verify_decoded_fec(
    fec_name: str,
    fec_bytes: bytes,
    source_frame: str,
    inputs_dir: Path = INPUTS_DIR,
) -> str | None:
    """Validate one decoded_fec_signal_*.bin file. Returns error str or None.

    ``inputs_dir`` defaults to the bundled ``inputs/``; ``diff-decode
    --reference`` overrides it so the post-FEC truth can live in an
    agreed external reference set (check-decode keeps the default).
    """
    if len(fec_bytes) != DECODED_FEC_FILE_LEN:
        return f"{fec_name}: {len(fec_bytes)} bytes, expected {DECODED_FEC_FILE_LEN}"
    if any(b not in (0, 1) for b in fec_bytes):
        bad_idx = next(i for i, b in enumerate(fec_bytes) if b not in (0, 1))
        return f"{fec_name}: non-{{0,1}} byte at offset {bad_idx} (value {fec_bytes[bad_idx]})"
    input_path = inputs_dir / _input_filename_for(source_frame)
    if not input_path.exists():
        return f"{fec_name}: companion {input_path.name} missing under inputs/"
    expected = input_path.read_bytes()
    if len(expected) != INPUT_BYTE_COUNT:
        return (
            f"{fec_name}: companion {input_path.name} is "
            f"{len(expected)} bytes, expected {INPUT_BYTE_COUNT}"
        )
    if fec_bytes == expected:
        return None
    first = next(i for i, (a, b) in enumerate(zip(fec_bytes, expected, strict=True)) if a != b)
    return (
        f"{fec_name}: first FEC byte mismatch at index {first} "
        f"(expected {expected[first]}, got {fec_bytes[first]})"
    )


# ─────────────────────────────── diff-decode ───────────────────────────────


def cmd_diff_decode(args: argparse.Namespace) -> int:
    """Validate a third party's decoded outputs against the original input.

    Default — the interoperability-plan **Level 4 pass criterion**
    ("Decoded data matches original input exactly").  For every signal in
    SIGNAL_TEST_VECTORS:

    * **Post-FEC (required)** — ``<other_dir>/decoded_fec_signal_*.bin``
      is compared byte-for-byte against ``inputs/<source>_input.bin``.
      This *is* the pass criterion; an absent file is a failure.
    * **Channel-symbol (optional diagnostic)** —
      ``<other_dir>/decoded_signal_*.bin`` against
      ``frames/<source>[64:6064]``.  It localises *where* a decoder
      diverges, but the post-sync / pre-deinterleave tap it needs is not
      something every receiver exposes, so an absent file is *not* a
      failure; a present-but-wrong one still is.

    Both use first-mismatch localisation — exactly the comparison
    ``check-decode`` runs on our own reference outputs, applied to a
    third-party directory with no indirection through our decoder.

    With ``--vs-pocketsdr``, additionally diff ``<other_dir>`` against the
    bundled ``references/pocketsdr-afs/decoded/`` reference (secondary,
    optional — "do you match our specific decoder's output too").
    """
    other = Path(args.other_dir).resolve()
    if not other.is_dir():
        print(f"ERROR: {other} is not a directory", file=sys.stderr)
        return 2

    if args.reference is not None:
        ref = Path(args.reference).resolve()
        frames_dir, inputs_dir = ref / "frames", ref / "inputs"
        if not frames_dir.is_dir() or not inputs_dir.is_dir():
            print(
                f"ERROR: --reference {ref} must contain frames/ and inputs/",
                file=sys.stderr,
            )
            return 2
    else:
        ref, frames_dir, inputs_dir = REPO_ROOT, FRAMES_DIR, INPUTS_DIR

    records = _diff_decode_vs_input(other, frames_dir, inputs_dir)
    secondary = _diff_decode_vs_pocketsdr(other) if args.vs_pocketsdr else None
    failures = _diff_decode_failures(records, secondary)

    if args.json:
        print(json.dumps(_diff_decode_json(other, ref, records, secondary), indent=2))
        return 1 if failures else 0

    _diff_decode_print_human(records, secondary)
    if failures:
        print(f"\n{len(failures)} difference(s) (first 10):", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        return 1
    print("\nOK — decoded data matches original input exactly.")
    return 0


def _diff_decode_vs_input(
    other: Path, frames_dir: Path, inputs_dir: Path
) -> list[dict[str, object]]:
    """Validate <other> against frames/+inputs/ (the Level 4 pass criterion).

    Post-FEC (vs ``inputs/``) is **required** — it is the pass criterion,
    so an absent file is a failure.  Channel-symbol (vs
    ``frames/[64:6064]``) is an **optional diagnostic** — an absent file
    is not a failure, but a present-but-wrong one still is.

    Returns one record per signal:
    ``{"signal", "post_fec": {status, detail}, "channel": {status, detail}}``
    where status ∈ {pass, fail, missing} (post_fec) / {pass, fail, absent}
    (channel).
    """
    records: list[dict[str, object]] = []
    for signal_filename, _prn, source_frame in SIGNAL_TEST_VECTORS:
        chan_name = _decoded_filename_for(signal_filename)
        chan_path = other / chan_name
        if not chan_path.exists():
            channel = {"status": "absent", "detail": None}
        elif (
            e := _verify_decoded_chan(chan_name, chan_path.read_bytes(), source_frame, frames_dir)
        ) is None:
            channel = {"status": "pass", "detail": None}
        else:
            channel = {"status": "fail", "detail": e}

        fec_name = _decoded_fec_filename_for(signal_filename)
        fec_path = other / fec_name
        if not fec_path.exists():
            post_fec = {
                "status": "missing",
                "detail": f"{fec_name}: missing in {other} "
                "(required — post-FEC vs inputs/ is the Level 4 pass criterion)",
            }
        elif (
            e := _verify_decoded_fec(fec_name, fec_path.read_bytes(), source_frame, inputs_dir)
        ) is None:
            post_fec = {"status": "pass", "detail": None}
        else:
            post_fec = {"status": "fail", "detail": e}

        records.append({"signal": signal_filename, "post_fec": post_fec, "channel": channel})
    return records


def _diff_decode_failures(
    records: list[dict[str, object]], secondary: tuple[int, int, list[str]] | None
) -> list[str]:
    """Collect blocking messages: any post-FEC non-pass + any channel fail."""
    failures: list[str] = []
    for r in records:
        pf, ch = r["post_fec"], r["channel"]  # type: ignore[index]
        if pf["status"] != "pass":  # type: ignore[index]
            failures.append(pf["detail"])  # type: ignore[index,arg-type]
        if ch["status"] == "fail":  # type: ignore[index]
            failures.append(ch["detail"])  # type: ignore[index,arg-type]
    if secondary is not None:
        failures.extend(secondary[2])
    return failures


def _diff_decode_counts(records: list[dict[str, object]]) -> dict[str, int]:
    """Aggregate per-status tallies from the per-signal records."""

    def n(layer: str, status: str) -> int:
        return sum(1 for r in records if r[layer]["status"] == status)  # type: ignore[index]

    return {
        "fec_pass": n("post_fec", "pass"),
        "fec_fail": n("post_fec", "fail"),
        "fec_missing": n("post_fec", "missing"),
        "chan_pass": n("channel", "pass"),
        "chan_fail": n("channel", "fail"),
        "chan_absent": n("channel", "absent"),
    }


def _diff_decode_print_human(
    records: list[dict[str, object]], secondary: tuple[int, int, list[str]] | None
) -> None:
    """Print the console summary (unchanged shape from earlier versions)."""
    total = len(records)
    c = _diff_decode_counts(records)
    print("vs original input — Level 4 pass criterion:")
    print(
        f"  Post-FEC vs inputs/    (required):  {c['fec_pass']:>2}/{total}  "
        f"(missing: {c['fec_missing']})"
    )
    chan_provided = c["chan_pass"] + c["chan_fail"]
    if chan_provided == 0:
        print("  Channel-symbol vs frames/ (optional):  not provided — fine")
    else:
        print(
            f"  Channel-symbol vs frames/ (optional):  {c['chan_pass']:>2}/{chan_provided} "
            f"provided  ({c['chan_absent']} not provided)"
        )
    if secondary is not None:
        ps_chan, ps_fec, _ = secondary
        print("\nvs PocketSDR reference decode (secondary):")
        print(f"  Channel-symbol: {ps_chan:>2}/{total}")
        print(f"  Post-FEC:       {ps_fec:>2}/{total}")


def _diff_decode_json(
    other: Path,
    ref: Path,
    records: list[dict[str, object]],
    secondary: tuple[int, int, list[str]] | None,
) -> dict[str, object]:
    """One round-robin matrix cell, machine-readable (see INTEROP-ROUNDROBIN.md)."""
    total = len(records)
    c = _diff_decode_counts(records)
    verdict = "PASS" if not _diff_decode_failures(records, secondary) else "FAIL"
    out: dict[str, object] = {
        "tool": "lsis-afs-validate diff-decode",
        "criterion": "interoperability.pdf Level 4 — decoded data matches original input exactly",
        "candidate": str(other),
        "reference": str(ref),
        "signals": records,
        "summary": {
            "post_fec": {
                "required": True,
                "pass": c["fec_pass"],
                "fail": c["fec_fail"],
                "missing": c["fec_missing"],
                "total": total,
            },
            "channel": {
                "required": False,
                "pass": c["chan_pass"],
                "fail": c["chan_fail"],
                "absent": c["chan_absent"],
                "total": total,
            },
            "verdict": verdict,
        },
    }
    if secondary is not None:
        ps_chan, ps_fec, ps_fail = secondary
        out["pocketsdr_secondary"] = {
            "channel_pass": ps_chan,
            "post_fec_pass": ps_fec,
            "total": total,
            "failures": ps_fail,
        }
    return out


def _diff_decode_vs_pocketsdr(other: Path) -> tuple[int, int, list[str]]:
    """Secondary diff of <other> against the bundled PocketSDR reference.

    Returns (chan_matches, fec_matches, failures).  Missing files are not
    re-reported here — the primary pass already flags them.
    """
    failures: list[str] = []
    ps_chan = ps_fec = 0
    for signal_filename, _prn, _sf in SIGNAL_TEST_VECTORS:
        cn = _decoded_filename_for(signal_filename)
        fn = _decoded_fec_filename_for(signal_filename)
        ce = _diff_one(POCKETSDR_DECODED_DIR / cn, other / cn, DECODED_FILE_LEN)
        fe = _diff_one(POCKETSDR_DECODED_DIR / fn, other / fn, DECODED_FEC_FILE_LEN)
        if ce is None:
            ps_chan += 1
        elif ce != "_missing_":
            failures.append(ce)
        if fe is None:
            ps_fec += 1
        elif fe != "_missing_":
            failures.append(fe)
    return ps_chan, ps_fec, failures


def _diff_one(ours_path: Path, their_path: Path, expected_len: int) -> str | None:
    """Compare two binary files. Returns None on match, error string on mismatch.

    Returns the literal token ``"_missing_"`` if the user-side file is absent
    so the caller can tally it separately.
    """
    if not their_path.exists():
        return "_missing_"
    try:
        ours_bytes = ours_path.read_bytes()
        their_bytes = their_path.read_bytes()
    except OSError as exc:
        return f"{their_path.name}: read error ({exc})"
    if len(their_bytes) != expected_len:
        return f"{their_path.name}: their file is {len(their_bytes)} bytes, expected {expected_len}"
    if ours_bytes == their_bytes:
        return None
    first = next(i for i, (a, b) in enumerate(zip(ours_bytes, their_bytes, strict=True)) if a != b)
    return (
        f"{their_path.name}: first byte mismatch at index {first} "
        f"(expected {ours_bytes[first]}, got {their_bytes[first]})"
    )


# ─────────────────────────────── Level 5 constants ─────────────────────────
#
# Per references/interoperability.pdf "Level 5: Message Parsing
# Interoperability", the pass criterion is that all implementations
# extract identical navigation data — FID/TOI/WN/ITOW/CED/Health/ToT
# — from the canonical frames.  Of those fields, LSIS V1.0 itself only
# pins the bit-level representation of FID, TOI (Tables 13/14), WN,
# ITOW (Table 22) and the ToT formula (§2.5.5).  CED, Health, time
# conversions, and the SB3/SB4 type-field width are V1.0-TBW or
# V1.0-TBC (LSIS-TBW-2005/2006/2012, LSIS-TBC-2023/2024), so the
# shipped parsed JSONs report those regions as raw bit-slices and
# leave the semantic interpretation to whatever V2.0 settles on.
#
# This module's check-parsed subcommand verifies the shipped JSONs
# round-trip:
#
#   1. JSON schema valid (required fields present, types correct).
#   2. Spec-range checks (FID 0..3, TOI 0..99, WN 0..8191, ITOW 0..511
#      raw 9-bit max — see PARSED_ITOW_RAW_MAX; spec max 503 is exercised
#      by frame_boundary_max_fields, not enforced as the range ceiling).
#   3. ToT round-trip: t_F = WN*604800 + ITOW*1200 + TOI*12 + dt_lrt
#      (with dt_lrt=0 documented in the JSON itself).
#   4. FID/TOI ground-truth match (we know what we encoded).
#   5. WN/ITOW byte-compare against inputs/*_input.bin[0..21] raw bits.
#   6. SB2/SB3/SB4 raw-data hex byte-equal to inputs/*_input.bin slices.
#   7. CRC-24Q status flag = true on every subframe.
#
# It is NOT a parser: there is no BCH(51,8) decoder, no LDPC, no
# bit-level CED extraction.  Independence at the parser level comes
# from PocketSDR-AFS at L4 (which independently extracts WN/ITOW/TOI
# from the symbol stream), not from a reimplementation in this file.

PARSED_DIR = REPO_ROOT / "parsed"

# Top-level required JSON keys, per interoperability.pdf p.3-4.
PARSED_TOP_LEVEL_KEYS = {
    "version",
    "timestamp",
    "frame_id",
    "subframe1",
    "subframe2",
    "subframe3",
    "subframe4",
    "time_of_transmission",
}

# Spec-range maxima (inclusive).  Per LSIS V1.0 Tables 13, 22 and
# §2.4.3.1.6 (LSIS-FID0-520).
PARSED_FID_MAX = 3  # 2-bit field
PARSED_TOI_MAX = 99
PARSED_WN_MAX = 8191  # 13-bit raw maximum
# ITOW range here is the 9-bit raw max (511), not the spec max (503 per
# §2.4.3.1.6).  Test vectors that exercise SB2 corner cases (e.g. all-ones
# SB2 in TM2) can carry ITOW values 504..511 as a side effect of the input
# pattern; the parser must surface them faithfully.  TC4-Boundary frames
# specifically clamp ITOW to 503 (see frame_boundary_max_fields), so the
# spec-max boundary is exercised by that frame, not by the range check
# itself.  Out-of-spec ITOW does not invalidate the JSON at the parser
# layer — it just means the receiver should treat the time as unreliable.
PARSED_ITOW_RAW_MAX = 511
PARSED_SF_TYPE_MAX_4BIT = 15
PARSED_SF_TYPE_MAX_6BIT = 63

# Per V1.0 §2.5.5: t_F = WN*SECWEEK + ITOW*BI_d + TOI*F_d + dt_lrt
PARSED_SECWEEK = 604800
PARSED_BLOCK_INTERVAL = 1200
PARSED_FRAME_DURATION = 12

# (parsed_filename, source_frame_filename, expected_fid, expected_toi).
# The 7 parsed JSONs are 1:1 with FRAME_TEST_VECTORS — every L2 frame
# has a corresponding L5 parsed JSON produced by the LunaLink reference
# implementation running its full RX path (sync → BCH → LDPC → CRC →
# parse) on the frame.bin symbol stream.
PARSED_TEST_VECTORS: list[tuple[str, str, int, int]] = [
    ("parsed_frame_message_1.json", "frame_message_1.bin", 0, 0),
    ("parsed_frame_message_2.json", "frame_message_2.bin", 0, 0),
    ("parsed_frame_message_3.json", "frame_message_3.bin", 0, 0),
    ("parsed_frame_message_4.json", "frame_message_4.bin", 0, 0),
    ("parsed_frame_message_5.json", "frame_message_5.bin", 0, 0),
    ("parsed_frame_boundary.json", "frame_boundary.bin", 3, 99),
    (
        "parsed_frame_boundary_max_fields.json",
        "frame_boundary_max_fields.bin",
        3,
        99,
    ),
]


def _pack_bits_msbfirst(bit_bytes: bytes) -> str:
    """Pack a bytes-of-{0,1} buffer into MSB-first hex (lowercase).

    LSB-side of the last byte is zero-padded if ``len(bit_bytes) % 8 != 0``.
    Mirrors lunalink's ``_bits_to_hex`` in
    ``src/lunalink/afs/vectors.py`` so byte-equal comparison is well-defined.
    """
    out = bytearray()
    n = len(bit_bytes)
    for i in range(0, n, 8):
        byte = 0
        for j in range(8):
            byte <<= 1
            if i + j < n:
                byte |= bit_bytes[i + j] & 1
            # else: pad with 0 (LSB-side of last byte)
        out.append(byte)
    return out.hex()


def _bits_to_int_msbfirst(bit_bytes: bytes) -> int:
    """MSB-first integer from a slice of a {0,1} bit buffer."""
    value = 0
    for b in bit_bytes:
        value = (value << 1) | (b & 1)
    return value


def _validate_one_parsed(  # noqa: PLR0911, PLR0912, PLR0915
    parsed_filename: str,
    source_frame: str,
    expected_fid: int,
    expected_toi: int,
) -> list[str]:
    """Validate a single parsed_*.json file. Returns a list of error strings.

    Empty list = all checks passed.

    Long by design — this is the L5 oracle's full check sequence (schema,
    range, ToT round-trip, ground-truth match, raw-data byte-equal, CRC).
    Splitting into smaller helpers would scatter the per-file failure
    messages and obscure the linear validation flow.
    """
    errors: list[str] = []
    parsed_path = PARSED_DIR / parsed_filename

    if not parsed_path.exists():
        return [f"{parsed_filename}: missing"]

    try:
        with parsed_path.open("r", encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as exc:
        return [f"{parsed_filename}: invalid JSON ({exc.msg} at line {exc.lineno})"]
    except OSError as exc:
        return [f"{parsed_filename}: read error ({exc})"]

    if not isinstance(doc, dict):
        return [f"{parsed_filename}: top-level value is not an object"]

    # ── 1. Schema: required top-level keys ────────────────────────────────
    missing = PARSED_TOP_LEVEL_KEYS - doc.keys()
    if missing:
        errors.append(f"{parsed_filename}: missing top-level keys {sorted(missing)}")

    # If any required top-level key is missing we can't safely run the
    # field-level checks; return now to surface the schema problem first.
    if errors:
        return errors

    sf1 = doc["subframe1"]
    sf2 = doc["subframe2"]
    sf3 = doc["subframe3"]
    sf4 = doc["subframe4"]
    tot = doc["time_of_transmission"]

    if not all(isinstance(x, dict) for x in (sf1, sf2, sf3, sf4)):
        return [f"{parsed_filename}: subframe1..4 must be JSON objects"]

    for required, parent, parent_name in (
        ({"fid", "toi"}, sf1, "subframe1"),
        ({"wn", "itow"}, sf2, "subframe2"),
        ({"type"}, sf3, "subframe3"),
        ({"type"}, sf4, "subframe4"),
    ):
        miss = required - parent.keys()
        if miss:
            errors.append(f"{parsed_filename}: {parent_name} missing keys {sorted(miss)}")
    if errors:
        return errors

    fid = sf1["fid"]
    toi = sf1["toi"]
    wn = sf2["wn"]
    itow = sf2["itow"]
    sf3_type = sf3["type"]
    sf4_type = sf4["type"]

    for label, value in (
        ("subframe1.fid", fid),
        ("subframe1.toi", toi),
        ("subframe2.wn", wn),
        ("subframe2.itow", itow),
        ("subframe3.type", sf3_type),
        ("subframe4.type", sf4_type),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{parsed_filename}: {label} must be int, got {type(value).__name__}")
    if not isinstance(tot, (int, float)) or isinstance(tot, bool):
        errors.append(
            f"{parsed_filename}: time_of_transmission must be number, got {type(tot).__name__}"
        )
    if errors:
        return errors

    # ── 2. Spec-range checks (LSIS V1.0 Tables 13, 22; §2.4.3.1.6) ────────
    if not 0 <= fid <= PARSED_FID_MAX:
        errors.append(f"{parsed_filename}: fid={fid} out of spec range [0,{PARSED_FID_MAX}]")
    if not 0 <= toi <= PARSED_TOI_MAX:
        errors.append(f"{parsed_filename}: toi={toi} out of spec range [0,{PARSED_TOI_MAX}]")
    if not 0 <= wn <= PARSED_WN_MAX:
        errors.append(f"{parsed_filename}: wn={wn} out of spec range [0,{PARSED_WN_MAX}]")
    if not 0 <= itow <= PARSED_ITOW_RAW_MAX:
        errors.append(
            f"{parsed_filename}: itow={itow} out of 9-bit raw range "
            f"[0,{PARSED_ITOW_RAW_MAX}] (parser must read 9 bits MSB-first)"
        )
    # Type-field width comes from the JSON itself (lunalink-resolved).
    type_max = PARSED_SF_TYPE_MAX_6BIT  # default: lunalink's choice
    type_width = sf3.get("_type_width_bits")
    if type_width == 4:
        type_max = PARSED_SF_TYPE_MAX_4BIT
    if not 0 <= sf3_type <= type_max:
        errors.append(
            f"{parsed_filename}: subframe3.type={sf3_type} exceeds "
            f"{type_width or 6}-bit field max {type_max}"
        )
    if not 0 <= sf4_type <= type_max:
        errors.append(
            f"{parsed_filename}: subframe4.type={sf4_type} exceeds "
            f"{type_width or 6}-bit field max {type_max}"
        )

    # ── 3. ToT round-trip per V1.0 §2.5.5 (dt_lrt = 0 documented) ─────────
    expected_tot = wn * PARSED_SECWEEK + itow * PARSED_BLOCK_INTERVAL + toi * PARSED_FRAME_DURATION
    if float(tot) != float(expected_tot):
        errors.append(
            f"{parsed_filename}: time_of_transmission={tot}, "
            f"expected {expected_tot} per V1.0 §2.5.5 "
            f"(WN·{PARSED_SECWEEK} + ITOW·{PARSED_BLOCK_INTERVAL} "
            f"+ TOI·{PARSED_FRAME_DURATION})"
        )

    # ── 4. FID/TOI ground-truth match (PARSED_TEST_VECTORS) ───────────────
    if fid != expected_fid:
        errors.append(
            f"{parsed_filename}: subframe1.fid={fid}, expected {expected_fid} "
            f"(ground truth from {source_frame} encoding)"
        )
    if toi != expected_toi:
        errors.append(
            f"{parsed_filename}: subframe1.toi={toi}, expected {expected_toi} "
            f"(ground truth from {source_frame} encoding)"
        )

    # ── 5. WN/ITOW byte-compare against inputs/*_input.bin ────────────────
    input_path = INPUTS_DIR / _input_filename_for(source_frame)
    if not input_path.exists():
        errors.append(f"{parsed_filename}: companion input {input_path.name} missing under inputs/")
    else:
        input_bytes = input_path.read_bytes()
        if len(input_bytes) != INPUT_BYTE_COUNT:
            errors.append(
                f"{parsed_filename}: companion {input_path.name} is "
                f"{len(input_bytes)} bytes, expected {INPUT_BYTE_COUNT}"
            )
        else:
            expected_wn = _bits_to_int_msbfirst(
                input_bytes[SB2_WN_OFFSET : SB2_WN_OFFSET + SB2_WN_BITS]
            )
            expected_itow = _bits_to_int_msbfirst(
                input_bytes[SB2_ITOW_OFFSET : SB2_ITOW_OFFSET + SB2_ITOW_BITS]
            )
            if wn != expected_wn:
                errors.append(
                    f"{parsed_filename}: subframe2.wn={wn}, expected {expected_wn} "
                    f"from {input_path.name}[0..12] MSB-first"
                )
            if itow != expected_itow:
                errors.append(
                    f"{parsed_filename}: subframe2.itow={itow}, expected {expected_itow} "
                    f"from {input_path.name}[13..21] MSB-first"
                )

            # ── 6. Raw-data hex byte-equal to inputs slices ───────────────
            sb2_input = input_bytes[:SB2_BITS]
            sb3_input = input_bytes[SB2_BITS : SB2_BITS + SB3_BITS]
            sb4_input = input_bytes[SB2_BITS + SB3_BITS :]
            for sf_label, sf_doc, expected_bits in (
                ("subframe2", sf2, sb2_input),
                ("subframe3", sf3, sb3_input),
                ("subframe4", sf4, sb4_input),
            ):
                got_hex = sf_doc.get("_data_raw_hex")
                if got_hex is None:
                    # Optional disclosure field — silently skip if absent.
                    continue
                expected_hex = _pack_bits_msbfirst(expected_bits)
                if str(got_hex).lower() != expected_hex:
                    errors.append(
                        f"{parsed_filename}: {sf_label}._data_raw_hex does not match "
                        f"{input_path.name} bit-slice (first 32 expected_hex chars: "
                        f"{expected_hex[:32]}…)"
                    )

    # ── 7. CRC status flag (V1.0 §2.4.3.1.3): every subframe must report OK ──
    for sf_label, sf_doc in (("subframe2", sf2), ("subframe3", sf3), ("subframe4", sf4)):
        crc_ok = sf_doc.get("_crc24q_ok")
        if crc_ok is False:
            errors.append(
                f"{parsed_filename}: {sf_label}._crc24q_ok=false (CRC verify failed; "
                f"V1.0 §2.4.3.1.3)"
            )

    return errors


def cmd_check_parsed(_args: argparse.Namespace | None = None) -> int:
    """Validate the shipped Level-5 parsed JSONs (structural + range + ground-truth).

    Performs the seven L5 checks documented in CORRECTNESS.md:
    schema, spec-range, ToT round-trip, FID/TOI ground-truth, WN/ITOW
    byte-compare against inputs/, raw-data hex byte-equal to inputs/,
    and CRC-24Q status flag.

    NOT a parser — independence at the parser level comes from
    PocketSDR-AFS at L4, which independently extracts (WN, ITOW, TOI)
    from the symbol stream.
    """
    del _args
    if not PARSED_DIR.is_dir():
        print(f"ERROR: {PARSED_DIR} not found", file=sys.stderr)
        return 2

    failures: list[str] = []
    passed = 0
    for parsed_filename, source_frame, expected_fid, expected_toi in PARSED_TEST_VECTORS:
        errs = _validate_one_parsed(parsed_filename, source_frame, expected_fid, expected_toi)
        if errs:
            failures.extend(errs)
        else:
            passed += 1

    total = len(PARSED_TEST_VECTORS)
    print(f"  Parsed-JSON oracle: {passed:>2}/{total}")
    if failures:
        print(f"\nFAIL: {len(failures)} problems", file=sys.stderr)
        for msg in failures[:20]:
            print(f"  {msg}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  … ({len(failures) - 20} more)", file=sys.stderr)
        return 1
    print(
        f"\nOK — all {total} parsed JSONs pass schema, spec-range, ToT round-trip, "
        f"FID/TOI/WN/ITOW ground-truth, raw-data byte-compare, and CRC checks."
    )
    return 0


# ─────────────────────────────── diff-parsed ───────────────────────────────


# Top-level fields that count as load-bearing for cross-impl comparison.
# Keys with leading underscore are LunaLink-specific metadata (provenance,
# disclosure markers); they are excluded from the diff so a third-party
# implementation that emits only the spec-shaped fields still matches.
_PARSED_DIFF_FIELDS_TOP = {"version", "frame_id", "time_of_transmission"}
_PARSED_DIFF_FIELDS_SF1 = {"fid", "toi"}
_PARSED_DIFF_FIELDS_SF2 = {"wn", "itow"}
_PARSED_DIFF_FIELDS_SF3 = {"type"}
_PARSED_DIFF_FIELDS_SF4 = {"type"}


def _diff_parsed_one(  # noqa: PLR0911
    parsed_filename: str, ours_path: Path, theirs_path: Path
) -> str | None:
    """Compare two parsed_*.json files field-by-field. Returns None on match.

    Uses ``"_missing_"`` if their file is absent so the caller can tally.

    Multiple early returns are deliberate — each represents a distinct
    diff outcome (missing file, parse error, top-level field mismatch,
    per-subframe field mismatch) that the caller surfaces verbatim.
    """
    if not theirs_path.exists():
        return "_missing_"
    try:
        ours = json.loads(ours_path.read_text(encoding="utf-8"))
        theirs = json.loads(theirs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"{parsed_filename}: read/parse error ({exc})"

    if not isinstance(ours, dict) or not isinstance(theirs, dict):
        return f"{parsed_filename}: one side is not a JSON object"

    # Top-level fields.
    for key in _PARSED_DIFF_FIELDS_TOP:
        if key not in theirs:
            return f"{parsed_filename}: their JSON missing top-level field {key!r}"
        if ours.get(key) != theirs.get(key):
            return (
                f"{parsed_filename}: {key!r} differs "
                f"(ours={ours.get(key)!r}, theirs={theirs.get(key)!r})"
            )

    # Per-subframe fields.
    for sf_key, fields in (
        ("subframe1", _PARSED_DIFF_FIELDS_SF1),
        ("subframe2", _PARSED_DIFF_FIELDS_SF2),
        ("subframe3", _PARSED_DIFF_FIELDS_SF3),
        ("subframe4", _PARSED_DIFF_FIELDS_SF4),
    ):
        ours_sf = ours.get(sf_key)
        theirs_sf = theirs.get(sf_key)
        if not isinstance(ours_sf, dict) or not isinstance(theirs_sf, dict):
            return f"{parsed_filename}: {sf_key} missing or not an object on one side"
        for key in fields:
            if key not in theirs_sf:
                return f"{parsed_filename}: their {sf_key!r} missing field {key!r}"
            if ours_sf.get(key) != theirs_sf.get(key):
                return (
                    f"{parsed_filename}: {sf_key}.{key} differs "
                    f"(ours={ours_sf.get(key)!r}, theirs={theirs_sf.get(key)!r})"
                )

    return None


def cmd_diff_parsed(args: argparse.Namespace) -> int:
    """Compare a directory of parsed_*.json files against ours, field-by-field.

    Compares the V1.0-pinned fields only — `version`, `frame_id`,
    `time_of_transmission`, and per-subframe (FID, TOI, WN, ITOW, type).
    LunaLink-specific metadata (underscore-prefixed disclosure markers)
    is intentionally excluded so a third-party implementation emitting
    only the spec-shaped fields still matches.

    For comparing the V1.0-TBW raw-bit slices (CED, almanac, network
    access), use ``diff-inputs`` against ``inputs/`` instead — those
    bits are the load-bearing transmit-side ground truth and exist
    upstream of the L5 parsing layer.
    """
    other = Path(args.other_dir).resolve()
    if not other.is_dir():
        print(f"ERROR: {other} is not a directory", file=sys.stderr)
        return 2

    failures: list[str] = []
    matches = 0
    missing = 0
    for parsed_filename, *_ in PARSED_TEST_VECTORS:
        ours_path = PARSED_DIR / parsed_filename
        theirs_path = other / parsed_filename
        result = _diff_parsed_one(parsed_filename, ours_path, theirs_path)
        if result is None:
            matches += 1
        elif result == "_missing_":
            missing += 1
            failures.append(f"{parsed_filename}: missing in {other}")
        else:
            failures.append(result)

    total = len(PARSED_TEST_VECTORS)
    print(f"Compared {total - missing}/{total} parsed JSONs (missing: {missing})")
    print(f"  Field-equal: {matches:>2}/{total}")
    if failures:
        print(f"\n{len(failures)} differences (first 10):", file=sys.stderr)
        for msg in failures[:10]:
            print(f"  {msg}", file=sys.stderr)
        return 1
    print("\nOK — all spec-shaped fields match.")
    return 0


# ─────────────────────────────── fec (component vectors) ────────────────────
#
# Phase-3 FEC component test vectors: isolated BCH(51,8), CRC-24Q, 60×98 block
# interleaver, and LDPC(1/2) SF2/SF3/SF4 encode vectors with FULL inputs and
# outputs, so a third party can byte-compare each FEC stage in isolation rather
# than only at the assembled-frame level (diff-frames).
#
# check-fec is NOT a BCH/LDPC/CRC reimplementation.  It verifies:
#   1. schema + bit-length + binary-value structure of every vector;
#   2. producer self-consistency flags (BCH hamming_distance == 0 + decode
#      round-trip; CRC verify_passes; interleaver round_trip_ok);
#   3. interleaver: full column-major permutation (input → output) verified
#      end-to-end, plus round-trip flag and spot-check mapping;
#   4. a BCH frame-anchor — for the (FID, TOI) pairs that match a shipped
#      frame, the BCH codeword must equal that frame's SB1 region bit-for-bit.
#
# Full-pipeline equivalence to frames/ (inputs → CRC → LDPC → interleave →
# frame) is established at generation time by the maintainer stitch check and
# inherited from the L2 structural / LANS-AFS-SIM / L4 PocketSDR-AFS oracles;
# the BCH anchor ties this component set to that verified ground truth here.

FEC_DIR = REPO_ROOT / "fec"
FEC_BCH_CODEWORD_BITS = 52
FEC_CRC_BITS = 24
FEC_LDPC_PARAMS: dict[str, tuple[int, int]] = {
    "SF2": (1200, 2400),
    "SF3": (870, 1740),
    "SF4": (870, 1740),
}
FEC_INTERLEAVER_ROWS = 60
FEC_INTERLEAVER_COLS = 98
FEC_INTERLEAVER_SIZE = 5880
# (fid, toi) → representative shipped frame whose SB1 the BCH codeword must equal.
FEC_BCH_ANCHORS: dict[tuple[int, int], str] = {
    (0, 0): "frame_message_1.bin",
    (3, 99): "frame_boundary.bin",
}


def _fec_load(name: str) -> tuple[dict | None, str | None]:
    path = FEC_DIR / name
    if not path.exists():
        return None, f"{name}: missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{name}: invalid JSON ({exc})"


def _is_bit_list(x: object, n: int | None = None) -> bool:
    if not isinstance(x, list):
        return False
    if n is not None and len(x) != n:
        return False
    return all(b in (0, 1) for b in x)


def _hex_to_bits(hexstr: object, nbits: int) -> list[int] | None:
    if not isinstance(hexstr, str):
        return None
    try:
        raw = bytes.fromhex(hexstr)
    except ValueError:
        return None
    bits: list[int] = []
    for byte in raw:
        for j in range(7, -1, -1):
            bits.append((byte >> j) & 1)
    return bits[:nbits]


def _bits_hex_match(bit_list: list[int], hexstr: object) -> bool:
    return _pack_bits_msbfirst(bytes(bit_list)).lower() == str(hexstr).lower()


def _frame_sb1_bits(frame_filename: str) -> list[int]:
    data = (FRAMES_DIR / frame_filename).read_bytes()
    payload = data[FRAME_HEADER_LEN:FRAME_FILE_LEN]
    return list(payload[68 : 68 + FEC_BCH_CODEWORD_BITS])


def _check_fec_bch(doc: dict, errors: list[str]) -> None:
    if doc.get("codeword_length") != FEC_BCH_CODEWORD_BITS:
        errors.append(f"bch_vectors.json: codeword_length != {FEC_BCH_CODEWORD_BITS}")
    for i, v in enumerate(doc.get("vectors", [])):
        tag = f"bch_vectors[{i}]"
        fid, toi = v.get("fid"), v.get("toi")
        if not (isinstance(fid, int) and 0 <= fid <= 3):
            errors.append(f"{tag}: fid {fid} out of range [0,3]")
        if not (isinstance(toi, int) and 0 <= toi <= 99):
            errors.append(f"{tag}: toi {toi} out of range [0,99]")
        cw = v.get("codeword_bits")
        if not _is_bit_list(cw, FEC_BCH_CODEWORD_BITS):
            errors.append(f"{tag}: codeword_bits not {FEC_BCH_CODEWORD_BITS} binary symbols")
            continue
        if not _bits_hex_match(cw, v.get("codeword_hex", "")):
            errors.append(f"{tag}: codeword_hex does not match codeword_bits")
        if v.get("decoded_fid") != fid or v.get("decoded_toi") != toi:
            errors.append(f"{tag}: decode round-trip mismatch")
        if v.get("hamming_distance") != 0:
            errors.append(f"{tag}: hamming_distance != 0 (noiseless encode must round-trip)")
        anchor = FEC_BCH_ANCHORS.get((fid, toi))
        if anchor is not None and cw != _frame_sb1_bits(anchor):
            errors.append(f"{tag}: codeword != {anchor} SB1 region (frame-anchor failed)")


def _check_fec_crc(doc: dict, errors: list[str]) -> None:
    for i, v in enumerate(doc.get("vectors", [])):
        tag = f"crc24_vectors[{i}] {v.get('description', '')}"
        if not _is_bit_list(v.get("input_bits")):
            errors.append(f"{tag}: input_bits not binary")
        cb = v.get("crc_bits")
        if not _is_bit_list(cb, FEC_CRC_BITS):
            errors.append(f"{tag}: crc_bits not {FEC_CRC_BITS} binary")
            continue
        if not _bits_hex_match(cb, v.get("crc_hex", "")):
            errors.append(f"{tag}: crc_hex does not match crc_bits")
        if v.get("verify_passes") is not True:
            errors.append(f"{tag}: verify_passes not true")


def _check_fec_interleaver(doc: dict, errors: list[str]) -> None:
    rows, cols, size = doc.get("rows"), doc.get("cols"), doc.get("size")
    if not (
        rows == FEC_INTERLEAVER_ROWS
        and cols == FEC_INTERLEAVER_COLS
        and size == FEC_INTERLEAVER_SIZE
        and rows * cols == size
    ):
        errors.append("interleaver_vectors.json: rows/cols/size inconsistent")
    for i, v in enumerate(doc.get("vectors", [])):
        tag = f"interleaver_vectors[{i}] {v.get('description', '')}"
        inb = _hex_to_bits(v.get("input_hex", ""), FEC_INTERLEAVER_SIZE)
        outb = _hex_to_bits(v.get("output_hex", ""), FEC_INTERLEAVER_SIZE)
        if (
            inb is None
            or outb is None
            or len(inb) != FEC_INTERLEAVER_SIZE
            or len(outb) != FEC_INTERLEAVER_SIZE
        ):
            errors.append(f"{tag}: input/output_hex not {FEC_INTERLEAVER_SIZE} bits")
            continue
        # Full spec permutation: write row-wise (COLS per row), read column-wise
        # (ROWS per col) → out[col*ROWS + row] = in[row*COLS + col]. This is the
        # deterministic framing permutation (LSIS-FID0-470), not FEC math, so
        # verifying it in full is on-policy — and unlike a popcount invariant it
        # fully discriminates the non-constant patterns across all 5880 bits.
        expected = [0] * FEC_INTERLEAVER_SIZE
        for idx in range(FEC_INTERLEAVER_SIZE):
            r, c = divmod(idx, FEC_INTERLEAVER_COLS)
            expected[c * FEC_INTERLEAVER_ROWS + r] = inb[idx]
        if outb != expected:
            errors.append(f"{tag}: output is not the spec column-major interleave of input")
        if v.get("round_trip_ok") is not True:
            errors.append(f"{tag}: round_trip_ok not true")
        for m in v.get("spot_check_mapping", []):
            ip, op = m.get("input_pos"), m.get("output_pos")
            if not isinstance(ip, int) or not isinstance(op, int):
                errors.append(f"{tag}: malformed spot_check_mapping")
                break
            row, col = divmod(ip, FEC_INTERLEAVER_COLS)
            if op != col * FEC_INTERLEAVER_ROWS + row:
                errors.append(f"{tag}: spot-check {ip}->{op} != column-major formula")
                break


def _check_fec_ldpc(doc: dict, errors: list[str]) -> None:
    for i, v in enumerate(doc.get("vectors", [])):
        tag = f"ldpc_vectors[{i}] {v.get('subframe', '')}/{v.get('pattern', '')}"
        sf = v.get("subframe")
        if sf not in FEC_LDPC_PARAMS:
            errors.append(f"{tag}: unknown subframe {sf!r}")
            continue
        k, n = FEC_LDPC_PARAMS[sf]
        if v.get("k") != k or v.get("n") != n or v.get("codeword_length") != n:
            errors.append(f"{tag}: k/n/codeword_length != ({k}, {n})")
        if len(_hex_to_bits(v.get("message_hex", ""), k) or []) != k:
            errors.append(f"{tag}: message_hex does not decode to {k} bits")
        if len(_hex_to_bits(v.get("codeword_hex", ""), n) or []) != n:
            errors.append(f"{tag}: codeword_hex does not decode to {n} bits")


def cmd_check_fec(_args: argparse.Namespace | None = None) -> int:
    """Validate fec/ component vectors (structural + self-consistency + BCH frame-anchor).

    NOT a BCH/LDPC/CRC reimplementation — see the module comment above.
    """
    del _args
    if not FEC_DIR.is_dir():
        print(f"ERROR: {FEC_DIR} not found", file=sys.stderr)
        return 2
    if not FRAMES_DIR.is_dir():
        # The BCH frame-anchor cross-checks fec/ against frames/; without it
        # the oracle cannot run. Fail cleanly (matching check-frames/check-decode)
        # rather than raising FileNotFoundError mid-check.
        print(f"ERROR: {FRAMES_DIR} not found (required for the BCH frame-anchor)", file=sys.stderr)
        return 2
    checks = [
        ("bch_vectors.json", _check_fec_bch),
        ("crc24_vectors.json", _check_fec_crc),
        ("interleaver_vectors.json", _check_fec_interleaver),
        ("ldpc_vectors.json", _check_fec_ldpc),
    ]
    errors: list[str] = []
    ok = 0
    for name, fn in checks:
        doc, err = _fec_load(name)
        if doc is None:
            errors.append(err or f"{name}: load failed")
            continue
        fn(doc, errors)
        ok += 1
    print(f"  FEC component oracle: {ok}/{len(checks)} files")
    if errors:
        print(f"\nFAIL: {len(errors)} problems", file=sys.stderr)
        for m in errors[:20]:
            print(f"  {m}", file=sys.stderr)
        if len(errors) > 20:
            print(f"  … ({len(errors) - 20} more)", file=sys.stderr)
        return 1
    print(
        "\nOK — BCH/CRC/interleaver/LDPC vectors pass structure, self-consistency, "
        "and the BCH frame-anchor."
    )
    return 0


# (filename, vector-key function, fields compared) for diff-fec.
_FEC_DIFF_SPEC: list[tuple[str, Callable[[dict], object], list[str]]] = [
    ("bch_vectors.json", lambda v: (v.get("fid"), v.get("toi")), ["codeword_bits", "codeword_hex"]),
    ("crc24_vectors.json", lambda v: v.get("description"), ["crc_bits", "crc_hex"]),
    ("interleaver_vectors.json", lambda v: v.get("description"), ["input_hex", "output_hex"]),
    (
        "ldpc_vectors.json",
        lambda v: (v.get("subframe"), v.get("pattern")),
        ["message_hex", "codeword_hex"],
    ),
]


def _diff_fec_one(
    name: str, keyfn: Callable[[dict], object], fields: list[str], other: Path
) -> str | None:
    """Compare one fec/*.json file against theirs. Returns None on match, else a message."""
    ours, oerr = _fec_load(name)
    if ours is None:
        return f"{name}: our copy {oerr}"
    tpath = other / name
    if not tpath.exists():
        return f"{name}: missing in {other}"
    try:
        theirs = json.loads(tpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"{name}: their copy invalid ({exc})"
    their_by_key = {str(keyfn(v)): v for v in theirs.get("vectors", [])}
    for v in ours.get("vectors", []):
        key = str(keyfn(v))
        tv = their_by_key.get(key)
        if tv is None:
            return f"{name}: vector {key} missing in theirs"
        for f in fields:
            a, b = v.get(f), tv.get(f)
            if f.endswith("_hex") and isinstance(a, str) and isinstance(b, str):
                a, b = a.lower(), b.lower()
            if a != b:
                return f"{name}: vector {key} field {f!r} differs"
    return None


def cmd_diff_fec(args: argparse.Namespace) -> int:
    """Compare a directory of fec/*.json vectors against ours, field-by-field.

    Pure comparison — runs no FEC.  *_hex fields are matched case-insensitively.
    """
    other = Path(args.other_dir).resolve()
    if not other.is_dir():
        print(f"ERROR: {other} is not a directory", file=sys.stderr)
        return 2
    failures = [
        msg
        for name, keyfn, fields in _FEC_DIFF_SPEC
        if (msg := _diff_fec_one(name, keyfn, fields, other)) is not None
    ]
    files_ok = len(_FEC_DIFF_SPEC) - len(failures)
    print(f"Compared {files_ok}/{len(_FEC_DIFF_SPEC)} FEC vector files")
    if failures:
        print(f"\n{len(failures)} differences (first 10):", file=sys.stderr)
        for m in failures[:10]:
            print(f"  {m}", file=sys.stderr)
        return 1
    print("\nOK — all FEC component vectors match.")
    return 0


# ─────────────────────────────── verify-manifest ────────────────────────────


def cmd_verify_manifest(_args: argparse.Namespace) -> int:
    del _args
    if not MANIFEST_PATH.exists():
        print(f"ERROR: {MANIFEST_PATH} not found", file=sys.stderr)
        return 2
    manifest = json.loads(MANIFEST_PATH.read_text())
    expected: dict[str, str] = manifest["files"]

    mismatches: list[str] = []
    for rel, want in sorted(expected.items()):
        path = REPO_ROOT / rel
        if not path.exists():
            mismatches.append(f"{rel}: missing")
            continue
        got = sha256(path)
        if got != want:
            mismatches.append(f"{rel}: sha256 mismatch")

    print(f"Checked {len(expected)} files against manifest")
    if mismatches:
        print(f"FAIL: {len(mismatches)} problems", file=sys.stderr)
        for m in mismatches[:10]:
            print(f"  {m}", file=sys.stderr)
        return 1
    print("OK — all SHA256s match.")
    return 0


# ─────────────────────────────── rebuild-manifest ──────────────────────────


# Path components and suffixes that are git-ignored generated artefacts.
# Anything matching is skipped by _rebuild_manifest so a maintainer's local
# build state cannot leak into the SHA-pinned distribution.
_GENERATED_DIR_NAMES: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
    }
)
_GENERATED_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo", ".pyd"})


def _is_generated_artefact(path: Path) -> bool:
    """Return True if ``path`` looks like a build/cache product that must
    not appear in manifest.json."""
    if path.suffix in _GENERATED_SUFFIXES:
        return True
    return any(part in _GENERATED_DIR_NAMES for part in path.parts)


def _rebuild_manifest() -> int:
    """Recompute SHA256s over codes/ and references/ and overwrite manifest.json.

    Returns the number of files hashed.

    Excludes generated artefacts (Python bytecode, common cache dirs) so
    a maintainer who has imported the harness modules locally — which
    Python silently writes ``.pyc`` files into ``references/pocketsdr-afs/
    harnesses/__pycache__/`` for — does not pull those non-tracked files
    into the manifest.  If they leaked in, ``verify-manifest`` would fail
    on every clean checkout.
    """
    entries: dict[str, str] = {}
    for sub in ("codes", "frames", "inputs", "signals", "parsed", "fec", "references"):
        base = REPO_ROOT / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            if _is_generated_artefact(p):
                continue
            entries[p.relative_to(REPO_ROOT).as_posix()] = sha256(p)

    manifest: dict[str, object] = (
        json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {}
    )
    # Drop any stale scalar-schema key before rewriting, plus the legacy
    # wall-clock "generated" timestamp — we want the manifest itself to be
    # byte-stable across rebuilds (consistent with the pinned frame-header
    # timestamp), so verify-manifest and CI don't churn on no-op rebuilds.
    manifest.pop("level", None)
    manifest.pop("generated", None)
    manifest.update(
        {
            "version": "1.0",
            "levels": [1, 2, 3, 4, 5],
            "implementation": "LuarSpace",
            "spec": "LSIS-AFS V1.0, 29 January 2025",
            "oracles": [
                "LNIS AD1 Volume A, Annex 3 (10 December 2024) — L1 normative",
                "LANS-AFS-SIM (BSD-2-Clause, © 2025 Takuji Ebinuma) — L1+L2 independent",
                "LSIS-AFS V1.0 §2.4 + Gateway 3 checklist — L2 structural",
                "interoperability.pdf Signal Export Format + LSIS V1.0 §4 + first-chip "
                "polarity (chains L1 codes + L2 sync prefix into L3) — L3 structural",
                "PocketSDR-AFS (BSD-2-Clause, © 2025 Takuji Ebinuma; pinned SHA "
                "5b23809f30d68518b7fad7a564fd0fac57cc497d) — L4 cross-decode",
                "interoperability.pdf §Parsed Data Export Format + LSIS V1.0 Tables "
                "13/22 + §2.5.5 + §2.4.3.1.3 — L5 structural + spec-range + ToT "
                "round-trip + raw-data byte-equal vs inputs/",
                "LSIS-AFS-501/FID0-467/FID0-470 + Tables 16/19/20 — FEC components "
                "(BCH/CRC/interleaver/LDPC) structural + self-consistency + BCH "
                "frame-anchor vs frames/ (full-pipeline stitch verified at generation)",
            ],
            "files": dict(sorted(entries.items())),
        }
    )
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
    return len(entries)


def cmd_rebuild_manifest(_args: argparse.Namespace | None = None) -> int:
    del _args
    n = _rebuild_manifest()
    print(f"OK — manifest rebuilt: {n} files covered.")
    return 0


# ─────────────────────────────── refresh ────────────────────────────────────


def cmd_refresh(args: argparse.Namespace) -> int:
    url_map: dict[str, str] = {}
    if args.url_map:
        for entry in args.url_map:
            key, _, url = entry.partition("=")
            if key not in ANNEX3_FILES.values():
                print(f"ERROR: unknown reference file {key}", file=sys.stderr)
                return 2
            url_map[key] = url
    elif args.base_url:
        for filename in ANNEX3_FILES.values():
            url_map[filename] = args.base_url.rstrip("/") + "/" + filename
    else:
        print(
            "ERROR: pass --base-url <URL> or --url <file>=<URL> (repeatable).",
            file=sys.stderr,
        )
        print(
            "Canonical source is the electronic attachment set of LNIS AD1 Volume A\n"
            "(ESA / CCSDS distribution). No public URL is currently defined.",
            file=sys.stderr,
        )
        return 2

    ANNEX3_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in url_map.items():
        target = ANNEX3_DIR / filename
        print(f"  fetching {filename} ← {url}", file=sys.stderr)
        try:
            with urllib.request.urlopen(url) as resp:
                data = resp.read()
        except Exception as exc:  # pragma: no cover
            print(f"  FAIL: {exc}", file=sys.stderr)
            return 1
        target.write_bytes(data)
        print(f"  wrote {len(data):>8} bytes → {target}", file=sys.stderr)

    _rebuild_manifest()
    return cmd_check_annex3()


# ─────────────────────────────── CLI ────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="validate.py",
        description="LSIS-AFS interoperability test-vector validator (Levels 1-5).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "check-annex3",
        help="Compare codes/ against references/ Annex 3 (normative reference).",
    ).set_defaults(func=cmd_check_annex3)

    sub.add_parser(
        "check-lans-afs-sim",
        help="Compare codes/ against LANS-AFS-SIM dumps (L1 second oracle).",
    ).set_defaults(func=cmd_check_lans_afs_sim)

    sub.add_parser(
        "check-frames",
        help="Validate frames/ structurally per LSIS V1.0 §2.4 (L2 structural oracle).",
    ).set_defaults(func=cmd_check_frames)

    sub.add_parser(
        "check-lans-afs-sim-frames",
        help="Compare frames/ payloads against LANS-AFS-SIM dumps (L2 second oracle).",
    ).set_defaults(func=cmd_check_lans_afs_sim_frames)

    p_diff = sub.add_parser(
        "diff",
        help="Compare a directory of code vectors to ours, section-by-section.",
    )
    p_diff.add_argument(
        "other_dir",
        help="Directory containing codes_prnNNN.hex files to compare against ours.",
    )
    p_diff.set_defaults(func=cmd_diff)

    p_diff_frames = sub.add_parser(
        "diff-frames",
        help="Compare a directory of frame vectors to ours, byte-by-byte.",
    )
    p_diff_frames.add_argument(
        "other_dir",
        help="Directory containing frame_*.bin files to compare against ours.",
    )
    p_diff_frames.set_defaults(func=cmd_diff_frames)

    sub.add_parser(
        "check-canonical-inputs",
        help="Verify inputs/ canonical files reproduce from the documented patterns.",
    ).set_defaults(func=cmd_check_canonical_inputs)

    p_diff_inputs = sub.add_parser(
        "diff-inputs",
        help="Compare a directory of canonical-input files (frame_*_input.bin) against ours.",
    )
    p_diff_inputs.add_argument(
        "other_dir",
        help="Directory containing frame_*_input.bin files to compare against ours.",
    )
    p_diff_inputs.set_defaults(func=cmd_diff_inputs)

    sub.add_parser(
        "build-canonical-inputs",
        help="Regenerate inputs/ from the documented patterns (maintainer command).",
    ).set_defaults(func=cmd_build_canonical_inputs)

    sub.add_parser(
        "check-signals",
        help="Validate signals/ structurally + first-chip polarity (L3 oracle).",
    ).set_defaults(func=cmd_check_signals)

    p_diff_signals = sub.add_parser(
        "diff-signals",
        help="Compare a directory of L3 signal vectors to ours, byte-by-byte.",
    )
    p_diff_signals.add_argument(
        "other_dir",
        help="Directory containing signal_*_12s.iq[.gz] files to compare against ours.",
    )
    p_diff_signals.set_defaults(func=cmd_diff_signals)

    sub.add_parser(
        "check-decode",
        help="Verify references/pocketsdr-afs/decoded/ matches frames/ payloads (L4 oracle).",
    ).set_defaults(func=cmd_check_decode)

    p_diff_decode = sub.add_parser(
        "diff-decode",
        help="Validate a third party's decoded outputs against the original input "
        "(frames/ + inputs/) — the Level 4 pass criterion.",
    )
    p_diff_decode.add_argument(
        "other_dir",
        help="Directory of decoded_signal_*.bin / decoded_fec_signal_*.bin to validate.",
    )
    p_diff_decode.add_argument(
        "--reference",
        metavar="DIR",
        default=None,
        help="Reference set to validate against (must contain frames/ and inputs/). "
        "Default: this repo. Use an agreed external set for the workshop round-robin.",
    )
    p_diff_decode.add_argument(
        "--json",
        action="store_true",
        help="Emit one machine-readable matrix cell as JSON (see INTEROP-ROUNDROBIN.md).",
    )
    p_diff_decode.add_argument(
        "--vs-pocketsdr",
        action="store_true",
        help="Also diff against the bundled PocketSDR reference decode (secondary).",
    )
    p_diff_decode.set_defaults(func=cmd_diff_decode)

    sub.add_parser(
        "check-parsed",
        help="Validate parsed/ JSONs structurally + spec-range + ground-truth (L5 oracle).",
    ).set_defaults(func=cmd_check_parsed)

    p_diff_parsed = sub.add_parser(
        "diff-parsed",
        help="Compare a directory of parsed_*.json files against ours, field-by-field.",
    )
    p_diff_parsed.add_argument(
        "other_dir",
        help="Directory containing parsed_*.json files to compare against ours.",
    )
    p_diff_parsed.set_defaults(func=cmd_diff_parsed)

    sub.add_parser(
        "check-fec",
        help="Validate fec/ component vectors (BCH/CRC/interleaver/LDPC) structurally "
        "+ self-consistency + BCH frame-anchor (Phase-3 oracle).",
    ).set_defaults(func=cmd_check_fec)

    p_diff_fec = sub.add_parser(
        "diff-fec",
        help="Compare a directory of fec/*.json component vectors against ours.",
    )
    p_diff_fec.add_argument(
        "other_dir",
        help="Directory containing fec/*.json vectors to compare against ours.",
    )
    p_diff_fec.set_defaults(func=cmd_diff_fec)

    sub.add_parser(
        "verify-manifest",
        help="Re-hash every file and compare to manifest.json.",
    ).set_defaults(func=cmd_verify_manifest)

    sub.add_parser(
        "rebuild-manifest",
        help="Regenerate manifest.json from the contents of codes/ and references/.",
    ).set_defaults(func=cmd_rebuild_manifest)

    p_refresh = sub.add_parser(
        "refresh",
        help="Download Annex 3 reference files from a URL and update the manifest.",
    )
    p_refresh.add_argument(
        "--base-url",
        help="Base URL; filenames are appended (e.g. https://example/annex3/).",
    )
    p_refresh.add_argument(
        "--url",
        action="append",
        dest="url_map",
        metavar="FILE=URL",
        help="Per-file URL override, repeatable (e.g. 006_GoldCode...txt=https://...)",
    )
    p_refresh.set_defaults(func=cmd_refresh)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
