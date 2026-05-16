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
| 0.2.2     | + Level-2 boundary frame with WN=8191 / ITOW=503 maxima |
| 0.3.0     | + Level 3 |
| **0.4.0** | + Level 4 (this release) |
| 0.5.0     | + Level 5 (feature-complete) |
| **1.0.0** | first stable — all five levels verified, formats frozen |

Patch versions (e.g. 0.1.1, 0.2.1, 0.2.2) carry corrections or additional
verification artefacts for an already-shipped level without adding new ones.

## [0.4.0] — 2026-05-05

Fourth public release — **Level 4: Decoding Interoperability** vectors.
Closes the receive-side loop deferred from v0.3.0.  The bundled
PocketSDR-AFS receiver runs its full decode chain — acquisition,
tracking, sync detection, symbol demodulation, deinterleave, LDPC
decode, CRC validation — over every shipped L3 baseband-IQ signal,
recovering both the channel-bit layer (vs `frames/`) and the post-FEC
canonical-input layer (vs `inputs/`) byte-for-byte.

### Added
- `references/pocketsdr-afs/` — bundled cross-decode oracle:
  - `LICENSE.txt` — upstream PocketSDR-AFS BSD-2-Clause license
    redistributed verbatim (© 2021–2023 T. Takasu, additional ©
    2025 Takuji Ebinuma).
  - `decoded/` — 20 cross-decode outputs (~88 KB total):
    * 10 × `decoded_signal_*.bin` (6000 bytes each) — channel-symbol
      oracle.  One uint8 per AFS-D hard-decision symbol (post-sync,
      pre-deinterleave); byte-equal to `frames/frame_*.bin[64:6064]`.
    * 10 × `decoded_fec_signal_*.bin` (2868 bytes each) — post-FEC
      oracle.  Concatenated SB2 + SB3 + SB4 LDPC-decoded data bits
      (1176 + 846 + 846, no CRC trailer); byte-equal to
      `inputs/frame_*_input.bin`.
  - `harnesses/` — Apache-2.0 verifier source:
    - `verify_pocketsdr_decode.py` — one-command end-to-end verifier.
      Clones PocketSDR-AFS at the pinned upstream SHA, applies two
      bundled patches, builds, converts each L3 signal to the
      receiver's expected `INT8X2` format, runs `pocket_trk
      -dump-symbols -dump-fec` on each, compares both layers
      bytewise (vs `frames/` and vs `inputs/`), and (with
      `--regenerate-decoded`) refreshes
      `references/pocketsdr-afs/decoded/`.  Mirrors the L1+L2
      `verify_oracle.py` CLI shape (`--upstream-dir`,
      `--workdir`, `--keep-workdir`, `--no-sha-check`).
    - `decode_signal.py` — per-signal driver: tile-convert →
      `pocket_trk` → both-layer bytewise compare with
      first-mismatch localisation.
    - `lsisiq_to_pocketsdr.py` — signal-format converter.
      LSISIQ float32 zero-IF → headerless `INT8X2` int8 at
      ×100 scale (lossless given the strict ±1.0 BPSK enforced
      by `check-signals`).  Optional deterministic AWGN
      injection (`--awgn-cn0`, off by default).
    - `parse_pocketsdr_log.py` — extracts `$SB2`/`$SB3`/`$SB4`
      CRC-pass counts from the `pocket_trk -log` file as
      corroborating evidence (the load-bearing oracles are the
      bytewise channel and post-FEC comparators above).
  - `harnesses/patches/` — two bundled patches against pinned
    upstream SHA `5b23809f30d68518b7fad7a564fd0fac57cc497d`:
    - `dump-symbols.patch` — ~85 LoC, narrow surface for
      maintenance across upstream bumps.  Adds two opt-in CLI
      flags to `pocket_trk` plus one small SB1 bypass:
      * `-dump-symbols <path>` — emits the raw 6000
        hard-decision AFS-D frame symbols at the entry of
        `decode_AFSD_frame()`, before deinterleave / LDPC.
        Polarity-normalised via XOR with the receiver's chosen
        lock polarity.
      * `-dump-fec <path>` — emits the post-LDPC, post-CRC
        SB2 + SB3 + SB4 data bits (2868 bytes per successful
        frame; output layout matches `inputs/frame_*_input.bin`)
        immediately after each subframe's LDPC + CRC succeeds.
      * SB1 FID-bypass (gated on `-dump-fec` being set):
        when upstream's `sync_AFS_SF1_FID0` fails (i.e. the
        frame carries FID > 0 — a real case for our boundary
        frames at FID=3), continue past the SB1 search with
        a placeholder TOI rather than bailing.  LDPC + CRC are
        FID-agnostic, so SB2/SB3/SB4 still decode and produce
        the post-FEC dump.  Receiver PVT/TOW state ends up
        wrong (TOI is consumed downstream by `update_tow()`),
        which the LSIS-AFS test-vector verifier doesn't read.
        Upstream behaviour is restored exactly when `-dump-fec`
        is not used.
    - `clang17-build-fix.patch` — renames a file-static
      `satpos()` in `src/sdr_pvt_afs.c` whose name clashes
      with RTKLIB's exported `satpos()` under Apple Clang 17+
      strict-declaration checking.  No semantic change; required
      for clean builds on contemporary macOS Command Line Tools.
  - `README.md` (oracle), `harnesses/README.md` (build + run).

- `validate.py` gained two subcommands:
  - `check-decode` — runs **both** L4 oracles end-to-end,
    each at 10/10 coverage:
    * Channel-symbol oracle: every `decoded_signal_*.bin` is
      6000 bytes of {0, 1} and byte-equal to the corresponding
      `frames/frame_*.bin[64:6064]` payload.
    * Post-FEC oracle: every `decoded_fec_signal_*.bin` is
      2868 bytes of {0, 1} and byte-equal to the corresponding
      `inputs/frame_*_input.bin`.
  - `diff-decode` — validate a third party's decoded outputs
    against the **original input** (`frames/[64:6064]` +
    `inputs/`), i.e. the interop-plan Level 4 pass criterion
    ("Decoded data matches original input exactly"), with
    first-mismatch localised to `(filename, byte_index,
    expected, got)`.  This is `check-decode`'s comparison
    applied to a foreign directory — no indirection through
    our decoder's rendering.  `--vs-pocketsdr` adds an
    optional secondary diff against the bundled PocketSDR
    reference decode.
- `_rebuild_manifest()` excludes generated artefacts (`__pycache__/*.pyc`,
  `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.tox`).  A maintainer
  who has imported the harness modules locally creates `.pyc` files
  under `references/pocketsdr-afs/harnesses/__pycache__/` — those are
  git-ignored and don't exist in a clean checkout.  Without this fix,
  `rebuild-manifest` would pin them, and `verify-manifest` would then
  fail on every clean checkout.  Regression test:
  `test_rebuild_manifest_excludes_generated_artefacts`.
- **Verifier hardening** (four post-review strengthenings):
  - **All-frames check** in `decode_signal.py`: the verifier walks
    every frame-dump in the symbol/FEC streams and asserts each
    matches the canonical, plus that total length is a whole multiple
    of the frame size.  Catches misaligned dumps and any drift
    between consecutive frame decodes.  At the current `tile_count=3`
    each signal yields one frame-dump per oracle; the walker handles
    multi-frame trivially when tile_count is bumped, so future
    multi-frame coverage drops in without validator changes.
  - **Native vs with-bypass split** in `verify_pocketsdr_decode.py`:
    the verifier now parses the bundled patch's `"SB1 FID mismatch"`
    log marker, reports `8/8 native (no bypass) + 2/2 with FID-bypass
    = 10/10 total`, and asserts the bypass fired iff the source frame
    is FID=3.  Removes the asterisk on "10/10" by making the patch's
    scope of effect transparent.
  - **Determinism check** (`--check-determinism`): re-decodes
    `signal_message_1_12s.iq.gz` twice and asserts byte-identical
    channel + FEC dumps across runs.  Catches non-determinism in
    acquisition / tracking-loop convergence / LDPC iteration that
    would surface as flaky CI once Doppler/noise are added.
  - **Linux nightly CI** (`.github/workflows/verify-pocketsdr-decode-linux.yml`):
    runs the full clone + apt-deps + g++ build + decode + compare +
    determinism chain on `ubuntu-latest`.  Triggers: tag pushes,
    weekly Sunday cron, manual `workflow_dispatch`.  Does not run on
    PRs (5–10 min cost).  Validates patches apply under g++, Ubuntu
    `libfftw3-dev` / `libusb-1.0-0-dev` are sufficient, and the
    decoder is platform-deterministic at the byte level.
- Manifest grew to **704 SHA256-hashed content files** (was 675 in v0.3.0):
  20 decoded outputs (10 channel + 10 FEC) + harness sources +
  the 2 patches + the upstream LICENSE + READMEs.
- `pyproject.toml` packaging includes the new
  `references/pocketsdr-afs/` subtree in the wheel + sdist
  (already covered by the `references/` glob); no new runtime deps;
  version bumped to **0.4.0**.
- `.github/workflows/verify.yml` runs `check-decode` on every
  push.  The expensive `verify_pocketsdr_decode.py` rebuild
  (~5–10 min: clone + libfec + LDPC-codes + `pocket_trk` build +
  10× decode) is **not** run in CI — it is the maintainer
  command exercised before every release tag.

### Disclosed normalisations
- **Tile 3×.** PocketSDR-AFS's `decode_AFSD` requires
  `ch->lock >= 6068 + 500` symbols of locked tracking before
  attempting frame sync, and `sync_frame()` further requires
  the 68-bit AFSD preamble at **two** buffer offsets 6000 symbols
  apart — i.e., two consecutive frames' sync prefixes.  Our
  shipped signals are 12 s = 6000 symbols = exactly **one** frame.
  The harness tiles the converted INT8X2 stream three times
  before feeding it to `pocket_trk`; all tiles are byte-identical,
  so the dumped symbols match the source frame regardless of
  which tile triggers sync.  Disclosed in `CORRECTNESS.md`.
- **float32 → int8 ×100 scale.** PocketSDR-AFS only consumes
  int8 input (`-fmt INT8X2`).  Our BPSK ±1.0 float32 stream is
  cast to ±100 int8 (well below the ±127 saturation limit),
  then PocketSDR-AFS's internal `SDR_CSCALE = 1/24` rescales
  back to ~±4.17 floats internally.  The cast is lossless
  given the strict ±1.0 sample range enforced by
  `check-signals`.

### Correctness
- **Oracle 1 — Channel-symbol round-trip via PocketSDR-AFS** (L4 demod
  layer): **10/10 signals** pass byte-for-byte recovery of the
  corresponding `frames/frame_*.bin[64:6064]` payload via
  `verify_pocketsdr_decode.py`.  Exercises acquisition + tracking +
  sync detection + symbol demodulation against an independent
  receiver codebase.
- **Oracle 2 — Post-FEC round-trip via PocketSDR-AFS** (L4 FEC layer):
  **10/10 signals** pass byte-for-byte recovery of the corresponding
  `inputs/frame_*_input.bin` (canonical pre-encode SB2 + SB3 + SB4
  bits, shipped at v0.2.1) — including the 2 FID=3 boundary frames,
  thanks to the bundled SB1 FID-bypass.  Exercises the deinterleave
  + LDPC + CRC pipeline.  This is the strict reading of the L4 pass
  criterion ("Decoded data matches original input exactly") — the
  receiver's post-FEC output equals the encoder's pre-FEC input.
- **Cheap CI form**: both oracles round-trip on `validate.py check-decode`.

### Why no upstream PR for the symbol-dump patch
The patch is bundled locally and applied at build time by the
verifier — not submitted to `osqzss/PocketSDR-AFS`.  Two reasons:
(a) the dump format is specifically tuned to the LSIS-AFS
test-vector consumer (raw uint8 per symbol, polarity-normalised,
no header), not a generally-useful upstream feature; (b) keeping
the patch local lets us re-pin the SHA without coordinating with
upstream's release cadence.  We can offer it upstream later
on Ebinuma's request.

## [0.3.0] — 2026-05-04

Third public release — **Level 3: Signal Generation Interoperability**
vectors.  Builds on v0.2.2's L2 boundary-max-fields frame to also cover
TC4's WN/ITOW maxima at the signal level.

### Added
- `signals/` — 10 baseband I/Q signal vectors in the binary format defined by
  `interoperability.pdf` (128-byte `LSISIQ\0\0` header + interleaved float32
  I/Q at 10.23 MHz × 12 s, BPSK ±1.0). Files chosen to cover the L3 spec
  test cases:
  - `signal_message_1_12s.iq.gz` — PRN 1, all-zeros (**TC1 verbatim** +
    TC2 first endpoint + TC4 all-zeros minimum)
  - `signal_message_2_12s.iq.gz` — PRN 1, all-ones (bonus content variation)
  - `signal_message_3_12s.iq.gz` — PRN 1, alternating bits (bonus)
  - `signal_message_4_12s.iq.gz` — PRN 1, bytewise marker (bonus, surrogate)
  - `signal_message_5_12s.iq.gz` — PRN 1, xorshift32 (bonus)
  - `signal_prn2_baseline_12s.iq.gz` — PRN 2, TM1 (**TC2 — AFS-Q secondary
    index S1**)
  - `signal_prn3_baseline_12s.iq.gz` — PRN 3, TM1 (**TC2 — AFS-Q secondary
    index S2**)
  - `signal_prn12_baseline_12s.iq.gz` — PRN 12, TM1 (**TC2 — AFS-Q secondary
    index S3** + high end of legal interim PRN range)
  - `signal_boundary_at_prn12_12s.iq.gz` — PRN 12, `frame_boundary.bin`
    nav data (**TC4 — FID=3 / TOI=99 maxima** at PRN 12; PRN 210 is not
    signal-realisable so we substitute PRN 12)
  - `signal_boundary_max_fields_at_prn12_12s.iq.gz` — PRN 12,
    `frame_boundary_max_fields.bin` nav data (**TC4 — WN=8191 /
    ITOW=503 maxima** at PRN 12).  Pairs with the v0.2.2 L2 frame to
    propagate the SB2-field maxima coverage through to L3.

  The four PRN baselines (1, 2, 3, 12) together exercise all four AFS-Q
  secondary codes S0–S3 per LSIS V1.0 §4.4.2 / Annex 3 Table 2. A
  generator that is correct on PRN 1 (S0) but mishandles the
  secondary-index assignment for S1, S2, or S3 fails at L3 instead of
  leaking into L4.

  Each file is **982 080 128 bytes raw** (128 header + 122 760 000 sample
  pairs × 8 bytes), **~22 MB after `gzip -9`**. Total `signals/` footprint:
  ~221 MB. Repository grows from ~11 MB to ~232 MB; consumers who only need
  L1/L2 can skip `signals/` via sparse-checkout or a shallow clone.

  **PRN 210 itself remains unrealisable at L3.** Annex 3 Table 11 publishes
  the AFS-Q matched-code phase assignment for PRN 1–12 only; PRN 13–210
  are reserved for the future LunaNet operational deployment.  The interop
  doc's Test Case 2 mirrors this scope ("PRN: 1-12 (Table 11)").  L1
  (`codes/codes_prn210.hex`) and L2 (`frames/frame_boundary*.bin`) remain
  shipped at PRN=210 — those layers are content-agnostic at the PRN level
  and the Annex 3 reference covers all 210 PRNs.

- `validate.py` gained two subcommands:
  - `check-signals` — L3 structural + first-chip polarity oracle.
    Verifies header layout (magic, version, sample rate, duration, PRN,
    format string, reserved bytes), file size, strict ±1.0 BPSK across
    every sample, and first-chip I- and Q-channel polarity at samples
    0, 10, 20, 30 within the sync-prefix region **and** at sample
    2 455 200 (start of frame symbol 120, the first message-distinguishing
    interleaver-output symbol — catches LDPC / interleaver bit-ordering
    errors that the sync-prefix probe alone cannot). Cross-validated
    against the L1 codes (Gold/Weil/Tertiary chips from `codes/`) and the
    L2 sync prefix (FAQ Q17 / spec Table 12).
  - `diff-signals` — compare your `signal_*_12s.iq[.gz]` files to ours
    byte-for-byte, with first-mismatch localised to either a header byte
    or `(sample, channel)`.
- Manifest grew to **675 SHA256-hashed content files** (was 665 in v0.2.2).
- `pyproject.toml` packaging includes `signals/` in wheel + sdist;
  version bumped to **0.3.0**; numpy added as optional dep (`[fast]` extra)
  for the bulk-sample range scan.
- `.github/workflows/verify.yml` runs `check-signals` on every push.
- `tests/test_validate.py` — 59 cases (was 48 in v0.2.2); 11 new cases
  cover the new subcommands, header / sample-range / polarity mutations,
  gzip handling, and manifest coverage of `signals/`.

### Correctness
- **Oracle 1 — Structural + first-chip polarity** (L3 normative-equivalent
  + cross-chained with L1/L2 oracles): 10/10 signals pass `check-signals`.
  This is materially weaker than L1 (Annex 3 + LANS-AFS-SIM) and L2
  (structural + LANS-AFS-SIM): v0.3.0 ships **one** formal oracle. See
  `CORRECTNESS.md` Level 3 chapter for the full disclosure and the
  receive-side closure deferred to v0.4.0.

### Why no L3 second oracle in this release
- **LANS-AFS-SIM** is not the right shape for L3: its `afs_sim` produces a
  multi-satellite IF-style dump (sums all visible SVs, applies Doppler +
  path-loss + carrier rotation, writes int16 / 2-bit, no LSISIQ header,
  drives nav data from its internal almanac with no flag for external
  frame injection). Coercing it to clean single-PRN baseband requires
  forking the inner sample loop, which breaks the "thin caller invoking
  unchanged routines" chain-of-trust we relied on at L1/L2.
- **PocketSDR-AFS** is a receiver, not a generator — it can only *consume*
  signals, not produce reference signals to byte-compare. Receive-side
  closure (decode our L3 signal → recovered frame ≡ shipped L2 frame) is
  the natural shape for the L3 Pass Criteria the interop doc actually
  specifies, and lands formally in v0.4.0 alongside the L4 deliverable.
- **No other open-source AFS generator exists** in May 2026. LSIS V1.0
  (January 2025) is too new for a third independent implementation.

Signal content in v0.3.0 is **trust-by-construction**: each signal is a
deterministic function of the matching `frames/frame_*.bin` (already L2
oracle-verified) and `codes/codes_prn{N}.hex` (already L1 oracle-verified)
under the LSIS V1.0 §4 BPSK + I/Q-multiplex math. The first-chip polarity
check chains those L1 + L2 verifications through into L3 without
re-implementing the full pipeline.

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
