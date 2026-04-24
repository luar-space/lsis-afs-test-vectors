"""LSIS-AFS Level 1 test-vector validator.

Subcommands
-----------
check-annex3
    Confirm every code in ``codes/`` matches the corresponding entry in the
    Annex 3 reference files in ``references/``.  This is the normative
    correctness proof: 210 PRNs × 3 code types (Gold, Weil-10230, Weil-1500).

check-lans-afs-sim
    Confirm every code in ``codes/`` matches chip-for-chip against the
    LANS-AFS-SIM reference dumps in ``references/lans-afs-sim/``.  This is
    a second, independent oracle: 210 PRNs × 2 code families (Gold, Weil-10230).

diff
    Compare a directory of test vectors (e.g. your own implementation's
    output) against the vectors in this repository.  Reports per-PRN,
    per-section pass/fail.

verify-manifest
    Re-compute SHA256 for every file listed in ``manifest.json`` and confirm
    nothing has been altered.

rebuild-manifest
    Regenerate ``manifest.json`` from the current contents of ``codes/`` and
    ``references/``.  Maintainer command; run after adding or updating any
    content file so that ``verify-manifest`` keeps passing.

refresh
    Download the Annex 3 reference files from a user-supplied URL, replacing
    the local copies and updating ``manifest.json`` SHA256s.  Use when a new
    normative release is published.

Stdlib-only — no third-party dependencies required to run this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
CODES_DIR = REPO_ROOT / "codes"
REFERENCES_DIR = REPO_ROOT / "references"
ANNEX3_DIR = REFERENCES_DIR / "annex-3"
LANS_DIR = REFERENCES_DIR / "lans-afs-sim"
LANS_CODES_DIR = LANS_DIR / "codes"
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
    for sub in ("codes", "references"):
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
    # Drop any stale scalar-schema key before rewriting.
    manifest.pop("level", None)
    manifest.update(
        {
            "version": "1.0",
            "levels": [1],
            "generated": datetime.now(UTC).isoformat(),
            "implementation": "LuarSpace",
            "spec": "LSIS-AFS V1.0, 29 January 2025",
            "oracles": [
                "LNIS AD1 Volume A, Annex 3 (10 December 2024) — normative",
                "LANS-AFS-SIM (BSD-2-Clause, © 2025 Takuji Ebinuma) — independent",
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
        description="LSIS-AFS Level 1 test-vector validator.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "check-annex3",
        help="Compare codes/ against references/ Annex 3 (normative reference).",
    ).set_defaults(func=cmd_check_annex3)

    sub.add_parser(
        "check-lans-afs-sim",
        help="Compare codes/ against references/lans-afs-sim/ binary dumps (second oracle).",
    ).set_defaults(func=cmd_check_lans_afs_sim)

    p_diff = sub.add_parser(
        "diff",
        help="Compare a directory of test vectors to ours, section-by-section.",
    )
    p_diff.add_argument(
        "other_dir",
        help="Directory containing codes_prnNNN.hex files to compare against ours.",
    )
    p_diff.set_defaults(func=cmd_diff)

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
