# LANS-AFS-SIM dump harnesses

Source code for the small C harnesses that produced the
`references/lans-afs-sim/codes/` and `references/lans-afs-sim/frames/`
oracle dumps shipped with this package, plus a Python orchestrator that
drives the L2 harness through all six test messages.

These harnesses are bundled here so that a reader can **independently
reproduce the second-oracle dumps from scratch**, without trusting the
shipped `.bin` files. Build LANS-AFS-SIM, compile a harness, run it, and
compare the output byte-for-byte to what's in
`references/lans-afs-sim/{codes,frames}/`.

## Files

| File | Lines | Purpose |
|:---|---:|:---|
| [`dump_lans_codes.c`](./dump_lans_codes.c) | 77 | L1 — calls `icodegen()` / `qcodegen()` for each PRN, dumps Gold + Weil-10230 chips. |
| [`dump_lans_frame.c`](./dump_lans_frame.c) | 185 | L2 — calls `generate_BCH_AFS_SF1()` + `append_CRC24()` + `encode_LDPC_AFS_SF2/3()` + `interleave_AFS_SF234()`, with five input patterns. |
| [`dump_l2_test_vectors.py`](./dump_l2_test_vectors.py) | 90 | Orchestrator that invokes `dump_lans_frame` once per Level-2 test message. |
| [`verify_oracle.py`](./verify_oracle.py) | — | One-command end-to-end verifier: clones upstream at the pinned SHA, builds, runs every dumper, and `cmp`s against the shipped `.bin` set. Exits 0 only on a full byte-exact match. |

The harnesses contain no encoding logic of their own — they are thin
callers into LANS-AFS-SIM's encoder routines. See
[`../../../CORRECTNESS.md`](../../../CORRECTNESS.md) for what each
routine does and the one disclosed normalisation applied to SB2 spare
bits.

## One-command verifier (recommended)

For the impatient: `verify_oracle.py` automates everything below.  It
clones LANS-AFS-SIM at the pinned SHA, builds it, builds the harnesses,
runs every dumper, and `cmp`s against the shipped `.bin` set.

```bash
python references/lans-afs-sim/harnesses/verify_oracle.py
# ...
# OK — full reproduction: 420 L1 + 6 L2 dumps bit-exact against upstream 0578f298ba68.
```

Useful flags:

- `--lans-dir DIR`  — reuse an existing checkout instead of cloning fresh.
- `--workdir DIR`   — keep build artefacts and dumps under a known path.
- `--keep-workdir`  — don't delete the work directory on success.
- `--cc PATH`       — override compiler discovery (default: Homebrew GCC
  on macOS, system `cc` on Linux).

Exit code 0 means every shipped dump in `references/lans-afs-sim/{codes,frames}/`
was independently reproduced from upstream BSD-2-Clause source — the
chain of trust ends at Ebinuma's encoder code.

The rest of this document explains the manual build path that the
verifier automates, in case you want to reproduce by hand or debug a
mismatch.

## Manual build instructions

These harnesses link against [LANS-AFS-SIM](https://github.com/osqzss/LANS-AFS-SIM).
Build it first, then compile each harness against the resulting object
files.

> **macOS note:** every step below uses `-fopenmp`, which Apple Clang
> does not support.  Install Homebrew GCC (`brew install gcc`) and use
> `gcc-15` (or the latest) wherever the snippet says `cc`.  This applies
> to **upstream `make`** as well — pass `CC=$(brew --prefix gcc)/bin/gcc-15`
> to the make invocation in step 1, otherwise the very first build fails
> with `unsupported option '-fopenmp'`.

```bash
# 1. Clone and build LANS-AFS-SIM.  The shipped .bin dumps were produced
#    against upstream commit 0578f298ba68d8508ab7d780be843faed3e2b274 —
#    check that out for guaranteed bit-identical reproduction.
git clone https://github.com/osqzss/LANS-AFS-SIM.git
cd LANS-AFS-SIM
git checkout 0578f298ba68d8508ab7d780be843faed3e2b274
make                  # produces afs_sim, afs_sim.o, afs_nav.o, afs_rand.o,
                      # ldpc/*.o, rtklib/rtkcmn.o, pocketsdr/pocketsdr.o
                      # (macOS: make CC=$(brew --prefix gcc)/bin/gcc-15)
LANS_DIR=$(pwd)
HARNESSES=/path/to/lsis-afs-test-vectors/references/lans-afs-sim/harnesses

# 2. Build afs_sim_lib.o — afs_sim.c recompiled with main renamed.
#    Upstream `make` does not produce this; the harnesses need it because
#    afs_sim.o carries its own main() that would clash with the harness's.
cc -O2 -fopenmp -Dmain=afs_sim_main \
   -I"$LANS_DIR" -I"$LANS_DIR/pocketsdr" -I"$LANS_DIR/rtklib" \
   -I"$LANS_DIR/ldpc" \
   -c afs_sim.c -o afs_sim_lib.o

# 3. Link dump_lans_frame against the object set (incl. afs_sim_lib.o).
cc -O2 -fopenmp \
   -I"$LANS_DIR" -I"$LANS_DIR/pocketsdr" -I"$LANS_DIR/rtklib" \
   -I"$LANS_DIR/ldpc" \
   "$HARNESSES/dump_lans_frame.c" \
   afs_sim_lib.o afs_nav.o afs_rand.o \
   ldpc/alloc.o ldpc/mod2sparse.o \
   rtklib/rtkcmn.o pocketsdr/pocketsdr.o \
   -lm -o dump_lans_frame

# 4. Same for dump_lans_codes.
cc -O2 -fopenmp \
   -I"$LANS_DIR" -I"$LANS_DIR/pocketsdr" -I"$LANS_DIR/rtklib" \
   -I"$LANS_DIR/ldpc" \
   "$HARNESSES/dump_lans_codes.c" \
   afs_sim_lib.o afs_nav.o afs_rand.o \
   ldpc/alloc.o ldpc/mod2sparse.o \
   rtklib/rtkcmn.o pocketsdr/pocketsdr.o \
   -lm -o dump_lans_codes
```

These commands have been smoke-tested on macOS (Homebrew `gcc-15`)
against an unmodified `osqzss/LANS-AFS-SIM` clone; the rebuilt harness
binaries reproduce every shipped `.bin` byte-for-byte.

## Running

```bash
# L1 — produce all 210 Gold + Weil chip dumps.
./dump_lans_codes ./codes_out 210

# L2 — produce all 6 frame dumps for the prescribed test messages.
python dump_l2_test_vectors.py ./frames_out --harness ./dump_lans_frame
```

Compare output byte-for-byte to the bundled dumps:

```bash
diff -r ./codes_out ../codes/
diff -r ./frames_out ../frames/
```

A clean `diff` confirms that the second-oracle dumps in this package were
produced by these exact harnesses against an unmodified LANS-AFS-SIM.

## Licence

The harness `.c` and `.py` files in this directory are **Apache-2.0**,
© 2026 LuarSpace contributors (SPDX header in each file).

They link against and call LANS-AFS-SIM, which is **BSD-2-Clause**,
© 2025 Takuji Ebinuma — preserved verbatim in
[`../LICENSE.txt`](../LICENSE.txt). Apache-2.0 wrapper code linking
against BSD-2-Clause library is a permissive-on-permissive combination
with no obligations beyond preserving each licence and notice.

The compiled harness binaries derive from both licences and may be
redistributed under either, provided the LANS-AFS-SIM copyright notice
travels alongside.

## Provenance

The L1 harness was first written as part of the LuarSpace generator-side
toolchain in April 2026 to produce the L1 chip dumps shipped in `v0.1.0`.
The L2 harness was added a few weeks later for the L2 frame dumps. Both
files were vendored into this directory in `v0.2.0` alongside the frame
vectors so that the second-oracle reproducibility chain ends at upstream
BSD-2-Clause encoder code rather than at trusting the LuarSpace build.
