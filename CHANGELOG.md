# Changelog

All notable releases of LSIS-AFS test vectors are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## Versioning

The package is a staged drop of the five interoperability test levels
prescribed by the competition. The 0.x series tracks that staged build-up:

| Version | Ships |
|:---|:---|
| 0.1.0     | Level 1 |
| **0.2.0** | + Level 2 (this release) |
| 0.3.0     | + Level 3 |
| 0.4.0     | + Level 4 |
| 0.5.0     | + Level 5 (feature-complete) |
| **1.0.0** | first stable — all five levels verified, formats frozen |

Patch versions (e.g. 0.1.1) carry corrections to an already-shipped level
without adding new ones.

## [0.2.0] — 2026-04-27

Second public release — **Level 2: Encoding Interoperability** vectors.

### Added
- `frames/` — 6 encoded-frame vectors in the binary format defined by
  `interoperability.pdf` (64-byte `LSISAFS\0` header + 6000 unpacked symbols).
  The five public Level-2 message slots from the interop doc plus one
  boundary frame from Test Case 4 (PRN=210, FID=3, TOI=99). `frame_message_4.bin`
  uses a documented bytewise marker surrogate in place of realistic ephemeris:
  - `frame_message_1.bin` — all-zeros input
  - `frame_message_2.bin` — all-ones input
  - `frame_message_3.bin` — alternating bits (first byte 0xAA, matches interop-doc TM3 literally)
  - `frame_message_4.bin` — bytewise marker pattern (`0x00, 0x01, 0x02, …`)
  - `frame_message_5.bin` — xorshift32 PRNG, seed = `0xAF52`
  - `frame_boundary.bin`  — boundary header values, alternating payload
- `references/lans-afs-sim/frames/` — 6 raw 6000-byte frame dumps from
  LANS-AFS-SIM matching the six test messages above.
- `references/lans-afs-sim/harnesses/` — Apache-2.0 source for the C
  harnesses + Python orchestrator that produced the bundled second-oracle
  dumps (both L1 codes and L2 frames). Reader can clone LANS-AFS-SIM,
  build these against it, and verify output matches the shipped `.bin`
  files byte-for-byte:
  - `dump_lans_codes.c` (77 LOC) — calls `icodegen()` / `qcodegen()`
    for each PRN, dumps Gold + Weil-10230 chips.
  - `dump_lans_frame.c` (185 LOC) — calls `generate_BCH_AFS_SF1()` +
    `append_CRC24()` + `encode_LDPC_AFS_SF2/3()` + `interleave_AFS_SF234()`
    with five input-pattern modes.
  - `dump_l2_test_vectors.py` — orchestrator that drives the L2 harness
    through the six prescribed test messages.
  - `verify_oracle.py` — one-command end-to-end verifier: clones
    LANS-AFS-SIM at the pinned SHA, builds upstream + the harnesses,
    runs every dumper, and `cmp`s the result against the shipped
    `.bin` set. Exits 0 only on a full byte-exact match.
  - `README.md` — build instructions, licence interaction note, provenance.
- `validate.py` gained three subcommands:
  - `check-frames` — L2 structural oracle (sync pattern, header magic,
    frame length, PRN field, symbol-domain values).
  - `check-lans-afs-sim-frames` — L2 second oracle (bit-exact vs LANS dumps).
  - `diff-frames` — compare your `frame_*.bin` files to ours.
- Manifest grew to **656 SHA256-hashed content files** (was 639).
- `pyproject.toml` packaging includes `frames/` in wheel + sdist.
- `tests/test_validate.py` — 35 pytest cases (was 20).

### Correctness
- **Oracle 1 — LSIS V1.0 §2.4 structural** (L2 normative-equivalent):
  6/6 frames pass `check-frames`. There is no published "Annex 2" with
  reference encoded frames — the competition's L2 requirements and the
  Gateway 3 deliverables Risk Assessment ("Test vector availability —
  Medium — Generate own test vectors from encoder") name this gap
  explicitly.
- **Oracle 2 — LANS-AFS-SIM** (L2 independent): 6/6 frames bit-exact against
  raw `lans_frame_*.bin` dumps. Encoder primitives (BCH(51,8) gen-poly 763,
  CRC-24Q `0x1864CFB`, LDPC(1/2) for SB2/SB3/SB4, 60×98 block interleaver)
  are upstream BSD-2-Clause code; the harness only wires inputs and writes
  outputs. Full source for the harnesses ships in
  `references/lans-afs-sim/harnesses/`, so the chain of trust ends at
  upstream Ebinuma code rather than at trusting our invocation.
- One disclosed normalisation: SB2 bits 1150–1175 are pre-filled with the
  spec-mandated alternating pattern in the harness (FAQ Q21, LSIS-300).
  LuarSpace's encoder enforces this internally; the harness applies it to
  Ebinuma's input so both encoders operate on spec-compliant data.

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
- `tests/test_validate.py` — 20 pytest cases exercising every subcommand
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
