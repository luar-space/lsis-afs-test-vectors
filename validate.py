"""LSIS-AFS interoperability test-vector validator (Levels 1–3).

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
import gzip
import hashlib
import json
import re
import struct
import sys
import urllib.request
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


def parse_codes_hex(path: Path) -> dict[str, str]:
    """Parse a ``codes_prnNNN.hex`` file into a dict of section → uppercase hex."""
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
# Q-channel chip rate is 5.115 Mchip/s; each Q-chip spans 2 samples.
SIGNAL_Q_SAMPLES_PER_CHIP = 2

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


def _rebuild_manifest() -> int:
    """Recompute SHA256s over codes/ and references/ and overwrite manifest.json.

    Returns the number of files hashed.
    """
    entries: dict[str, str] = {}
    for sub in ("codes", "frames", "inputs", "signals", "references"):
        base = REPO_ROOT / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
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
            "levels": [1, 2, 3],
            "implementation": "LuarSpace",
            "spec": "LSIS-AFS V1.0, 29 January 2025",
            "oracles": [
                "LNIS AD1 Volume A, Annex 3 (10 December 2024) — L1 normative",
                "LANS-AFS-SIM (BSD-2-Clause, © 2025 Takuji Ebinuma) — L1+L2 independent",
                "LSIS-AFS V1.0 §2.4 + Gateway 3 checklist — L2 structural",
                "interoperability.pdf Signal Export Format + LSIS V1.0 §4 + first-chip "
                "polarity (chains L1 codes + L2 sync prefix into L3) — L3 structural",
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
        description="LSIS-AFS interoperability test-vector validator (Levels 1-3).",
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
