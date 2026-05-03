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
| **3** | Baseband I/Q signal generation                         | main spec §4 + LANS-AFS-SIM                  | planned |
| **4** | Cross-decoding between implementations                 | PocketSDR-AFS (reference decoder)            | planned |
| **5** | Navigation-data parsing from decoded frames            | main spec §5–6 + PocketSDR-AFS parser        | planned |

L1–L3 oracles sit on the transmit side and are externally verified using [LANS-AFS-SIM](https://github.com/osqzss/LANS-AFS-SIM) alongside the normative references from LSIS-AFS v1.0. 
L4–L5 sit on the receive side and are covered by [PocketSDR-AFS](https://github.com/osqzss/PocketSDR-AFS) —
Ebinuma's companion software-defined receiver. Both tools are BSD-2-Clause
and redistributable as derived outputs the same way [Annex 3](references/annex-3/) and the
LANS-AFS-SIM chip dumps are bundled today. The PocketSDR-AFS integration
will be exercised ahead of the June workshop so that L4/L5 evidence is demonstrated before the in-person hackathon.

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
├── signals/                         # Level 3 — I/Q signal files                  (planned)
│                                    # Level 4 — no content dir; see references/pocketsdr-afs/
├── parsed/                          # Level 5 — parsed navigation JSON            (planned)
├── references/                      # bundled oracles, grows with future levels
│   ├── annex-3/                     #   L1 normative — 3 × .txt + README
│   ├── lans-afs-sim/                #   transmit-side oracle (BSD-2-Clause, © Ebinuma)
│   │   ├── codes/                   #     L1 chip dumps — 420 × .bin        ✅ shipped
│   │   ├── frames/                  #     L2 frame dumps — 6 × .bin         ✅ shipped
│   │   ├── signals/                 #     L3 (planned)
│   │   ├── harnesses/               #     Apache-2.0 source for the dump tools
│   │   ├── LICENSE.txt
│   │   └── README.md
│   ├── pocketsdr-afs/               #   receive-side oracle                       (planned)
│   │   ├── decode/                  #     L4 cross-decode reference
│   │   └── parsed/                  #     L5 parsed-JSON reference
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

## Current release — `v0.2.1` (Levels 1 + 2 + canonical pre-encode inputs)

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

### Level 2 — encoded frames (6 frames)

Six `frames/frame_*.bin` files exercise the BCH(51,8) + CRC-24Q + LDPC(1/2)
+ 60×98 interleaver pipeline per LSIS V1.0 §2.4. Each file is **6064 bytes**:
a 64-byte `LSISAFS\0` header (per the interop doc) followed by 6000 unpacked
symbols. The set covers the interop doc's five public Level-2 message slots
plus one boundary frame from Test Case 4; `frame_message_4.bin` intentionally
uses a documented bytewise marker surrogate rather than realistic ephemeris:

| File | PRN | FID | TOI | Input pattern |
|:---|:---:|:---:|:---:|:---|
| `frame_message_1.bin` |   1 | 0 |  0 | all zeros |
| `frame_message_2.bin` |   1 | 0 |  0 | all ones |
| `frame_message_3.bin` |   1 | 0 |  0 | alternating bits (first byte 0xAA, matches interop doc TM3) |
| `frame_message_4.bin` |   1 | 0 |  0 | bytewise marker (`0x00, 0x01, …`) — restarts per subframe |
| `frame_message_5.bin` |   1 | 0 |  0 | xorshift32 PRNG, seed = `0xAF52` |
| `frame_boundary.bin`  | 210 | 3 | 99 | alternating (PRN/FID/TOI at max) |

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
├── frame_message_1_input.bin   # 2868 bytes — all-zero input
├── frame_message_2_input.bin   #              all-one input
├── frame_message_3_input.bin   #              alternating, start-with-1 (0xAA)
├── frame_message_4_input.bin   #              bytewise marker, per-subframe restart
├── frame_message_5_input.bin   #              xorshift32 (seed 0xAF52, single stream)
└── frame_boundary_input.bin    #              alternating, start-with-0 (0x55)
```

Workflow for validating your encoder against the reference set:

```bash
# 1. Read inputs/frame_message_X_input.bin → feed SB2/SB3/SB4 to your encoder.
# 2. Diff your encoder's output against ours:
python validate.py diff-frames /path/to/your/frames/
# Bit-exact agreement isolates correctness to the FEC pipeline alone.
```

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

# Compare your implementation's output against this repo.
python validate.py diff         /path/to/your/codes/
python validate.py diff-frames  /path/to/your/frames/
python validate.py diff-inputs  /path/to/your/inputs/

# Re-hash everything to confirm the distribution is intact.
python validate.py verify-manifest
```

### Correctness

Three independent oracles ship with this release, all reproducible offline:

1. **LNIS AD1 Volume A, Annex 3** (L1 normative) — every `.hex` file matches
   byte-for-byte; `check-annex3` reports 630/630.
2. **LANS-AFS-SIM** (BSD-licensed community reference by Takuji Ebinuma) —
   chip dumps and frame dumps bundled in `references/lans-afs-sim/`;
   `check-lans-afs-sim` reports 420/420 and `check-lans-afs-sim-frames`
   reports 6/6.
3. **LSIS V1.0 §2.4 structural rules** (L2) — sync pattern bit-exact match
   to `0xCC63F74536F49E04A`, header magic / version / frame-length /
   PRN integrity, symbol-domain values in {0, 1}; `check-frames` reports 6/6.

All four checks run in CI on every push, alongside `ruff check`, `ruff
format --check`, `pytest`, and `verify-manifest`. See
[`CORRECTNESS.md`](./CORRECTNESS.md) for the oracle-coverage table, the
encoding rules they validate, and the disclosed normalisations applied to
the LANS-AFS-SIM frame harness.

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
