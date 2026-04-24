# Annex 3 — normative spreading-code reference

The three `.txt` files in this directory are the spreading-code references
attached to **LNIS Applicable Document 1 – Volume A, Annex 3**
(10 December 2024). They are redistributed here verbatim for convenience so
that `validate.py` can perform a self-contained byte-for-byte
correctness check against the normative reference.

These files are ESA / CCSDS material included here under fair-use /
normative-reference conventions for implementation verification only, and
are **not** relicensed under Apache-2.0. If the original distribution
changes, use `python validate.py refresh --base-url …` to update the local
copies from the canonical source.

| File | Signal | Code | Length (symbols) | Hex digits |
|:-----|:-------|:-----|-----------------:|-----------:|
| `006_GoldCode2046hex210prns.txt` | AFS-I | Primary (Gold)       |  2046 |  512 |
| `007_l1cp_hex210prns.txt`        | AFS-Q | Primary (Weil 10230) | 10230 | 2558 |
| `008_Weil1500hex210prns.txt`     | AFS-Q | Tertiary (Weil 1500) |  1500 |  375 |

## Encoding

Per Annex 3, spreading chips are packed MSB-first in 4-bit nibbles. For the
2046- and 10230-chip codes the bit count is not a multiple of 4, so **two
zero bits are prepended on the MSB side** before nibble-packing. The 1500-chip
code has no padding and therefore yields an odd number of hex digits (375).

## Canonical source

The authoritative distribution is the electronic attachment set shipped with
LNIS AD1 Volume A. If you need to confirm these files are in sync with the
current normative release, run `python validate.py refresh --base-url <URL>`
pointing at wherever ESA / CCSDS publishes the attachments (see the
top-level `validate.py` for details).

## Self-check

```bash
python validate.py check-annex3
```

Expected output: `210/210 GOLD · 210/210 WEIL_PRIMARY · 210/210 WEIL_TERTIARY`.
