# Workshop baseline bundle

Reference artefacts in the shape expected by the **ESA-CCSDS LSIS-AFS
mid-project workshop** at Goonhilly Earth Station, June 2026, as defined by
the workshop programme (authoritative for Day 1 / Day 2 file exchange,
superseding the older `references/interoperability.pdf` shapes).

All artefacts here were produced by **LunaLink** — the reference
implementation — via its CLI. Other teams should be able to byte-compare
their own outputs against these files.

## Shape (per workshop programme)

| Artefact | Shape | Workshop spec |
|---|---|---|
| `codes.txt` | 210 lines × 512 hex chars | Gold codes only, MSB-first per Annex 3 (with 2 leading pad bits) |
| `frame.bin` | exactly 6000 bytes | one byte per symbol (0x00 / 0x01), no header |
| `signal_*.iq32` | raw float32 LE, interleaved `[I, 0.0, I, 0.0, ...]` | 1.023 MHz (1 sample per chip), 12 s, no header — AFS-I-only baseline (Q = 0.0) |

Per the admin's clarification on the workshop thread, the Q channel is
forced to `0.0` for the workshop baseline (AFS-I-only). Lunalink's full
LSIS-AFS pipeline including AFS-Q remains available via `--rate 10230000`
for the advanced track.

## Canonical L1 frame input

The single canonical frame in this bundle:

| Field | Value |
|---|---|
| PRN | 1 |
| FID | 0 |
| TOI | 42 |
| WN | 100 |
| ITOW | 250 |
| CED, Health, Time Conversions | all zeros |
| SB3, SB4 | all zeros |

## Level-3 cross-decode signals

The four cases from the workshop programme:

| File | PRN | FID | TOI | WN | ITOW | Notes |
|---|---|---|---|---|---|---|
| `signal_l3_tc1.iq32.gz` | 1 | 0 | 0 | 0 | 0 | all-zeros baseline |
| `signal_l3_tc2.iq32.gz` | 1 | 0 | 42 | 100 | 250 | identical to `signal_canonical.iq32.gz` |
| `signal_l3_tc3.iq32.gz` | 5 | 2 | 99 | 8191 | 503 | maxima (except PRN/FID) |
| `signal_l3_tc4.iq32.gz` | 12 | 3 | 50 | 1000 | 100 | mid-range |

CED defaults to all-zeros for all four cases (only the four navigation
timing fields appear in the workshop pass criterion; CED is V1.0-TBW).

## Decompressing the signals

The `.iq32.gz` files compress ~30× (the Q channel is constant zero).
Decompress before submission to the workshop GitLab:

```bash
gunzip -k workshop/signal_canonical.iq32.gz
# → workshop/signal_canonical.iq32 (98,208,000 bytes)
```

## Reproducing with the LunaLink CLI

Each artefact is produced by a single command:

```bash
# codes.txt — Gold spreading codes for PRN 1..210
lunalink generate-codes --out codes.txt

# Canonical 6000-byte frame
lunalink encode --format frame \
    --prn 1 --fid 0 --toi 42 --wn 100 --itow 250 \
    --out frame.bin

# Canonical workshop signal (1.023 MHz, AFS-I-only, Q=0)
lunalink encode --format iq32 --rate 1023000 \
    --prn 1 --fid 0 --toi 42 --wn 100 --itow 250 \
    --out signal_canonical.iq32

# Level-3 test cases (TC1, TC3, TC4 — TC2 is the canonical above)
lunalink encode --format iq32 --rate 1023000 \
    --prn 1 --fid 0 --toi 0 --wn 0 --itow 0 \
    --out signal_l3_tc1.iq32
lunalink encode --format iq32 --rate 1023000 \
    --prn 5 --fid 2 --toi 99 --wn 8191 --itow 503 \
    --out signal_l3_tc3.iq32
lunalink encode --format iq32 --rate 1023000 \
    --prn 12 --fid 3 --toi 50 --wn 1000 --itow 100 \
    --out signal_l3_tc4.iq32
```

The CLI is deterministic — re-running any of the above against the same
LunaLink build yields a byte-identical output.

## Validation lineage

Each artefact is verified end-to-end:

- **`codes.txt`** — all 210 Gold lines byte-equal to
  `references/annex-3/006_GoldCode2046hex210prns.txt`.
- **`frame.bin`** (canonical) — produced by the same `frame_build`
  pathway used to produce the LANS-AFS-SIM-validated frames in
  `frames/`; the all-zeros boundary case (`frame_message_1.bin` with its
  64-byte header stripped) is byte-equal to LunaLink CLI output for
  `--prn 1 --fid 0 --toi 0 --wn 0 --itow 0`.
- **`signal_*.iq32`** — every one of the 12,276,000 I samples satisfies
  `I[k] = (1 − 2·sym[e]) · (1 − 2·gold[k mod 2046])` exactly, where
  `e = k // 2046` is the epoch index and `sym` is the frame symbol
  stream from `frame_build`. This is the same polarity invariant used
  by `validate.py check-signals` for Level-3 L1+L2 chain verification.
  Q is strictly `0.0` at every sample.
