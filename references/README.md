# Reference material

This directory contains **third-party reference material** that the test
vectors in this repository validate against. Contents here are **not**
covered by the top-level Apache-2.0 licence; each item is redistributed
verbatim with its original provenance preserved.

| Item | Source |
|:---|:---|
| [`interoperability.pdf`](./interoperability.pdf) | Competition interoperability plan — defines the five test levels, the `codes_prnNNN.hex` / `frame_{id}.bin` / `signal_{prn}_{duration}s.iq` / `parsed_{id}.json` schemas, and the pass criteria cited in this repository. Redistributed for reader convenience. |
| [`technical-faq.pdf`](./technical-faq.pdf) | Competition *Technical FAQ* — clarifies common implementation ambiguities (Gold/Weil construction, BCH MSB trick, interleaver scope, CRC-24Q, BPSK mapping) and documents known errata in the spec-tables distribution. Cited by [`../CORRECTNESS.md`](../CORRECTNESS.md) as the authoritative pitfalls checklist. |

| Sub-directory | Oracle | Scope | Licence |
|:---|:---|:---|:---|
| [`annex-3/`](./annex-3/)           | LNIS AD1 Volume A Annex 3 (normative)  | L1: Gold, Weil-10230, Weil-1500 — all 210 PRNs | ESA / CCSDS (redistributed under fair-use / normative-reference conventions) |
| [`lans-afs-sim/codes/`](./lans-afs-sim/codes/)   | LANS-AFS-SIM (independent) | L1: Gold, Weil-10230 — all 210 PRNs | BSD 2-Clause, © 2025 Takuji Ebinuma |
| [`lans-afs-sim/frames/`](./lans-afs-sim/frames/) | LANS-AFS-SIM (independent) | L2: 6 encoded frames matching `frames/frame_*.bin` | BSD 2-Clause, © 2025 Takuji Ebinuma |

Future test-vector levels (I/Q signals, parsed JSON) will add their own
oracle sub-directories here as they ship. See each sub-directory's
`README.md` for details.

## Self-check

```bash
python validate.py check-annex3                   # 630/630 vs Annex 3 (L1)
python validate.py check-lans-afs-sim             # 420/420 vs LANS dumps (L1)
python validate.py check-frames                   #   6/6 structural (L2)
python validate.py check-lans-afs-sim-frames      #   6/6 vs LANS dumps (L2)
```
