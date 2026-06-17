# Offline kit — pre-Goonhilly checklist

Goonhilly is rural (Lizard Peninsula, Cornwall) and the workshop brief
says car transport is essential. Assume no reliable internet from
arrival onward. The aim of this checklist is: even if WiFi dies, the
laptop drops a venv, or the GitLab server is flaky, the demo and the
artefact pushes still work.

## Pack on the laptop (do the day before)

- [ ] `lunalink` repo at `~/code/lunalink/`, **on a clean working tree**
      with the CLI subcommands committed
- [ ] `lsis-afs-test-vectors` repo with the `workshop/` bundle committed
      (this repo)
- [ ] Working venv where `python -c "from lunalink.cli import main"`
      runs without error, and `python -c "from lunalink.afs import
      transmit_frame, prn_code"` works too
- [ ] PocketSDR-AFS harness verified: run a sample decode against a
      shipped signal to confirm the pipeline still works on this
      laptop

```bash
# Verify lunalink CLI is healthy
python -c "from lunalink.cli import main; main(['--help'])"

# Verify a quick end-to-end smoke
lunalink generate-codes --out /tmp/codes_smoke.txt
diff /tmp/codes_smoke.txt workshop/codes.txt && echo OK

lunalink encode --format frame --prn 1 --fid 0 --toi 42 \
    --wn 100 --itow 250 --out /tmp/frame_smoke.bin
cmp /tmp/frame_smoke.bin workshop/frame.bin && echo OK
```

## External backup (USB stick)

A single tarball on the stick so any of these can be redeployed in
minutes:

```bash
# Build the offline tarball
cd ~/code
tar --exclude='.venv' --exclude='build' --exclude='*.pyc' \
    --exclude='__pycache__' --exclude='.pytest_cache' \
    --exclude='.ruff_cache' --exclude='node_modules' \
    -czf ~/goonhilly_offline_kit.tar.gz \
    lunalink/ lsis-afs-test-vectors/

ls -lh ~/goonhilly_offline_kit.tar.gz
# ~200-400 MB expected (mostly references/ oracle data)
```

Also copy onto the stick:

- [ ] A pre-built lunalink wheel for macOS arm64:
      `cd lunalink && uv build --wheel && cp dist/lunalink-*.whl
      ~/goonhilly_wheels/`
- [ ] A pre-generated copy of `/tmp/workshop/` with raw (uncompressed)
      `.iq32` files so the workshop pushes can happen *even if lunalink
      breaks on the day*
- [ ] The two draft messages: `workshop_thread_reply.txt`,
      `matjaz_coordination_msg.txt`

## Things to avoid the day before

- `uv sync` / `uv pip install -U` / `brew upgrade` — anything that
  perturbs the Python or C++ toolchain
- Force-pushing or resetting branches in either repo (lose the
  workshop bundle)
- Cleaning `build/` or `.venv/` directories
- macOS updates that touch Xcode / clang

## On the day — recovery if something breaks

| Symptom | Quick fix |
|---|---|
| `lunalink: command not found` | `cd ~/code/lunalink && uv pip install -e . --no-build-isolation` |
| Import errors after `uv sync` | `uv pip install ~/goonhilly_wheels/lunalink-*.whl` |
| Live encode hangs | Use the pre-built artefacts in `workshop/` (uncompressed copies on USB) for the push; do the live demo on a smaller `.iq32` if needed |
| GitLab unreachable | Ask the admin for the local mirror IP; failing that, share via USB / `scp` |

## Goonhilly logistics

- Contact on the day: David Johnson, G4DPZ — `esa-competition@amsat-uk.org`
  — 07733 106990 (per workshop programme)
- Car transport essential — no public transport to the Lizard
- Welcome at 09:15, GitLab onboarding 09:30 — be set up by 09:00
- Standards clarification slot at 11:15 (LSIS-AFS in Track A) — the
  natural slot to raise any leftover artefact-shape questions in person
- Informal demos at 16:00 — see `DEMO_STORYBOARD.md`
