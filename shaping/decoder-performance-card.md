---
shaping: true
---

# LSIS-AFS Decoder Performance Card — Shaping

> Decoupled from the L5 / v0.5.0 release. Lives on branch `perf-card-standard`
> (off `main`), **not** part of v0.5.0. The buildable spec
> (`PERF-CARD-STANDARD.md` + schema + stdio contract) is deferred — for later.
> History below (negative-vectors → channel/BER → performance card) kept as
> audit trail.

## Frame

### Source

> the negative vectors angle is interesting, let's dig

(in response to review finding #3: the repo ships TC1/TC4 vectors but **zero** for
PDF Test Case 5 — the one substantive coverage gap for a cross-check repo.)

> [reframe] A channel / BER extension to the vectors repo. Not a new kind of
> artifact — the same repo, the same verifiable-and-oracle-backed format,
> extended along the one axis it doesn't cover. Noisy signals at a sweep of
> C/N₀, the BER/FER curve, and — critically — the existing public
> PocketSDR-AFS cross-decode oracle run over the impaired signals showing
> recovery within the stated bound. That last part is what makes this
> bulletproof: performance claims without source normally invite "trust us,"
> but a claim that an independent decoder recovers your noisy signal within a
> BER bound is oracle-backed, not trust-me — exactly the property that made
> the correctness drop credible. The substance largely exists internally (the
> ironclad-BER infrastructure); the drip is packaging it into the public
> verifiable form, not building it. Highest-leverage next move: closes the
> biggest scored gap, needs no source, deepens the interop story (teams can
> round-robin under noise, not just clean).

> [reframe 2] We have already characterised our receiver and it's strong —
> `../lunalink/docs/signal/ldpc_performance.rst` (waterfall, Shannon gap,
> alpha tuning, quantization resilience, fixed seeds, BER < 1e-5 @ SNR>0 dB
> under both SNR interpretations). The idea: show WE can recover this — can
> other teams do the same? Outsource the way to generate that report as a
> standardised format so others are forced to compare their performance with
> ours. Good idea? feasible?

(Existing internal substance: `lunalink/docs/signal/ldpc_performance.rst` is a
**decoder-block** characterisation — LLR-in, BPSK-AWGN, σ=1/√(2·R·Eb/N0),
mt19937 fixed seeds, random uniform messages. Note: this is the *FEC code*
axis (Eb/N0, waterfall knee ≈1.4 dB), **not** the end-to-end *signal* axis the
L4 spike measured (C/N₀, acquisition cliff ≈37 dB-Hz). The standardised
cross-team comparison must live at the decoder block; the noisy-signal +
PocketSDR run is a separate, acquisition-gated, system-level oracle.)

> [adoption] I want to make it easy for other teams to adopt this.

> [scope] But we are stressing / measuring the LDPC mostly? … why not BCH too?

### Problem

`references/interoperability.pdf` **Test Case 5: Error Conditions** —
*Purpose: verify error handling and CRC validation. Inputs: corrupted frames
(bit flips), invalid CRC, out-of-range field values. Expected: CRC validation
detects errors, decoders handle errors gracefully, error rates match
specification.*

The repo ships only the **happy path**. A third-party implementation can verify
it decodes our good frames identically (L4 `diff-decode`), but **cannot
cross-check its negative path** — "does my decoder reject a corrupted frame /
flag a bad CRC / handle an out-of-range field the same way the reference
does?" There is no shipped corrupted vector and no declared expected outcome
to compare against.

### Outcome

Ship a `negative/` vector set + an oracle so any implementation can verify its
decoder **detects and handles** corruption and out-of-range fields consistently
with the reference — **without the repo reimplementing CRC-24Q / LDPC / BCH**
(independence stays with the external decoder, exactly as at L4).

---

## 🟡 STANDARD SCOPE — PINNED (authoritative; supersedes the exploratory A–E / negative-vector framing below, which is kept as audit trail)

**This artifact is: the LSIS-AFS *Decoder Performance Card* standard** — a
tiered, reproducible benchmark of FEC *decoding* performance, where the repo
ships the harness and other teams adopt by supplying only a decode adapter.
(Renamed `negative-vectors-tc5.md` → `decoder-performance-card.md`;
decoupled onto branch `perf-card-standard` off `main`. Not in v0.5.0.)

### What it measures — two distinct metrics, reported separately

| Metric | Code(s) | Captures | Why its own section |
|---|---|---|---|
| **LDPC waterfall** | SF2, SF3/SF4 (spec-pinned H, rate 1/2) | BER/FER vs Eb/N0 + `BER<1e-5 @ Es/N0≥0 dB` verdict | high implementation spread (~1 dB: algorithm, iterations, LLR scaling, fixed-point, puncturing/filler) — the rich differentiator |
| 🟡 **SB1/BCH frame-detection** | SB1 BCH(51,8) | FID/TOI recovery rate vs Eb/N0 + `FER<0.01 @ Es/N0≥0 dB` verdict (= interop PDF "Frame Detection > 99%") | low spread but operationally *gating* (fail SB1 → whole frame lost); a **different** PDF metric. Adopters report whichever decoder family they built; a `decoder_class` tag (`hard_ML`/`soft_ML`/`BDD`/`other`) records the family for context — the standard does **not** require shipping both |

CRC-24 is **instrumentation** (frame-failure detector), not a measured
subject. Codes are **spec-fixed** (LSIS Annex 1 / §2.4.3.1.2) — the fairness
anchor: identical code for all ⇒ differences attributable to the decoder.

### Level mapping

L4 (Decoding) **performance** axis — repo already does L4 *correctness*
(PocketSDR bit-exact, noise-free); this adds L4 *robustness under noise*. It
closes the PDF's standalone **Interoperability Metrics** block in full: BER
Performance (LDPC) + Frame Detection Rate (SB1/BCH) + Decode Success.
**Explicitly NOT** L3 end-to-end signal BER (acquisition-gated — spike's
honest-scoping line; decoder-block Eb/N0, not signal C/N₀), and not L1/L2/L5.
Consumes L2-defined codes; tests the decode path.

### Adoption model (key ergonomics decision)

Repo ships a **deterministic harness** (pinned seeds, fixed Eb/N0 grid,
`message_ensemble=uniform_random`, AWGN σ²=1/(2·R·Eb/N0), Wilson CI, JSON
emit). Teams supply only their decoder over a **language-neutral stdio
contract** with `code ∈ {SB1, SF2, SF3}`: soft symbols/LLRs + σ² in → info
bits out. Methodology-conformance becomes **by construction** (it's our
harness), which also removes the fairness/comparability risk. Two paths, same
`algo_card.json`:
- **Easy (default):** `perf-card run --decoder ./adapter --tier core`; shipped
  adapter stubs (Py/C/Rust) + `--self-test` vs a shipped reference vector set.
- **Independent (fallback):** produce JSON from the written standard;
  `perf-card validate` / `diff`.

### Tiering

`core` (MUST — the comparable contract: identity/config, channel, methodology
incl. **`message_ensemble: "uniform_random"` mandatory at every tier**,
fixed Eb/N0 grid, LDPC waterfall, SB1 frame-detection, spec-compliance
verdict, reference-vector anchor) · `extended` (SHOULD — convergence,
quantization) · `full` (MAY — α sweep, error floor, Shannon gap,
forensics; lunalink ships this as the reference exemplar). `perf-card diff`
compares **core-only** regardless of tier.

🟡 *Note: the all-zero-vs-random symmetry test is **not** part of any
tier.* In a finite-iteration sum-product decoder the all-zero codeword
converges in fewer iterations than a random codeword (the initial LLRs
are all positive-biased), so under realistic max-iteration budgets the
two ensembles produce different FERs — observed in lunalink's
characterisation as 0/15000 (all-zero) vs 132/15000 (random) at 1.2 dB.
This is honest iteration-budget asymmetry, not a decoder defect, and
mandating `uniform_random` at every tier sidesteps it without losing
operational realism (real nav data is random).

### Source / engine

🟡 **LDPC side** — lunalink `task ldpc-algo-card` (`scripts/ldpc_characterise.cpp`)
→ `sp_results.json`, rendered by `plot_algo_card.py`; `ldpc_algo_card.rst`
is the full-tier exemplar.

🟡 **SB1/BCH side** — characterized today in
`cpp/tests/performance/test_bch_ber.cpp` (Catch2 unit test, runs under
`task ber`). Same methodology as the LDPC side (BPSK-AWGN,
σ²=1/(2·R·Eb/N0), seeds {42,137,313}, Wilson CI), but emits stdout text,
not JSON, and grid is 6 anchors {2.0, 3.0, 4.0, 5.0, 6.0, 7.6} dB (R=9/52).
**Buildable-spec gap:** lift the simulation core into a standalone
`scripts/bch_characterise.cpp` modelled on `ldpc_characterise.cpp` so both
codes emit into one unified `sp_results.json` (top-level `ldpc:` +
`sb1:` blocks). Catch2 BCH tests can remain as a CI gate, asserting
against the JSON.

🟡 **Soft-vs-hard is a lunalink-internal exploration finding, not part of
the schema.** Lunalink characterizes both decoder families to illustrate
the gain (~1.6 dB in soft's favor); the standard's tier-core has each
adopter report a single decoder (the one they built), tagged with
`decoder_class ∈ {hard_ML, soft_ML, BDD, other}`. Lunalink ships **two
algo cards** (one per family) — two instances of the schema, not an
asymmetric extension of it.

**No PocketSDR** — a statistical claim's oracle is a fully specified
reproducible methodology + open tooling + reference data, not a second
decoder.

### 🟡 Open knob — RESOLVED

The core Eb/N0 grids — **two of them**, because R differs between the
codes; both anchor to a common operating point `Es/N0 = 0 dB`.

🟡 **LDPC (R=1/2)** — lunalink's SF2/SF3 hi-res sweep as-is:
{0.2, 0.4, 0.6, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.6, 2.0, 3.0} dB (**12 pts**,
not the "0.5→3.0 dB, 9 pts" the earlier draft claimed). Dense at the
waterfall knee + the spec point at Eb/N0 = 0 dB = Es/N0 0 dB. Sub-1e-5
probing is extended/full tier.

🟡 **SB1/BCH (R=9/52)** — lunalink's BCH waterfall sweep as-is:
{2.0, 3.0, 4.0, 5.0, 6.0, 7.6} dB (**6 anchors**: noise floor, waterfall,
operating point 7.6 dB ≈ Es/N0 = 0 dB at R=9/52).

🟡 **Verdict bars (spec-grounded, code-family-neutral):**
- LDPC: `BER < 1e-5 at Es/N0 ≥ 0 dB` (LSIS spec)
- SB1/BCH: `FER < 0.01 at Es/N0 ≥ 0 dB` (interop PDF "Frame Detection > 99%")

Both bars are clearable by all reasonable decoder families — so a hard-ML
SB1 implementation can pass even though it sits ~1.6 dB worse than
soft-ML.

### 🟡 Schema strawman (`sp_results.json`)

The unified algo card. Each adopter (or lunalink, as the reference
exemplar) produces one instance. `core` blocks are mandatory; `extended`
/ `full` blocks are present iff `tier` ≥ the corresponding level. `diff`
compares only the `core` fields, so a `core` adopter is comparable with a
`full` one.

```jsonc
{
  // ─── Identity & provenance ────────────────────────────────────────
  "schema_version": "1.0.0",
  "tier": "full",                          // "core" | "extended" | "full"
  "produced_by": "lunalink algo-card@<commit>",
  "produced_at": "2026-05-25T12:00:00Z",
  "reference_anchor": {
    "repo":   "luar-space/lsis-afs-test-vectors",
    "tag":    "v0.6.0",
    "commit": "<sha>"
  },
  "elapsed_seconds": 1234.5,
  "threads": 8,

  // ─── Operating point (anchors both codes) ─────────────────────────
  "operating_point": {
    "system_es_n0_db": 0.0,
    "notes": "LSIS spec SNR ≥ 0 dB. Each code's Eb/N0 derives via its rate."
  },

  // ─── Channel (lifted from C++ so adopters can verify config) ──────
  "channel": {
    "model":          "BPSK-AWGN",
    "sigma_formula":  "1 / sqrt(2 * R * 10^(Eb_N0_db/10))",
    "llr_formula":    "2 * y / sigma^2",
    "symbol_mapping": "bit 0 → +1, bit 1 → −1"
  },

  // ─── Methodology (lifted from C++) ────────────────────────────────
  "methodology": {
    "seeds":            [42, 137, 313],
    "message_ensemble": "uniform_random",
    "ci_method":        "wilson_95"
  },

  // ─── LDPC metric (one decoder, per-subframe) ──────────────────────
  "ldpc": {
    "decoder":           "sum-product",
    "algorithm":         "Layered Sum-Product BP (phi-transform)",
    "arithmetic":        "float64",
    "max_iterations":    50,
    "early_termination": "syndrome check every iteration",
    "subframes": {
      "SF2": {
        "code": {"k": 1200, "n": 2400, "rate": "1/2",
                 "spec_ref": "LSIS V1.0 §2.4.3.1.2"},
        "frames_per_seed": 5000,
        "eb_n0_grid_db": [0.2, 0.4, 0.6, 0.8, 1.0, 1.1, 1.2, 1.3, 1.4, 1.6, 2.0, 3.0],
        "waterfall": [
          {"eb_n0_db": 0.2, "fer": 0.99, "ber": 0.42, "ci_fer": 0.0015,
           "frames": 15000, "frame_errors": 14850, "bit_errors": 7560000,
           "total_bits": 18000000, "not_converged": 14850}
          // … one entry per grid point
        ],
        "verdict": {
          "criterion":    "BER < 1e-5 at Es/N0 ≥ 0 dB",
          "at_eb_n0_db":  0.0,           // = Es/N0 0 dB at R=1/2
          "ber":          0.0,
          "pass":         true
        }
      },
      "SF3_SF4": {                       // same code; reported once
        "code": {"k": 870, "n": 1740, "rate": "1/2",
                 "spec_ref": "LSIS V1.0 §2.4.3.1.2",
                 "applies_to": ["SF3", "SF4"]},
        "frames_per_seed": 5000,
        "eb_n0_grid_db": [/* same density as SF2 */],
        "waterfall":     [/* … */],
        "verdict":       {/* same shape as SF2 */}
      }
    }
  },

  // ─── SB1/BCH metric (one decoder per card; family tagged) ─────────
  "sb1": {
    "code": {
      "k": 9, "n": 52, "rate": "9/52",
      "codebook_size": 400, "structure": "4 FIDs × 100 TOIs",
      "spec_ref": "LSIS V1.0 Tables 13/14 + §2.4.3.1.1"
    },
    "decoder": {
      "name":      "bch_decode_soft",
      "class":     "soft_ML",            // hard_ML | soft_ML | BDD | other
      "algorithm": "exhaustive ML over inner-product LLR"
    },
    "frame_error_definition":
      "decoded FID ≠ transmitted OR decoded TOI ≠ transmitted",
    "frames_per_seed": 3000,
    "eb_n0_grid_db": [2.0, 3.0, 4.0, 5.0, 6.0, 7.6],
    "waterfall": [
      {"eb_n0_db": 2.0, "fer": 0.18, "ci_fer": 0.008,
       "frame_errors": 1620, "frames": 9000}
      // … one entry per grid point
    ],
    "verdict": {
      "criterion":   "FER < 0.01 at Es/N0 ≥ 0 dB",  // ← interop PDF "Frame Detection > 99%"
      "at_eb_n0_db": 7.6,                           // = Es/N0 0 dB at R=9/52
      "fer":         0.0,
      "pass":        true
    }
  },

  // ─── Extended tier (omitted if tier == "core") ────────────────────
  "ldpc_extended": {
    "convergence_cdf": [/* … */],
    "quantisation":    [/* … */]
    // (symmetry block intentionally removed — see tiering note above)
  },

  // ─── Full tier (omitted if tier < "full") ─────────────────────────
  "ldpc_full": {
    "error_floor":       {/* … */},
    "error_patterns":    [/* … */],
    "saturation_stress": {/* … */}
  }
}
```

#### Design choices the strawman commits to

| Choice | Picked | Alternative considered |
|---|---|---|
| One file or two | **Unified** `sp_results.json` with `ldpc:` + `sb1:` blocks | Separate files combined upstream |
| Verdict anchor | **`operating_point.system_es_n0_db = 0.0`**; each code names its own Eb/N0 for that Es/N0 | Same Eb/N0 number for both codes (different physical SNR — apples-to-oranges) |
| BCH shape | **One decoder per card**, `decoder_class` tag; lunalink ships two cards to show soft-vs-hard | Per-point hard/soft columns (forces every adopter to ship both) |
| SF3 / SF4 (identical code) | **One block `SF3_SF4`** with `applies_to: ["SF3","SF4"]` | Two duplicate blocks |
| Tier blocks | **Top-level `tier` field + `ldpc_extended` / `ldpc_full` keys at top level**, present iff tier ≥ that level | Nest extended/full inside each code block |
| LDPC ↔ SB1 asymmetry | **Visible** — LDPC has `subframes:{}`, SB1 doesn't; SB1 has `decoder.class`, LDPC doesn't | Force symmetry (awkward — one subframe of LDPC, one decoder of BCH) |
| Methodology / channel placement | **Top level** (shared across both codes — same C++ already shares them) | Per-code (allows divergence but no current need) |

#### Out of scope (call-outs)

- **Soft-vs-hard delta is not a schema field.** Lunalink demonstrates it by publishing two cards; `diff` reports each card's verdict independently.
- **PocketSDR is not invoked.** Cards are pure simulation + adapter; the L4 *correctness* oracle (which uses PocketSDR) is a separate axis.
- **L3-style C/N₀ end-to-end performance is not measured here** — different axis (acquisition-gated, not decoder-block).

---

## 🟡 Pinned-scope requirements (R') and fit check

The pinned standard scope above redefines what's being shaped — away from
the older TC5-negative-vectors framing (R0–R8, kept as audit trail
below). R' states what the **pinned scope** must satisfy; the fit check
shows how.

### R' — Requirements for the pinned scope

| ID | Requirement | Status |
|----|-------------|--------|
| **R'0** | Publish an oracle-backed, reproducible benchmark of FEC *decoding* performance covering both LSIS-AFS codes (LDPC SF2/SF3-SF4 + SB1/BCH) — the L4 *robustness under noise* axis, distinct from L4 *correctness* already shipped | Core goal |
| **R'1** | Two measured curves, both anchored to a common operating point (Es/N0 = 0 dB), with spec-grounded verdict bars: LDPC `BER<1e-5 @ Es/N0≥0 dB` (LSIS spec) + SB1/BCH `FER<0.01 @ Es/N0≥0 dB` (interop PDF "Frame Detection > 99%") | Must-have |
| **R'2** | Methodology integrity: (a) no reimplementation of LDPC/BCH/CRC in the harness; (b) the comparability oracle is a fully-specified reproducible methodology + open tooling + shipped reference vectors — **no** second decoder, **no** PocketSDR; (c) each algo card cites the shipped reference vector set (commit/tag) as its anchor | Must-have |
| **R'3** | Adopter contract is stdio: the harness owns methodology (pinned seeds, σ²=1/(2·R·Eb/N0), grids, Wilson CI, JSON emit); adopters supply only a decode adapter for `code ∈ {SB1, SF2, SF3}` — LLRs + σ² in → info bits out. Language-neutral. Two paths: easy (harness runs adapter) or fallback (produce JSON from spec, then `perf-card validate`) | Must-have |
| **R'4** | Unified algo card output `sp_results.json` with top-level `ldpc:` and `sb1:` blocks; methodology / channel / grids / verdicts / `reference_anchor` are explicit JSON fields (not buried in source code). Tiered: `core` (MUST — the comparable contract) · `extended` (SHOULD) · `full` (MAY). `diff` compares only core regardless of tier, so a core-only adopter is comparable with a full-tier one | Must-have |
| **R'5** | Code-family-neutral: an adopter shipping a single decoder per code (any family — hard-ML, soft-ML, BDD, sum-product variant) produces a valid algo card. A `decoder_class ∈ {hard_ML, soft_ML, BDD, other}` tag records family for context. The standard does **not** require shipping both hard and soft. Soft-vs-hard is lunalink's internal exploration, published as two algo cards (two schema instances), not an asymmetric schema | Must-have |
| **R'6** | Lunalink ships the full-tier reference exemplar `sp_results.json` (LDPC + SB1) as the canonical instance of the schema — proof-of-existence that the standard is buildable, and the natural target for `diff` during round-robin | Should-have |

### R' × Pinned Scope fit check

| Req | Requirement | Status | Pinned Scope |
|-----|-------------|--------|:------------:|
| R'0 | Oracle-backed FEC-decoding benchmark, both codes, L4 robustness axis | Core goal | ✅ |
| R'1 | Two curves at common Es/N0=0 dB anchor + spec-grounded verdicts | Must-have | ✅ |
| R'2 | Methodology integrity (no-reimpl + no-PocketSDR + ref-anchor) | Must-have | ✅ |
| R'3 | Stdio adopter contract; harness owns methodology; two paths | Must-have | ✅ |
| R'4 | Unified algo card with explicit JSON fields + tiered schema | Must-have | ✅ |
| R'5 | Code-family-neutral; `decoder_class` tag; one decoder per card | Must-have | ✅ |
| R'6 | Lunalink full-tier exemplar | Should-have | ✅ |

**Notes:** the fit check is on the *design*, not the implementation. The
pinned scope addresses every R'. Implementation gaps remain — tracked
below.

### 🟡 Unsolved (implementation work derived from R')

| Status | Item | Origin | Where / next step |
|:------:|------|--------|-------------------|
| 🟡 ✅ | BCH characterization packaged as a JSON-emitting standalone tool | R'4, R'6 | lunalink `interop/bch-characterise`@44fbd94 — `scripts/bch_characterise.cpp` |
| 🟡 ✅ | LDPC `sp_results.json` surfaces methodology / channel / `operating_point` as explicit top-level JSON fields | R'4 | lunalink `interop/bch-characterise`@f73c367 — additive `ldpc_characterise.cpp` patch (existing keys unchanged, `plot_algo_card.py` unaffected) |
| 🟡 ✅ | Unified harness emitting one `sp_results.json` from both codes | R'4, R'6 | lunalink `interop/bch-characterise`@f73c367 — `scripts/make_algo_card.py` + `task algo-card` (emits `sp_results_{soft,hard}.json`, ~23 KB each, full tier) |
| open | `perf-card` harness (`run` / `validate` / `diff` / `--self-test`) | R'3 | repo: build once schema is concrete (next phase) |
| open | Reference vector set for `--self-test` | R'2(c) | repo: concurrent with harness |

🟡 **R'6 (lunalink full-tier exemplar) is now both design-✅ AND
implementation-✅.** End-to-end verified: `task algo-card` produces both
algo cards with LDPC + methodology blocks byte-identical across families,
differing only in `sb1:`. Sample run:

- **Soft card**: LDPC SF2/SF3-SF4 verdict `BER<1e-5 @ Es/N0≥0 dB` cleared
  at Eb/N0 = 2.0 dB (BER=0 measured); SB1 soft-ML verdict
  `FER<0.01 @ Es/N0≥0 dB` cleared at Eb/N0 = 7.6 dB (FER=0, 0/30000).
- **Hard card**: identical LDPC; SB1 hard-ML verdict cleared at
  Eb/N0 = 7.6 dB (FER=3.3e-5, 1/30000) — ~10× higher than soft but well
  under the 0.01 bar, as the family-neutral verdict was designed to be.

Remaining 2 open items are repo-side (lsis-afs-test-vectors).

---

## Requirements (R) — historical (TC5 negative-vectors framing, superseded by R' above)

| ID | Requirement | Status |
|----|-------------|--------|
| R0 | Exercise PDF TC5's three error classes — (a) corrupted frames (bit flips), (b) invalid CRC, (c) out-of-range field values — as shipped vectors a third party can consume | Core goal |
| R1 | No reimplementation of CRC-24Q / LDPC / BCH in `validate.py` — independence comes from the external decoder, never a Python re-impl (standing repo principle) | Must-have |
| R2 | Every negative vector ships a machine-readable **declared expected outcome** (fail-to-sync \| FEC-uncorrectable \| CRC-mismatch on subframe N \| field-out-of-range), so the oracle is declared-vs-observed, not computed | Must-have |
| R3 | Deterministic & byte-stable — each corrupted artifact is a documented deterministic transform of an existing positive vector (recipe + maintainer regen command, like `inputs/` + `build-canonical-inputs`) | Must-have |
| R4 | Declared outcomes are **empirically grounded** in the bundled independent decoder's (PocketSDR-AFS) actual behavior, not assumed — observe, then pin as the contract | Must-have |
| R5 | Respect FEC reality — vectors explicitly distinguish *below* correction capacity (decode still succeeds, output ≡ original — robustness) from *above* capacity (error surfaces); declared outcome must be robust across reasonable decoders | Must-have |
| R6 | Cross-impl oracle parity — a `diff`-style command so a third party runs the same declared-vs-observed check against *their* decoder (mirrors `diff-decode`) | Should-have |
| R7 | Bounded footprint & CI cost — small vector count, sensible compression, fast oracle (repo explicitly rejected heavy fault-sweeps for marginal gain) | Must-have |
| R8 | At least one out-of-range vector (ITOW 504..511 / field maxima beyond spec) with declared "handle gracefully, treat as unreliable — do not crash" | Nice-to-have |

---

## Shapes

### A: Corrupt-at-channel — bit-flip the shipped frame symbol stream, oracle = decoder-observed

| Part | Mechanism | Flag |
|------|-----------|:----:|
| A1 | `negative/frame_*_corrupt_*.bin`: deterministic XOR mask over chosen channel-symbol index ranges of an existing `frames/frame_*.bin` (recipe in CORRECTNESS) | |
| A2 | Corruption levels chosen relative to LDPC capacity: one *sub-threshold* (FEC corrects → output ≡ original) and one *supra-threshold* per targeted subframe (LDPC fails → CRC-24Q flags) | ⚠️ |
| A3 | `negative/expected/<name>.json`: declared outcome `{class, expect:{sync, sbN_decode, sbN_crc_ok, payload_eq_original}}` | |
| A4 | `check-negative` oracle: run shipped PocketSDR-AFS over the corrupt frame (reuse L4 harness), assert observed == A3 declared | ⚠️ |
| A5 | `diff-negative <decoder-out-dir>`: third party drops their decoder's output; assert it matches A3 declared | ⚠️ |
| A6 | `build-negative` maintainer command + CORRECTNESS recipe (deterministic, byte-stable) | |

### B: Corrupt-at-payload — re-encode with a deliberately wrong CRC (deterministic "invalid CRC")

| Part | Mechanism | Flag |
|------|-----------|:----:|
| B1 | Producer (lunalink) gains an "encode with tampered/forced-wrong CRC-24Q" mode: payload D, CRC computed over D, then one payload bit flipped → conformant receiver decodes D′, CRC≠CRC(D′) **deterministically, FEC-independent** | ⚠️ |
| B2 | `negative/frame_*_badcrc_sbN.bin` produced by B1 over each subframe | ⚠️ |
| B3 | `negative/expected/<name>.json`: `{class: invalid-crc, expect:{sbN_crc_ok:false, other_sb_crc_ok:true}}` | |
| B4 | Oracle: shared with A4/A5 (`check-negative`/`diff-negative`) | ⚠️ |
| B5 | `build-negative` regen depends on the lunalink producer feature (upstream change, like the L5 emitter was) | ⚠️ |

### C: Structural-only out-of-range negatives (no channel corruption)

| Part | Mechanism | Flag |
|------|-----------|:----:|
| C1 | `negative/inputs/*_input.bin` or `negative/parsed/*.json` with ITOW 504..511 / field maxima beyond spec | |
| C2 | `negative/expected/<name>.json`: `{class: out-of-range, expect:{flagged:true, no_crash:true}}` | |
| C3 | `check-negative` (structural subset): assert range logic flags exactly the declared fields; no decoder needed | |
| C4 | Covers **only** TC5 class (c); does nothing for (a)/(b) | |

### D: Hybrid — best mechanism per TC5 class, one `negative/` dir + one oracle

| Part | Mechanism | Flag |
|------|-----------|:----:|
| D1 | TC5(a) bit-flip → **A1+A2** (sub/supra-threshold channel corruption) | ⚠️ |
| D2 | TC5(b) invalid CRC → **B1+B2** (deterministic forced-wrong CRC, FEC-independent) | ⚠️ |
| D3 | TC5(c) out-of-range → **C1** | |
| D4 | Unified `negative/expected/*.json` schema (A3/B3/C2) + `check-negative` + `diff-negative` (A4/A5) | ⚠️ |
| D5 | Single `build-negative` recipe; channel-corrupt parts pure-local, CRC-tamper parts gated on the upstream lunalink producer feature (B5) | ⚠️ |

---

## Fit Check (post-spike)

Component shapes A/B/C, then the two composable final options:
**D1 = A + C** (pure-local), **D2 = A + B + C** (full TC5, B gated on an
upstream lunalink producer PR).

| Req | Requirement | Status | A | B | C | D1 | D2 |
|-----|-------------|--------|---|---|---|----|----|
| R0 | Exercise TC5's three error classes as shipped vectors | Core goal | ❌ | ❌ | ❌ | 🟡 ⚠️➜❌ | 🟡 ✅ |
| R1 | No CRC/LDPC/BCH reimplementation in validate.py | Must-have | ✅ | ✅ | ✅ | ✅ | ✅ |
| R2 | Machine-readable declared expected outcome per vector | Must-have | 🟡 ✅ | ✅ | ✅ | ✅ | ✅ |
| R3 | Deterministic & byte-stable transform + regen command | Must-have | 🟡 ✅ | ❌ | ✅ | 🟡 ✅ | ❌ |
| R4 | Declared outcomes empirically grounded in PocketSDR-AFS behavior | Must-have | 🟡 ✅ | ❌ | ✅ | 🟡 ✅ | ❌ |
| R5 | Respect FEC reality (sub- vs supra-threshold) | Must-have | 🟡 ✅ | ✅ | ✅ | 🟡 ✅ | 🟡 ✅ |
| R6 | Cross-impl `diff`-style command (mirrors diff-decode) | Should-have | ✅ | ✅ | ✅ | ✅ | ✅ |
| R7 | Bounded footprint & CI cost | Must-have | ✅ | ✅ | ✅ | ✅ | ✅ |
| R8 | Out-of-range vector with graceful-handling expectation | Nice-to-have | ❌ | ❌ | ✅ | 🟡 ✅ | 🟡 ✅ |

**Notes (post-spike):**
- 🟡 **A fully resolved by the spike.** The existing deterministic AWGN gives two byte-stable, empirically-observed regimes — **42 dB-Hz** (channel errors present, FEC fully corrects, all CRC pass) and **30 dB-Hz** (graceful acquisition-fail, zero frames, no crash). R2/R3/R4/R5 → ✅ for A's scope. A still fails **R0** (only TC5 class a) and **R8**.
- 🟡 **B confirmed *necessary* but still ❌ on R3/R4.** Spike proved channel noise can never produce a "decoded-but-CRC-fails" frame (receiver acquisition-fails first). Only a producer-emitted payload-consistent frame with a deliberately wrong embedded CRC yields TC5(b). B therefore depends on an upstream lunalink "forced-wrong-CRC" mode → not pure-local (R3 ❌) and unobserved until that lands (R4 ❌).
- C unchanged: structural-only out-of-range; R4 vacuous (no decoder dependency). Fails **R0** alone (one class).
- 🟡 **D1 = A + C**: pure-local, ships now, no upstream coupling. Fails **R0** because true TC5(b) "decoded-but-CRC-fails" is omitted — *unless* TC5(b) is reinterpreted as "graceful failure under noise," which A's 30 dB-Hz regime already demonstrates. Covers 2-of-3 classes literally (a, c) + a defensible weaker (b).
- 🟡 **D2 = A + B + C**: spans all three TC5 classes literally → **R0 ✅**, but inherits B's **R3/R4 ❌** until an upstream lunalink producer PR lands and is observed (same pattern as the L5 emitter via PR #88).

---

## ✅ Selected shape: D1 = A + C (pure-local, v0.5.x follow-up)

Accepted trade-off: literal TC5(b) "decoded-but-CRC-fails" is **out of scope**
(physically unreachable via channel noise per the spike); it is covered as the
honest weaker "graceful acquisition-failure under noise," which satisfies the
PDF's *"decoders handle errors gracefully."* D2's B may be added later if a
lunalink forced-wrong-CRC producer PR is independently justified.

### Concrete D1 spec

**Footprint (resolves Q3) — recipe-defined, not shipped binaries:**

| Vector | Class | Recipe | Declared expected outcome |
|---|---|---|---|
| `neg_fec_corrects` | corrupt (a) | base `signal_message_1_12s.iq.gz`, AWGN **C/N0 = 42 dB-Hz**, seed `SHA256(name)[:8]` | `chan` differs; `fec` **byte-equal** `inputs/frame_message_1_input.bin`; SB2/3/4 CRC **pass** |
| `neg_acq_fail` | corrupt (a, graceful) | same base, **C/N0 = 30 dB-Hz** | zero frames / no symbol dump / `TOI NOT FOUND`; **no crash** |
| `neg_itow_oor` | out-of-range (c) | structural `negative/parsed/parsed_itow_504.json` (ITOW ∈ 504..511) | validator flags out-of-9-bit-spec; **no crash** |

Only the 3 tiny `negative/expected/*.json` (+ the one structural parsed JSON)
are shipped; corrupt `.iq` is regenerated deterministically by
`build-negative` (maintainer cmd, like `build-canonical-inputs`). Strongly
satisfies R3/R7.

**Oracle — follow the `check-decode` pattern, not a live build in CI:**
`check-negative` asserts observed-vs-declared. For the corrupt class it
compares against a **shipped reference decode result** (mirroring how
`check-decode` validates shipped `decoded/` rather than rebuilding
PocketSDR-AFS), keeping the per-push CI gate fast; the live AWGN→PocketSDR
reproduction is the maintainer-side `verify-*` path (heavy, off the gate).
`diff-negative <decoder-out>` = third-party reciprocal (mirrors
`diff-decode`). R6 ✅.

### Fit check — selected shape D1

| Req | Requirement | D1 |
|-----|-------------|----|
| R0 | Exercise TC5's three error classes | ⚠️ a + c literal; b as graceful-fail (accepted) |
| R1 | No CRC/LDPC/BCH reimpl | ✅ |
| R2 | Machine-readable declared outcome | ✅ |
| R3 | Deterministic + regen command | ✅ |
| R4 | Empirically grounded (PocketSDR-AFS) | ✅ (a pinned by spike; c spec-derived) |
| R5 | Respect FEC reality | ✅ |
| R6 | Cross-impl diff command | ✅ |
| R7 | Bounded footprint & CI cost | ✅ |
| R8 | Out-of-range vector | ✅ |

**Unsolved:** only R0's literal TC5(b) — consciously de-scoped (not a flag;
a decision). D1 is ready to slice.

---

## 🟡 Reframe → Shape E: Channel / BER extension (oracle-backed performance drop) — SUPERSEDES D1 framing

Not fault-injection; a **positive, measured, oracle-confirmed** extension of
the repo along the channel-impairment axis. **D1 becomes the MVP first slice
of E** (E ⊇ D1: the 42 dB-Hz FEC-corrects point + 30 dB-Hz graceful-fail point
are two operating points on E's curve; out-of-range stays as the structural
component).

### R deltas (🟡 changed/added)

| ID | Requirement | Status |
|----|-------------|--------|
| 🟡 R0 | Ship an **oracle-backed channel-impairment extension**: deterministic noisy signals across a C/N₀ sweep + the **measured** BER/FER curve + bundled PocketSDR-AFS confirming byte-exact post-FEC recovery within a stated bound (supersedes the TC5-three-classes R0) | Core goal |
| 🟡 R9 | Every published BER/FER number is **measured** from the pinned independent decoder and **reproducible** from the shipped recipe — never asserted | Must-have |
| 🟡 R10 | Noise model + **C/N₀ ↔ Eb/N0 ↔ SNR** mapping documented precisely (PDF states "BER at SNR = 0 dB"); "within bound" unambiguous and third-party-reproducible | Must-have |
| 🟡 R11 | **Honest curve framing**: separate (a) pre-FEC channel-symbol BER vs C/N₀ (smooth, measurable even when frames fail) from (b) post-FEC FER / oracle byte-exact recovery (a *cliff* dominated by the receiver's acquisition lock — per the spike). No "waterfall" the receiver does not exhibit | Must-have |
| 🟡 R12 | **Round-robin under noise**: `diff-decode`-style command so a third-party decoder run over the same impaired signals is comparable (compatibility matrix under impairment, not just clean) | Should-have |

R1/R7 unchanged and still binding (BER measured by symbol-dump-vs-known-tx +
CRC flags — **no codec re-impl**; recipe + measured table shipped, live sweep
is the maintainer verify path, per-push CI asserts vs shipped — `check-decode`
pattern).

### Shape E parts

| Part | Mechanism | Flag |
|------|-----------|:----:|
| E1 | Deterministic noisy-signal recipes `(base, C/N₀ grid, seed=SHA256(name·cn0)[:8])`; grid brackets the acquisition knee densely; **2–3 representative bases** (all-zeros msg1, a content-varied msg, a higher PRN e.g. prn12) to expose content/PRN dependence the 1-signal spike could not | |
| E2 | Measurement harness extending the existing PocketSDR harness: per point emit **channel-symbol BER** (dump vs known tx), FER/SBn-CRC, post-FEC byte-exact vs `inputs/`, acquisition success | ⚠️ |
| E3 | Published `channel/` artifacts: `ber_curve.{json,csv}` (measured, SHA-pinned), methodology doc (noise model, C/N₀↔Eb/N0↔SNR, seed rule, pinned PocketSDR SHA), byte-exact recovered post-FEC outputs at within-bound points (the oracle-backed proof) | |
| E4 | Oracle `check-channel` (assert shipped curve+recovered bytes vs recipe, deterministic) + `diff-channel <decoder-out>` (round-robin-under-noise, R12) | ⚠️ |
| E5 | CORRECTNESS honesty section: cliff-vs-waterfall reality; "within bound" ≔ byte-exact post-FEC recovery for C/N₀ ≥ X (X from measured knee + margin), pinned receiver only | |
| E6 | PDF-metrics mapping: Interoperability Metrics (BER @ SNR=0 dB, Frame Detection >99%, Decode Success >99%) + L3/L4 BER pass criteria + TC5 "error rates match spec" — the currently-unscored gap | |

### Fit Check — Shape E

| Req | Requirement | E |
|-----|-------------|---|
| R0 | Oracle-backed channel-impairment extension (measured curve + recovery bound) | ❌ (pending characterization sweep) |
| R1 | No CRC/LDPC/BCH reimpl | ✅ |
| R7 | Bounded footprint & CI cost | ✅ |
| R9 | Every BER/FER number measured & reproducible | ❌ (not yet measured — see below) |
| R10 | C/N₀↔Eb/N0↔SNR mapping documented | ❌ (mapping not yet derived) |
| R11 | Honest cliff-vs-waterfall framing | ✅ (spike already established the cliff) |
| R12 | Round-robin under noise (`diff-channel`) | ✅ (extends proven `diff-decode`) |

**Reality check (sharpening the "substance exists" claim):** the *infrastructure*
exists (deterministic AWGN, pinned PocketSDR oracle, build cached) and the
spike established the **failure topology** (acquisition cliff ≈ 37 dB-Hz on
one signal). But the spike measured **pass/fail only — no BER was tallied**,
on **one signal / all-zeros**, with **no SNR mapping**. So E's core numbers
(R0/R9/R10) **do not exist yet** — measuring them *is* the work. Modest
(harness extension + a wider sweep on the cached build), but it is "measure +
package," not "package."

### E's remaining spike (gates R0/R9/R10)

Extend `spike-pocketsdr-negative.md`: (1) add a channel-symbol **BER tally**
(dump vs known tx symbols) to the harness; (2) **fine sweep near the knee**
(e.g. 40→33 dB-Hz @ 0.5 dB) across **2–3 bases/PRNs**; (3) derive the
**C/N₀ ↔ Eb/N0 ↔ SNR** mapping from `_sigma_for_cn0` + chip/symbol rates so
the PDF's "SNR = 0 dB" point is locatable. Output: the real (BER, FER,
recovered?) table → becomes `ber_curve` + fixes the "within bound" X.

---

## Decision required (resolved)

The spike turned the deferred B-coupling question into a concrete, evidenced
fork:

- **D1 (A + C), pure-local, v0.5.x-ready now.** No upstream dependency.
  TC5(a) via the AWGN dual-regime + TC5(c) via structural out-of-range.
  TC5(b) only as the honest weaker "acquisition-fail under noise." Lower
  ceiling, zero coupling, immediately buildable.
- **D2 (A + B + C), full TC5, gated upstream.** Adds a genuine
  "decoded-but-CRC-fails" vector — requires a new lunalink forced-wrong-CRC
  producer mode to land first (precedent: L5 emitter, PR #88), then B is
  observed and pinned. Highest fidelity to the PDF; couples the timeline to
  an upstream PR.

Resolved open questions: **Q1** (R4 strictness) — spec-derived is fine for
the no-decoder out-of-range class (C); decoder-dependent classes (A) are now
empirically pinned. **Q2** (B coupling) — evidenced: B is *required* for
literal TC5(b); without upstream, TC5(b) degrades to "acquisition-fail."
**Q4** (scope) — decided: v0.5.x follow-up either way. Remaining: **Q3**
footprint (defer until D1/D2 chosen) and the **D1-vs-D2** decision itself.
