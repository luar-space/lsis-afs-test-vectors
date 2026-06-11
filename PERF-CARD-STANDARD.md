# LSIS-AFS Decoder Performance Card — Standard

> **Status:** v1.0 standard, paired with `lsis-afs-test-vectors` release `v0.7.0`.
> The `perf-card` harness in this repo is the reference implementation;
> what follows specifies the contract that the harness enforces by
> construction, so that adopters who produce algo cards by a different
> path (no harness) can still produce conformant output.

## Goal

A **reproducible, cross-team-comparable benchmark of FEC decoder
performance** for the LSIS-AFS spec's two codes: BCH(52, 9) for SB1
(navigation-message header) and LDPC(2400, 1200) / LDPC(1740, 870)
for SF2 / SF3-SF4 (navigation-message payload subframes).

Each adopter's decoder produces a structured JSON document
(`sp_results.json`, "the algo card") describing its measured FER and
BER under a fixed simulated channel, at a fixed Eb/N0 grid, against
fixed message inputs. Cards from different teams are then
statistically compared via `perf-card compare` or ranked via
`perf-card leaderboard`.

**Performance comparison, not interoperability.** Cards diverging is
expected and is the point. The standard pins methodology — the
channel, the inputs, the grid, the verdict bars — so that observed
differences attribute to the decoder, not the test setup.

---

## Contents

1. [Adoption paths](#1-adoption-paths)
2. [Methodology — what the standard pins](#2-methodology--what-the-standard-pins)
3. [Codes and operating point](#3-codes-and-operating-point)
4. [Verdicts](#4-verdicts)
5. [Tiers](#5-tiers)
6. [Schema — `sp_results.json`](#6-schema--sp_resultsjson)
7. [Adapter contract — wire protocol](#7-adapter-contract--wire-protocol)
8. [Reference codeword pool](#8-reference-codeword-pool)
9. [Reproducibility guarantees](#9-reproducibility-guarantees)
10. [Conformance test](#10-conformance-test)

---

## 1. Adoption paths

There are two ways to produce a conformant algo card:

### Path A — Run your adapter through the harness (recommended)

You provide an executable adapter that speaks the [stdio protocol](#7-adapter-contract--wire-protocol).
The shipped `perf-card run` harness handles everything else
(methodology, grid, RNG, statistics, JSON emission). You get
conformance **by construction** — methodology choices are baked into
the harness, you can't accidentally drift.

```bash
perf-card run --decoder ./my_adapter --out my_algo_card.json
```

See `perf_card_templates/` for adapter skeletons in Python and C with
the protocol I/O already written — you fill in the decode functions.

### Path B — Produce JSON directly from this written standard

If you can't or don't want to use the harness (e.g., your decoder
runs on hardware that can't host a Python subprocess), you write
the algo-card JSON directly per the [schema](#6-schema--sp_resultsjson)
in this document, then validate it:

```bash
perf-card validate my_algo_card.json
```

The validator enforces the schema, fixed Eb/N0 grids, methodology
values, and verdict criteria. It does **not** verify that your FER
numbers came from a faithful channel simulation — that's on your honour.

Adopters who go this path MUST reproduce the [methodology](#2-methodology--what-the-standard-pins)
in their own simulation pipeline.

---

## 2. Methodology — what the standard pins

The card's `methodology` block MUST report:

```json
"methodology": {
  "seeds": [42, 137, 313],
  "message_ensemble": "uniform_random",
  "ci_method": "wilson_95"
}
```

| Field | Value | Notes |
|---|---|---|
| `seeds` | `[42, 137, 313]` | Three fixed integer seeds for the RNG. Every grid point is measured across all three; aggregate counts feed CI computation. |
| `message_ensemble` | `"uniform_random"` | Random info bits sampled uniformly. **All-zero codeword shortcut is forbidden** — under finite max-iters it over-reports relative to operational performance. |
| `ci_method` | `"wilson_95"` | Wilson score 95% confidence interval on the binomial frame-error count. Half-widths reported per grid point. |

The card's `channel` block MUST report:

```json
"channel": {
  "model": "BPSK-AWGN",
  "sigma_formula": "1 / sqrt(2 * R * 10^(Eb_N0_db/10))",
  "llr_formula": "2 * y / sigma^2",
  "symbol_mapping": "bit 0 -> +1, bit 1 -> -1"
}
```

### RNG implementation

The harness uses `numpy.random.default_rng(SeedSequence(entropy=seed, spawn_key=(code_id, int(eb_n0_db * 1000))))`. Path-B adopters MAY use any RNG, but
each card's measurements at each grid point should reflect ≥ the same
total number of independent Monte Carlo frames as the [methodology
defaults](#3-codes-and-operating-point) — wider CIs make comparison
coarser but not unfair.

### Quantisation of wire LLRs

LLRs presented to the decoder are float32 by default — full precision.
The standard treats wire-LLR quantisation as an **extended-tier
informational sweep** (not a comparison axis), justified by the current
interop pool being all-software. If a hardware-target implementation
joins the pool, this can be promoted to tier-core without schema
changes (see [Tiers](#5-tiers)).

---

## 3. Codes and operating point

Common operating-point anchor:

```json
"operating_point": {
  "system_es_n0_db": 0.0,
  "notes": "LSIS spec SNR ≥ 0 dB. Each code's Eb/N0 derives via its rate."
}
```

Per-code Eb/N0 grids and default frame counts:

| code_id | Name | k | n | rate R | Eb/N0 grid (dB) | Default frames/seed |
|---|---|---|---|---|---|---|
| 0 | **SB1** (BCH) | 9 | 52 | 9/52 ≈ 0.173 | 2.0, 3.0, 4.0, 5.0, 6.0, **7.6** | 3000 (10000 at 7.6 dB op point) |
| 1 | **SF2** (LDPC) | 1200 | 2400 | 1/2 | 0.2, 0.4, 0.6, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.6, 2.0, 3.0 | 5000 |
| 2 | **SF3** (LDPC, applies to SF3+SF4) | 870 | 1740 | 1/2 | same as SF2 | 5000 |

Operating-point Eb/N0 derives from Es/N0 via the code rate:
`Es/N0 [dB] = Eb/N0 [dB] + 10·log10(R)`, so
`Eb/N0 [dB] = Es/N0 [dB] + 10·log10(1/R)`. With Es/N0 = 0 dB:

- SB1 (R = 9/52): Eb/N0 = 10·log10(52/9) ≈ **7.6 dB**
- LDPC SF2/SF3 (R = 1/2): Eb/N0 = 10·log10(2) ≈ **3.0 dB**

These are the boundary of the spec operating region for each code.
The verdict's `at_eb_n0_db` reports the **lowest in-band grid point
where the bar is met** — i.e., the lowest grid point with Eb/N0 ≥
the per-code operating-point Eb/N0 above where BER (LDPC) or FER
(SB1) drops below the bar. For monotone waterfalls (LDPC, BCH soft-ML)
this is also the lowest Eb/N0 anywhere in the operating region where
the bar holds.

---

## 4. Verdicts

Each verdict block carries **two distinct signals** — spec compliance
(binary) and cross-team comparison (continuous).

### 4.1 Spec compliance

| Code | Criterion | `at_eb_n0_db` (per-code Eb/N0 for Es/N0 = 0 dB) |
|---|---|---|
| LDPC SF2 | `BER < 1e-5 at Es/N0 ≥ 0 dB (CI upper)` | 3.0 |
| LDPC SF3/SF4 | `BER < 1e-5 at Es/N0 ≥ 0 dB (CI upper)` | 3.0 |
| SB1 (BCH) | `FER < 0.01 at Es/N0 ≥ 0 dB (CI upper)` | 7.6 |

LDPC verdict bar is sourced from the LSIS spec's BER goal for nav-data
recovery. SB1 verdict bar is sourced from the interop PDF's "Frame
Detection Rate > 99%" requirement (1 − 0.99 = 0.01).

The PASS test compares the **one-sided Wilson 95% upper bound** on
BER (LDPC) or FER (SB1) to the bar. At zero observed errors this is
`z² / (n + z²)` — the rule-of-three-style bound, *not* the symmetric
half-width (which is half that, and would let a lucky zero claim PASS
on too few frames). The verdict block carries both the point estimate
(`ber` / `fer`) and the CI upper (`ci_ber_upper` / `ci_fer_upper`) so
adopters can sanity-check the verdict against their own assumptions.

The harness validator requires **both** `SF2` and `SF3_SF4` LDPC
subframe blocks for a `core` tier card (they're different codes, not
flavours of the same one). A submission that benchmarks only one
subframe is implementing a strictly smaller scope and is not
apples-to-apples comparable on the per-code leaderboards.

### 4.2 Cross-team comparison

The spec-compliance verdict has a binary outcome; once two cards both
PASS, it tells you nothing more. Two additional fields on each verdict
block surface the **cliff position** — the load-bearing signal for
ranking implementations:

| Field | Meaning |
|---|---|
| `first_bar_crossing_eb_n0_db` | Lowest Eb/N0 *on the grid* where the **point estimate** crosses the bar, ignoring whether that Eb/N0 sits in the spec's operating region. Point estimate (not CI upper) is intentional here — using the CI upper would null this out for short-frame submissions and lose discrimination. |
| `margin_below_spec_db` | Mathematically derived: `at_eb_n0_db − first_bar_crossing_eb_n0_db`. Since `at_eb_n0_db` is constant per code (3.0 dB for LDPC, 7.6 dB for SB1), this field is a literal restatement of the cliff in a different reference frame — not a second independent metric. |

`perf-card leaderboard` ranks cards by `first_bar_crossing_eb_n0_db`
ascending (lower = better implementation). The PASS/FAIL outcome stays
as the conformance gate; the cliff position is the discriminator.

**Grid resolution caveat.** `first_bar_crossing_eb_n0_db` is reported
to grid resolution — not interpolated. The LDPC grid has 0.1–0.2 dB
spacing around the cliff (1.0–1.6 dB), the BCH grid has 1.0 dB spacing
throughout. Two implementations whose true cliffs differ by less than
the local grid step typically report the same value (tied at the
grid's resolution); a 0.1 dB reported difference on the LDPC grid is
real; a 1.0 dB reported difference on the BCH grid may overstate the
true gap by up to a grid step. Tied cards on the leaderboard are
indistinguishable at the grid's resolution — no extra CI-overlap test
is applied to a derived discrete quantity.

The leaderboard also surfaces the **frame count at the cliff row**
(`n@cliff` column) — readers should treat low-frame submissions
(e.g., < 1000 frames at the cliff) as nominal ranking signals
backed by an underpowered sample. A future tier extension may pin a
minimum frame count; for now the disclosure is per-row.

For decoders that don't meet the bar anywhere on the grid, both
comparison fields are `null`. The compliance verdict still reports
its best in-band point with `pass: false`.

---

## 5. Tiers

Cards declare a `tier` field at top level. Each tier is a superset of
the previous.

| Tier | Mandatory blocks | Use case |
|---|---|---|
| `core` | identity, anchor, operating_point, channel, methodology, ldpc.subframes.{SF2,SF3_SF4}, ldpc.subframes.*.verdict, sb1, sb1.verdict | The minimum comparable contract. `perf-card compare` operates on core fields only. |
| `extended` | core + `ldpc_extended.convergence_cdf` | Adds an LDPC max_iters sweep at the cliff Eb/N0 — characterises how many iterations the decoder actually needs. |
| `full` | extended + `ldpc_full.{error_floor, error_patterns, saturation_stress}` | Adds deep-floor probing, per-frame bit-error histograms, and extreme-SNR sanity checks. Maintainer / publication grade. |

`perf-card compare` reads only `core` fields regardless of tier — so a
core-only adopter is directly comparable with a full-tier reference.

---

## 6. Schema — `sp_results.json`

The complete schema, in JSONC for explanatory comments. Adopters
implement the production version without comments.

```jsonc
{
  // ─── Identity & provenance ────────────────────────────────────────
  "schema_version": "1.0.0",
  "tier": "core",                  // "core" | "extended" | "full"
  "produced_by": "<harness or tool identity>",
  "produced_at": "<RFC3339 UTC timestamp>",
  "reference_anchor": {
    "repo":   "luar-space/lsis-afs-test-vectors",
    "tag":    "v0.7.0",
    "commit": "<sha optional>"
  },
  "elapsed_seconds": 617.2,
  "threads": 9,                    // optional, informational

  // ─── Operating point ──────────────────────────────────────────────
  "operating_point": {
    "system_es_n0_db": 0.0,
    "notes": "LSIS spec SNR >= 0 dB. Each code's Eb/N0 derives via its rate."
  },

  // ─── Channel (explicit so adopters can verify config) ─────────────
  "channel": {
    "model":          "BPSK-AWGN",
    "sigma_formula":  "1 / sqrt(2 * R * 10^(Eb_N0_db/10))",
    "llr_formula":    "2 * y / sigma^2",
    "symbol_mapping": "bit 0 -> +1, bit 1 -> -1"
  },

  // ─── Methodology ──────────────────────────────────────────────────
  "methodology": {
    "seeds":            [42, 137, 313],
    "message_ensemble": "uniform_random",
    "ci_method":        "wilson_95"
  },

  // ─── LDPC metric ──────────────────────────────────────────────────
  "ldpc": {
    "decoder":           "<adapter-declared decoder identity>",
    "algorithm":         "<adapter-declared algorithm description>",
    "max_iterations":    50,
    "early_termination": "<adapter-declared strategy>",
    "subframes": {
      "SF2": {
        "code": {
          "k": 1200, "n": 2400, "rate": "1/2",
          "spec_ref": "LSIS V1.0 §2.4.3.1.2"
        },
        "frames_per_seed": 5000,
        "eb_n0_grid_db": [0.2, 0.4, 0.6, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.6, 2.0, 3.0],
        "waterfall": [
          {"eb_n0_db": 0.2, "fer": 0.9607, "ber": 0.479,
           "ci_fer": 0.0031, "ci_ber": 0.00023,
           "frames": 15000, "frame_errors": 14411, "bit_errors": 7194000,
           "total_bits": 18000000, "not_converged": 14411}
          // … one entry per grid point
        ],
        "verdict": {
          // Spec compliance — Wilson 95% upper bound on BER below the bar.
          "criterion":    "BER < 1e-05 at Es/N0 >= 0 dB (CI upper)",
          "at_eb_n0_db":  3.0,           // = Es/N0 0 dB at R=1/2
          "ber":          0.0,           // point estimate
          "ci_ber":       2.13e-7,       // symmetric Wilson half-width
          "ci_ber_upper": 4.27e-7,       // one-sided Wilson 95% upper — verdict tests this
          "pass":         true,
          // Cross-team comparison metric — where the cliff actually is.
          // Multiple decoders that all PASS at the spec boundary will
          // still differ here by ~dB; this is what `leaderboard` ranks.
          "first_bar_crossing_eb_n0_db": 2.0,   // lowest Eb/N0 (anywhere) where point-est BER < 1e-5
          "margin_below_spec_db":         1.0   // derived: at_eb_n0_db − first_bar_crossing_eb_n0_db
        }
      },
      "SF3_SF4": {                     // same code as SF3 alone; reported once
        "code": {
          "k": 870, "n": 1740, "rate": "1/2",
          "spec_ref": "LSIS V1.0 §2.4.3.1.2",
          "applies_to": ["SF3", "SF4"]
        },
        "frames_per_seed": 5000,
        "eb_n0_grid_db": [/* same density as SF2 */],
        "waterfall":     [/* … */],
        "verdict":       {/* same shape */}
      }
    }
  },

  // ─── SB1/BCH metric ──────────────────────────────────────────────
  "sb1": {
    "code": {
      "k": 9, "n": 52, "rate": "9/52",
      "codebook_size": 400,
      "structure": "4 FIDs * 100 TOIs",
      "spec_ref": "LSIS V1.0 Tables 13/14 + §2.4.3.1.1"
    },
    "decoder": {
      "name":      "<adapter-declared decoder name>",
      "class":     "soft_ML",          // hard_ML | soft_ML | BDD | other
      "algorithm": "<adapter-declared algorithm description>"
    },
    "frame_error_definition":
      "decoded FID != transmitted OR decoded TOI != transmitted",
    "frames_per_seed_default": 3000,   // bumps at the operating point
    "eb_n0_grid_db": [2.0, 3.0, 4.0, 5.0, 6.0, 7.6],
    "waterfall": [
      {"eb_n0_db": 2.0, "fer": 0.023, "ci_fer": 0.0024,
       "frame_errors": 352, "frames": 15000}
      // … one entry per grid point
    ],
    "verdict": {
      "criterion":     "FER < 0.01 at Es/N0 >= 0 dB (CI upper)",
      "at_eb_n0_db":   7.6,              // = Es/N0 0 dB at R=9/52
      "fer":           0.0,              // point estimate
      "ci_fer":        4.27e-5,          // symmetric Wilson half-width at p=0
      "ci_fer_upper":  8.54e-5,          // one-sided Wilson 95% upper — verdict tests this
      "pass":          true,
      "first_bar_crossing_eb_n0_db": 3.0,   // cliff position (comparison metric)
      "margin_below_spec_db":         4.6   // derived: 7.6 − 3.0
    }
  },

  // ─── Extended tier (omitted if tier == "core") ───────────────────
  "ldpc_extended": {
    "convergence_cdf": [
      // max_iters sweep at Eb/N0 = 1.5 dB; pinned set:
      // {1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50}
    ]
  },

  // ─── Full tier (omitted if tier < "full") ────────────────────────
  "ldpc_full": {
    "error_floor":       { /* keyed by floor_<sf>_<eb_n0>, 60k frames/point */ },
    "error_patterns":    [ /* per-frame bit-error histograms at 1.0/1.2/1.4 dB */ ],
    "saturation_stress": { /* low_snr + high_snr extreme-SNR sanity */ }
  }
}
```

### Schema invariants

The validator enforces:

1. All top-level required fields present.
2. `tier ∈ {core, extended, full}`.
3. `methodology` fields exactly match the pinned values (seeds = [42,137,313], message_ensemble = "uniform_random", ci_method = "wilson_95").
4. `channel.model == "BPSK-AWGN"`; `channel.sigma_formula`, `channel.llr_formula` present.
5. `operating_point.system_es_n0_db == 0.0`.
6. LDPC `eb_n0_grid_db` matches the pinned 12-point LDPC grid exactly.
7. SB1 `eb_n0_grid_db` matches the pinned 6-point BCH grid exactly.
8. SB1 `decoder.class ∈ {hard_ML, soft_ML, BDD, other}`.
9. Verdict `criterion` text matches the spec-grounded string exactly.
10. Each waterfall row has the expected fields per code.

---

## 7. Adapter contract — wire protocol

Adapters speak a binary stdio protocol with a one-time JSON handshake.
Full details (byte layouts, examples in Python and C, gotchas) live in
[`perf_card_templates/README.md`](./perf_card_templates/README.md).

Summary:

- **Session lifecycle:** harness spawns adapter; one-time handshake
  (length-prefixed JSON); per-frame binary request/response loop until
  the harness closes stdin (EOF = exit cleanly).
- **Handshake:** adapter declares its identity, supported codes, and
  per-code decoder metadata (algorithm, decoder_class, early-termination
  strategy). This metadata lands in the algo card's identity block.
- **Per-frame request:** 11-byte little-endian header
  `<u8 code_id><u16 max_iters><f32 sigma_sq><u32 n_bits>`
  followed by `n_bits × float32` LLRs.
- **Per-frame response:** 7-byte little-endian header
  `<u8 status><u16 iters_used><u32 n_info_bits>`
  followed by `n_info_bits` bytes of decoded info bits ∈ {0, 1}.
- **SB1 info-bit packing:** 9 bits = FID (2 bits, MSB-first) | TOI
  (7 bits, MSB-first).

---

## 8. Reference codeword pool

The harness samples `(info_bits, codeword)` pairs from a shipped binary
pool (`perf_card_reference_codewords.npz`):

| Code | Pool size | Generated at |
|---|---|---|
| SB1 / BCH | 100 pairs | Generated once via lunalink encoder, deterministic seed `0xC0DE_BEEF` |
| LDPC SF2 | 1000 pairs | Same |
| LDPC SF3 | 1000 pairs | Same |

The pool is bit-packed (`np.packbits`) and compressed; total ~780 KB.

The harness picks pool indices via the seeded RNG, so two harnesses
running the same seed sequence see the same `(info_bits, codeword)`
pairs at the same positions in the frame stream — full reproducibility
across runs and across worker counts.

Adopters using Path B (no harness) MUST encode their own messages
correctly per the LSIS spec, OR load the shipped pool and use it the
same way the harness does.

---

## 9. Reproducibility guarantees

The reference harness produces **bit-exact identical** algo cards
across:

- repeat runs on the same machine
- runs with `--workers 1` vs `--workers N`
- runs on different machines (same NumPy + lunalink versions)

This is verified empirically: 30/30 grid points byte-identical across
two independent production runs (5000 frames/seed × 3 seeds × 12 LDPC
grid points × 2 codes + 6 BCH grid points).

The deterministic chain is:
1. SeedSequence purely from `(entropy=seed, spawn_key=(code_id, int(eb_n0_db*1000)))`
2. Pool index = `rng.integers(0, pool_size)`
3. AWGN noise samples = `rng.normal(0, sigma, n_bits)`
4. LLRs = `(2/sigma²) · (bpsk_symbol + noise)`
5. Adapter decode is deterministic given LLRs (for any sane decoder)

Adopters in Path A get this for free. Adopters in Path B should
specify their RNG implementation in `produced_by` so peers can
reproduce.

---

## 10. Conformance test

A card is conformant iff `perf-card validate <card.json>` exits 0.

A card is **CI-comparable to the reference** iff
`perf-card compare <card.json> perf_card_reference_card.json` reports
no statistically-distinct grid points beyond expected sampling noise.
(Reference cards from different implementations are SUPPOSED to
diverge; this test checks comparability against the shipped exemplar,
not equality.)

A card is **regression-passing** iff `perf-card self-test --decoder
<your-adapter>` reports PASS — fresh card generated at small frame
counts is CI-overlap-tied with the shipped reference at every grid
point.

---

## Versioning and amendments

This document specifies **v1.0** of the standard, paired with
`lsis-afs-test-vectors` release `v0.7.0`. Backward-incompatible
changes (wire protocol, schema fields, methodology values) require a
major bump (v2.0). Additive changes (new tier blocks, new optional
schema fields) are minor bumps (v1.1).

The `protocol_version` field in the handshake locks the wire protocol.
The `schema_version` field in the algo card locks the JSON shape.
Both are independently versioned and currently both at `1.0` / `1.0.0`.

---

## See also

- [`shaping/decoder-performance-card.md`](./shaping/decoder-performance-card.md) — design history, why each decision was made
- [`perf_card_templates/`](./perf_card_templates/) — adapter skeletons + protocol details
- [`perf_card.py`](./perf_card.py) — reference harness implementation
- [`perf_card_reference_card.json`](./perf_card_reference_card.json) — shipped lunalink reference exemplar
