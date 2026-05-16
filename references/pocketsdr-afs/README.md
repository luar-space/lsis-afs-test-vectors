# PocketSDR-AFS — L4 cross-decode oracle

This subtree is the **Level 4** (decoding interoperability) verification
artefact set for the LSIS-AFS Test Vectors package.  It bundles
[**PocketSDR-AFS**](https://github.com/osqzss/PocketSDR-AFS) — Takasu /
Ebinuma's AFS-specific software-defined receiver, BSD-2-Clause —
together with a small Apache-2.0 verifier that closes the receive-side
loop the L3 Pass Criteria call for.

The pass criterion (interoperability.pdf, *Level 4: Decoding
Interoperability*):

> Frame synchronization successful (>99% detection); All subframes
> decoded correctly; CRC validation passes; Decoded data matches
> original input exactly.

Two oracles are run end-to-end against every shipped L3 signal
(`signals/signal_*_12s.iq.gz`) by PocketSDR-AFS at the pinned upstream
SHA below:

- **Channel-symbol oracle** — the receiver's 6000 hard-decision AFS-D
  symbols (post-sync, pre-deinterleave) are byte-equal to the
  corresponding `frames/frame_*.bin[64:6064]` payload.  Applies to all
  10 signals.  Shipped as `decoded_signal_*.bin` (10 files, 6000 bytes
  each).
- **Post-FEC oracle** — the receiver's post-LDPC + post-CRC SB2 + SB3
  + SB4 data bits are byte-equal to the corresponding
  `inputs/frame_*_input.bin` (the canonical pre-encode bytes shipped
  at v0.2.1).  Applies to all 10 signals (including the 2 FID=3
  boundary frames, via a small bundled SB1 FID-bypass — see
  `harnesses/patches/dump-symbols.patch`).  Shipped as
  `decoded_fec_signal_*.bin` (10 files, 2868 bytes each).

## Pinned upstream

| | |
|:---|:---|
| Upstream | https://github.com/osqzss/PocketSDR-AFS |
| Pinned SHA | `5b23809f30d68518b7fad7a564fd0fac57cc497d` |
| License | BSD-2-Clause (© 2021–2023 T. Takasu; additional © 2025 Takuji Ebinuma) |

The upstream `LICENSE.txt` is redistributed verbatim as
[`./LICENSE.txt`](./LICENSE.txt).

## Layout

```
references/pocketsdr-afs/
├── LICENSE.txt              # upstream BSD-2-Clause, redistributed verbatim
├── README.md                # this file
├── decoded/                 # 20 files:
│                            #   10 × decoded_signal_*.bin       (6000 bytes, channel-symbol oracle)
│                            #   10 × decoded_fec_signal_*.bin   (2868 bytes, post-FEC oracle)
└── harnesses/               # Apache-2.0 verifier (clone + build + decode)
    ├── verify_pocketsdr_decode.py   # one-command end-to-end verifier
    ├── decode_signal.py             # per-signal driver
    ├── lsisiq_to_pocketsdr.py       # signal-format converter
    ├── parse_pocketsdr_log.py       # CRC-pass log parser
    ├── README.md
    └── patches/                     # local patches against pinned SHA
        ├── dump-symbols.patch       # adds -dump-symbols + -dump-fec flags
        └── clang17-build-fix.patch
```

## Verifying the oracle (one command)

```bash
brew install fftw libusb     # macOS Homebrew dependencies (Linux: apt equivalents)
python references/pocketsdr-afs/harnesses/verify_pocketsdr_decode.py
# Clones PocketSDR-AFS @ pinned SHA, applies the 2 patches, builds
# pocket_trk, decodes all 10 signals, compares both oracles
# (channel vs frames/, post-FEC vs inputs/).  ~5–10 min.
# On success:
#   Channel-symbol oracle: 10/10 signals byte-exactly recovered
#   Post-FEC oracle:       10/10 signals byte-exactly recovered
```

The cheap CI form (no clone, no build, no `pocket_trk` run — just
bytewise compare the shipped `decoded/` files to `frames/` and to
`inputs/`) is exposed on the top-level validator:

```bash
python validate.py check-decode
#   Channel-symbol oracle: 10/10
#   Post-FEC oracle:       10/10
```

## What's in `decoded/`

Twenty files: a channel-symbol output for every L3 signal (10 files,
6000 bytes each), plus a post-FEC output for every L3 signal (10
files, 2868 bytes each — the 2 FID=3 boundary frames are covered too
thanks to the bundled SB1 FID-bypass).  Every byte is 0 or 1; no header.

| Source signal | Channel output (vs `frames/[64:6064]`) | FEC output (vs `inputs/`) | Coverage |
|:---|:---|:---|:---|
| `signal_message_1_12s.iq.gz` | `decoded_signal_message_1_12s.bin` | `decoded_fec_signal_message_1_12s.bin` | TC1 baseline (all-zeros) |
| `signal_message_2_12s.iq.gz` | `decoded_signal_message_2_12s.bin` | `decoded_fec_signal_message_2_12s.bin` | content variation (all-ones) |
| `signal_message_3_12s.iq.gz` | `decoded_signal_message_3_12s.bin` | `decoded_fec_signal_message_3_12s.bin` | content variation (alternating) |
| `signal_message_4_12s.iq.gz` | `decoded_signal_message_4_12s.bin` | `decoded_fec_signal_message_4_12s.bin` | content variation (marker surrogate) |
| `signal_message_5_12s.iq.gz` | `decoded_signal_message_5_12s.bin` | `decoded_fec_signal_message_5_12s.bin` | content variation (xorshift32) |
| `signal_prn2_baseline_12s.iq.gz` | `decoded_signal_prn2_baseline_12s.bin` | `decoded_fec_signal_prn2_baseline_12s.bin` | TC2 — secondary S1 |
| `signal_prn3_baseline_12s.iq.gz` | `decoded_signal_prn3_baseline_12s.bin` | `decoded_fec_signal_prn3_baseline_12s.bin` | TC2 — secondary S2 |
| `signal_prn12_baseline_12s.iq.gz` | `decoded_signal_prn12_baseline_12s.bin` | `decoded_fec_signal_prn12_baseline_12s.bin` | TC2 — secondary S3 |
| `signal_boundary_at_prn12_12s.iq.gz` | `decoded_signal_boundary_at_prn12_12s.bin` | `decoded_fec_signal_boundary_at_prn12_12s.bin` | TC4 — FID/TOI maxima |
| `signal_boundary_max_fields_at_prn12_12s.iq.gz` | `decoded_signal_boundary_max_fields_at_prn12_12s.bin` | `decoded_fec_signal_boundary_max_fields_at_prn12_12s.bin` | TC4 — WN/ITOW maxima |

Boundary frames (FID=3) are covered on both layers thanks to a small
bundled SB1 bypass: PocketSDR-AFS's `sync_AFS_SF1_FID0` only matches
FID=0 SB1 codewords, but LDPC + CRC are FID-agnostic block codes, so
the bundled patch proceeds past the SB1 search with a placeholder TOI
to let LDPC + CRC run for FID>0 frames.  The bypass is gated behind
`-dump-fec`; upstream behaviour is unchanged when the flag isn't set.
Total `decoded/` footprint: **~88 KB** (60 KB channel + 28 KB FEC).

## See also

- [`../../CORRECTNESS.md`](../../CORRECTNESS.md) §"Level 4 — Decoding"
  for the full disclosure: pass criterion, two bundled patches,
  disclosed normalisations (3× tile, ×100 int8 cast, optional AWGN),
  and the trust chain L1 → L2 → L3 → L4.
- [`harnesses/README.md`](./harnesses/README.md) for build + run
  instructions and the harness file map.
