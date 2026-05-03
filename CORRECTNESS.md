# Correctness — how these vectors are proved right

This package ships test vectors for **Level 1 (spreading codes)** and
**Level 2 (encoded frames)** of the LSIS-AFS interoperability plan.
Each level is verified by independent oracles whose pass criteria
trace directly to the competition's published rules.

| Level | Oracle 1 (normative) | Oracle 2 (independent) | Pass criteria source |
|:---|:---|:---|:---|
| **1** | LNIS AD1 Volume A Annex 3 hex files | LANS-AFS-SIM chip dumps | interop doc "Pass Criteria — Level 1" |
| **2** | LSIS V1.0 §2.4 + Gateway 3 checklist | LANS-AFS-SIM frame dumps | competition Gateway 3 deliverables checklist + interop doc Test Cases 1 + 4 |

The remainder of this document walks through each oracle, the encoding
rules it enforces, and any disclosed normalisations.

---

# Level 1 — Spreading Codes

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

---

# Level 2 — Encoded Frames

Level 2 verifies the **encoder pipeline**: BCH(51,8) on Subframe 1, CRC-24Q
+ LDPC(1/2) + 60×98 block interleaver on SB2/SB3/SB4, plus the 68-symbol
sync prefix. Per the competition's L2 requirements, **no canonical "Annex 2"
distribution of reference encoded frames exists** — the competition's
deliverables Risk Assessment names this gap explicitly:

> **Test vector availability — Medium — Generate own test vectors from encoder.**

This package fills that gap. Two oracles are used.

## Oracle 1 — LSIS V1.0 §2.4 structural rules (L2 normative-equivalent)

Each `frame_*.bin` is **6064 bytes**: a 64-byte `LSISAFS\0` header (defined
in [`interoperability.pdf`](./references/interoperability.pdf), *Frame
Export Format*) followed by 6000 unpacked binary symbols.
`validate.py check-frames` enforces every checklist item from the
competition's Gateway 3 *Validation Checklist*:

| Rule | Source | What `check-frames` verifies |
|:---|:---|:---|
| Header magic = `LSISAFS\0` | interop doc, *Frame Export Format* | First 8 bytes of every file |
| Header version = 1 (uint32 LE) | interop doc, *Frame Export Format* | Bytes 8–11 |
| Header frame length = 6000 (uint32 LE) | interop doc, *Frame Export Format* | Bytes 12–15 |
| Header PRN matches expected | interop doc, Test Cases 1 + 4 | Bytes 16–19 (1 for messages 1–5, 210 for boundary) |
| Total file = 6064 bytes | interop doc, *Frame Export Format* | `os.stat().st_size` |
| Symbol-domain values ∈ {0, 1} | spec §2.4 (BPSK input is binary) | Every byte of the 6000-symbol payload |
| Sync prefix (first 68 sym) = `0xCC63F74536F49E04A` | FAQ Q17, spec Table 12 | First 68 bytes of payload, MSB-first decode of the hex |
| Symbol count = 68 + 52 + 5880 = 6000 | spec §2.4, Gateway 3 checklist | Implicit in the 6000-byte payload size |
| Header field byte order | this package's *Frame Export Format* convention | uint32 fields (version, frame_length, PRN) and the int64 header timestamp are stored little-endian; magic is the byte sequence `LSISAFS\0` (no endianness) |

The structural oracle confirms the frame *layout* matches the spec; bit-exact
agreement on the BCH/CRC/LDPC/interleaver content is verified by Oracle 2.

```bash
python validate.py check-frames
#   Structural checks:  6/6
#
# OK — all 6 frames pass spec structural checks.
```

### Coverage scope

The interop doc's L2 *Test Cases 1–5* split across three concerns. This
package fills two of them in v0.2.0 and disclaims the rest:

| Test Case | Concern | v0.2.x coverage |
|:---|:---|:---|
| **TC1** Public Test Messages 1–5 | encoder bit-exactness on canonical inputs | ✅ exercised — `frame_message_1..5.bin` |
| **TC2** PRN sweep (1–12) | header PRN field is carried and round-trippable | partial — boundary case PRN=210 covered by `frame_boundary.bin`; full PRN sweep is content-agnostic at L2 (the spreading code only enters at L3) and lands meaningfully when L3 ships |
| **TC3** Message-type coverage in SB3/SB4 | semantic (almanac / ephemeris / corrections) | not exercised — encoder bit-exactness is content-agnostic; semantic coverage lands with the L5 parser drop |
| **TC4** Boundary frame | PRN/FID/TOI at field maxima | ✅ exercised — `frame_boundary.bin` (PRN=210, FID=3, TOI=99) |
| **TC5** Error conditions / CRC detection | receiver-side robustness | not exercised — out of scope for a transmit-side encoder vector set; lands with the L4 cross-decoder drop |

Pass-criteria the validator enforces today are TC1 + TC4 plus the structural
checklist above. TC2/TC3/TC5 are listed here so any consumer reading only the
oracle table doesn't mistake silence for coverage.

## Oracle 2 — LANS-AFS-SIM (L2 independent at encoder-logic level)

This is a **two-encoder cross-validation**: the LuarSpace encoder produced
the bytes in `frames/`, and an independent encoder (LANS-AFS-SIM, BSD-2-Clause,
© 2025 Takuji Ebinuma) produced the bytes in `references/lans-afs-sim/frames/`.
Oracle 2 compares them byte-for-byte. For a defect to slip through, both
encoders would have to fail in the same way on the same input — a much
narrower failure mode than either encoder's bugs taken alone.

The harness at
[`references/lans-afs-sim/harnesses/dump_lans_frame.c`](./references/lans-afs-sim/harnesses/dump_lans_frame.c)
(Apache-2.0, bundled in this repo since v0.2.0) is a **thin caller** that
invokes LANS-AFS-SIM's encoding routines unchanged. Encoding logic is
entirely upstream BSD-2-Clause code:

| Routine | What it does |
|:---|:---|
| `sdr_unpack_bits()` | Unpack sync bytes (PocketSDR utility) |
| `generate_BCH_AFS_SF1()` | BCH(51,8) gen-poly 763 octal → 52-symbol SB1 |
| `append_CRC24()` | CRC-24Q polynomial `0x1864CFB` |
| `encode_LDPC_AFS_SF2()` | LDPC(1/2) for SB2 (1200 → 2400 sym) |
| `encode_LDPC_AFS_SF3()` | LDPC(1/2) for SB3 + SB4 (870 → 1740 sym each) |
| `interleave_AFS_SF234()` | 60×98 block interleaver |

The harness contributes only: input-pattern fill (zeros / ones / alternating
/ marker / random), the constant sync-pattern bytes, and the output file
write. None of that replicates encoding logic.

`validate.py check-lans-afs-sim-frames` strips our 64-byte header and
compares the 6000-symbol payload byte-for-byte against the corresponding
`references/lans-afs-sim/frames/lans_frame_*.bin`:

```bash
python validate.py check-lans-afs-sim-frames
#   Bit-exact vs LANS-AFS-SIM:  6/6
#
# OK — all 6 frames bit-exact against LANS-AFS-SIM reference.
```

### Disclosed normalisation: SB2 spare bits

The harness pre-fills SB2 input bits **1150–1175** with the spec-mandated
alternating pattern (FAQ Q21: *"Spare bits in subframes are filled with
alternating 0/1 starting with 0"*) **before** CRC-24Q + LDPC encoding.
LuarSpace's `frame_build()` enforces this rule inside the encoder; the
harness applies it to LANS-AFS-SIM's input so both implementations
encode spec-compliant data, regardless of whether Ebinuma's encoder
would have applied the rule itself.

This is normalisation **to the spec**, not to LuarSpace. For those 26 SB2
input bits, both implementations are operating on identical spec-compliant
data rather than independently arriving at the same content; for the other
2842 input bits (1150 SB2 + 846 SB3 + 846 SB4), the comparison is genuinely
independent. The LDPC / BCH / CRC / interleaver agreement (the 99% heavy
lifting) is fully cross-validated.

## Test message inputs

The 6 frames cover the five public Level-2 message slots from
`interoperability.pdf` plus one boundary case from *Test Case 4: Boundary
Conditions*. Where the interop doc leaves message content illustrative rather
than machine-readable, this package pins a reproducible convention:

| File | Pattern | First 8 SB2 input bits | Source |
|:---|:---|:---|:---|
| `frame_message_1.bin` | All zeros | `00000000` | TM1 |
| `frame_message_2.bin` | All ones | `11111111` | TM2 |
| `frame_message_3.bin` | Alternating bits, start 1 (first packed byte 0xAA) | `10101010` | TM3 |
| `frame_message_4.bin` | Bytewise marker (`0x00, 0x01, 0x02, …`) | `00000000` | TM4 surrogate (replaces "known ephemeris data") |
| `frame_message_5.bin` | xorshift32 PRNG, seed=`0xAF52` | (seed-derived) | TM5 |
| `frame_boundary.bin`  | Alternating, start 0 (first packed byte 0x55), max field values (PRN=210, FID=3, TOI=99) | `01010101` | Test Case 4 |

### TM4 bytewise marker convention

The marker byte sequence `[0x00, 0x01, 0x02, …]` is unpacked MSB-first to
bits and **truncated to each subframe's data-bit count independently**.
Each of SB2 (1176 bits), SB3 (846 bits) and SB4 (846 bits) starts the
marker afresh from byte `0x00` — the marker stream is *not* one
continuous bytestream split across the three subframes. A third-party
reimplementation that fed the same bytes as a single concatenated stream
into all three subframes would see SB3 start at byte 147 (≈ `0x93`) and
SB4 at byte 252 (≈ `0xFC`), giving a different bit pattern and a
mismatch on `check-lans-afs-sim-frames`.

### TM3 alternating convention

The interop doc annotates TM3 as *"Alternating pattern (0xAA…)"*. This
package matches that literally — the first packed byte of the SB2/SB3/SB4
input is **`0xAA` (bits `10101010`, MSB-first)**, i.e. `bit_i = (i + 1) mod 2`.

FAQ Q21's spec-mandated *"alternating 0/1 starting with 0"* rule is scoped
to **spare bits inside subframes**, not to test-message data, and is not
extended here. Spare-bit normalisation (SB2 bits 1150–1175) continues to
follow Q21 verbatim — see *Disclosed normalisation* above.

Other teams cross-checking against `frame_message_3.bin` should use
`bit_i = (i + 1) mod 2` for SB2 (1176 bits), SB3 (846 bits), and SB4
(846 bits) independently. `frame_boundary.bin` uses the start-with-0
(`0x55`) convention because *Test Case 4* does not pin a starting bit;
`diff-frames` will report a high mismatch count if two implementations
choose different starting bits, so the disagreement is trivial to surface
and resolve.

> **v0.2.x note.** Earlier drafts of this document (and the LANS-AFS-SIM
> harness pre-rebuild) used `0x55` for TM3, treating the doc's "(0xAA…)"
> as illustrative. v0.2.0 reverses that — the convention now matches the
> interop doc verbatim. Only `frame_message_3.bin` and the matching
> `lans_frame_message_3.bin` change byte content; the other five frames
> are unaffected.

### TM5 PRNG: xorshift32

Reproducible bit stream. Reference C:

```c
uint32_t state = 0xAF52u;
for (int i = 0; i < 2868; i++) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    bits[i] = state & 1u;
}
/* SB2 = bits[0..1175], SB3 = bits[1176..2021], SB4 = bits[2022..2867] */
```

Equivalent in Python, Rust, Go, Java — five lines, no library dependencies.
The seed value `0xAF52` is mnemonic ("AF5₂" → AFS L2) and otherwise
arbitrary; the only requirement is that it is documented and reproducible.

### Header timestamp

The 8-byte Unix-epoch timestamp at header offset 20 is pinned to
**`1738108800`** (2025-01-29T00:00:00Z, the LSIS V1.0 publication date) so
that every `frame_*.bin` is byte-stable across rebuilds. This keeps the
manifest SHA-256 reproducible — important for `verify-manifest` and CI.

## Using these vectors

This package exists for **other teams to validate their own encoder
output** against a reference set that is itself externally verified.

### Verifying your implementation against ours

If you've built an LSIS-AFS encoder and exported frames in the format
defined by `interoperability.pdf`, run:

```bash
python validate.py diff-frames /path/to/your/frames/
```

This compares your `frame_*.bin` payloads byte-for-byte against the
shipped LuarSpace set, reporting any mismatches per-frame.  A clean
diff means your encoder produces the same encoded bits as ours **and**
as LANS-AFS-SIM (since the two oracles agree).  A mismatch is
interesting — please open an issue with the test message name and your
first 64 differing payload bytes.  Cross-team disagreements are the
easiest to resolve and most useful to catch early.

### Verifying the LANS-AFS-SIM oracle from upstream sources

Both Level-1 (`references/lans-afs-sim/codes/`) and Level-2
(`references/lans-afs-sim/frames/`) second-oracle dumps are fully
reproducible from upstream BSD-2-Clause sources.  The one-command
verifier:

```bash
python references/lans-afs-sim/harnesses/verify_oracle.py
```

clones LANS-AFS-SIM at the pinned SHA
`0578f298ba68d8508ab7d780be843faed3e2b274`, builds upstream, builds
the bundled harnesses, runs every dumper, and `cmp`s the result against
the shipped `.bin` set.  Exit 0 means every Level-1 chip dump (420
files) and every Level-2 frame dump (6 files) reproduces byte-for-byte.

For manual reproduction or debugging, the same steps are documented
in
[`references/lans-afs-sim/harnesses/README.md`](./references/lans-afs-sim/harnesses/README.md).

This rebuilds the *oracle*, not the LuarSpace vectors themselves —
the public Apache-2.0 release of the LuarSpace generator is scheduled
for Phase 2 of the competition timeline.  Until then, the LANS-AFS-SIM
agreement at the encoded-bit level is the external evidence that the
LuarSpace vectors in `frames/` are correct.

---

# Canonical pre-encode inputs (`inputs/`, v0.2.1)

For each shipped `frames/frame_*.bin`, the corresponding
`inputs/frame_*_input.bin` provides the **exact SB2 + SB3 + SB4 bytes the
encoder consumed** before BCH/CRC/LDPC/interleave.  The intent is to let
any team feed the documented inputs into their encoder and bit-compare
the output against `frames/frame_*.bin` via `diff-frames`, isolating
correctness to the FEC pipeline alone.

## File format

| Field | Bytes | Notes |
|:---|---:|:---|
| SB2 input bits | 1176 | Unpacked, 1 byte per bit, value 0x00 / 0x01 |
| SB3 input bits |  846 | Same |
| SB4 input bits |  846 | Same |
| **Total** | **2868** | per file |

No header, no padding.  The unpacked-symbol convention matches
`frames/frame_*.bin`'s payload format for consistency across the package.

## What's normalised, what isn't

**Applied in the canonical inputs (post-normalisation):**
- **FAQ Q21 / LSIS-300 spare-bit pattern** on SB2 bits 1150–1175.  Every
  shipped `inputs/frame_*_input.bin` has these 26 bits set to the
  spec-mandated alternating-0/1 pattern starting with 0, regardless of
  the underlying test message pattern.  This makes the file
  self-describing ground truth: a contestant whose encoder consumes the
  file produces our `frames/frame_*.bin` regardless of whether their
  encoder applies Q21 internally.

**Not applied (the encoder's responsibility):**
- **CRC-24Q** computation over the 1176-bit SB2 / 846-bit SB3 / 846-bit
  SB4 data, producing the 1200- / 870- / 870-bit codeword info inputs.
- **LDPC(1/2)** rate-1/2 quasi-cyclic encoding of each subframe info
  block.
- **60×98 block interleaver** over the concatenation of encoded SB2 +
  SB3 + SB4 (5880 symbols total).
- **BCH(51,8)** encoding of SB1 from the (FID, TOI) tuple — these are
  not stored in `inputs/`; they're per-file constants pinned in the
  `FRAME_TEST_VECTORS` table in `validate.py`.
- **64-byte `LSISAFS\0` header** prepended to the 6000-symbol payload.

## Per-file pattern

| File | Pattern (SB2/SB3/SB4 before Q21) |
|:---|:---|
| `frame_message_1_input.bin` | All zeros |
| `frame_message_2_input.bin` | All ones |
| `frame_message_3_input.bin` | Alternating, `bit_i = (i + 1) mod 2` (first packed byte `0xAA`) |
| `frame_message_4_input.bin` | Bytewise marker: bit at position `i` is the MSB-first bit of byte `(i // 8) mod 256` within each subframe; subframes restart from byte `0x00` |
| `frame_message_5_input.bin` | xorshift32 PRNG, seed `0xAF52`, single bitstream consumed across SB2 → SB3 → SB4 |
| `frame_boundary_input.bin` | Alternating, `bit_i = i mod 2` (first packed byte `0x55`) |

After the pattern is filled into SB2, the FAQ Q21 normalisation overwrites
SB2 bits 1150–1175 with `[0, 1, 0, 1, …]` regardless of pattern.  SB3 and
SB4 are not touched by Q21 (no spare-bit ranges in those subframes for
this normalisation).

## Reproducibility

The inputs are reproducible from the documented patterns + Q21
normalisation alone — no encoder, no decoder, no orbital data.  The
helper logic in `validate.py` (`_build_canonical_input`) is stdlib-only
Python; `python validate.py check-canonical-inputs` re-derives the bytes
and confirms they match the shipped files (`6/6`), and
`python validate.py build-canonical-inputs` regenerates `inputs/` from
the same logic.  CI runs both on every push.

## Workflow for cross-team validation

```bash
# 1. Read inputs/frame_message_X_input.bin → 2868 bytes of SB2/SB3/SB4 input.
# 2. Feed into your encoder pipeline (CRC + LDPC + interleave + BCH SB1 +
#    sync prefix + header).
# 3. Diff against ours:
python validate.py diff-frames /path/to/your/frames/
```

Bit-exact agreement isolates correctness to the FEC pipeline.  Input-
construction conventions (TM3 starting bit, TM4 marker stream restart,
TM5 PRNG, FAQ Q21 spare bits) are no longer a source of cross-team
disagreement — every team consumes the same canonical bytes.

If you generate your own canonical inputs (e.g., to test your
input-builder logic independently), `python validate.py diff-inputs
/path/to/your/inputs/` byte-compares against ours and localises the
first disagreement to a specific subframe + bit position.
