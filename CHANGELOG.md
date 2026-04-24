# Changelog

All notable releases of LSIS-AFS test vectors are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## Versioning

The package is a staged drop of the five interoperability test levels
prescribed by the competition. The 0.x series tracks that staged build-up:

| Version | Ships |
|:---|:---|
| **0.1.0** | Level 1 (this release) |
| 0.2.0     | + Level 2 |
| 0.3.0     | + Level 3 |
| 0.4.0     | + Level 4 |
| 0.5.0     | + Level 5 (feature-complete) |
| **1.0.0** | first stable — all five levels verified, formats frozen |

Patch versions (e.g. 0.1.1) carry corrections to an already-shipped level
without adding new ones.

## [0.1.0] — 2026-04-22

First public release — **Level 1: Code Generation** interoperability vectors.

### Added
- 210 × `codes_prnNNN.hex` spreading-code vectors in the standardized
  competition format, covering every PRN from 1 to 210.
  Each file contains `[GOLD_CODE]`, `[WEIL_PRIMARY]`, `[WEIL_TERTIARY]`
  and the four `[SECONDARY_Sx]` sections.
- `references/` — two bundled oracles plus the competition interoperability
  document itself as the schema reference:
  - `annex-3/` — LNIS AD1 Volume A Annex 3 attachment files (normative
    reference).
  - `lans-afs-sim/` — chip-level binary dumps from LANS-AFS-SIM
    (BSD-2-Clause, © 2025 Takuji Ebinuma), 420 files total.
  - `interoperability.pdf` — competition interoperability plan,
    redistributed verbatim as the schema reference.
  - `technical-faq.pdf` — competition Technical FAQ, redistributed
    verbatim; cited by `CORRECTNESS.md` for its pitfalls checklist and
    the PRN 62 insertion-index errata note.
- `validate.py` — stdlib-only CLI with `check-annex3`,
  `check-lans-afs-sim`, `diff`, `verify-manifest`, `rebuild-manifest`,
  and `refresh` subcommands. Installable as `lsis-afs-validate` via
  uv / pip. `rebuild-manifest` is the maintainer command for refreshing
  SHA256s after adding or editing shipped files; it replaces the ad-hoc
  regeneration scripts used during drafting.
- `pyproject.toml` — hatchling-backed single-module package, zero runtime
  dependencies. Dev group adds pytest + ruff.
- `tests/test_validate.py` — 17 pytest cases exercising every subcommand
  including positive and negative paths for `diff`.
- `manifest.json` — SHA256 over 639 content files (excludes the validator
  itself, since a self-hash does not protect against tampering of the code
  that computes it).
- GitHub Actions workflow `verify.yml` running `uv sync`, `ruff check`,
  `ruff format --check`, `pytest`, and the three validator subcommands.

### Correctness
- **Oracle 1 — Annex 3:** 630/630 sections (210 PRN × 3 code types)
  byte-exact against the normative reference.
- **Oracle 2 — LANS-AFS-SIM:** 420/420 chip dumps (210 PRN × 2 code families,
  Gold + Weil primary) bit-exact. Scope is what LANS-AFS-SIM exercises in its
  transmit path; Weil-1500 tertiary and S0–S3 secondaries are covered by
  Oracle 1 only.
