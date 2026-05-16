# LSIS-AFS Decoding Round-Robin — Proposed Protocol

> **Status: PROPOSAL.** This document outlines a concrete, runnable proposal 
> on how to run the Level 4 cross-decoding exercise as described in 
> [`references/interoperability.pdf`](references/interoperability.pdf) 
> that could be used during the Goonhilly June workshop. 
> It also provides the tooling to automate the comparison and a set of 
> standardised input vectors.

## What this is

The interop plan's **Level 4 — Decoding Interoperability** is a round-robin
matrix: every team's signals are decoded by every team's decoder and the
recovered data is compared to the original input.

```
            Decoder A   Decoder B   Decoder C
Signal A       ·           B(A)        C(A)
Signal B      A(B)          ·          C(B)
Signal C      A(C)         B(C)         ·
```

Each off-diagonal cell is: *decode someone else's signal, check the result
matches the original input exactly* — the plan's verbatim L4 pass criterion:

> "Frame synchronization successful (>99% detection); All subframes decoded
> correctly; CRC validation passes; **Decoded data matches original input
> exactly.**"

This document fixes the on-disk contract so that **any team's decoder
output can be checked by any team's tooling**, and `validate.py diff-decode`
is offered as a reference implementation of that check.

## Key assumption: the test messages are standardized

The interop plan standardizes the message set (TM1 all-zeros … TM5
seeded-random, plus the Test Case 4 boundary frames). So in the round-robin
the **expected decoded bytes are shared by everyone** — they are not
per-team secrets. The variable axes are only *whose signal* and *whose
decoder*.

**Proposed standard reference set.** Rather than have each team re-derive
the standardized messages and hope the bytes agree, this protocol proposes
**this repository's `inputs/` (and `frames/`), pinned to an agreed
release tag (TBD before the workshop), as the single agreed reference
everyone diffs against.** These
are not ad-hoc: `inputs/` is the canonical pre-encode data, unchanged since
v0.2.1, cross-checked by the L2 structural oracle and LANS-AFS-SIM, and
round-tripped byte-exact by an independent receiver (PocketSDR-AFS) at L4.
With this proposal accepted, `diff-decode` with **no `--reference`**
already compares against the standard; `--reference DIR` exists only for a
group-agreed *alternate or extended* set.

## File contract

### Input: signals (one team's L3 output, the thing under test)

`signals/<name>.iq.gz` — LSISIQ format (128-byte header + interleaved
float32 I/Q @ 10.23 MHz × 12 s), exactly as shipped in this repo. A team
decodes these with their own receiver.

### Output: what a decoder must emit

For each input signal, write into one directory:

| File | Bytes | Layout | Status |
|---|---|---|---|
| `decoded_fec_signal_<stem>.bin` | 2868 | one byte per bit ∈ {0,1}: SB2(1176) ‖ SB3(846) ‖ SB4(846), post-LDPC + post-CRC | **REQUIRED** |
| `decoded_signal_<stem>.bin` | 6000 | one byte per channel symbol ∈ {0,1}, post-sync / pre-deinterleave | OPTIONAL |

`<stem>` = the signal filename with `.gz` and `.iq` removed
(`signal_message_1_12s.iq.gz` → `decoded_fec_signal_message_1_12s.bin`).

- **Post-FEC is the pass criterion.** It is the recovered navigation data;
  it must equal the original input byte-for-byte. An absent post-FEC file
  is a failure.
- **Channel-symbol is an optional diagnostic.** It localizes *where* a
  decoder diverges (channel-OK-but-FEC-wrong ⇒ bug in deinterleave/LDPC/CRC;
  channel-wrong ⇒ bug upstream in demod/sync). The post-sync /
  pre-deinterleave tap it needs is not something every receiver exposes
  (the bundled PocketSDR-AFS oracle itself needs a patch for it), so an
  absent channel file is **not** a failure. A *present-but-wrong* one is.

### Reference: the expected bytes

The proposed standard reference is **this repository at an agreed pinned
tag (TBD before the workshop)**:

- `inputs/frame_<id>_input.bin` — 2868 bytes, the canonical pre-encode data
  the post-FEC output must equal. **This is the round-robin's source of
  truth** (it is the original input the L4 criterion names).
- `frames/frame_<id>.bin[64:6064]` — 6000 bytes, the encoded channel
  symbols the (optional) channel output must equal.

Pin to the tag and `verify-manifest` so every team diffs against
byte-identical truth. A group-agreed *alternate or extended* set may be
supplied via `--reference DIR` where `DIR/frames/` and `DIR/inputs/` mirror
this layout — but the default, and the proposal, is the set shipped here.

## Running one matrix cell

```bash
# Decode someone's signals with YOUR decoder, emit the files above into out/,
# then:
python validate.py diff-decode out/                 # vs this repo's reference
python validate.py diff-decode out/ --reference R/   # vs an agreed set R/
python validate.py diff-decode out/ --json           # machine-readable cell
```

Exit codes: **0** = pass (all post-FEC match; no channel file is
present-but-wrong), **1** = fail, **2** = usage error (bad directory /
malformed `--reference`).

`diff-decode` does **not** run any decoder and does not fetch anything — it
is purely the comparison step. Decoding is done by each team's own code.

## Machine-readable output (`--json`)

`--json` emits exactly one matrix cell so an N×N matrix can be assembled
automatically across teams:

```json
{
  "tool": "lsis-afs-validate diff-decode",
  "criterion": "interoperability.pdf Level 4 — decoded data matches original input exactly",
  "candidate": "/path/to/out",
  "reference": "/path/to/reference-root",
  "signals": [
    {"signal": "signal_message_1_12s.iq.gz",
     "post_fec": {"status": "pass",    "detail": null},
     "channel":  {"status": "absent",  "detail": null}}
  ],
  "summary": {
    "post_fec": {"required": true,  "pass": 10, "fail": 0, "missing": 0, "total": 10},
    "channel":  {"required": false, "pass": 0,  "fail": 0, "absent": 10, "total": 10},
    "verdict": "PASS"
  }
}
```

`post_fec.status` ∈ `pass | fail | missing`; `channel.status` ∈
`pass | fail | absent`. `verdict` is `PASS` iff no post-FEC is non-pass and
no channel file is present-but-wrong. With `--vs-pocketsdr` a
`pocketsdr_secondary` block is added (supplementary — it compares against
*our specific decoder's* output, not the spec reference).

To build the compatibility matrix the interop plan asks for, each team runs
one `--json` invocation per (signal-set, their-decoder) pair and the cells
are collected — e.g. `matrix[signal_owner][decoder_team] = summary.verdict`,
with `signals[]` giving per-vector and first-mismatch detail for triage.

## Pass definition and honesty

A green cell means: **every post-FEC output equals the original input
exactly, on every signal attempted, and no provided channel dump is wrong.**

Partial coverage is reported honestly, never silently passed: `missing`
post-FEC counts as fail; signals a team did not attempt show up explicitly
in `signals[]`. The tool reports the cell faithfully; the *competition*
decides what bar (which signals, which FIDs/PRNs) constitutes conformance.
"Pass" cannot be obtained by omitting hard signals — an omitted post-FEC is
a failure, not a skip.

## Scope / non-goals

- The tool **does not execute** anyone's decoder, build anything, or fetch
  code. Each team decodes independently; this only standardizes the compare.
- The message set is standardized by the interop plan; the reference is
  shared. This is intentional — it is what makes cross-team cells
  comparable. Per-team private inputs are out of scope.
- `--vs-pocketsdr` is supplementary, not the criterion: matching our
  bundled decoder's rendering is weaker than matching the original input.

## Process

This is a proposal for the workshop. If the format, layout, or pass
definition doesn't fit your implementation, say so before the round-robin —
agreeing the contract (interop-plan Phase 1) is the point of publishing it
early.
