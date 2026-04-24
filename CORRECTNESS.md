# Correctness — how these vectors are proved right

Level 1 of the LSIS-AFS interoperability plan requires a **100 % byte-exact
match against the Annex 3 reference codes** (see the competition
interoperability specification, *"Pass Criteria — Level 1"*). Two independent
oracles are used to prove this package meets that bar.

## Oracle 1 — LNIS AD1 Volume A, Annex 3 (normative)

The files in [`references/annex-3/`](./references/annex-3/) are redistributed
verbatim from **LNIS AD1 Volume A — Annex 3 — PRN Spreading Codes**
(10 December 2024):

| File | Signal | Code | Symbols | Hex digits |
|:-----|:-------|:-----|--------:|-----------:|
| `006_GoldCode2046hex210prns.txt` | AFS-I | Primary (Gold)       |  2046 |  512 |
| `007_l1cp_hex210prns.txt`         | AFS-Q | Primary (Weil 10230) | 10230 | 2558 |
| `008_Weil1500hex210prns.txt`      | AFS-Q | Tertiary (Weil 1500) |  1500 |  375 |

The encoding rule, per the Annex 3 README: "Note that codes with length of
2046 and 10230 symbols are not divisible by 4. In order to represent these
codes in hexadecimal format and provide them electronically, **two zeros are
added to the MSB (i.e., to the lefthand side) for all PRNs 1-210.**" The
1500-chip code requires no padding and yields an odd (375) number of hex
digits.

Every `codes_prnNNN.hex` in this package encodes its `[GOLD_CODE]`,
`[WEIL_PRIMARY]` and `[WEIL_TERTIARY]` sections with exactly this scheme,
and every byte matches Annex 3. To verify locally:

```bash
python validate.py check-annex3
#   GOLD_CODE      : 210/210
#   WEIL_PRIMARY   : 210/210
#   WEIL_TERTIARY  : 210/210
```

This is also the check that runs on every push in CI
(`.github/workflows/verify.yml`).

## Oracle 2 — LANS-AFS-SIM

As a second, fully independent cross-check, the chip-level dumps produced
by **LANS-AFS-SIM** — the BSD-licensed open-source LSIS-AFS simulator by
[Takuji Ebinuma](https://github.com/osqzss/LANS-AFS-SIM) — are bundled in
`references/lans-afs-sim/codes/` (210 × `gold_prn_NNN.bin`, 210 × `weil_prn_NNN.bin`,
one byte per chip). `validate.py check-lans-afs-sim` decodes the hex in
`codes/` back to raw chips and compares byte-for-byte:

```bash
python validate.py check-lans-afs-sim
#   Gold (2046 chips) : 210/210
#   Weil (10230 chips): 210/210
#
# OK — all 420 code dumps bit-exact against LANS-AFS-SIM reference.
```

Scope: LANS-AFS-SIM's transmit path exercises only the AFS-I Gold (2046-chip)
and AFS-Q Weil primary (10230-chip) codes, so the second oracle covers those
two families. The tertiary Weil-1500 and the four 4-bit secondary codes are
covered by Oracle 1 (Annex 3) alone. Across the two oracles, every code
section in every `codes/codes_prnNNN.hex` file is independently verified at
least once; the Gold and Weil primary sections are verified twice.

Two independent implementations — one the normative reference, one a
community reference — agreeing byte-for-byte gives high confidence that
the vectors in this package are correct.

## Known spec errata — and why this package is not affected

The competition *Technical FAQ* ([`references/technical-faq.pdf`](./references/technical-faq.pdf),
"Errata" section) documents an incorrect insertion index for **PRN 62** in
`appendix_d_weil_primary_params.csv`:

> PRN 62: correct values are `k=2360, p=1622` — the CSV lists `p=6284`,
> which is actually PRN 63's value.

`codes/codes_prn062.hex` in this distribution is built from the **correct**
parameters: it matches Annex 3 byte-for-byte (via `check-annex3`) *and*
the LANS-AFS-SIM chip dump (via `check-lans-afs-sim`), so the two
independent oracles rule out the CSV bug being reproduced here.

If your own implementation drives code generation from the parameter CSV
rather than the Annex 3 reference, PRN 62 is worth a dedicated sanity
check. `python validate.py diff /path/to/your/codes/` will surface any
disagreement PRN-by-PRN.

## Common implementation pitfalls

The same competition *Technical FAQ* is the authoritative checklist of
known LSIS-AFS implementation pitfalls — BCH(51,8) MSB-prepend, interleaver
scope (SB2+SB3+SB4 only), Weil insertion length (10223 → 10230, 1499 → 1500),
CRC-24Q scope, BPSK mapping (Logic 0 → +1.0). Every Level-1 `.hex` file
in this package is consistent with the FAQ's guidance; future level drops
(encoded frames, I/Q signals) will cite it section-by-section as the
rules they validate against.

## Secondary codes

The four 4-bit AFS-Q secondaries (`S0`…`S3`) are the literal values published
in Annex 3, Table 2:

```
S0 = 1110   →  [SECONDARY_S0] hex: E
S1 = 0111   →  [SECONDARY_S1] hex: 7
S2 = 1011   →  [SECONDARY_S2] hex: B
S3 = 1101   →  [SECONDARY_S3] hex: D
```

These do not vary by PRN. The PRN-to-secondary-index assignment
(`k = i - 1 for i ≤ 4; k = (i - 1) mod 4 for i > 4`) is a receiver/transmitter
concern and is not encoded in Level 1 vectors; only the four secondary
sequences themselves need to cross-check.

## What to do if you see a disagreement

Please open an issue on this repository with:

1. The PRN number and section (e.g. `PRN 47, WEIL_PRIMARY`).
2. The first 64 hex digits of your output for that section.
3. The first 64 hex digits of this package's output for that section.
4. A short description of your implementation (language, generator approach).

Cross-team disagreements on Level 1 are the easiest kind to resolve and the
most useful to catch early. We'd rather find the issue here than at the
interop bench.

## Reproducing from scratch

The generator that produced these vectors is part of the LuarSpace reference
implementation and is not yet public. Every code in this package was produced
by:

- **Gold code (2046 chips):** the indexed Gold-code construction of LNIS AD1
  Volume A — same taps, same initial conditions, same truncation as specified.
- **Weil primary (10230 chips):** Weil code over GF(10223) with the Annex 3
  assignment table; MSB-prepended by 2 zero bits for hex encoding.
- **Weil tertiary (1500 chips):** Weil code over GF(1499) with the Annex 3
  assignment table; no MSB padding.
- **Secondary codes:** literal 4-bit sequences from Annex 3, Table 2.

The Apache-2.0 reference implementation is scheduled for public release at
Phase 2 of the competition timeline.
