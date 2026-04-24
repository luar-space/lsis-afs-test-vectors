"""End-to-end tests for the validator CLI and its subcommands.

These tests exercise every subcommand against the real test vectors
shipped in the repository.  They're the pytest equivalent of running
``validate.py check-annex3``, ``check-lans-afs-sim`` and ``verify-manifest``
from the command line, plus a positive+negative pair for ``diff`` and an
error-path test for ``refresh``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert manifest["levels"] == [1]  # grows as future drops land
    assert "oracles" in manifest
    assert len(manifest["oracles"]) == 2  # Annex 3 + LANS-AFS-SIM
    assert len(manifest["files"]) >= 630  # 210 codes + 3 annex + 420 lans + readmes


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
        for needle in ('"codes"', '"references"', '"manifest.json"'):
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
        "diff",
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
    assert after["levels"] == [1]
    assert len(after["files"]) >= 630
    # Restore the original manifest so the test has no side-effect on other tests
    (REPO_ROOT / "manifest.json").write_text(before)
    _ = tmp_path  # unused — reserved for future symlink isolation if needed
