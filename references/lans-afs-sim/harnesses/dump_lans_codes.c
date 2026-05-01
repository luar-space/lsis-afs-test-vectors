/*
 * SPDX-License-Identifier: Apache-2.0
 * Copyright (c) 2026 LuarSpace contributors
 *
 * Harness to extract PRN codes from LANS-AFS-SIM for interop comparison.
 *
 * Compiled against afs_sim.c (with main renamed via -Dmain=afs_sim_main).
 * Dumps Gold (I-channel) and Weil primary (Q-channel) codes as binary files.
 *
 * Output format: one file per PRN per code type, raw uint8 {0, 1} chips.
 *   gold_prn_001.bin   (2046 bytes)
 *   weil_prn_001.bin   (10230 bytes)
 *
 * Linkage note: this file is Apache-2.0 wrapper code that links against
 * LANS-AFS-SIM (BSD-2-Clause, © 2025 Takuji Ebinuma). See README.md in
 * this directory for build instructions and the licence interaction.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* LANS-AFS-SIM functions (defined in afs_sim.c) */
extern void icodegen(int* code, int prn);
extern void qcodegen(int* code, int prn);

#define MAX_PRN 12
#define GOLD_LEN 2046
#define WEIL_LEN 10230

static void dump_code(const char* dir, const char* prefix, int prn,
                      const int* code, int len)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/%s_prn_%03d.bin", dir, prefix, prn);

    FILE* fp = fopen(path, "wb");
    if (!fp) {
        fprintf(stderr, "Cannot open %s\n", path);
        exit(1);
    }

    /* Convert signal-level {-1, +1} → logic-level {0, 1} */
    for (int i = 0; i < len; i++) {
        unsigned char chip = (unsigned char)((1 - code[i]) / 2);
        fwrite(&chip, 1, 1, fp);
    }
    fclose(fp);
}

int main(int argc, char** argv)
{
    const char* outdir = "interop_codes";
    int max_prn = MAX_PRN;

    if (argc > 1) outdir = argv[1];
    if (argc > 2) max_prn = atoi(argv[2]);

    mkdir(outdir, 0755);

    int gold[GOLD_LEN];
    int weil[WEIL_LEN];

    for (int prn = 1; prn <= max_prn; prn++) {
        icodegen(gold, prn);
        dump_code(outdir, "gold", prn, gold, GOLD_LEN);

        qcodegen(weil, prn);
        dump_code(outdir, "weil", prn, weil, WEIL_LEN);

        printf("PRN %3d: gold (%d chips) + weil (%d chips) dumped\n",
               prn, GOLD_LEN, WEIL_LEN);
    }

    printf("Done. Output in %s/\n", outdir);
    return 0;
}
