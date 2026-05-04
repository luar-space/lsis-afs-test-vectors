# Changelog

All notable releases of LSIS-AFS test vectors are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## Versioning

The package is a staged drop of the five interoperability test levels
prescribed by the competition. The 0.x series tracks that staged build-up:

| Version | Ships |
|:---|:---|
| 0.1.0     | Level 1 |
| 0.2.0     | + Level 2 |
| 0.2.1     | + canonical pre-encode inputs |
| **0.2.2** | + Level-2 boundary frame with WN=8191 / ITOW=503 maxima (this release) |
| 0.3.0     | + Level 3 |
| 0.4.0     | + Level 4 |
| 0.5.0     | + Level 5 (feature-complete) |
| **1.0.0** | first stable — all five levels verified, formats frozen |

Patch versions (e.g. 0.1.1, 0.2.1, 0.2.2) carry corrections or additional
verification artefacts for an already-shipped level without adding new ones.

## [0.2.2] — 2026-05-04

Patch release — **TC4 max-field coverage at Level 2**.

### Added
- `frames/frame_boundary_max_fields.bin` (1 new L2 frame, 6064 bytes) — covers
  the **WN=8191 (max) and ITOW=503 (spec max)** corners of interop-doc
  Test Case 4 that the original `frame_boundary.bin` does **not** exercise.
  The original boundary frame uses an alternating-start-with-0 SB2 pattern
  that fills WN/ITOW with whatever that pattern produces, neither at minimum
  nor at maximum; this is a documented L2-omission carried since v0.2.0.
  The new frame keeps PRN=210, FID=3, TOI=99 (max field values) and adds the
  SB2 field-maxima coverage by filling SB2/SB3/SB4 with all-ones EXCEPT the
  9-bit ITOW field (SB2[13..21]) which is clamped to 503 (the spec maximum,
  MSB-first `0b111110111`).  The 9-bit raw maximum 511 is invalid per LSIS
  V1.0 §2.4.3.1.6 — it would land in TC5 territory (out-of-range), not TC4.
  FAQ Q21 normalisation on SB2[1150..1175] continues to apply.
- `inputs/frame_boundary_max_fields_input.bin` — matching canonical
  pre-encode input file (2868 bytes, same format as the other 6 inputs).
  Pattern name: `max_fields`.
- `references/lans-afs-sim/frames/lans_frame_boundary_max_fields.bin` —
  matching LANS-AFS-SIM second-oracle dump, regenerated from the bundled
  harness against the same upstream SHA `0578f298ba68d8508ab7d780be843faed3e2b274`.
- `references/lans-afs-sim/harnesses/dump_lans_frame.c` gained the
  `max_fields` pattern + post-fill ITOW=503 override; `dump_l2_test_vectors.py`
  now invokes the harness for 7 messages instead of 6.
- `validate.py` extended:
  - `FRAME_TEST_VECTORS` and `INPUT_TEST_VECTORS` each gain one entry.
  - `_build_canonical_input` learns the `max_fields` pattern.
  - New constants: `SB2_WN_OFFSET/_BITS`, `SB2_ITOW_OFFSET/_BITS`,
    `SB2_ITOW_SPEC_MAX` for the field-position semantics.
- Manifest grew to **665 SHA256-hashed content files** (was 662).
- `tests/test_validate.py` — 48 cases (was 45); existing cases that asserted
  `6/6` / `OK — all 6 frames…` were updated to `7/7` / `…all 7 frames…`.

### Coverage matrix update

| TC4 boundary dimension | v0.2.1 | v0.2.2 |
|:---|:---:|:---:|
| FID=3 (max — 2-bit field) | ✅ `frame_boundary.bin` | ✅ both boundary frames |
| TOI=99 (max — 0..99 per spec) | ✅ `frame_boundary.bin` | ✅ both boundary frames |
| **WN=8191 (max — 13-bit)** | ❌ | ✅ `frame_boundary_max_fields.bin` |
| **ITOW=503 (spec max — 9-bit)** | ❌ | ✅ `frame_boundary_max_fields.bin` |
| All-zeros minimum | ✅ `frame_message_1.bin` | ✅ `frame_message_1.bin` |
| PRN=210 (max field — header only) | ✅ both boundary frames at L2 | (L3 not signal-realisable; see v0.3.0) |

### Correctness
- **Oracle 1 — LSIS V1.0 §2.4 structural** (L2): 7/7 frames pass `check-frames`.
- **Oracle 2 — LANS-AFS-SIM** (L2 independent): 7/7 frames bit-exact against
  upstream SHA `0578f298ba68d8508ab7d780be843faed3e2b274` rebuild.

## [0.2.1] — 2026-05-03

Patch release — **canonical pre-encode inputs** for the Level-2 frames.

### Added
- `inputs/` — 6 canonical input files, one per shipped frame, containing the
  exact SB2 + SB3 + SB4 bits the encoder consumed before BCH/CRC/LDPC/interleave.
  Each file is 2868 bytes in unpacked form (1 byte per bit, value 0x00 / 0x01;
  layout: 1176 SB2 + 846 SB3 + 846 SB4). FAQ Q21 spare-bit normalisation is
  applied to SB2 bits 1150–1175 in every file, so the bytes are self-describing
  ground truth — a contestant whose encoder consumes the file produces the
  matching `frames/frame_*.bin` regardless of whether their encoder applies
  Q21 internally.
  - `frame_message_1_input.bin` — all-zero input
  - `frame_message_2_input.bin` — all-one input
  - `frame_message_3_input.bin` — alternating, start-with-1 (`0xAA`)
  - `frame_message_4_input.bin` — bytewise marker (per-subframe restart)
  - `frame_message_5_input.bin` — xorshift32, seed `0xAF52`, single stream
    consumed across SB2 → SB3 → SB4
  - `frame_boundary_input.bin` — alternating, start-with-0 (`0x55`)
- `validate.py` gained three subcommands:
  - `check-canonical-inputs` — verify the shipped input files reproduce
    from the documented patterns + FAQ Q21 normalisation. Reports 6/6.
  - `diff-inputs` — compare a directory of canonical-input files against
    ours, with first-mismatch localised to `SB{n} bit {k}`.
  - `build-canonical-inputs` — maintainer command, regenerates `inputs/`.
- Manifest grew to **662 SHA256-hashed content files** (was 656).
- `tests/test_validate.py` — 45 cases (was 37); 8 new cases cover the new
  subcommands, file size invariants, and FAQ Q21 normalisation in inputs.
- `pyproject.toml` packaging includes `inputs/` in wheel + sdist.

### Workflow
Implementers can now run a clean two-step encoder validation:
1. Read `inputs/frame_message_X_input.bin` (canonical SB2/SB3/SB4 bytes).
2. Feed into your encoder; compare the output against `frames/frame_message_X.bin`
   via `python validate.py diff-frames /path/to/your/frames/`.

Bit-exact agreement isolates correctness to the FEC pipeline alone — input-
construction conventions (TM3 starting bit, TM4 marker stream, TM5 PRNG,
FAQ Q21 spare bits) are no longer a source of cross-team disagreement.

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
