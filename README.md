# LSIS-AFS Interoperability Test Vectors

A shared set of test vectors for the
**LunaNet Signal-In-Space Augmented Forward Signal** ([LSIS-AFS V1.0, 29 January 2025](https://www.nasa.gov/wp-content/uploads/2025/02/lunanet-signal-in-space-recommended-standard-augmented-forward-signal-vol-a.pdf)),
maintained as a community contribution for teams working toward the
[ESA–CCSDS Protocol Development Outreach Competition](https://esa-competition.amsat-uk.org/).

## Test levels

The competition's [interoperability plan](./references/interoperability.pdf)
defines five test levels, each verifying a different stage of the
transmit / receive chain. 

| Level | Covers | Oracle | Status |
|:-----:|:---|:---|:---:|
| **1** | Spreading-code generation                              | LNIS Vol A Annex 3 + LANS-AFS-SIM            | ✅ shipped in `v0.1.0` |
| **2** | Encoded-frame generation (BCH + CRC-24 + LDPC + interleaving) | LSIS V1.0 §2.4 structural + LANS-AFS-SIM | ✅ shipped in `v0.2.0` |
| **3** | Baseband I/Q signal generation                         | LSIS V1.0 §4 structural + first-chip polarity (chains L1+L2) | ✅ shipped in `v0.3.0` (single oracle, see below) |
| **4** | Cross-decoding between implementations                 | PocketSDR-AFS (reference decoder)            | ✅ shipped in `v0.4.0` |
| **5** | Navigation-data parsing from decoded frames            | LSIS V1.0 Tables 13/22 + §2.5.5 + interop-PDF spec shape; LunaLink reference parser; transitive cross-impl via L4 PocketSDR-AFS | ✅ shipped in `v0.5.0` |

L1–L2 oracles sit on the transmit side and are externally verified using [LANS-AFS-SIM](https://github.com/osqzss/LANS-AFS-SIM) alongside the normative references from LSIS-AFS v1.0.
L3 sits on the transmit side too but currently has only one formal oracle (structural + first-chip polarity, chaining L1 codes and L2 sync prefix); LANS-AFS-SIM is not the right shape for L3 (multi-PRN sum, internal-almanac nav data, carrier+Doppler applied) and no second open-source AFS generator exists yet. Receive-side closure for L3 ("decode our signal → frame ≡ shipped L2 frame") is the natural shape for the spec's L3 Pass Criteria and lands with v0.4.0.
L4 sits on the receive side and is covered by [PocketSDR-AFS](https://github.com/osqzss/PocketSDR-AFS) —
Ebinuma's companion software-defined receiver, BSD-2-Clause and redistributable
as derived outputs the same way [Annex 3](references/annex-3/) and the LANS-AFS-SIM chip
dumps are bundled today.  L5 sits on the parsing layer downstream of L4 and is
covered by a stdlib structural oracle in this repo (`check-parsed`), with
load-bearing independence on the V1.0-pinned fields (FID, TOI, WN, ITOW, ToT)
inherited transitively from L4: PocketSDR-AFS already extracts those same
fields from the symbol stream into `decoded_fec_signal_*.bin`, which the L5
parsed JSONs cite by raw-bit-slice byte-equality.

Each level drops its content into its own sibling directory (`codes/`,
`frames/`, `signals/`, `parsed/`) and its oracle(s) into `references/`.

If you are an implementer working on the competition: please cross-check
and tell us if anything disagrees. The goal is a shared baseline, not a
monoculture; finding a discrepancy is a win for everyone.

## Directory layout

```
lsis-afs-test-vectors/
├── codes/                           # Level 1 — 210 × codes_prnNNN.hex      ✅ shipped
├── frames/                          # Level 2 — 6 × frame_*.bin              ✅ shipped
├── inputs/                          # Level 2 — 6 × frame_*_input.bin (canonical pre-encode bytes) ✅ shipped
├── signals/                         # Level 3 — 10 × signal_*_12s.iq.gz      ✅ shipped (~221 MB total)
│                                    # Level 4 — no content dir; see references/pocketsdr-afs/
├── parsed/                          # Level 5 — 7 × parsed_frame_*.json     ✅ shipped (~30 KB)
├── references/                      # bundled oracles, grows with future levels
│   ├── annex-3/                     #   L1 normative — 3 × .txt + README
│   ├── lans-afs-sim/                #   transmit-side oracle (BSD-2-Clause, © Ebinuma)
│   │   ├── codes/                   #     L1 chip dumps — 420 × .bin        ✅ shipped
│   │   ├── frames/                  #     L2 frame dumps — 6 × .bin         ✅ shipped
│   │   ├── harnesses/               #     Apache-2.0 source for the dump tools
│   │   ├── LICENSE.txt
│   │   └── README.md
│   │   # No signals/ — LANS-AFS-SIM doesn't fit L3 (see CORRECTNESS.md).
│   ├── pocketsdr-afs/               #   receive-side oracle (BSD-2-Clause, © Takasu+Ebinuma)
│   │   ├── decoded/                 #     L4 cross-decode outputs (20 × .bin) ✅ shipped
│   │   ├── harnesses/               #     Apache-2.0 source for the verifier
│   │   ├── LICENSE.txt
│   │   └── README.md
│   ├── interoperability.pdf         #   competition schema reference
│   ├── technical-faq.pdf            #   competition FAQ + errata (PRN 62 note)
│   └── README.md
├── validate.py                      # stdlib-only CLI — single file, zero runtime deps
├── tests/                           # pytest suite — exercises every subcommand
├── manifest.json                    # SHA256 over every shipped content file (662)
├── pyproject.toml  uv.lock          # hatchling packaging + pinned dev deps
├── .github/workflows/verify.yml     # CI: ruff + pytest + every validator oracle
├── CHANGELOG.md  CORRECTNESS.md  CITATION.cff
└── LICENSE                          # Apache-2.0 — covers the package;
                                     #   third-party material in references/
                                     #   carries its own licence (see Licence below)
```

The top-level directory names match those prescribed by the competition
interoperability document so that consumers can wire a single path template
and have it apply to every level drop.

## Quick start

`validate.py` is a single-file CLI with **no runtime dependencies** — just
CPython ≥ 3.10. Clone the repo, then run it either way:

```bash
# Zero-install: just run it against the shipped files.
python validate.py check-annex3

# Or set up a dev environment (adds pytest + ruff for contributors).
uv sync
uv run lsis-afs-validate check-annex3
```

## Current release — `v0.5.0` (Levels 1 + 2 + 3 + 4 + 5)

> Versioning follows a staged-drop scheme: 0.x adds one level per minor
> bump; 1.0.0 is reserved for the feature-complete release with all five
> levels verified. See [`CHANGELOG.md`](./CHANGELOG.md) for the full plan.

### Level 1 — spreading codes (210 PRNs)

210 PRNs × (Gold 2046, Weil 10230, Weil 1500, four S0–S3 secondaries),
bit-exact against LNIS AD1 Volume A Annex 3.

Each `codes_prnNNN.hex` contains seven sections in the format defined by the
competition interoperability document:

```
[GOLD_CODE]        # 2046 chips → 512 hex digits (2 MSB zero-pad bits per Annex 3)
[WEIL_PRIMARY]     # 10230 chips → 2558 hex digits (2 MSB zero-pad bits per Annex 3)
[WEIL_TERTIARY]    # 1500 chips → 375 hex digits (no padding, odd nibble count)
[SECONDARY_S0..3]  # 4-bit AFS-Q secondaries (E, 7, B, D), per Annex 3 Table 2
```

### Level 2 — encoded frames (7 frames)

Seven `frames/frame_*.bin` files exercise the BCH(51,8) + CRC-24Q + LDPC(1/2)
+ 60×98 interleaver pipeline per LSIS V1.0 §2.4. Each file is **6064 bytes**:
a 64-byte `LSISAFS\0` header (per the interop doc) followed by 6000 unpacked
symbols. The set covers the interop doc's five public Level-2 message slots
plus two boundary frames from Test Case 4; `frame_message_4.bin` intentionally
uses a documented bytewise marker surrogate rather than realistic ephemeris:

| File | PRN | FID | TOI | Input pattern |
|:---|:---:|:---:|:---:|:---|
| `frame_message_1.bin` |   1 | 0 |  0 | all zeros |
| `frame_message_2.bin` |   1 | 0 |  0 | all ones |
| `frame_message_3.bin` |   1 | 0 |  0 | alternating bits (first byte 0xAA, matches interop doc TM3) |
| `frame_message_4.bin` |   1 | 0 |  0 | bytewise marker (`0x00, 0x01, …`) — restarts per subframe |
| `frame_message_5.bin` |   1 | 0 |  0 | xorshift32 PRNG, seed = `0xAF52` |
| `frame_boundary.bin`  | 210 | 3 | 99 | alternating-start-0 (header-field maxima: PRN/FID/TOI) |
| `frame_boundary_max_fields.bin`  | 210 | 3 | 99 | all-ones SB with ITOW=503 (SB2-field maxima: WN=8191, ITOW=503) — added v0.2.2 |

Marker patterns rather than realistic ephemeris: encoder bit-exactness is
content-agnostic, and the exact substitutions/conventions are documented in
[`CORRECTNESS.md`](./CORRECTNESS.md).

### Canonical pre-encode inputs (`inputs/`)

For each `frame_*.bin`, the corresponding `inputs/frame_*_input.bin` ships the
**SB2 + SB3 + SB4 input bytes** that produced it. Every file is **2868 bytes**
in unpacked form (1 byte per bit, value `0x00` / `0x01`; layout: 1176 SB2 +
846 SB3 + 846 SB4). FAQ Q21 spare-bit normalisation (alternating 0/1 starting
with 0 in SB2 bits 1150–1175) is already applied — the bytes are
self-describing ground truth.

```
inputs/
├── frame_message_1_input.bin           # 2868 bytes — all-zero input
├── frame_message_2_input.bin           #              all-one input
├── frame_message_3_input.bin           #              alternating, start-with-1 (0xAA)
├── frame_message_4_input.bin           #              bytewise marker, per-subframe restart
├── frame_message_5_input.bin           #              xorshift32 (seed 0xAF52, single stream)
├── frame_boundary_input.bin            #              alternating, start-with-0 (0x55)
└── frame_boundary_max_fields_input.bin #              all-ones with ITOW=503 (SB2-field maxima)
```

Workflow for validating your encoder against the reference set:

```bash
# 1. Read inputs/frame_message_X_input.bin → feed SB2/SB3/SB4 to your encoder.
# 2. Diff your encoder's output against ours:
python validate.py diff-frames /path/to/your/frames/
# Bit-exact agreement isolates correctness to the FEC pipeline alone.
```

### Level 3 — baseband I/Q signals (10 signals, ~221 MB compressed)

Ten `signals/signal_*_12s.iq.gz` files in the binary LSISIQ format
defined by `interoperability.pdf` (128-byte `LSISIQ\0\0` header +
interleaved float32 I/Q at 10.23 MHz × 12 s, BPSK ±1.0). Each file is
**982 080 128 bytes raw**, **~22 MB after `gzip -9`**:

| File | PRN | Source frame | Spec mapping |
|:---|:---:|:---|:---|
| `signal_message_1_12s.iq.gz` |   1 | `frame_message_1.bin` (all-zeros) | **TC1 verbatim** + TC2 first endpoint + TC4 all-zeros minimum |
| `signal_message_2_12s.iq.gz` |   1 | `frame_message_2.bin` (all-ones) | bonus content variation (interleaver-error debugging) |
| `signal_message_3_12s.iq.gz` |   1 | `frame_message_3.bin` (alternating) | bonus content variation |
| `signal_message_4_12s.iq.gz` |   1 | `frame_message_4.bin` (marker surrogate) | bonus content variation |
| `signal_message_5_12s.iq.gz` |   1 | `frame_message_5.bin` (xorshift32) | bonus content variation |
| `signal_prn2_baseline_12s.iq.gz` |   2 | `frame_message_1.bin` (all-zeros) | **TC2 — secondary index S1** |
| `signal_prn3_baseline_12s.iq.gz` |   3 | `frame_message_1.bin` (all-zeros) | **TC2 — secondary index S2** |
| `signal_prn12_baseline_12s.iq.gz` |  12 | `frame_message_1.bin` (all-zeros) | **TC2 — secondary index S3** + high end of legal interim PRN range |
| `signal_boundary_at_prn12_12s.iq.gz` |  12 | `frame_boundary.bin` (BCH SB1: FID=3, TOI=99) | **TC4 — TOI=99 / FID=3 maxima** at PRN 12 |
| `signal_boundary_max_fields_at_prn12_12s.iq.gz` |  12 | `frame_boundary_max_fields.bin` (SB2 with WN=8191, ITOW=503) | **TC4 — WN=8191 / ITOW=503 maxima** at PRN 12 (uses v0.2.2 boundary-max-fields frame) |

The four PRN-baseline files (PRN 1, 2, 3, 12) together exercise all four
AFS-Q secondary codes (S0–S3) per LSIS V1.0 §4.4.2 — a generator that is
correct at PRN 1 but mishandles the secondary-index assignment table for
S1/S2/S3 fails at L3 instead of leaking into L4.

**TC2 not fully exercised**: PRN 4–11 mid-range are not shipped. The 4
shipped PRNs cover all 4 secondary indices, which is the dimension that
actually distinguishes L3 generators in practice; full PRN 1–12 sweep
adds 8 more files (~176 MB) with limited extra coverage.

**TC4 fully exercised at L3 (except PRN=210)**: FID=3 / TOI=99 maxima
via `signal_boundary_at_prn12`, WN=8191 / ITOW=503 maxima via
`signal_boundary_max_fields_at_prn12` (which uses the v0.2.2
boundary-max-fields L2 frame), all-zeros minimum via `signal_message_1`.
PRN=210 itself is not signal-realisable; everything else from the TC4
input list is propagated through to L3.

**No PRN=210 L3 entry**: PRN 13–210 are reserved for the future LunaNet
operational deployment and have no defined matched-code assignment yet,
and the interop doc's Test Case 2 itself scopes L3 PRN coverage to
"PRN: 1-12 (Table 11)" for that reason.

**TC3 (message-type coverage) not exercised at L3**: TC3 is about SF3 /
SF4 *semantic* message types, not bit-pattern variations. L3 ships
content patterns; TC3 lands formally with the L5 parser drop.

**Three of the four L3 Pass Criteria** — *identical chip rates*,
*identical symbol rates*, *identical code synchronisation* — are
checked by `check-signals` (structural + first-chip + symbol-120
polarity).  The fourth Pass Criterion — *cross-decoding recovers
original data with BER < 10⁻ⁿ* — is closed at L4 (see below) by
running PocketSDR-AFS over every shipped L3 signal and confirming
byte-exact recovery of the corresponding L2 frame's payload.

**Repository size.** `signals/` adds ~221 MB. If you only need L1/L2,
sparse-checkout to skip:

```bash
git clone --filter=blob:none https://github.com/luar-space/lsis-afs-test-vectors
cd lsis-afs-test-vectors
git sparse-checkout init --cone
git sparse-checkout set codes frames inputs references validate.py manifest.json
```

### Level 4 — cross-decode (20 outputs, ~88 KB)

Twenty files under `references/pocketsdr-afs/decoded/` produced by
**PocketSDR-AFS** (BSD-2-Clause, © Takuji Ebinuma) at the pinned
upstream SHA `5b23809f`, exercising **two oracles** end-to-end, each
at 10/10 coverage:

- **Channel-symbol oracle** (10 × `decoded_signal_*.bin`, 6000 bytes
  each) — the raw 6000-symbol AFS-D payload at the receiver's
  symbol-detection output, byte-equal to `frames/frame_*.bin[64:6064]`.
- **Post-FEC oracle** (10 × `decoded_fec_signal_*.bin`, 2868 bytes
  each) — the SB2 + SB3 + SB4 bits after deinterleave + LDPC + CRC,
  byte-equal to `inputs/frame_*_input.bin` (the canonical pre-encode
  bytes shipped at v0.2.1).

The post-FEC layer is the strict reading of the interop doc's L4 pass
criterion ("Decoded data matches original input exactly") — the
receiver's post-FEC output equals the encoder's pre-FEC input.  The
2 FID=3 boundary frames are covered too thanks to a small bundled
SB1 FID-bypass: upstream's `sync_AFS_SF1_FID0` only matches FID=0,
but LDPC + CRC are FID-agnostic block codes, so the bundled patch
proceeds past the SB1 search with a placeholder TOI to let LDPC + CRC
run for FID>0 frames.  The bypass is gated behind `-dump-fec`;
upstream behaviour is unchanged when the flag isn't set.

For the workshop's L4 **cross-decoding round-robin** (every team's
signals × every team's decoder), the on-disk contract and the
`diff-decode` checker are specified — as a proposal pending Phase-1
agreement — in [`INTEROP-ROUNDROBIN.md`](./INTEROP-ROUNDROBIN.md).

| Decoded output | Source frame | PRN | Coverage |
|:---|:---|:---:|:---|
| `decoded_signal_message_1_12s.bin` | `frame_message_1.bin` | 1 | TC1 baseline |
| `decoded_signal_message_2_12s.bin` | `frame_message_2.bin` | 1 | content variation |
| `decoded_signal_message_3_12s.bin` | `frame_message_3.bin` | 1 | content variation |
| `decoded_signal_message_4_12s.bin` | `frame_message_4.bin` | 1 | content variation |
| `decoded_signal_message_5_12s.bin` | `frame_message_5.bin` | 1 | content variation |
| `decoded_signal_prn2_baseline_12s.bin` | `frame_message_1.bin` | 2 | TC2 — secondary S1 |
| `decoded_signal_prn3_baseline_12s.bin` | `frame_message_1.bin` | 3 | TC2 — secondary S2 |
| `decoded_signal_prn12_baseline_12s.bin` | `frame_message_1.bin` | 12 | TC2 — secondary S3 |
| `decoded_signal_boundary_at_prn12_12s.bin` | `frame_boundary.bin` | 12 | TC4 — FID/TOI maxima |
| `decoded_signal_boundary_max_fields_at_prn12_12s.bin` | `frame_boundary_max_fields.bin` | 12 | TC4 — WN/ITOW maxima |

The bundled cross-decode oracle source lives under
`references/pocketsdr-afs/`:

```
references/pocketsdr-afs/
├── LICENSE.txt              # upstream BSD-2-Clause, redistributed verbatim
├── README.md                # oracle description
├── decoded/                 # 10 × decoded_signal_*.bin     (6000 bytes, channel)
│                            # +10 × decoded_fec_signal_*.bin (2868 bytes, post-FEC)
└── harnesses/
    ├── verify_pocketsdr_decode.py   # one-command end-to-end verifier
    ├── decode_signal.py             # per-signal driver
    ├── lsisiq_to_pocketsdr.py       # signal-format converter
    ├── parse_pocketsdr_log.py       # CRC-pass log parser
    ├── README.md
    └── patches/                     # local patches against pinned SHA
        ├── dump-symbols.patch       # adds -dump-symbols + -dump-fec flags
        └── clang17-build-fix.patch  # macOS Apple Clang 17 build fix
```

To verify the L4 oracles from upstream sources end-to-end:

```bash
brew install fftw libusb     # macOS Homebrew dependencies
python references/pocketsdr-afs/harnesses/verify_pocketsdr_decode.py
# Clones PocketSDR-AFS @ pinned SHA, applies the two patches, builds
# pocket_trk, decodes all 10 signals, compares both layers (vs frames/
# and vs inputs/).  ~5–10 min.
# On success:
#   Channel-symbol oracle: 10/10 signals byte-exactly recovered
#   Post-FEC oracle:       10/10 signals byte-exactly recovered
```

The cheap CI form runs both oracles from the shipped `decoded/` files:

```bash
python validate.py check-decode
#   Channel-symbol oracle: 10/10
#   Post-FEC oracle:       10/10
```

**Repository footprint.** L4 adds **< 1 MB**: 88 KB of decoded
outputs (60 KB channel + 28 KB FEC) + harness sources + the upstream
LICENSE.

### Level 5 — parsed navigation JSON (7 files, ~30 KB)

Seven files under `parsed/` produced by the **LunaLink** reference
implementation (the LuarSpace generator that produces every shipped
artefact in this repo) running its full RX path on each `frames/frame_*.bin`:
sync detect → BCH(51,8) on SB1 → deinterleave → LDPC(1/2) on SB2/SB3/SB4
→ CRC-24Q verify → field-level parse.  Each JSON follows the
[interop-PDF p.3-4 "Parsed Data Export Format"](./references/interoperability.pdf)
shape literally:

```json
{
  "version": "1.0",
  "timestamp": "2025-01-29T00:00:00+00:00",
  "frame_id": "frame_message_1",
  "subframe1": {"fid": 0, "toi": 0, ...},
  "subframe2": {"wn": 0, "itow": 0, "ced": {...}, "health": {...}, ...},
  "subframe3": {"type": 0, "data": {...}, ...},
  "subframe4": {"type": 0, "data": {...}, ...},
  "time_of_transmission": 0.0,
  ...
}
```

**LSIS V1.0 honesty.**  V1.0 (29 January 2025) only fully pins five
field families: FID/TOI per Tables 13/14, WN/ITOW per Table 22, the
ToT formula per §2.5.5, and CRC-24Q per §2.4.3.1.3.  CED (§2.5.3 /
LSIS-TBW-2006), Health (§2.5.2 / LSIS-TBW-2005), Time Conversions
(§2.5.20 / LSIS-TBW-2012), and the SF3/SF4 type-field width
(LSIS-TBC-2023/2024, "4 or 6 bits") are TBW or TBC.  The shipped
JSONs reflect this faithfully:

- V1.0-pinned fields ship as concrete values
- V1.0-TBW regions ship as raw bit-slice hex with explicit
  `_lsis_ref: "LSIS-TBW-NNNN"` markers
- V1.0-TBC type widths ship LunaLink's resolved 6-bit choice with a
  disclosure marker so a third-party 4-bit implementation surfaces
  the disagreement at `diff-parsed` time

| Parsed JSON | Source frame | FID | TOI | WN | ITOW | Coverage |
|:---|:---|:---:|:---:|:---:|:---:|:---|
| `parsed_frame_message_1.json` | `frame_message_1.bin` | 0 | 0 | 0 | 0 | TC1 baseline |
| `parsed_frame_message_2.json` | `frame_message_2.bin` | 0 | 0 | 8191 | 511 | TC1 — all-ones (out-of-spec ITOW=511 surfaced) |
| `parsed_frame_message_3.json` | `frame_message_3.bin` | 0 | 0 | 5461 | 170 | TC1 — alternating-1 |
| `parsed_frame_message_4.json` | `frame_message_4.bin` | 0 | 0 | 0 | 64 | TC1 — bytewise marker |
| `parsed_frame_message_5.json` | `frame_message_5.bin` | 0 | 0 | 5434 | 47 | TC1 — xorshift32 |
| `parsed_frame_boundary.json` | `frame_boundary.bin` | 3 | 99 | 2730 | 341 | TC4 — FID/TOI maxima |
| `parsed_frame_boundary_max_fields.json` | `frame_boundary_max_fields.bin` | 3 | 99 | 8191 | 503 | TC4 — WN/ITOW maxima (ITOW clamped to spec max) |

The cheap CI form runs the structural + ground-truth oracle:

```bash
python validate.py check-parsed
#   Parsed-JSON oracle:  7/7
#
# OK — all 7 parsed JSONs pass schema, spec-range, ToT round-trip,
# FID/TOI/WN/ITOW ground-truth, raw-data byte-compare, and CRC checks.
```

The third-party diff tool (mirrors `diff-frames`, `diff-decode`):

```bash
python validate.py diff-parsed /path/to/your/parsed/
```

`diff-parsed` compares only the spec-shaped fields (`fid`, `toi`,
`wn`, `itow`, `type`, `time_of_transmission`); the underscore-prefixed
LunaLink metadata is excluded so a third-party implementation emitting
only the spec-shaped fields still matches.  For raw-bit-slice cross-impl
comparison of the V1.0-TBW regions, use `diff-inputs` against `inputs/`
instead — those bits are the transmit-side ground truth and exist
upstream of the L5 parsing layer.

**Repository footprint.** L5 adds **~30 KB** (7 plain-text JSON files,
no compression so consumers can `git diff` them with no setup).

### Validator subcommands

```bash
# Level 1 — normative oracle (Annex 3).
python validate.py check-annex3

# Level 1 — second oracle (LANS-AFS-SIM chip dumps, bundled).
python validate.py check-lans-afs-sim

# Level 2 — structural oracle (LSIS V1.0 §2.4 + Gateway 3 checklist).
python validate.py check-frames

# Level 2 — second oracle (LANS-AFS-SIM frame dumps, bundled).
python validate.py check-lans-afs-sim-frames

# Level 2 — verify canonical-input files reproduce from the documented patterns.
python validate.py check-canonical-inputs

# Level 3 — structural + first-chip polarity oracle (chains L1 codes + L2 sync prefix).
python validate.py check-signals

# Level 4 — cheap round-trip oracle (decoded outputs vs frame payloads).
python validate.py check-decode

# Level 5 — parsed-JSON structural + spec-range + ground-truth oracle.
python validate.py check-parsed

# Compare your implementation's output against this repo.
python validate.py diff         /path/to/your/codes/
python validate.py diff-frames  /path/to/your/frames/
python validate.py diff-inputs  /path/to/your/inputs/
python validate.py diff-signals /path/to/your/signals/

# diff-decode validates your decoded output against the original input
# — the Level 4 pass criterion itself, not a diff against our decoder.
# Required: decoded_fec_signal_*.bin vs inputs/ (the recovered data).
# Optional diagnostic: decoded_signal_*.bin vs frames/ (channel symbols;
# absent is fine).  --reference DIR uses an agreed external truth set;
# --json emits one round-robin matrix cell; --vs-pocketsdr adds a
# secondary diff.  Full protocol: INTEROP-ROUNDROBIN.md
python validate.py diff-decode  /path/to/your/decoded/
python validate.py diff-parsed  /path/to/your/parsed/

# Re-hash everything to confirm the distribution is intact.
python validate.py verify-manifest
```

### Correctness

Six oracles ship with this release, all reproducible offline:

1. **LNIS AD1 Volume A, Annex 3** (L1 normative) — every `.hex` file matches
   byte-for-byte; `check-annex3` reports 630/630.
2. **LANS-AFS-SIM** (BSD-licensed community reference by Takuji Ebinuma) —
   chip dumps and frame dumps bundled in `references/lans-afs-sim/`;
   `check-lans-afs-sim` reports 420/420 and `check-lans-afs-sim-frames`
   reports 7/7.
3. **LSIS V1.0 §2.4 structural rules** (L2) — sync pattern bit-exact match
   to `0xCC63F74536F49E04A`, header magic / version / frame-length /
   PRN integrity, symbol-domain values in {0, 1}; `check-frames` reports 7/7.
4. **L3 structural + first-chip polarity** — LSISIQ header layout
   per the interop doc, file-size invariant, strict ±1.0 BPSK across every
   sample, and first-chip polarity cross-validated against the L1 codes
   and the L2 sync prefix; `check-signals` reports 10/10.
5. **PocketSDR-AFS cross-decode** (L4) — every shipped L3 signal,
   when fed to PocketSDR-AFS @ pinned upstream SHA, recovers (a) the
   matching L2 frame's 6000-symbol payload byte-for-byte (channel
   layer, 10/10), AND (b) the canonical pre-encode SB2/SB3/SB4 bits
   byte-for-byte (post-FEC layer, 10/10 — boundary frames included
   via the bundled SB1 FID-bypass); `check-decode` reports both, and
   `verify_pocketsdr_decode.py` reproduces the full clone + build +
   decode chain (~5–10 min).
6. **Parsed-JSON structural + ground-truth** (L5) — every shipped
   `parsed/parsed_frame_*.json` passes seven checks: JSON schema, V1.0
   spec-range (FID 0..3, TOI 0..99, WN 0..8191, ITOW 0..511 raw 9-bit
   max, type 0..63), ToT round-trip per V1.0 §2.5.5, FID/TOI
   ground-truth match against the encoding table, WN/ITOW byte-compare
   against `inputs/*_input.bin[0..21]` MSB-first bit-unpack, per-subframe
   `_data_raw_hex` byte-equal to the corresponding `inputs/` slice, and
   `_crc24q_ok` flag = `true` on every subframe; `check-parsed` reports
   7/7.  This is NOT a parser — load-bearing parser independence on the
   V1.0-pinned fields comes from L4 (PocketSDR-AFS independently extracts
   WN/ITOW/TOI in `decode_AFSD_frame` and produces post-FEC bits byte-equal
   to the same `inputs/` files cited at L5).

All seven checks run in CI on every push, alongside `ruff check`, `ruff
format --check`, `pytest`, and `verify-manifest`. See
[`CORRECTNESS.md`](./CORRECTNESS.md) for the oracle-coverage table, the
encoding rules they validate, and the disclosed normalisations applied to
the LANS-AFS-SIM and PocketSDR-AFS harnesses.

These vectors do **not** reproduce the known PRN 62 insertion-index error
documented in the competition *Technical FAQ* errata
([`references/technical-faq.pdf`](./references/technical-faq.pdf));
the Annex 3 bit-exact match on `codes_prn062.hex` proves it.

## How this package is generated

The vectors in each drop are the output of the `luar-space` implementation of
LSIS-AFS. The generator itself is not yet public, but will be ahead of the
competition workshop.

If a particular vector disagrees with your implementation, please open an
issue. We'd rather catch it here than at the interop bench.

## Contributing

```bash
git clone https://github.com/luar-space/lsis-afs-test-vectors
cd lsis-afs-test-vectors
uv sync                  # installs pytest + ruff into .venv/
uv run ruff check        # lint
uv run ruff format       # format
uv run pytest            # full test suite — exercises every subcommand
```

If you add or update any file under `codes/`, `frames/`, `inputs/`, or `references/`,
refresh the integrity manifest before committing:

```bash
uv run lsis-afs-validate rebuild-manifest   # recompute SHA256s → manifest.json
uv run lsis-afs-validate verify-manifest    # sanity-check the result
```

Issues and pull requests welcome. Please run `ruff check` and `pytest`
before opening a PR; CI will run both on your branch.

## Licence

- **Test vectors and validator**: Apache License 2.0 — see [`LICENSE`](./LICENSE).
- **Annex 3 reference files** in `references/annex-3/` are redistributed
  verbatim from the LNIS AD1 Volume A distribution and are **not**
  relicensed under Apache-2.0; see
  [`references/README.md`](./references/README.md).
- **LANS-AFS-SIM chip dumps** in `references/lans-afs-sim/` are derived data
  from a BSD-2-Clause project (Copyright © 2025 Takuji Ebinuma) and retain
  that licence — see `references/lans-afs-sim/LICENSE.txt`.
- **Competition interoperability plan** (`references/interoperability.pdf`)
  is redistributed verbatim for reader convenience; it is the schema
  reference cited throughout this README and `CORRECTNESS.md`.
- **Competition Technical FAQ** (`references/technical-faq.pdf`) is
  likewise redistributed verbatim; `CORRECTNESS.md` cites its errata and
  pitfalls guidance, including the PRN 62 insertion-index note above.

Future level drops will bundle their own oracle material under
`references/` with the applicable licence travelling alongside.

## Citation

See [`CITATION.cff`](./CITATION.cff). If you use these vectors in an
implementation report or publication, a citation is appreciated but not
required.

---

*Generated with the LuarSpace implementation of LSIS-AFS. More drops will follow.*
