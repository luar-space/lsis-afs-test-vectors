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
#   Structural checks:  7/7
#
# OK — all 7 frames pass spec structural checks.
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
#   Bit-exact vs LANS-AFS-SIM:  7/7
#
# OK — all 7 frames bit-exact against LANS-AFS-SIM reference.
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

The 7 frames cover the five public Level-2 message slots from
`interoperability.pdf` plus two boundary cases from *Test Case 4: Boundary
Conditions*. Where the interop doc leaves message content illustrative rather
than machine-readable, this package pins a reproducible convention:

| File | Pattern | First 8 SB2 input bits | Source |
|:---|:---|:---|:---|
| `frame_message_1.bin` | All zeros | `00000000` | TM1 |
| `frame_message_2.bin` | All ones | `11111111` | TM2 |
| `frame_message_3.bin` | Alternating bits, start 1 (first packed byte 0xAA) | `10101010` | TM3 |
| `frame_message_4.bin` | Bytewise marker (`0x00, 0x01, 0x02, …`) | `00000000` | TM4 surrogate (replaces "known ephemeris data") |
| `frame_message_5.bin` | xorshift32 PRNG, seed=`0xAF52` | (seed-derived) | TM5 |
| `frame_boundary.bin`  | Alternating, start 0 (first packed byte 0x55), header field maxima (PRN=210, FID=3, TOI=99) | `01010101` | Test Case 4 — FID/TOI/PRN maxima |
| `frame_boundary_max_fields.bin` | All-ones SB2/SB3/SB4 with SB2[13..21] (ITOW field) clamped to 503 spec max; same FID=3, TOI=99, PRN=210 | `11111111` | Test Case 4 — WN=8191, ITOW=503 maxima (added v0.2.2) |

### Why two boundary frames?

`frame_boundary.bin` (shipped in v0.2.0) covers TC4's **header-field
maxima**: PRN=210, FID=3, TOI=99.  Its SB2/SB3/SB4 use the alternating-
start-with-0 pattern, which fills the WN field (SB2[0..12]) and the
ITOW field (SB2[13..21]) with whatever that pattern produces — neither
at minimum nor at maximum.

`frame_boundary_max_fields.bin` (added in v0.2.2) covers TC4's
**SB2-field maxima**: WN=8191 (the 13-bit raw maximum) and ITOW=503
(the spec maximum per LSIS V1.0 §2.4.3.1.6 — the 9-bit raw max 511 is
out-of-range and would be a TC5 case).  All other SB2/SB3/SB4 bits are
all-ones.  Header fields stay at maxima (PRN=210, FID=3, TOI=99).

Together the two frames exercise every TC4 boundary input listed in
`interoperability.pdf` *except* PRN-210 at L3 (no spec-defined matched-
code phase assignment for PRN > 12, see v0.3.0 §"Why no L3 boundary
frame").

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
| `frame_boundary_max_fields_input.bin` | All-ones EXCEPT SB2 bits 13..21 (ITOW field) = `0b111110111` MSB-first (= 503, the spec max per LSIS V1.0 §2.4.3.1.6) — added v0.2.2 |

After the pattern is filled into SB2, the FAQ Q21 normalisation overwrites
SB2 bits 1150–1175 with `[0, 1, 0, 1, …]` regardless of pattern.  SB3 and
SB4 are not touched by Q21 (no spare-bit ranges in those subframes for
this normalisation).

### `max_fields` ITOW override

The `max_fields` pattern fills SB2/SB3/SB4 with all-ones, then explicitly
clamps the 9-bit ITOW field at SB2[13..21] to `0b111110111` (= 503,
MSB-first) before applying the FAQ Q21 normalisation.  All-ones in the
ITOW field would yield 511, which the spec declares invalid (valid range
0..503 per LSIS V1.0 §2.4.3.1.6) — that's TC5 (error conditions),
not TC4 (boundary).  The constants `SB2_ITOW_OFFSET`, `SB2_ITOW_BITS`,
and `SB2_ITOW_SPEC_MAX` in `validate.py` carry the spec field positions.

Other field maxima reachable from this pattern:
- **WN** (SB2[0..12], 13 bits): all-ones → WN = 8191 = 13-bit raw max ✓
- **Health** (SB2[22..29], 8 bits): all-ones → Health = 255
- **CED + time-conv fields** (SB2[30..1149]): all-ones; not field-boundary
  semantics (these are quantised orbital parameters, not bounded counters)
- **SB3, SB4**: all-ones throughout

## Reproducibility

The inputs are reproducible from the documented patterns + Q21
normalisation alone — no encoder, no decoder, no orbital data.  The
helper logic in `validate.py` (`_build_canonical_input`) is stdlib-only
Python; `python validate.py check-canonical-inputs` re-derives the bytes
and confirms they match the shipped files (`7/7`), and
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

---

# Level 3 — I/Q Signals

Level 3 ships **10 baseband I/Q signal vectors** in the binary format
defined by [`interoperability.pdf`](./references/interoperability.pdf)
*Signal Export Format*.  All signals are generated at the spec baseline
rate (10.23 MHz × 12 s = one full frame).  Every signal is a
deterministic function of an already-shipped L2 frame and an
already-shipped L1 PRN code.

| File | PRN | Source frame | Spec mapping |
|:---|:---:|:---|:---|
| `signal_message_1_12s.iq.gz` |   1 | `frames/frame_message_1.bin` (all-zeros) | **TC1 verbatim** + TC2 first endpoint + TC4 all-zeros minimum |
| `signal_message_2_12s.iq.gz` |   1 | `frames/frame_message_2.bin` (all-ones) | bonus content variation (interop-doc TM2 is L2-only); useful for cross-team interleaver-error debugging |
| `signal_message_3_12s.iq.gz` |   1 | `frames/frame_message_3.bin` (alt 0xAA) | bonus content variation (TM3 is L2-only) |
| `signal_message_4_12s.iq.gz` |   1 | `frames/frame_message_4.bin` (marker surrogate) | bonus content variation (TM4 is L2-only and our L2 file is itself a marker surrogate, not real ephemeris) |
| `signal_message_5_12s.iq.gz` |   1 | `frames/frame_message_5.bin` (xorshift32) | bonus content variation (TM5 is L2-only) |
| `signal_prn2_baseline_12s.iq.gz` |   2 | `frames/frame_message_1.bin` (all-zeros) | **TC2 — secondary index S1** (`k = (PRN−1) mod 4 = 1`) |
| `signal_prn3_baseline_12s.iq.gz` |   3 | `frames/frame_message_1.bin` (all-zeros) | **TC2 — secondary index S2** (`k = 2`) |
| `signal_prn12_baseline_12s.iq.gz` |  12 | `frames/frame_message_1.bin` (all-zeros) | **TC2 — secondary index S3** + high end of legal interim PRN range |
| `signal_boundary_at_prn12_12s.iq.gz` |  12 | `frames/frame_boundary.bin` | **TC4 — TOI=99 + FID=3 maxima** in BCH SB1, modulated at PRN 12 (PRN 210 not signal-realisable) |
| `signal_boundary_max_fields_at_prn12_12s.iq.gz` |  12 | `frames/frame_boundary_max_fields.bin` | **TC4 — WN=8191 + ITOW=503 maxima** in SB2, modulated at PRN 12; uses the v0.2.2 boundary-max-fields L2 frame |

Each file is **982 080 128 bytes raw** (128-byte LSISIQ header + 122 760 000
sample pairs × 8 bytes for interleaved float32 I/Q), **~22 MB after
`gzip -9`**.  Total `signals/` footprint: ~221 MB.

## L3 spec test-case coverage matrix

| Interop-doc Test Case | Required inputs | Coverage | Files |
|:---|:---|:---|:---|
| **TC1 Baseline** | PRN 1, all-zeros, 12 s | ✅ full | `signal_message_1_12s.iq.gz` |
| **TC2 All Test Codes** | PRN 1–12, standard message, 12 s | ⚠️ secondary-index alphabet (S0/S1/S2/S3) at PRN 1/2/3/12; mid-range PRNs 4–11 not exercised | `signal_message_1`, `signal_prn2`, `signal_prn3`, `signal_prn12` |
| **TC3 Message Type Coverage** | All SF3 / SF4 message types | ❌ not exercised — L3 ships *content patterns*, not semantic *message types*; TC3 lands with the L5 parser drop |
| **TC4 Boundary Conditions** | WN=8191, ITOW=503, TOI=99, all-zeros minimum, PRN 210 | ⚠️ partial — TOI=99 + FID=3 maxima (`signal_boundary_at_prn12`), WN=8191 + ITOW=503 maxima (`signal_boundary_max_fields_at_prn12`), all-zeros minimum (`signal_message_1`); PRN=210 itself not signal-realisable | `signal_boundary_at_prn12` + `signal_boundary_max_fields_at_prn12` + `signal_message_1` |
| **TC5 Error Conditions** | Corrupted frames, invalid CRC | ❌ out of scope at L3 — these are receiver-side; lands with v0.4.0 L4 |

### AFS-Q secondary-code assignment coverage

LSIS V1.0 §4.4.2 / Annex 3 Table 2 publishes four 4-chip secondary codes
S0–S3, with the per-PRN assignment `k = (PRN−1) mod 4`:

| Secondary | PRN class | Exercised by |
|:---:|:---|:---|
| **S0** = `1110` | PRN 1, 5, 9 | `signal_message_1` (and TM2–5 variants) |
| **S1** = `0111` | PRN 2, 6, 10 | `signal_prn2_baseline` |
| **S2** = `1011` | PRN 3, 7, 11 | `signal_prn3_baseline` |
| **S3** = `1101` | PRN 4, 8, 12 | `signal_prn12_baseline` |

All four secondary codes are now exercised at L3.  A generator that is
correct on S0 (PRN 1) but mishandles any of S1/S2/S3 — e.g. uses the
wrong row of the assignment table — would have passed v0.3.0-pre and
fails the v0.3.0 release.

### TC4 WN/ITOW maxima — closed in v0.3.0 via v0.2.2

The interop doc's TC4 lists WN=8191 (max, 13-bit) and ITOW=503 (max,
9-bit) among the boundary-condition inputs.  The original
`frame_boundary.bin` does not exercise these — its SB2 carries the
alternating-start-with-0 pattern, which sets WN/ITOW to whatever that
pattern produces (neither min nor max).  v0.2.2 added a sibling L2
frame, `frame_boundary_max_fields.bin`, that fills SB2 with all-ones
plus an ITOW override to clamp the 9-bit field to 503 (the spec
maximum, since 511 is invalid).  v0.3.0 ships
`signal_boundary_max_fields_at_prn12_12s.iq.gz` to propagate that
coverage through to L3 — the SB2 maxima are now exercised at every
shipped level.  PRN=210 itself remains not signal-realisable; the
PRN-12 substitution carries everything else from the L2 boundary frame.

The PRN-12 baseline reuses `frame_message_1.bin`'s nav data (an L2 frame
is content-agnostic at PRN level — the spreading code only enters at L3),
so the signal carries the same TM1 all-zero subframes as
`signal_message_1_12s.iq.gz` but modulated against PRN 12's Gold / Weil /
secondary code instead of PRN 1's.  A contestant whose generator is
correct at PRN 1 but encodes the wrong matched-code phase for PRN 12
would pass `signal_message_1_12s.iq.gz` and fail
`signal_prn12_baseline_12s.iq.gz` — a real interop signal.

The two boundary-at-PRN-12 entries together exercise every TC4 input
the spec lists, except PRN=210 itself (not signal-realisable):

- `signal_boundary_at_prn12_12s.iq.gz` reuses `frame_boundary.bin`'s
  symbols (BCH SB1 of FID=3, TOI=99 plus alternating-start-with-0
  SB2/SB3/SB4) at PRN 12.  This propagates the **header-field maxima**
  (FID=3, TOI=99) from L2 into L3.
- `signal_boundary_max_fields_at_prn12_12s.iq.gz` reuses
  `frame_boundary_max_fields.bin`'s symbols (BCH SB1 of FID=3, TOI=99
  plus all-ones-with-ITOW-clamped-to-503 SB2/SB3/SB4, added in v0.2.2)
  at PRN 12.  This propagates the **SB2-field maxima** (WN=8191,
  ITOW=503) from L2 into L3.

When v0.4.0's PocketSDR-AFS oracle decodes either signal at PRN 12, the
recovered subframe bits must reproduce the corresponding L2 frame's
6000-symbol payload — a stricter check than the PRN-12 baseline because
BCH SB1 differs from TM1's (FID=0, TOI=0) and SB2 differs from TM1's
all-zeros.

## Why no L3 boundary frame (PRN 210)

The L2 set ships `frames/frame_boundary.bin` at PRN 210 (Test Case 4 —
boundary conditions: PRN/FID/TOI at field maxima).  L3 does **not** ship
a matching boundary signal because **the spec itself has not defined
AFS-Q content for PRN 210**.

LNIS AD1 Volume A, Annex 3, Table 11 publishes the *interim* AFS-Q
matched-code phase assignment — the per-PRN tertiary-code-phase + secondary-
index pair that combines Weil-10230 × Weil-1500 × Secondary into a single
AFS-Q stream — for **PRN 1–12 only**.  PRN 13–210 are reserved for the
future LunaNet operational deployment.  Without a published matched-code
assignment there is no "right answer" for what the AFS-Q chips at sample
0 should be; any PRN-210 baseband signal would just be an arbitrary
choice.  The interop doc's Test Case 2 mirrors this constraint
("PRN: 1-12 (Table 11)").

L1 (`codes/codes_prn210.hex`) and L2 (`frames/frame_boundary.bin`) remain
shipped at PRN=210 — those layers are content-agnostic at the PRN level
and Annex 3 covers all 210 PRNs.  When the operational AFS-Q assignment
is published for PRN 13–210, this drop will gain the matching L3 signal
in a patch release.

## Honest framing — one oracle, not two

v0.3.0 ships **one formal oracle** for Level 3: a structural + first-chip
polarity check (`validate.py check-signals`).  This is materially weaker
than L1 (Annex 3 + LANS-AFS-SIM) and L2 (structural + LANS-AFS-SIM), which
each ship two independent oracles.  Receive-side closure — decode our
L3 signal back to the original L2 frame using an independent receiver —
lands in v0.4.0 with PocketSDR-AFS bundling.  This section explains why.

| Level | Oracle 1 (normative) | Oracle 2 (independent) |
|:---|:---|:---|
| **1** Codes | LNIS AD1 Volume A Annex 3 | LANS-AFS-SIM chip dumps |
| **2** Frames | LSIS V1.0 §2.4 + Gateway 3 checklist | LANS-AFS-SIM frame dumps |
| **3** Signals | Interop doc *Signal Export Format* + LSIS V1.0 §4 + first-chip polarity | **— deferred to v0.4.0 (PocketSDR-AFS receive-side closure)** |
| 4 Decoded | (planned: PocketSDR-AFS) | — |
| 5 Parsed  | (planned: PocketSDR-AFS) | — |

## Oracle 1 — Structural + first-chip polarity

`validate.py check-signals` enforces the format from
[`interoperability.pdf`](./references/interoperability.pdf) *Signal Export
Format* and chains the L1 codes + L2 sync prefix through into L3 via a
first-chip polarity check.

| Rule | Source | What `check-signals` verifies |
|:---|:---|:---|
| Header magic = `LSISIQ\0\0` | interop doc, *Signal Export Format* | First 8 bytes of every file |
| Header version = 1 (uint32 LE) | interop doc | Bytes 8–11 |
| Sample rate = 10 230 000.0 (float64 LE) | interop doc + LSIS V1.0 §4 | Bytes 12–19 |
| Duration = 12.0 (float64 LE) | interop doc + spec frame duration | Bytes 20–27 |
| Header PRN matches expected | interop doc + Test Cases 1 + 4 | Bytes 28–31 |
| Format = `"float32"` (16-byte field, NUL-padded) | interop doc | Bytes 32–47 |
| Reserved 80 zero bytes | interop doc | Bytes 48–127 |
| Total file size = 982 080 128 | spec sample-rate × duration × 2 channels × 4 bytes | `os.stat().st_size` after gunzip |
| Sample-value range | LSIS V1.0 §4 + FAQ Q19 (clean baseband BPSK) | Every float32 ∈ {−1.0, +1.0} |
| First-chip I-polarity | FAQ Q19 + L1 codes + L2 sync prefix | I[0,10,20,30] = (1−2·sync_bit_0) × (1−2·Gold[0..3]) |
| First-chip Q-polarity | FAQ Q19 + L1 codes + LSIS V1.0 §4.4.2 secondary assignment | Q[0] = (1−2·Weil[0]) × (1−2·Tert[0]) × (1−2·Sec[0]) |
| Symbol-120 I-polarity | FAQ Q19 + L1 codes + L2 frame symbols | I[2 455 200 + 10·k] for k=0..3 against Gold[0..3] and the frame's first content-distinguishing symbol — catches LDPC / interleaver bit-ordering errors |

The polarity rules read `Gold[0..3]`, `Weil[0]` and `Tert[0]` from the
shipped `codes/codes_prn{N}.hex` (already byte-exact against Annex 3 and
LANS-AFS-SIM), the secondary code from the same file under the
LSIS V1.0 §4.4.2 assignment `k = (PRN − 1) mod 4`, and `sync_bit_0` from
the spec sync prefix `0xCC63F74536F49E04A` (FAQ Q17).  This chains the
L1 + L2 oracles through into L3 without re-implementing the BPSK +
multiplex pipeline; the validator stays stdlib-only and ~30 LoC.

```bash
python validate.py check-signals
#   Structural + first-chip polarity: 10/10
#
# OK — all 10 signals pass structural and first-chip polarity checks.
```

### What this catches

- BPSK polarity flip (logic 0 → −1 instead of +1, or vice versa).
- Wrong PRN in the I-channel Gold code (mismatch between header PRN and
  the chip values at sample 0).
- Wrong upsampling factor for the I-channel (probe samples at 0, 10, 20,
  30 verify the 10-samples-per-chip cadence at 10.23 MHz; an 8× or 12×
  upsample would land on different Gold chips and fail).
- Wrong PRN in the Q-channel matched code.
- Wrong secondary-index assignment for the Q-channel.
- **LDPC / interleaver bit-ordering errors past the sync prefix** —
  symbol 120 (first interleaver-output symbol of SB2/SB3/SB4) is probed
  against the actual frame symbol at that position, and that symbol
  differs across the 5 standard Test Messages.  An interleaver row/column
  swap or LDPC bit reversal that produces clean ±1.0 samples but flips
  the polarity at symbol 120 fails this check on at least one of the 5
  message signals.
- **Wrong matched-code phase assignment at high PRN** — the
  `signal_prn12_baseline` entry probes PRN 12's Gold / Weil / Tertiary /
  Secondary chips and the LSIS V1.0 §4.4.2 assignment `k = (PRN−1) mod 4`.
  A generator that is correct at PRN 1 but mishandles PRN 12 fails here.
- Header / size / format errors (any deviation from the LSISIQ envelope).
- Doppler, scaling, noise, pulse-shaping (caught by the strict ±1.0
  range check across every sample).

### What this does NOT catch

The polarity oracle is intentionally narrow — extending it to a full
frame walk would replicate the generator's BPSK + multiplex pipeline
in the validator, which is the trap we deliberately avoided.  Errors that
slip through the v0.3.0 oracle:

- Deep-frame errors at arbitrary symbol indices (the validator probes
  symbol 0 and symbol 120; symbols 1..119 are sync + BCH SB1, identical
  across all 5 messages, so no probe there would distinguish anything;
  symbols 121..5999 are not probed).
- I/Q channel swap further into the file (sample 0 happens to be self-
  consistent under swap for some PRNs).
- Secondary-code phase offset that only manifests after multiple secondary
  periods (the secondary cycles every 4 epochs; we only check the first
  chip of the first epoch).
- Tertiary-code phase offset that only manifests after multiple secondary
  periods (tertiary advances every 1500 epochs; we only check epoch 0).

These are exactly the failure modes a receiver-side decoder catches end-
to-end: if your decoder reads our signal and recovers the shipped L2
frame bytes, the loop closes.  v0.4.0 brings the L4 PocketSDR-AFS oracle
that performs this closure formally.

## Why no second oracle is feasible today

### LANS-AFS-SIM does not fit the L3 reference model

Confirmed against
[`third_party/LANS-AFS-SIM/afs_sim.c`](https://github.com/osqzss/LANS-AFS-SIM/blob/main/afs_sim.c)
at SHA `0578f298ba68d8508ab7d780be843faed3e2b274` (the SHA pinned by our
L1+L2 second-oracle harnesses):

- **Multi-satellite sum.**  Lines 1101–1117 walk all 12 SVs above the
  elevation mask; line 1358–1363 / 1379–1384 sums every channel before
  writing.  No flag restricts output to a single PRN.
- **Per-channel Doppler + path-loss + carrier rotation.**  Line 1289
  applies `path_loss = 5 200 000 / rho.range`; lines 1299–1300 modulate
  by the Doppler-evolved carrier (`I·cos(φ) − Q·sin(φ)`); line 1349
  advances `chan[i].carr_phase += chan[i].f_carr · delt` per sample.
- **int16 / 2-bit ADC output, no LSISIQ header.**  Lines 1387–1389 (16-bit)
  and 1373–1375 (2-bit + AWGN).  Header, dtype, and value range all
  diverge from the LSIS interop *Signal Export Format*.
- **Frame symbols come from the internal almanac.**  `afs_nav.c` builds
  SB2/SB3/SB4 from `default_almanac.txt` + the simulator's TOI rollover
  logic (line 1408 `igrx%120==110`).  No flag injects external frame
  bytes from a file.

Coercing LANS-AFS-SIM into a clean single-PRN baseband single-frame
oracle would require ~50–100 LoC of inner-loop modification across four
distinct concerns (channel sum, carrier wipe-off, gain normalisation,
frame injection).  At that point the harness is no longer "thin caller
invoking unchanged routines" — it's a fork — and the chain-of-trust
argument that anchored the L1/L2 second oracles to upstream Ebinuma code
breaks.  Rather than ship a fork-with-claims, v0.3.0 documents the gap.

### PocketSDR-AFS is a receiver, not a generator

PocketSDR-AFS (Ebinuma's AFS-specific software-defined receiver, BSD-2-
Clause) cannot byte-match our L3 signals — it consumes signals, it does
not synthesise reference ones.  But it can perform the closure that
matters for L3's actual Pass Criteria from the interop doc:

> Cross-decoding recovers original data with BER < 10⁻⁵.

That is receive-side, by construction.  v0.4.0 is the Level-4 drop where
PocketSDR-AFS is bundled with a verifier mirroring `verify_oracle.py` for
L1/L2; running it against our L3 signals proves the loop closes.  The
v0.4.0 deliverables retroactively cover L3 content correctness.

### No other open-source AFS generator exists

LSIS V1.0 was published 29 January 2025 — too new for a third independent
implementation to have appeared by May 2026.  The two known reference
implementations of the spec are LuarSpace (this package's source) and
LANS-AFS-SIM, with the latter unfit for L3 as documented above.

## Trust-by-construction

Signal content in v0.3.0 is a deterministic function of:

1. The matching `frames/frame_*.bin` payload (already verified at L2 by
   the structural + LANS-AFS-SIM oracles).
2. The Gold / Weil / tertiary / secondary chips for the assigned PRN
   (already verified at L1 by Annex 3 + LANS-AFS-SIM).
3. The LSIS V1.0 §4 BPSK + I/Q-multiplex math (FAQ Q19 polarity, 5×
   upsample for I-channel, 1:1 for Q-channel, then nearest-neighbour
   resample to the requested sample rate).

The first two are oracle-verified.  The third is single-sourced from
LuarSpace, and the first-chip polarity check above catches the
implementation errors that are most common in practice.  Deeper
correctness arrives with v0.4.0's receive-side closure.

## Using these vectors

### Validate your own implementation

If you've built an LSIS-AFS signal generator that emits the LSISIQ format
defined by `interoperability.pdf`, run:

```bash
python validate.py diff-signals /path/to/your/signals/
```

This gunzips both sides, byte-compares the 128-byte header and the full
float32 sample stream, and on mismatch reports either a header-byte
location or `(sample, channel)`.  A clean diff means your generator
produces the same bits as ours.  A mismatch is interesting — please open
an issue with the file name and the reported first mismatch.

### Validate just the format compliance

If you're not yet emitting `LSISIQ`-headered output and just want to
check whether your generator produces clean baseband BPSK at the right
rates and code phase, point `check-signals` at a single file and see
where it fails.  The polarity and range messages are designed to be
debuggable on first reading.

### Disk space

`signals/` adds ~130 MB to the repository.  If you only need L1 / L2,
sparse-checkout or shallow-clone them:

```bash
git clone --filter=blob:none https://github.com/luar-space/lsis-afs-test-vectors
cd lsis-afs-test-vectors
git sparse-checkout init --cone
git sparse-checkout set codes frames inputs references validate.py manifest.json
```

`signals/` then stays unfetched until you opt in.

## Reproducing from scratch

The generator that produced these vectors is part of the LuarSpace
reference implementation and is not yet public; the public Apache-2.0
release is scheduled for Phase 2 of the competition timeline (same
status as the L1/L2 generators).  Until then, the receive-side closure
in v0.4.0 (PocketSDR-AFS decoding our signals back to the shipped L2
frame bytes) will be the externally-verifiable evidence that the L3
content is correct end-to-end.
