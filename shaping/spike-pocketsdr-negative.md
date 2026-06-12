---
shaping: true
---

# Spike: PocketSDR-AFS negative-path behavior

### Context

The negative-vector shaping (`negative-vectors-tc5.md`) cannot select a shape
until we know how the bundled independent decoder (PocketSDR-AFS, the L4 oracle)
actually reacts to corrupted / tampered / out-of-range input. The
declared-outcome schema (R2) and the sub- vs supra-FEC-threshold split (R5) are
unknowable in the abstract — they must be *observed* and pinned (R4).

### Goal

Describe, concretely, PocketSDR-AFS's observable negative-path behavior and
where in the existing L4 harness a corrupted frame can be injected and its
failure signature read out.

### Questions

| # | Question |
|---|----------|
| **S1** | Does the L4 harness decode from `signals/*.iq` or from `frames/*.bin`? Where exactly can a corrupted artifact be injected (channel symbols vs I/Q), and what does the harness emit (`decoded_*.bin`, per-subframe `_crc24q_ok`, logs)? |
| **S2** | On a *sub-threshold* bit-flip (few symbols), does PocketSDR-AFS correct it (output ≡ original input, all CRC ok)? Confirms the FEC-robustness case is real and assertable. |
| **S3** | On a *supra-threshold* corruption (e.g. 50% of one subframe's symbols), what is the observable signature — fail to sync? sync but LDPC non-convergence? decode-with-`_crc24q_ok:false` on that subframe? partial/empty output? Is it stable across re-runs? |
| **S4** | What is the approximate LDPC(1/2) correction boundary on the hard-decision symbol stream — how many flipped symbols in a subframe before S2 turns into S3? (Needed to choose A2 corruption levels that are robust, not borderline.) |
| **S5** | Does corruption need to be applied pre- or post-interleave to land as the intended distributed/burst error after deinterleave? What does the recipe operate on (the shipped channel-symbol frame) and what is the post-deinterleave effect? |
| **S6** | Can the harness ingest a frame whose payload is self-consistent but whose embedded CRC was deliberately computed wrong (Shape B), and does it then report `_crc24q_ok:false` deterministically regardless of channel quality? |
| **S7** | For out-of-range fields (ITOW 504..511): does PocketSDR-AFS surface the raw field and continue (graceful), or reject? (Cross-checks the L5 parser's existing range behavior against the independent decoder.) |

### Method (proposed, read-only / throwaway)

- Reuse the existing L4 harness (`references/pocketsdr-afs/harnesses/`) and a
  scratch worktree; corrupt one copy of `frame_message_1.bin` /
  `signal_message_1_12s.iq` at a few levels; run; capture output + logs.
- No repo mutation; findings recorded back here.

### Acceptance

Complete when S1–S7 are answered such that we can (a) write the concrete
`negative/expected/*.json` schema, (b) state the deterministic corruption
recipe and the chosen sub/supra levels, and (c) say whether Shape B's
forced-wrong-CRC producer mode is necessary or whether channel corruption
alone yields a robust deterministic "invalid CRC" signal.

---

## FINDINGS (resolved)

Method: built PocketSDR-AFS at pinned SHA (deps present), deterministic
AWGN C/N0 sweep on `signal_message_1_12s.iq.gz` (PRN 1), cached build
reused across runs. Observables: channel-symbol oracle (`chan`),
post-FEC oracle vs `inputs/` (`fec`), `$SB2/3/4` CRC counts.

| Condition | chan | fec | CRC SB2/3/4 | Signature |
|---|---|---|---|---|
| clean (no AWGN) | OK | OK | 1/1 1/1 1/1 | perfect decode |
| C/N0 50 dB-Hz | **FAIL** | **OK** | 1/1 1/1 1/1 | channel-symbol errors present, **FEC fully corrects, all CRC pass** |
| C/N0 40 dB-Hz | **FAIL** | **OK** | 1/1 1/1 1/1 | same — FEC corrects |
| C/N0 35 dB-Hz | FAIL | FAIL | 0/0 0/0 0/0 | **acquire/sync fail — no symbol dump, zero frames** |
| C/N0 30 dB-Hz | FAIL | FAIL | 0/0 0/0 0/0 | acquire/sync fail |
| C/N0 25 dB-Hz | FAIL | FAIL | 0/0 0/0 0/0 | acquire/sync fail |
| C/N0 20 dB-Hz | FAIL | FAIL | 0/0 0/0 0/0 | acquire/sync fail |

- **S1** ✅ Inject at the `.iq` via the harness's existing deterministic
  seeded AWGN (`--awgn-cn0`, seed=`SHA256(name)[:8]`). No bit-flip recipe
  needed. Observable contract = `chan` / `fec` / `parse_pocketsdr_log`
  `$SBn` counts + `TOI NOT FOUND`.
- **S2** ✅ Sub-threshold is real and rich: at ≥40 dB-Hz the raw symbol
  stream has errors (`chan FAIL`) but **LDPC+CRC fully recover** — post-FEC
  bytes byte-equal `inputs/`, all CRC pass. A genuine "FEC handles errors
  gracefully" vector.
- **S3 / S6** ✅ **Counterintuitive & decisive:** AWGN never yields
  "decodes-but-CRC-fails." Below the knee the *acquisition/tracking loop*
  loses lock first → zero frames (no symbol dump), not a CRC-flagged wrong
  frame. The "CRC-24Q detects a corrupted decoded frame" signature
  (TC5 class b) is **unreachable via channel noise** with this receiver.
- **S4** ✅ Sharp transition between **40 and 35 dB-Hz** — and it is an
  *acquisition* threshold, not the LDPC code threshold. Robust vector
  levels: **42 dB-Hz** (FEC-corrects regime) and **30 dB-Hz** (graceful
  acquisition-fail), comfortably off the ~37 dB-Hz knee.
- **S5** ✅ Moot — corruption is Gaussian on I/Q samples (pre-int8),
  deterministic per seed; no interleave-position reasoning. Recipe =
  `(signal, awgn_cn0_dbhz, seed)`.
- **S7** ⏸ Out-of-range untouched by an AWGN sweep — it is a field-value
  concern. Needs either a producer-side re-encode (ITOW 504..511) or a
  structural-only `parsed/negative_*.json` (Shape C).

### Consequences for shape selection

1. **Shape A is stronger than hoped for TC5(a)** — the existing
   deterministic AWGN gives **two** clean byte-stable regimes
   (FEC-corrects @ 42 dB-Hz; graceful acquisition-fail @ 30 dB-Hz) with
   **zero new code/recipe** — parameters only. A's R3/R4/R5 → ✅.
2. **B is *necessary* for genuine TC5(b)** — channel noise cannot
   synthesize a "decoded-but-CRC-fails" vector; only a producer that
   emits a payload-consistent frame with a deliberately wrong embedded
   CRC can. Confirmed: the deferred B-coupling question now has an
   evidenced answer — *without* an upstream lunalink "forced-wrong-CRC"
   mode, TC5(b) at the decoded-frame level is **not cross-checkable**;
   the best A can offer for "error detected" is the acquisition-fail
   regime (a weaker, but honest, interpretation of TC5(b)).
3. **TC5(c) out-of-range** likewise needs producer-side tamper or a
   structural-only Shape C vector.
