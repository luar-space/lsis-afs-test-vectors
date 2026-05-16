# PocketSDR-AFS L4 cross-decode harnesses

Source code for the Apache-2.0 verifier that produces the L4
`references/pocketsdr-afs/decoded/` outputs from the shipped L3 signals
and the upstream PocketSDR-AFS receiver.

These harnesses are bundled here so a reader can **independently
reproduce the cross-decode outputs from scratch**, without trusting the
shipped `decoded/*.bin` files.  Clone PocketSDR-AFS at the pinned SHA,
apply the two bundled patches, build, run `pocket_trk` against each
shipped signal, and compare the result byte-for-byte to
`frames/frame_*.bin[64:6064]`.

## Files

| File | Purpose |
|:---|:---|
| [`verify_pocketsdr_decode.py`](./verify_pocketsdr_decode.py) | One-command end-to-end verifier: deps check, clone, patch, build, decode 10 signals, compare to `frames/`, optionally regenerate `decoded/`. |
| [`decode_signal.py`](./decode_signal.py) | Per-signal driver: tile-convert → run `pocket_trk -dump-symbols` → bytewise compare with first-mismatch localisation. |
| [`lsisiq_to_pocketsdr.py`](./lsisiq_to_pocketsdr.py) | Signal-format converter.  LSISIQ float32 zero-IF → headerless `INT8X2` int8 stream at ×100 scale.  Optional deterministic AWGN injection. |
| [`parse_pocketsdr_log.py`](./parse_pocketsdr_log.py) | Extracts `$SB2`/`$SB3`/`$SB4` CRC-pass counts from `pocket_trk -log` output.  Corroborating signal alongside the load-bearing bytewise comparator. |
| [`patches/dump-symbols.patch`](./patches/dump-symbols.patch) | Adds two opt-in CLI flags to `pocket_trk` plus one small SB1 bypass: `-dump-symbols <path>` (raw 6000 hard-decision AFS-D symbols at the entry of `decode_AFSD_frame()`, polarity-normalised via XOR with `rev`); `-dump-fec <path>` (post-LDPC + post-CRC SB2 + SB3 + SB4 data bits, 2868 bytes total per successful frame, output layout matches `inputs/frame_*_input.bin`); SB1 FID-bypass (gated on `-dump-fec`) so LDPC + CRC run on FID>0 frames too — covers boundary frames for the post-FEC oracle without changing upstream PVT behaviour when `-dump-fec` is unset. |
| [`patches/clang17-build-fix.patch`](./patches/clang17-build-fix.patch) | Renames a file-static `satpos()` in `src/sdr_pvt_afs.c` whose name shadows RTKLIB's exported `satpos()` under Apple Clang 17+ strict-declaration checking.  No semantic change. |

## One-command verifier (recommended)

```bash
brew install fftw libusb     # macOS Homebrew (Linux: libfftw3-dev libusb-1.0-0-dev)
python references/pocketsdr-afs/harnesses/verify_pocketsdr_decode.py
```

Useful flags:

- `--upstream-dir DIR` — reuse an existing PocketSDR-AFS checkout
  instead of cloning fresh.  Verifies HEAD matches `PINNED_SHA`
  unless `--no-sha-check`.
- `--workdir DIR` — keep build artefacts and per-signal outputs
  under a known path.
- `--keep-workdir` — don't delete the workdir on success (useful
  for inspecting intermediate `int8x2.bin`, `syms.bin`, and
  `*.log` files per signal).
- `--check-deps` — only check Homebrew/build dependencies and exit.
- `--signal NAME` — run only one entry from `SIGNAL_TEST_VECTORS`
  (debugging convenience).
- `--regenerate-decoded` — overwrite both
  `references/pocketsdr-afs/decoded/decoded_signal_*.bin` (channel,
  10 files) and `decoded_fec_signal_*.bin` (post-FEC, 10 files —
  boundary frames included via the bundled SB1 FID-bypass) with the
  byte-exactly-recovered outputs.  Maintainer command.
- `--awgn-cn0 DBHZ` — inject deterministic AWGN at the requested
  C/N0 before the int8 cast (default: off).

Exit code 0 means every shipped L3 signal was independently
demodulated to its source L2 frame's 6000-symbol payload (channel
oracle, 10/10) AND the receiver's post-FEC SB2/SB3/SB4 bits exactly
equal the canonical pre-encode bytes shipped at v0.2.1 (post-FEC
oracle, 10/10 — boundary frames included via the bundled SB1
FID-bypass) — both reproduced from upstream BSD-2-Clause source at
the pinned commit.  This is the strongest form of end-to-end
interoperability validation: every layer of the receive chain
(acquire → track → demod → deinterleave → LDPC → CRC) round-trips
bit-exactly to the corresponding shipped vector.

## Build dependencies

- **Homebrew** (macOS): `fftw` (provides `fftw3f`), `libusb`.
- **C compiler**: system `cc`/`clang` is sufficient on macOS arm64;
  upstream's makefiles pick the right compiler per platform.
- **Python**: stdlib + numpy ≥ 1.20 (already an opt-dep of the
  test-vectors package via `[fast]` extra).
- The verifier runs upstream's `lib/clone_lib.sh` to fetch
  `libfec` (https://github.com/quiet/libfec) and LDPC-codes
  (https://github.com/radfordneal/LDPC-codes) automatically.

## Disclosed normalisations

See [`../../../CORRECTNESS.md`](../../../CORRECTNESS.md) §"Level 4 —
Decoding" → "Disclosed normalisations" for the full statement.
Summary:

- **3× tile**: PocketSDR-AFS's `decode_AFSD` requires
  `ch->lock >= 6068 + 500` symbols + two consecutive sync prefixes
  (= ~24.14 s of locked tracking).  Our 12 s signals are tiled
  3× before being fed to `pocket_trk`; all tiles are byte-identical.
- **float32 → int8 ×100 cast**: lossless given the strict ±1.0 BPSK
  enforced by `validate.py check-signals`.
- **AWGN injection**: off by default; available via `--awgn-cn0`
  for robustness studies.

## Re-pinning the upstream SHA

When `PINNED_SHA` in `verify_pocketsdr_decode.py` is bumped:

1. Re-test that both patches in `patches/` apply cleanly to the
   new SHA: `git apply --check patches/*.patch` from inside a
   fresh checkout.
2. If a patch fails to apply, regenerate it from the new SHA and
   commit the updated `.patch` alongside the `PINNED_SHA` change.
3. Re-run `verify_pocketsdr_decode.py --regenerate-decoded` to
   refresh `references/pocketsdr-afs/decoded/`.
4. Run `validate.py rebuild-manifest` and commit the updated
   `manifest.json`.
5. Verify CI's cheap `check-decode` still reports 10/10.

## Related

- [`../README.md`](../README.md) — oracle scope and pass criterion.
- [`../../../validate.py`](../../../validate.py) `check-decode` —
  cheap CI-friendly oracle (just compares `decoded/` to `frames/`).
- [`../../lans-afs-sim/harnesses/verify_oracle.py`](../../lans-afs-sim/harnesses/verify_oracle.py)
  — analogue verifier for L1 + L2.
