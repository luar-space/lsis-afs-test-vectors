"""End-to-end tests for the validator CLI and its subcommands.

These tests exercise every subcommand against the real test vectors
shipped in the repository — codes (Level 1) and frames (Level 2).
They're the pytest equivalent of running each ``validate.py``
subcommand from the command line, plus positive+negative pairs for
``diff`` / ``diff-frames`` and an error-path test for ``refresh``.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATE = REPO_ROOT / "validate.py"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run validate.py as a subprocess and return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(VALIDATE), *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


# ─────────────────────────────── oracles ────────────────────────────────────


def test_check_annex3_passes() -> None:
    result = run("check-annex3")
    assert result.returncode == 0, result.stderr
    assert "210/210" in result.stdout
    assert "OK — all 630 codes" in result.stdout


def test_check_lans_afs_sim_passes() -> None:
    result = run("check-lans-afs-sim")
    assert result.returncode == 0, result.stderr
    assert "210/210" in result.stdout
    assert "OK — all 420 code dumps" in result.stdout


def test_check_frames_passes() -> None:
    result = run("check-frames")
    assert result.returncode == 0, result.stderr
    assert "7/7" in result.stdout
    assert "OK — all 7 frames pass spec structural checks" in result.stdout


def test_check_lans_afs_sim_frames_passes() -> None:
    result = run("check-lans-afs-sim-frames")
    assert result.returncode == 0, result.stderr
    assert "7/7" in result.stdout
    assert "OK — all 7 frames bit-exact against LANS-AFS-SIM" in result.stdout


def test_check_frames_counts_only_clean_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A payload fault must reduce the reported structural pass count."""
    mutated = tmp_path / "frames-mutated"
    shutil.copytree(REPO_ROOT / "frames", mutated)

    target = mutated / "frame_message_1.bin"
    data = bytearray(target.read_bytes())
    data[64] = 2  # corrupt the first payload symbol without touching the header
    target.write_bytes(bytes(data))

    monkeypatch.setattr(validate, "FRAMES_DIR", mutated)
    rc = validate.cmd_check_frames()
    captured = capsys.readouterr()

    assert rc == 1
    assert "Structural checks:  6/7" in captured.out
    assert "do not match sync pattern" in captured.err


# Each entry mutates one byte (or range) of the 64-byte header and asserts
# check-frames flags it. The mutation_substr is checked against captured stderr
# so the test fails noisily if a structural rule silently stops being enforced.
_HEADER_MUTATIONS = [
    # (test_id, offset, replacement_byte, expected_failure_substring)
    ("magic", 0, 0x00, "magic="),
    ("version", 8, 0x99, "version=153"),
    ("frame_length", 12, 0x42, "frame_length="),
    ("prn", 16, 0xFF, "header PRN="),
]


@pytest.mark.parametrize(("label", "offset", "value", "expected"), _HEADER_MUTATIONS)
def test_check_frames_catches_header_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    label: str,
    offset: int,
    value: int,
    expected: str,
) -> None:
    """Every Gateway 3 header field must be enforced by check-frames."""
    mutated = tmp_path / f"frames-{label}"
    shutil.copytree(REPO_ROOT / "frames", mutated)

    target = mutated / "frame_message_1.bin"
    data = bytearray(target.read_bytes())
    data[offset] = value
    target.write_bytes(bytes(data))

    monkeypatch.setattr(validate, "FRAMES_DIR", mutated)
    rc = validate.cmd_check_frames()
    captured = capsys.readouterr()

    assert rc == 1, f"check-frames failed to flag a corrupted {label} byte at offset {offset}"
    assert "Structural checks:  6/7" in captured.out
    assert expected in captured.err, (
        f"{label} mutation produced unexpected stderr: {captured.err!r}"
    )


def test_verify_manifest_passes() -> None:
    result = run("verify-manifest")
    assert result.returncode == 0, result.stderr
    assert "OK — all SHA256s match" in result.stdout


# ─────────────────────────────── diff ───────────────────────────────────────


def test_diff_self_is_clean() -> None:
    """Comparing codes/ against itself must report bit-exact match."""
    result = run("diff", "codes")
    assert result.returncode == 0, result.stderr
    assert "OK — bit-exact match" in result.stdout


def test_diff_detects_mutation(tmp_path: Path) -> None:
    """A deliberately-mutated copy must exit non-zero and name the differing PRN."""
    mutated = tmp_path / "codes-mutated"
    shutil.copytree(REPO_ROOT / "codes", mutated)

    target = mutated / "codes_prn001.hex"
    text = target.read_text()
    # Flip the first hex digit of the first `hex:` line (always the GOLD_CODE section).
    idx = text.index("hex: ") + len("hex: ")
    original = text[idx]
    flipped = "1" if original != "1" else "2"
    target.write_text(text[:idx] + flipped + text[idx + 1 :])

    result = run("diff", str(mutated))
    assert result.returncode != 0
    assert "PRN 1 GOLD_CODE" in result.stderr or "PRN 1 GOLD_CODE" in result.stdout


def test_diff_missing_file_reported(tmp_path: Path) -> None:
    """Dropping a file from the copy must show up as a missing PRN."""
    partial = tmp_path / "codes-partial"
    shutil.copytree(REPO_ROOT / "codes", partial)
    (partial / "codes_prn001.hex").unlink()

    result = run("diff", str(partial))
    assert result.returncode != 0
    assert "missing: 1" in result.stdout


# ─────────────────────────────── diff-frames ────────────────────────────────


def test_diff_frames_self_is_clean() -> None:
    """Comparing frames/ against itself must report bit-exact match."""
    result = run("diff-frames", "frames")
    assert result.returncode == 0, result.stderr
    assert "OK — bit-exact match" in result.stdout


def test_diff_frames_detects_mutation(tmp_path: Path) -> None:
    """A deliberately-mutated copy must exit non-zero and name the differing frame."""
    mutated = tmp_path / "frames-mutated"
    shutil.copytree(REPO_ROOT / "frames", mutated)

    target = mutated / "frame_message_1.bin"
    data = bytearray(target.read_bytes())
    # Flip a payload byte (skipping the 64-byte header to land on a real symbol).
    data[100] = 1 if data[100] == 0 else 0
    target.write_bytes(bytes(data))

    result = run("diff-frames", str(mutated))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "frame_message_1" in combined


def test_diff_frames_missing_file_reported(tmp_path: Path) -> None:
    """Dropping a frame from the copy must show up as missing."""
    partial = tmp_path / "frames-partial"
    shutil.copytree(REPO_ROOT / "frames", partial)
    (partial / "frame_boundary.bin").unlink()

    result = run("diff-frames", str(partial))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "frame_boundary" in combined or "missing: 1" in combined


def test_diff_frames_detects_header_mutation(tmp_path: Path) -> None:
    """Mutating only a header field (PRN here) must NOT pass silently."""
    mutated = tmp_path / "frames-header-mutated"
    shutil.copytree(REPO_ROOT / "frames", mutated)

    target = mutated / "frame_message_1.bin"
    data = bytearray(target.read_bytes())
    # Frame header PRN field is bytes 16..20 (uint32 little-endian).
    # frame_message_1 expects PRN=1; flip it to 99 — payload stays identical.
    data[16:20] = (99).to_bytes(4, "little")
    target.write_bytes(bytes(data))

    result = run("diff-frames", str(mutated))
    assert result.returncode != 0, (
        f"diff-frames silently passed a header-mutated file: {result.stdout}"
    )
    combined = result.stdout + result.stderr
    assert "frame_message_1" in combined and "prn" in combined.lower()


def test_check_lans_afs_sim_frames_handles_truncated_local_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated frames/frame_*.bin must produce a clean failure, not crash."""
    fake_frames = tmp_path / "frames"
    shutil.copytree(REPO_ROOT / "frames", fake_frames)
    target = fake_frames / "frame_message_1.bin"
    # Drop the last byte — file becomes 6063 bytes instead of 6064.
    target.write_bytes(target.read_bytes()[:-1])

    monkeypatch.setattr(validate, "FRAMES_DIR", fake_frames)

    rc = validate.cmd_check_lans_afs_sim_frames()
    assert rc == 1, "truncated local frame should be a failure, not a crash"


# ─────────────────────────────── canonical inputs ──────────────────────────


def test_check_canonical_inputs_passes() -> None:
    result = run("check-canonical-inputs")
    assert result.returncode == 0, result.stderr
    assert "7/7" in result.stdout
    assert "OK — all 7 canonical input files" in result.stdout


def test_canonical_inputs_have_expected_size() -> None:
    """Every canonical input file is exactly 2868 bytes (1176 SB2 + 846 SB3 + 846 SB4)."""
    for filename, _pattern in validate.INPUT_TEST_VECTORS:
        path = REPO_ROOT / "inputs" / filename
        assert path.stat().st_size == validate.INPUT_BYTE_COUNT, (
            f"{filename} is {path.stat().st_size} bytes, expected {validate.INPUT_BYTE_COUNT}"
        )


def test_canonical_inputs_carry_faq_q21_normalisation() -> None:
    """SB2 bits 1150..1175 must hold the FAQ Q21 alternating-0/1 pattern in every file."""
    expected_spare = bytes(i % 2 for i in range(validate.SB2_SPARE_BITS_LENGTH))
    for filename, _pattern in validate.INPUT_TEST_VECTORS:
        data = (REPO_ROOT / "inputs" / filename).read_bytes()
        sb2 = data[: validate.SB2_BITS]
        spare = sb2[
            validate.SB2_SPARE_BITS_OFFSET : validate.SB2_SPARE_BITS_OFFSET
            + validate.SB2_SPARE_BITS_LENGTH
        ]
        assert spare == expected_spare, (
            f"{filename}: SB2 spare bits 1150..1175 do not match FAQ Q21 normalisation"
        )


def test_diff_inputs_self_is_clean() -> None:
    """Comparing inputs/ against itself must report bit-exact match."""
    result = run("diff-inputs", "inputs")
    assert result.returncode == 0, result.stderr
    assert "OK — bit-exact match" in result.stdout


def test_diff_inputs_detects_mutation(tmp_path: Path) -> None:
    """A deliberately-mutated copy must exit non-zero and localise the disagreement."""
    mutated = tmp_path / "inputs-mutated"
    shutil.copytree(REPO_ROOT / "inputs", mutated)

    target = mutated / "frame_message_4_input.bin"
    data = bytearray(target.read_bytes())
    # Flip a SB2 byte well clear of the FAQ Q21 spare-bit window.
    data[100] = 1 if data[100] == 0 else 0
    target.write_bytes(bytes(data))

    result = run("diff-inputs", str(mutated))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "frame_message_4_input" in combined
    assert "SB2 bit 100" in combined


def test_diff_inputs_missing_file_reported(tmp_path: Path) -> None:
    """Dropping an input from the copy must show up as missing."""
    partial = tmp_path / "inputs-partial"
    shutil.copytree(REPO_ROOT / "inputs", partial)
    (partial / "frame_boundary_input.bin").unlink()

    result = run("diff-inputs", str(partial))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "frame_boundary_input" in combined or "missing: 1" in combined


def test_diff_inputs_wrong_size_reported(tmp_path: Path) -> None:
    """Wrong file size must produce a clean failure."""
    truncated = tmp_path / "inputs-truncated"
    shutil.copytree(REPO_ROOT / "inputs", truncated)

    target = truncated / "frame_message_1_input.bin"
    target.write_bytes(target.read_bytes()[:-1])  # 2867 bytes

    result = run("diff-inputs", str(truncated))
    assert result.returncode != 0
    assert "expected 2868" in result.stderr


def test_build_canonical_inputs_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running build-canonical-inputs twice produces the same bytes."""
    target = tmp_path / "inputs"
    monkeypatch.setattr(validate, "INPUTS_DIR", target)
    rc = validate.cmd_build_canonical_inputs()
    assert rc == 0
    first = {p.name: p.read_bytes() for p in sorted(target.iterdir())}

    rc = validate.cmd_build_canonical_inputs()
    assert rc == 0
    second = {p.name: p.read_bytes() for p in sorted(target.iterdir())}

    assert first == second
    # And what was just rebuilt must match what's shipped.
    for name, data in first.items():
        shipped = (REPO_ROOT / "inputs" / name).read_bytes()
        assert data == shipped, f"{name}: rebuilt bytes do not match shipped file"


# ─────────────────────────────── refresh ────────────────────────────────────


def test_refresh_without_url_is_helpful() -> None:
    """No URL → exit 2 with a pointer at the canonical LNIS attachment set."""
    result = run("refresh")
    assert result.returncode == 2
    assert "--base-url" in result.stderr or "--url" in result.stderr
    assert "LNIS" in result.stderr


# ─────────────────────────────── manifest invariants ───────────────────────


def test_manifest_structure() -> None:
    manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
    assert manifest["version"] == "1.0"
    assert manifest["levels"] == [1, 2, 3, 4]  # grows as future drops land
    assert "oracles" in manifest
    # L1 normative + L1/L2 LANS + L2 structural + L3 structural + L4 PocketSDR-AFS
    assert len(manifest["oracles"]) >= 5
    # 630 L1 codes + 7 frames + 7 LANS frames + 7 canonical inputs + 10 L3 signals
    # + 10 L4 channel-symbol + 8 L4 post-FEC outputs + harness sources + readmes
    assert len(manifest["files"]) >= 700


def test_manifest_covers_every_code_file() -> None:
    manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
    files = manifest["files"]
    for prn in range(1, 211):
        rel = f"codes/codes_prn{prn:03d}.hex"
        assert rel in files, f"{rel} missing from manifest"


def test_manifest_covers_every_lans_dump() -> None:
    manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
    files = manifest["files"]
    for prn in range(1, 211):
        for prefix in ("gold", "weil"):
            rel = f"references/lans-afs-sim/codes/{prefix}_prn_{prn:03d}.bin"
            assert rel in files, f"{rel} missing from manifest"


def test_manifest_covers_every_frame_file() -> None:
    manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
    files = manifest["files"]
    expected = [f"frame_message_{i}.bin" for i in range(1, 6)] + ["frame_boundary.bin"]
    for name in expected:
        rel = f"frames/{name}"
        assert rel in files, f"{rel} missing from manifest"


def test_manifest_covers_every_lans_frame_dump() -> None:
    manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
    files = manifest["files"]
    expected = [f"lans_frame_message_{i}.bin" for i in range(1, 6)] + ["lans_frame_boundary.bin"]
    for name in expected:
        rel = f"references/lans-afs-sim/frames/{name}"
        assert rel in files, f"{rel} missing from manifest"


def test_manifest_covers_every_annex3_file() -> None:
    manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
    for name in (
        "006_GoldCode2046hex210prns.txt",
        "007_l1cp_hex210prns.txt",
        "008_Weil1500hex210prns.txt",
    ):
        assert f"references/annex-3/{name}" in manifest["files"]


def test_build_config_includes_runtime_data() -> None:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    wheel = text.split("[tool.hatch.build.targets.wheel]", maxsplit=1)[1]
    wheel = wheel.split("[tool.hatch.build.targets.sdist]", maxsplit=1)[0]
    sdist = text.split("[tool.hatch.build.targets.sdist]", maxsplit=1)[1]
    sdist = sdist.split("[dependency-groups]", maxsplit=1)[0]

    for section in (wheel, sdist):
        for needle in (
            '"codes"',
            '"frames"',
            '"inputs"',
            '"signals"',
            '"references"',
            '"manifest.json"',
        ):
            assert needle in section


# ─────────────────────────────── help / no-arg ──────────────────────────────


def test_no_args_shows_help_and_errors() -> None:
    result = run()
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "check-annex3" in combined
    assert "check-lans-afs-sim" in combined


@pytest.mark.parametrize(
    "cmd",
    [
        "check-annex3",
        "check-lans-afs-sim",
        "check-frames",
        "check-lans-afs-sim-frames",
        "check-canonical-inputs",
        "check-signals",
        "check-decode",
        "diff",
        "diff-frames",
        "diff-inputs",
        "diff-signals",
        "diff-decode",
        "build-canonical-inputs",
        "verify-manifest",
        "rebuild-manifest",
        "refresh",
    ],
)
def test_every_subcommand_advertised_in_help(cmd: str) -> None:
    result = run("--help")
    assert result.returncode == 0
    assert cmd in result.stdout


def test_rebuild_manifest_is_idempotent(tmp_path: Path) -> None:
    """rebuild-manifest must leave verify-manifest passing afterwards."""
    # Snapshot before / after
    before = (REPO_ROOT / "manifest.json").read_text()
    result = run("rebuild-manifest")
    assert result.returncode == 0, result.stderr
    assert "manifest rebuilt" in result.stdout
    # verify-manifest must still pass immediately after a rebuild
    verify = run("verify-manifest")
    assert verify.returncode == 0, verify.stderr
    # Structural checks on the regenerated file
    after = json.loads((REPO_ROOT / "manifest.json").read_text())
    assert after["levels"] == [1, 2, 3, 4]
    assert len(after["files"]) >= 700
    # Restore the original manifest so the test has no side-effect on other tests
    (REPO_ROOT / "manifest.json").write_text(before)
    _ = tmp_path  # unused — reserved for future symlink isolation if needed


def test_rebuild_manifest_excludes_generated_artefacts(tmp_path: Path) -> None:
    """A maintainer importing the harness modules creates __pycache__/*.pyc
    files under references/pocketsdr-afs/harnesses/.  Those are git-ignored,
    so they don't exist in a clean checkout — but rebuild-manifest must not
    pull them into manifest.json either, otherwise verify-manifest fails on
    every clean checkout (https://github.com/luar-space/lsis-afs-test-vectors
    issue: drop-by-drop manifest churn from local imports)."""
    cache_dir = REPO_ROOT / "references/pocketsdr-afs/harnesses/__pycache__"
    cache_dir.mkdir(parents=True, exist_ok=True)
    fake_pyc = cache_dir / "regression_test.cpython-312.pyc"
    fake_pyc.write_bytes(b"\x00\x00\x00\x00fake bytecode")

    before = (REPO_ROOT / "manifest.json").read_text()
    try:
        result = run("rebuild-manifest")
        assert result.returncode == 0, result.stderr
        manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
        files = manifest["files"]

        # Hard assertion: no entry under the manifest may be a .pyc or live
        # under any __pycache__ directory.
        leaks = [k for k in files if "__pycache__" in k or k.endswith(".pyc")]
        assert leaks == [], f"manifest leaked generated artefacts: {leaks}"

        # And specifically: the file we just dropped is NOT in the manifest.
        leak_rel = fake_pyc.relative_to(REPO_ROOT).as_posix()
        assert leak_rel not in files, (
            f"{leak_rel} leaked into manifest despite __pycache__ exclusion"
        )

        # Sanity: the unrelated harness sources ARE still there.
        assert "references/pocketsdr-afs/harnesses/decode_signal.py" in files, (
            "regular harness sources should still be hashed"
        )
    finally:
        fake_pyc.unlink(missing_ok=True)
        # Drop the dir if we created it from scratch (don't trample a pre-existing one).
        with contextlib.suppress(OSError):
            cache_dir.rmdir()
        (REPO_ROOT / "manifest.json").write_text(before)
    _ = tmp_path  # unused — kept so the fixture-injection signature is stable


# ─────────────────────────────── L3 signals ────────────────────────────────


def _gunzip_to(path: Path, dst: Path) -> Path:
    """Copy a possibly-gzipped signal file into dst (uncompressed)."""

    if path.suffix == ".gz":
        out = dst / path.name[: -len(".gz")]
        with gzip.open(path, "rb") as f_in, out.open("wb") as f_out:
            while chunk := f_in.read(1 << 20):
                f_out.write(chunk)
        return out
    out = dst / path.name
    out.write_bytes(path.read_bytes())
    return out


def test_check_signals_passes() -> None:
    result = run("check-signals")
    assert result.returncode == 0, result.stderr
    assert "10/10" in result.stdout
    assert "OK — all 10 signals pass" in result.stdout


def test_check_signals_catches_header_magic_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Flipping the LSISIQ magic byte must be flagged."""

    mutated = tmp_path / "signals-mutated"
    mutated.mkdir()
    src = REPO_ROOT / "signals" / "signal_message_1_12s.iq.gz"
    raw = gzip.decompress(src.read_bytes())
    raw = b"X" + raw[1:]  # corrupt the first magic byte
    (mutated / "signal_message_1_12s.iq.gz").write_bytes(gzip.compress(raw, compresslevel=1))
    # Copy other files unchanged so the loop still finds them.
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        if name == "signal_message_1_12s.iq.gz":
            continue
        (mutated / name).write_bytes((REPO_ROOT / "signals" / name).read_bytes())

    monkeypatch.setattr(validate, "SIGNALS_DIR", mutated)
    rc = validate.cmd_check_signals()
    captured = capsys.readouterr()
    assert rc == 1
    assert "Structural + first-chip polarity:  9/10" in captured.out
    assert "magic=" in captured.err


def test_check_signals_catches_sample_range_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Writing a sample of 0.5 (not in {-1, +1}) must be flagged."""

    mutated = tmp_path / "signals-mutated"
    mutated.mkdir()
    src = REPO_ROOT / "signals" / "signal_message_1_12s.iq.gz"
    raw = bytearray(gzip.decompress(src.read_bytes()))
    # Overwrite the very first I sample (offset 128..132) with 0.5f.
    raw[128:132] = struct.pack("<f", 0.5)
    (mutated / "signal_message_1_12s.iq.gz").write_bytes(gzip.compress(bytes(raw), compresslevel=1))
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        if name == "signal_message_1_12s.iq.gz":
            continue
        (mutated / name).write_bytes((REPO_ROOT / "signals" / name).read_bytes())

    monkeypatch.setattr(validate, "SIGNALS_DIR", mutated)
    rc = validate.cmd_check_signals()
    captured = capsys.readouterr()
    assert rc == 1
    assert "Structural + first-chip polarity:  9/10" in captured.out
    # Could be flagged either by the probe-sample range check or the polarity
    # check (both fire on the same byte).  Accept either signal.
    assert "0.5" in captured.err or "I[0]=" in captured.err


def test_check_signals_catches_first_chip_polarity_flip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Inverting the I-channel polarity for chip 0 must be flagged by the polarity check."""

    mutated = tmp_path / "signals-mutated"
    mutated.mkdir()
    src = REPO_ROOT / "signals" / "signal_message_1_12s.iq.gz"
    raw = bytearray(gzip.decompress(src.read_bytes()))
    # Read the first I sample, negate it, write back.  This corrupts only the
    # polarity at sample 0; the value stays in {-1, +1} so the range check
    # still passes, isolating the failure to the polarity oracle.
    (i0,) = struct.unpack("<f", bytes(raw[128:132]))
    raw[128:132] = struct.pack("<f", -i0)
    (mutated / "signal_message_1_12s.iq.gz").write_bytes(gzip.compress(bytes(raw), compresslevel=1))
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        if name == "signal_message_1_12s.iq.gz":
            continue
        (mutated / name).write_bytes((REPO_ROOT / "signals" / name).read_bytes())

    monkeypatch.setattr(validate, "SIGNALS_DIR", mutated)
    rc = validate.cmd_check_signals()
    captured = capsys.readouterr()
    assert rc == 1
    assert "I[0]=" in captured.err
    assert "Gold[0]" in captured.err


def test_check_signals_catches_symbol_120_polarity_flip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Inverting the I-channel polarity at frame symbol 120 must be flagged.

    Symbol 120 is the first content-distinguishing frame symbol (symbols
    0..67 are sync, 68..119 are BCH SB1 of the shared FID/TOI), so the
    sym-120 polarity probe (added in v0.3.0) is what catches LDPC /
    interleaver bit-ordering errors that the chip-0 probe at sample 0
    would not.  This test is the regression lock for that oracle: flip
    the I sample at the start of symbol 120 (sample index 120 × 20460 =
    2 455 200) and confirm ``check-signals`` reports the sym_120 label
    and the correct sample index.
    """
    mutated = tmp_path / "signals-mutated"
    mutated.mkdir()
    src = REPO_ROOT / "signals" / "signal_message_1_12s.iq.gz"
    raw = bytearray(gzip.decompress(src.read_bytes()))
    # Sample N's I-byte starts at offset 128 + N × 8.  N = 2 455 200 (start
    # of symbol 120), so byte offset = 128 + 19 641 600 = 19 641 728.
    sym120_sample_idx = (
        validate.SIGNAL_DISTINGUISHING_SYMBOL_IDX * validate.SIGNAL_I_SAMPLES_PER_SYMBOL
    )
    assert sym120_sample_idx == 2_455_200, sym120_sample_idx
    offset = validate.SIGNAL_HEADER_LEN + sym120_sample_idx * validate.SIGNAL_BYTES_PER_SAMPLE_PAIR
    (i_val,) = struct.unpack("<f", bytes(raw[offset : offset + 4]))
    raw[offset : offset + 4] = struct.pack("<f", -i_val)
    (mutated / "signal_message_1_12s.iq.gz").write_bytes(gzip.compress(bytes(raw), compresslevel=1))
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        if name == "signal_message_1_12s.iq.gz":
            continue
        (mutated / name).write_bytes((REPO_ROOT / "signals" / name).read_bytes())

    monkeypatch.setattr(validate, "SIGNALS_DIR", mutated)
    rc = validate.cmd_check_signals()
    captured = capsys.readouterr()
    assert rc == 1
    # The error message uses the sym_120 label and reports the sample index.
    assert "sym_120" in captured.err, captured.err
    assert "I[2455200]" in captured.err, captured.err
    # Sanity: the chip-0 probe at sample 0 still passes (no I[0] error reported).
    assert "I[0]=" not in captured.err, captured.err


def test_diff_signals_self_is_clean(tmp_path: Path) -> None:
    """Comparing signals/ against an unzipped copy of itself must report bit-exact match."""
    other = tmp_path / "signals-copy"
    other.mkdir()
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        _gunzip_to(REPO_ROOT / "signals" / name, other)
    result = run("diff-signals", str(other))
    assert result.returncode == 0, result.stderr
    assert "OK — bit-exact match" in result.stdout


def test_diff_signals_detects_mutation(tmp_path: Path) -> None:
    """A deliberately-mutated copy must exit non-zero and name the differing signal."""

    other = tmp_path / "signals-mutated"
    other.mkdir()
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        src = REPO_ROOT / "signals" / name
        raw = bytearray(gzip.decompress(src.read_bytes()))
        if name == "signal_message_1_12s.iq.gz":
            # Flip a sample deep in the payload (sample 1000) so we know
            # diff-signals locates it correctly.
            offset = validate.SIGNAL_HEADER_LEN + 1000 * 8
            (val,) = struct.unpack("<f", bytes(raw[offset : offset + 4]))
            raw[offset : offset + 4] = struct.pack("<f", -val)
        (other / name).write_bytes(gzip.compress(bytes(raw), compresslevel=1))

    result = run("diff-signals", str(other))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "signal_message_1_12s" in combined


def test_diff_signals_missing_file_reported(tmp_path: Path) -> None:
    """Dropping a signal from the copy must show up as missing."""
    other = tmp_path / "signals-partial"
    other.mkdir()
    # Copy everything except signal_message_5_12s.iq.gz.
    dropped = "signal_message_5_12s.iq.gz"
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        if name == dropped:
            continue
        (other / name).write_bytes((REPO_ROOT / "signals" / name).read_bytes())

    result = run("diff-signals", str(other))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "signal_message_5" in combined or "missing: 1" in combined


def test_signals_are_strict_bpsk_at_probe_sites() -> None:
    """End-to-end smoke: every shipped signal has I,Q ∈ {-1, +1} at the probe sites."""
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        data = validate._read_signal_bytes(REPO_ROOT / "signals" / name)
        for sample_idx in (0, 10, 20, 30):
            i_val, q_val = validate._read_iq_pair(data, sample_idx)
            assert i_val in (1.0, -1.0), f"{name}: I[{sample_idx}]={i_val}"
            assert q_val in (1.0, -1.0), f"{name}: Q[{sample_idx}]={q_val}"


def test_manifest_covers_every_signal_file() -> None:
    manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
    files = manifest["files"]
    for name, _prn, _src in validate.SIGNAL_TEST_VECTORS:
        rel = f"signals/{name}"
        assert rel in files, f"{rel} missing from manifest"


# ─────────────────────────────── L4 decoded outputs ────────────────────────


def test_check_decode_passes() -> None:
    """check-decode must report 10/10 on both channel-symbol and post-FEC oracles."""
    result = run("check-decode")
    assert result.returncode == 0, result.stderr
    assert "Channel-symbol oracle: 10/10" in result.stdout
    assert "Post-FEC oracle:       10/10" in result.stdout
    assert "all 10 channel-symbol outputs match" in result.stdout
    assert "all 10 post-FEC outputs match" in result.stdout


def test_diff_decode_self_is_clean(tmp_path: Path) -> None:
    """The bundled reference is byte-equal to frames/+inputs/ (check-decode
    guarantees it), so diff-decode of a copy must pass the Level 4 criterion."""
    other = tmp_path / "decoded-copy"
    other.mkdir()
    for path in (REPO_ROOT / "references/pocketsdr-afs/decoded").iterdir():
        if path.is_file() and path.name.startswith("decoded_"):
            (other / path.name).write_bytes(path.read_bytes())
    result = run("diff-decode", str(other))
    assert result.returncode == 0, result.stderr
    assert "Post-FEC vs inputs/    (required):  10/10" in result.stdout
    assert "Channel-symbol vs frames/ (optional):  10/10 provided" in result.stdout
    assert "OK — decoded data matches original input exactly" in result.stdout


def test_diff_decode_vs_pocketsdr_secondary(tmp_path: Path) -> None:
    """--vs-pocketsdr adds the secondary diff against the bundled reference."""
    other = tmp_path / "decoded-copy"
    other.mkdir()
    for path in (REPO_ROOT / "references/pocketsdr-afs/decoded").iterdir():
        if path.is_file() and path.name.startswith("decoded_"):
            (other / path.name).write_bytes(path.read_bytes())
    result = run("diff-decode", str(other), "--vs-pocketsdr")
    assert result.returncode == 0, result.stderr
    assert "Level 4 pass criterion" in result.stdout
    assert "vs PocketSDR reference decode (secondary)" in result.stdout
    assert "OK — decoded data matches original input exactly" in result.stdout


def test_diff_decode_reports_missing_file(tmp_path: Path) -> None:
    """An empty directory must fail — post-FEC (the L4 criterion) is required."""
    other = tmp_path / "empty"
    other.mkdir()
    result = run("diff-decode", str(other))
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "missing in" in combined
    assert "post-FEC vs inputs/ is the Level 4 pass criterion" in combined


def test_diff_decode_postfec_only_passes(tmp_path: Path) -> None:
    """Post-FEC files alone (no channel-symbol tap) must pass — the channel
    layer is an optional diagnostic, the realistic third-party case."""
    other = tmp_path / "fec-only"
    other.mkdir()
    for path in (REPO_ROOT / "references/pocketsdr-afs/decoded").iterdir():
        if path.is_file() and path.name.startswith("decoded_fec_"):
            (other / path.name).write_bytes(path.read_bytes())
    result = run("diff-decode", str(other))
    assert result.returncode == 0, result.stderr
    assert "Post-FEC vs inputs/    (required):  10/10" in result.stdout
    assert "Channel-symbol vs frames/ (optional):  not provided — fine" in result.stdout
    assert "OK — decoded data matches original input exactly" in result.stdout


def test_diff_decode_detects_channel_mutation(tmp_path: Path) -> None:
    """A flip in a decoded_signal_*.bin must be reported."""
    other = tmp_path / "decoded-mutated"
    other.mkdir()
    for path in (REPO_ROOT / "references/pocketsdr-afs/decoded").iterdir():
        if path.is_file() and path.name.startswith("decoded_"):
            raw = bytearray(path.read_bytes())
            if path.name == "decoded_signal_message_1_12s.bin":
                raw[0] ^= 1  # flip first symbol
            (other / path.name).write_bytes(bytes(raw))

    result = run("diff-decode", str(other))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "decoded_signal_message_1_12s" in combined


def test_diff_decode_detects_fec_mutation(tmp_path: Path) -> None:
    """A flip in a decoded_fec_signal_*.bin must be reported."""
    other = tmp_path / "decoded-fec-mutated"
    other.mkdir()
    for path in (REPO_ROOT / "references/pocketsdr-afs/decoded").iterdir():
        if path.is_file() and path.name.startswith("decoded_"):
            raw = bytearray(path.read_bytes())
            if path.name == "decoded_fec_signal_message_3_12s.bin":
                raw[500] ^= 1  # flip a deep SB2 bit
            (other / path.name).write_bytes(bytes(raw))

    result = run("diff-decode", str(other))
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "decoded_fec_signal_message_3_12s" in combined


def test_check_decode_catches_size_mutation(tmp_path: Path) -> None:
    """check-decode must reject decoded files that are not the expected size."""
    target = REPO_ROOT / "references/pocketsdr-afs/decoded/decoded_signal_message_1_12s.bin"
    original = target.read_bytes()
    try:
        target.write_bytes(original[:5999])  # one byte short
        result = run("check-decode")
        assert result.returncode != 0
        assert "5999 bytes" in (result.stdout + result.stderr)
    finally:
        target.write_bytes(original)


def test_check_decode_catches_symbol_mutation(tmp_path: Path) -> None:
    """check-decode must reject a single-bit flip in any decoded_signal_*.bin file."""
    target = REPO_ROOT / "references/pocketsdr-afs/decoded/decoded_signal_message_3_12s.bin"
    original = target.read_bytes()
    try:
        raw = bytearray(original)
        raw[5000] ^= 1  # deep in payload, can't be confused with sync/SB1
        target.write_bytes(bytes(raw))
        result = run("check-decode")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "decoded_signal_message_3_12s" in combined
        assert "first symbol mismatch at index 5000" in combined
    finally:
        target.write_bytes(original)


def test_check_decode_catches_fec_mutation(tmp_path: Path) -> None:
    """check-decode must reject a single-bit flip in any decoded_fec_signal_*.bin file."""
    target = REPO_ROOT / "references/pocketsdr-afs/decoded/decoded_fec_signal_message_3_12s.bin"
    original = target.read_bytes()
    try:
        raw = bytearray(original)
        raw[500] ^= 1  # deep in SB2 data
        target.write_bytes(bytes(raw))
        result = run("check-decode")
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "decoded_fec_signal_message_3_12s" in combined
        assert "first FEC byte mismatch at index 500" in combined
    finally:
        target.write_bytes(original)


def test_manifest_covers_every_decoded_file() -> None:
    """Every decoded_signal_*.bin AND every decoded_fec_signal_*.bin (all 10
    of each, including the FID=3 boundary frames) must be SHA-pinned."""
    manifest = json.loads((REPO_ROOT / "manifest.json").read_text())
    files = manifest["files"]
    for name, _prn, _source_frame in validate.SIGNAL_TEST_VECTORS:
        chan_rel = f"references/pocketsdr-afs/decoded/{validate._decoded_filename_for(name)}"
        fec_rel = f"references/pocketsdr-afs/decoded/{validate._decoded_fec_filename_for(name)}"
        assert chan_rel in files, f"{chan_rel} missing from manifest"
        assert fec_rel in files, f"{fec_rel} missing from manifest"


def test_decoded_files_are_strictly_zero_or_one() -> None:
    """End-to-end smoke: every byte in decoded/ is 0 or 1 (symbol-domain)."""
    for path in (REPO_ROOT / "references/pocketsdr-afs/decoded").iterdir():
        if not path.is_file() or not path.name.startswith("decoded_"):
            continue
        data = path.read_bytes()
        expected = (
            validate.DECODED_FEC_FILE_LEN
            if path.name.startswith("decoded_fec_")
            else validate.DECODED_FILE_LEN
        )
        assert len(data) == expected, f"{path.name}: {len(data)} bytes (expected {expected})"
        assert all(b in (0, 1) for b in data), f"{path.name}: non-{{0,1}} byte"


def test_fec_outputs_match_inputs_byte_for_byte() -> None:
    """Smoke test: every decoded_fec_signal_*.bin (all 10 incl. FID=3 boundary
    frames, thanks to the bundled FID-bypass patch) matches the matching
    inputs/*_input.bin."""
    for name, _prn, source_frame in validate.SIGNAL_TEST_VECTORS:
        fec_path = (
            REPO_ROOT
            / "references/pocketsdr-afs/decoded"
            / validate._decoded_fec_filename_for(name)
        )
        input_path = REPO_ROOT / "inputs" / validate._input_filename_for(source_frame)
        assert fec_path.read_bytes() == input_path.read_bytes(), (
            f"{fec_path.name} != {input_path.name}"
        )
