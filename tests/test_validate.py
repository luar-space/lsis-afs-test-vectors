"""End-to-end tests for the validator CLI and its subcommands.

These tests exercise every subcommand against the real test vectors
shipped in the repository — codes (Level 1) and frames (Level 2).
They're the pytest equivalent of running each ``validate.py``
subcommand from the command line, plus positive+negative pairs for
``diff`` / ``diff-frames`` and an error-path test for ``refresh``.
"""

from __future__ import annotations

import json
import shutil
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
    assert "6/6" in result.stdout
    assert "OK — all 6 frames pass spec structural checks" in result.stdout


def test_check_lans_afs_sim_frames_passes() -> None:
    result = run("check-lans-afs-sim-frames")
    assert result.returncode == 0, result.stderr
    assert "6/6" in result.stdout
    assert "OK — all 6 frames bit-exact against LANS-AFS-SIM" in result.stdout


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
    assert "Structural checks:  5/6" in captured.out
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
    assert "Structural checks:  5/6" in captured.out
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
    assert "6/6" in result.stdout
    assert "OK — all 6 canonical input files" in result.stdout


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
    assert manifest["levels"] == [1, 2]  # grows as future drops land
    assert "oracles" in manifest
    assert len(manifest["oracles"]) >= 3  # L1 normative + L1/L2 LANS + L2 structural
    # 630 L1 codes + 6 frames + 6 LANS frames + 6 canonical inputs + readmes
    assert len(manifest["files"]) >= 651


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
        for needle in ('"codes"', '"frames"', '"references"', '"manifest.json"'):
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
        "diff",
        "diff-frames",
        "diff-inputs",
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
    assert after["levels"] == [1, 2]
    assert len(after["files"]) >= 645
    # Restore the original manifest so the test has no side-effect on other tests
    (REPO_ROOT / "manifest.json").write_text(before)
    _ = tmp_path  # unused — reserved for future symlink isolation if needed
