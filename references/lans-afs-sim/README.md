# LANS-AFS-SIM Reference Dumps

This directory hosts reference material produced by
**LANS-AFS-SIM**, used as a **second independent oracle** to cross-validate
the test vectors in this package. LANS-AFS-SIM is a transmit-side baseband
signal generator, so the levels it can serve as an oracle for are the ones
whose outputs live on the transmit side of the interoperability chain:

```
references/lans-afs-sim/
├── LICENSE.txt           # BSD 2-Clause, © 2025 Takuji Ebinuma
├── codes/                # Level 1 — chip dumps               ✅ shipped (420 × .bin)
├── frames/               # Level 2 — encoded-frame dumps      ✅ shipped (6 × .bin)
├── signals/              # Level 3 — baseband I/Q dumps       (planned)
└── harnesses/            # Apache-2.0 source for the dump tools that produced the above
    ├── README.md
    ├── dump_lans_codes.c       # L1 — Gold + Weil chip dumper
    ├── dump_lans_frame.c       # L2 — full frame encoder pipeline
    ├── dump_l2_test_vectors.py # orchestrator for the 6 L2 frames
    └── verify_oracle.py        # one-command upstream-rebuild + byte-exact verifier
```

Level 4 (cross-decoding) is behavioural by definition and does not produce
a material oracle. Level 5 (message parsing) lives on the receive side and
is outside the scope of a transmit-only simulator; its oracle will come
from a separate receive-side reference (e.g. PocketSDR-AFS) or from the
main spec's §5–6 frame-layout examples.

## Level 1 contents (`codes/`)

| Pattern | Count | Bytes per file | Total |
|:--------|------:|---------------:|------:|
| `codes/gold_prn_NNN.bin`  | 210 |  2046 |   429 660 bytes |
| `codes/weil_prn_NNN.bin`  | 210 | 10230 | 2 148 300 bytes |

Each `.bin` is raw chips, one byte per chip, value `0x00` or `0x01`,
in transmission order. No header, no padding.

## Level 2 contents (`frames/`)

| File | Bytes | Pattern |
|:---|------:|:---|
| `frames/lans_frame_message_1.bin` | 6000 | all-zeros input |
| `frames/lans_frame_message_2.bin` | 6000 | all-ones input |
| `frames/lans_frame_message_3.bin` | 6000 | alternating bits, start 1 (first byte 0xAA, interop-doc TM3) |
| `frames/lans_frame_message_4.bin` | 6000 | bytewise marker |
| `frames/lans_frame_message_5.bin` | 6000 | xorshift32 PRNG, seed=0xAF52 |
| `frames/lans_frame_boundary.bin`  | 6000 | alternating, FID=3 TOI=99 |

Each `.bin` is the **raw 6000-symbol payload** (one byte per symbol, value
`0x00` or `0x01`, in transmission order — sync pattern, then SB1, then
interleaved SB2+SB3+SB4). No header, no padding. To compare against the
package's `frames/frame_*.bin`, strip the latter's 64-byte `LSISAFS\0`
header and the rest is byte-for-byte identical.

## Provenance

These files were produced by small C harnesses that link against
[LANS-AFS-SIM](https://github.com/osqzss/LANS-AFS-SIM) and call the same
code- and frame-generation routines the simulator uses internally:

- `codes/*.bin` — produced by `dump_lans_codes.c`, calling
  `icodegen()` / `qcodegen()` (the AFS-I and AFS-Q code-generator
  routines defined in upstream `afs_sim.c`).
- `frames/*.bin` — produced by `dump_lans_frame.c`, calling
  `generate_BCH_AFS_SF1()` + `append_CRC24()` + `encode_LDPC_AFS_SF2()` /
  `encode_LDPC_AFS_SF3()` + `interleave_AFS_SF234()`. The harness applies
  one disclosed normalisation: SB2 input bits 1150–1175 are pre-filled with
  the spec-mandated alternating pattern (FAQ Q21), since LuarSpace's encoder
  enforces this internally and we want both implementations to operate on
  spec-compliant input. See [`../../CORRECTNESS.md`](../../CORRECTNESS.md)
  for full disclosure.

The harnesses themselves are not part of upstream LANS-AFS-SIM, but
their full Apache-2.0 source is bundled in [`harnesses/`](./harnesses/)
for end-to-end reproducibility. A reader can clone LANS-AFS-SIM, build
those harnesses against it, run them, and verify that the output matches
the `.bin` files shipped here byte-for-byte. See
[`harnesses/README.md`](./harnesses/README.md) for the build commands.

Bundling the outputs directly lets readers cross-check the test vectors
without that build step; bundling the source as well means the chain of
trust ends at the upstream BSD-2-Clause encoder, not at "LuarSpace ran
the tool and you have to take their word for it".

## Licence

LANS-AFS-SIM is distributed under the **BSD 2-Clause** licence,
**Copyright (c) 2025, Takuji Ebinuma**, preserved verbatim in
[`LICENSE.txt`](./LICENSE.txt). The chip-level dumps in this directory are
derived data from LANS-AFS-SIM and are redistributed under the same terms.
This is distinct from the Apache-2.0 licence covering the rest of the
package — see the top-level `LICENSE` file for that.

## Self-check

```bash
python validate.py check-lans-afs-sim          # L1 codes  → 420/420
python validate.py check-lans-afs-sim-frames   # L2 frames → 6/6
```

Both commands run in CI on every push.

## Scope

**Level 1 (codes):** Only the **Gold** (AFS-I primary) and **Weil-10230**
(AFS-Q primary) codes are dumped — these are the two code families
LANS-AFS-SIM actively exercises in its transmit path. The **Weil-1500**
tertiary code and the four 4-bit secondary codes (S0–S3) are covered by
the first oracle (Annex 3) only.

**Level 2 (frames):** All 6 test messages from the interop doc are dumped.
LANS-AFS-SIM exercises the full BCH(51,8) + CRC-24Q + LDPC(1/2) +
60×98 interleaver pipeline natively, so the second oracle covers the
entire encoder chain end-to-end.

See `../../CORRECTNESS.md` for the full oracle-coverage table.
