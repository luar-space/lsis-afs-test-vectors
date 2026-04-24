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
├── frames/               # Level 2 — encoded-frame dumps      (planned)
└── signals/              # Level 3 — baseband I/Q dumps       (planned)
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

## Provenance

These files were produced by a small C harness that links against
[LANS-AFS-SIM](https://github.com/osqzss/LANS-AFS-SIM) and calls the same
code-generation routines the simulator uses internally to build its I/Q
signal files, writing each PRN's chips to disk as raw bytes.

The harness itself is not part of upstream LANS-AFS-SIM and is not shipped
here. Any reader can reproduce the dumps by cloning LANS-AFS-SIM and
writing an equivalent caller (a few dozen lines); bundling the outputs
directly lets you cross-check the test vectors in this package without
that build step.

## Licence

LANS-AFS-SIM is distributed under the **BSD 2-Clause** licence,
**Copyright (c) 2025, Takuji Ebinuma**, preserved verbatim in
[`LICENSE.txt`](./LICENSE.txt). The chip-level dumps in this directory are
derived data from LANS-AFS-SIM and are redistributed under the same terms.
This is distinct from the Apache-2.0 licence covering the rest of the
package — see the top-level `LICENSE` file for that.

## Self-check

```bash
python validate.py check-lans-afs-sim
```

Expected output:

```
  Gold (2046 chips) : 210/210
  Weil (10230 chips): 210/210

OK — all 420 code dumps bit-exact against LANS-AFS-SIM reference.
```

The same command runs in CI on every push.

## Scope

Only the **Gold** (AFS-I primary) and **Weil-10230** (AFS-Q primary) codes
are dumped — these are the two code families LANS-AFS-SIM actively exercises
in its transmit path. The **Weil-1500** tertiary code and the four 4-bit
secondary codes (S0–S3) are covered by the first oracle (Annex 3) only.
See `../../CORRECTNESS.md` for the full oracle-coverage table.
