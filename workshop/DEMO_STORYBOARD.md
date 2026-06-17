# Day-1 16:00 informal demo storyboard

Goal: showcase **LunaLink** as the reference implementation, in ~5
minutes, against the Day-2 informal-demo categories the programme
lists ("LSIS Signal Generation: Show I/Q output, demonstrate decode").

The pitch in one sentence: *one encoder, both tracks — workshop
baseline (1.023 MHz AFS-I) and advanced (10.23 MHz with full AFS-Q
pilot), with a third-party independent decoder (PocketSDR-AFS) closing
the loop on the advanced case.*

## Setup (do before the slot)

- [ ] LunaLink installed and `lunalink --help` works
- [ ] `lsis-afs-test-vectors` repo cloned
- [ ] PocketSDR-AFS harness ready under
      `references/pocketsdr-afs/harnesses/`
- [ ] Terminal split: left for `lunalink`, right for the decoder
- [ ] Have a backup tarball with pre-generated outputs in case the
      live encode hangs

## Demo (5 minutes, three beats)

### Beat 1 — workshop baseline (~60 s)

> "The workshop programme defines the file shape — single codes.txt,
> raw 6000-byte frame, raw 1.023 MHz I/Q with Q=0. LunaLink produces
> all of it from one CLI."

```bash
# Generate the three workshop artefacts
lunalink generate-codes --out codes.txt
lunalink encode --format frame \
    --prn 1 --fid 0 --toi 42 --wn 100 --itow 250 \
    --out frame.bin
lunalink encode --format iq32 --rate 1023000 \
    --prn 1 --fid 0 --toi 42 --wn 100 --itow 250 \
    --out signal_canonical.iq32

# Inspect: shapes match the spec
wc -c codes.txt frame.bin signal_canonical.iq32
# 107730 codes.txt   (210 × 512 hex + newlines)
# 6000   frame.bin   (raw symbols)
# 98208000 signal_canonical.iq32  (~94 MB)
```

Optional one-liner showing Q=0:

```bash
python -c "
import numpy as np
iq = np.fromfile('signal_canonical.iq32', '<f4').reshape(-1, 2)
print('I range:', iq[:,0].min(), '..', iq[:,0].max())
print('Q all zero:', (iq[:,1] == 0).all())
"
# I range: -1.0 .. 1.0
# Q all zero: True
```

### Beat 2 — advanced track / decode round-trip (~150 s)

> "Same encoder, full pipeline. AFS-Q tiered pilot, 10.23 MHz. We
> point PocketSDR-AFS — Ebinuma's independent receiver — at it and
> recover the navigation fields we put in."

```bash
# Generate the full-pipeline signal via the existing example
# (transmit_frame at native 10.23 MHz, full AFS-Q pilot)
python examples/01_transmit.py
# → signal.iq (12-second AFS I/Q file)

# Run the PocketSDR-AFS harness on it
python references/pocketsdr-afs/harnesses/decode_signal.py signal.iq

# Show recovered fields
python references/pocketsdr-afs/harnesses/parse_pocketsdr_log.py
# Recovered: FID=0, TOI=42, WN=…, ITOW=…   (matches input)
```

Fallback if the live decode runs long: use the pre-decoded
`references/pocketsdr-afs/decoded/decoded_fec_signal_message_1_12s.bin`
as a known-good output and diff against a fresh frame.bin.

### Beat 3 — the lineage / why this matters (~60 s)

> "Same Apache-2.0 reference implementation across both workshop tracks,
> validated end-to-end against three independent oracles:
> - **Annex 3** for the spreading codes
> - **LANS-AFS-SIM** (Ebinuma) for the frame encoder
> - **PocketSDR-AFS** (Takasu+Ebinuma) for the receiver round-trip
>
> The reference repo at `github.com/[YOUR_GITHUB]/lsis-afs-test-vectors`
> ships both shapes — workshop-bundle baseline and full-rate advanced —
> so any team can byte-compare against it without running LunaLink.
> Happy to align with Matjaz on a common advanced-track artefact shape
> if useful."

## Things to have one-keystroke-ready

- `lunalink generate-codes --out /tmp/codes.txt`
- `lunalink encode --format frame --prn 1 --fid 0 --toi 42 --wn 100 --itow 250 --out /tmp/frame.bin`
- `lunalink encode --format iq32 --rate 1023000 --prn 1 --fid 0 --toi 42 --wn 100 --itow 250 --out /tmp/signal_workshop.iq32`
- A pre-generated 10.23 MHz signal.iq somewhere fast to read
- A terminal alias `decode` mapped to the PocketSDR harness invocation

## What to skip if time runs short

- Beat 1 entirely if PocketSDR-AFS round-trip is impressive enough
- The "advanced track" framing if it confuses people not in that track
- The Q-channel detail in Beat 1 — say "AFS-I only per the workshop
  baseline" and move on

## Backup talking points if the encoder hangs

- The workshop bundle in `workshop/` already contains pre-generated
  outputs — show them off the disk, no live encode needed.
- The polarity-invariant test
  (`I[k] = (1−2·sym[e])·(1−2·gold[k mod 2046])`) is a sub-second visual
  on small data; cheaper than the full encode.
