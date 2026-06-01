# perf-card adapter templates

This directory contains skeleton implementations of the perf-card decode
adapter contract in two languages. Copy the template that matches your
language, fill in the TODOs, and run via the harness:

```bash
# Python
python perf_card.py run --decoder "python my_adapter.py" --tier core

# C (compile first)
cc -O2 -std=c11 -o my_adapter adapter_c_template.c
python perf_card.py run --decoder ./my_adapter --tier core
```

Both templates are < 250 lines including the protocol boilerplate. You
should only need to edit three blocks: identity strings (declared at
handshake), per-code decode function bodies, and — if your decoder
recovers SB1 as (FID, TOI) rather than 9 raw bits — the optional info-bit
packing helper that ships ready-made.

## Protocol overview

A perf-card adapter is a long-running subprocess. The harness spawns it,
exchanges a one-time handshake, then sends a stream of decode requests
and reads responses until it closes stdin (signalling end-of-session).

**Session lifecycle:**

```
harness                                              adapter
   spawn ./adapter ────────────────────────────────►
   ◄────── handshake_request (length-prefixed JSON)
   ──────► handshake_ack     (length-prefixed JSON)
   ──────► request frame 1   (binary)
   ◄────── response frame 1  (binary)
   ──────► request frame 2
   ◄────── response frame 2
   ... thousands of frames ...
   close stdin ────────────────────────────────────►   EOF on stdin → exit 0
```

All multi-byte integers and floats on the wire are **little-endian**.

### Handshake

Length-prefixed JSON in each direction. The harness sends first:

```
<u32 LE length><length bytes JSON HandshakeRequest>
```

`HandshakeRequest`:

```json
{
  "type": "handshake",
  "protocol_version": "1.0",
  "harness": {"name": "lsis-afs perf_card", "version": "1.0.0"}
}
```

Adapter responds with `HandshakeAck`:

```json
{
  "type": "handshake_ack",
  "protocol_version": "1.0",
  "adapter": {
    "name": "human-readable decoder identity (carried into algo card)",
    "version": "1.0.0",
    "supports_codes": ["SB1", "SF2", "SF3"],
    "ldpc": {
      "algorithm": "Layered Sum-Product BP",
      "early_termination": "syndrome check every iter"
    },
    "sb1": {
      "name": "my-bch-decoder",
      "decoder_class": "soft_ML",
      "algorithm": "exhaustive ML over inner-product LLR"
    }
  }
}
```

The harness validates `protocol_version == "1.0"`; mismatch aborts the
session. The `adapter` block's fields land in the algo card's identity
section verbatim.

### Per-frame request (harness → adapter)

```
Offset  Size      Field        Notes
 0      u8        code_id      0 = SB1, 1 = SF2, 2 = SF3
 1      u16       max_iters    0 → adapter default
 3      f32       sigma_sq     AWGN variance σ²; LLRs already
                                scaled by 2·y/σ², but you may
                                need this for re-quantisation /
                                normalisation steps internally
 7      u32       n_bits       length of LLR array (52/2400/1740)
11      f32×n     llrs         channel LLRs, one per encoded bit
```

Total request size: `11 + 4·n_bits` bytes.

### Per-frame response (adapter → harness)

```
Offset  Size      Field           Notes
 0      u8        status          0 = ok
                                  1 = not_converged (decoder ran
                                       but didn't satisfy its own
                                       convergence criterion)
                                  2 = error (adapter problem;
                                       harness aborts)
 1      u16       iters_used      informational — pass 0 if
                                  non-iterative
 3      u32       n_info_bits     9 / 1200 / 870 — must match
                                  what code_id implies
 7      u8×n      info_bits       decoded info bits ∈ {0, 1},
                                  one byte each
```

Total response size: `7 + n_info_bits` bytes.

### SB1 info-bit packing convention

SB1 returns 9 info bits. The standard pins their layout:

```
Bit  0: FID bit 1 (MSB)
Bit  1: FID bit 0
Bit  2: TOI bit 6 (MSB)
Bit  3: TOI bit 5
...
Bit  8: TOI bit 0
```

Both templates include a `pack_sb1_info(fid, toi)` helper — use it if
your decoder produces `(FID, TOI)` directly rather than raw bits.

## Implementation gotchas

1. **Flush after every response.** Buffered stdout will hang the harness
   waiting for bytes that are stuck in the adapter's libc buffer.
   - Python: `sys.stdout.buffer.write(...); sys.stdout.buffer.flush()`
   - C: `fflush(stdout)` after every response (and call `setvbuf` once
     at startup for efficient batching between flushes).

2. **Binary mode on Windows.** Default text mode translates `\r\n` ↔
   `\n` and corrupts the binary protocol.
   - Python: use `sys.stdin.buffer` / `sys.stdout.buffer` (always binary).
   - C: `_setmode(_fileno(stdin), _O_BINARY)` plus same for stdout.

3. **EOF on stdin = clean shutdown.** When the harness is done it closes
   its end of the pipe. Your read loop should treat 0-byte reads as
   "session over, exit 0".

4. **Stderr is yours.** Diagnostics, progress notes, anything — print
   freely to stderr. The harness passes it through to its own stderr.
   Only stdout carries the protocol.

5. **C struct packing.** If you read the request header into a struct
   on the C side, the compiler will insert padding unless you mark the
   struct `__attribute__((packed))` or read field-by-field. The C
   template reads field-by-field for portability.

## Testing your adapter

You can drive your adapter end-to-end via the harness:

```bash
python perf_card.py run \
    --decoder "python my_adapter.py" \
    --codes SB1 \
    --frames-per-seed 100 \
    --tier core \
    --out my_algo_card.json
```

Then validate the resulting card against the standard schema:

```bash
python perf_card.py validate my_algo_card.json
```

And compare it against the shipped reference card (which contains the
lunalink reference decoder's measurements) to see how your decoder ranks:

```bash
python perf_card.py compare my_algo_card.json \
                            ../perf_card_reference_card.json --verbose
```

For an N-way ranking across multiple submissions:

```bash
python perf_card.py leaderboard my_algo_card.json \
                                ../perf_card_reference_card.json \
                                other_team_card.json
```

To isolate adapter mechanics from harness sweep cost, record a request
stream from the harness and feed it back into your adapter directly:

```bash
# Capture one harness run's binary protocol (not currently shipped as a
# helper — but you can wrap your adapter to log stdin to a file).
my_adapter < captured_requests.bin > my_responses.bin
```

## Specification reference

The protocol and algo-card schema are defined in
`shaping/decoder-performance-card.md` (in this repo). The lunalink
adapter (a complete working example) lives in the lunalink repository,
not here — this repo is vendor-agnostic, treating lunalink as one
adopter among many. If you have the lunalink source checkout, look for
its perf-card adapter there; otherwise the templates above plus your
own decoder calls are everything you need.
