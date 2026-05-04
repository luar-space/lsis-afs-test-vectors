/*
 * SPDX-License-Identifier: Apache-2.0
 * Copyright (c) 2026 LuarSpace contributors
 *
 * Harness to build an AFS frame using LANS-AFS-SIM's encoding pipeline.
 *
 * Produces a 6000-symbol binary frame with known payload for interop
 * comparison against LuarSpace's frame_build() and against the public
 * lsis-afs-test-vectors Level 2 frame_message_*.bin set.
 *
 * Linkage note: this file is Apache-2.0 wrapper code that links against
 * LANS-AFS-SIM (BSD-2-Clause, © 2025 Takuji Ebinuma). See README.md in
 * this directory for build instructions and the licence interaction.
 *
 * Usage: dump_lans_frame <outdir> <name> <fid> <toi> <pattern> [seed]
 *   name     : output basename (file is lans_frame_<name>.bin)
 *   pattern  : "zeros" | "ones" | "alternating" | "alternating1" | "marker" | "random" | "max_fields"
 *   seed     : xorshift32 seed for the "random" pattern (default 0xAF52)
 *
 * Patterns:
 *   zeros         — all input bits 0
 *   ones          — all input bits 1
 *   alternating   — bit_i = i mod 2        (first packed byte = 0x55, MSB-first)
 *   alternating1  — bit_i = (i + 1) mod 2  (first packed byte = 0xAA, MSB-first)
 *   marker        — bit_i is the MSB-first bit at position i within the
 *                   byte sequence [0x00, 0x01, 0x02, ...].  Each subframe
 *                   starts from byte 0.
 *   random        — xorshift32(seed) bitstream consumed once across all
 *                   2868 input bits (SB2 + SB3 + SB4 in that order).
 *
 * Output: <outdir>/lans_frame_<name>.bin  (6000 bytes, uint8 {0,1})
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "afs_nav.h"
#include "pocketsdr.h"

/* Sync pattern per LSIS V1.0 */
static const uint8_t SYNC_BYTES[9] = {
    0xCC, 0x63, 0xF7, 0x45, 0x36, 0xF4, 0x9E, 0x04, 0xA0
};

static void fill_pattern(uint8_t* buf, int len, const char* pattern)
{
    if (strcmp(pattern, "zeros") == 0) {
        memset(buf, 0, len);
    } else if (strcmp(pattern, "ones") == 0) {
        memset(buf, 1, len);
    } else if (strcmp(pattern, "alternating") == 0) {
        for (int i = 0; i < len; i++)
            buf[i] = (uint8_t)(i % 2);
    } else if (strcmp(pattern, "alternating1") == 0) {
        for (int i = 0; i < len; i++)
            buf[i] = (uint8_t)((i + 1) % 2);
    } else if (strcmp(pattern, "marker") == 0) {
        for (int i = 0; i < len; i++) {
            int byte_val = (i / 8) % 256;
            int bit_pos = i % 8;
            buf[i] = (uint8_t)((byte_val >> (7 - bit_pos)) & 1u);
        }
    } else if (strcmp(pattern, "max_fields") == 0) {
        /* All-ones, with caller-side override of ITOW after this fill. */
        memset(buf, 1, len);
    } else {
        fprintf(stderr, "Unknown per-subframe pattern: %s\n", pattern);
        exit(1);
    }
}

/* Override SB2 bits 13..21 (ITOW field, 9 bits MSB-first) with the spec
 * maximum 503 = 0b111110111.  Used by the "max_fields" pattern; raw 9-bit
 * max 511 is invalid per LSIS V1.0 §2.4.3.1.6 (TC5, not TC4). */
static void apply_itow_spec_max(uint8_t* sb2)
{
    static const int ITOW_OFFSET = 13;
    static const int ITOW_BITS = 9;
    static const int ITOW_SPEC_MAX = 503;
    for (int i = 0; i < ITOW_BITS; i++) {
        sb2[ITOW_OFFSET + i] = (uint8_t)((ITOW_SPEC_MAX >> (ITOW_BITS - 1 - i)) & 1u);
    }
}

static void fill_random_stream(uint8_t* sb2, uint8_t* sb3, uint8_t* sb4,
                               uint32_t seed)
{
    /* xorshift32; consume bits sequentially across SB2 -> SB3 -> SB4 to
     * match the Python export script's _xorshift32_bits() ordering. */
    const int total = 1176 + 846 + 846;
    uint8_t bits[2868];
    uint32_t s = seed;
    for (int i = 0; i < total; i++) {
        s ^= s << 13;
        s ^= s >> 17;
        s ^= s << 5;
        bits[i] = (uint8_t)(s & 1u);
    }
    memcpy(sb2, bits, 1176);
    memcpy(sb3, bits + 1176, 846);
    memcpy(sb4, bits + 1176 + 846, 846);
}

int main(int argc, char** argv)
{
    if (argc < 6) {
        fprintf(stderr,
                "Usage: dump_lans_frame <outdir> <name> <fid> <toi> "
                "<pattern> [seed]\n"
                "  pattern: zeros | ones | alternating | alternating1 | marker | random | max_fields\n"
                "  seed   : xorshift32 seed for 'random' (default 0xAF52)\n");
        return 1;
    }

    const char* outdir = argv[1];
    const char* name = argv[2];
    int fid = atoi(argv[3]);
    int toi = atoi(argv[4]);
    const char* pattern = argv[5];
    uint32_t seed = (argc >= 7) ? (uint32_t)strtoul(argv[6], NULL, 0) : 0xAF52u;

    mkdir(outdir, 0755);

    /* --- Build frame (6000 symbols) --- */
    uint8_t frame[6000];
    memset(frame, 0, sizeof(frame));

    /* Sync pattern: 68 bits */
    sdr_unpack_bits(SYNC_BYTES, 68, frame);

    /* Subframe 1: BCH-encoded FID + TOI (52 bits) */
    uint8_t sb1[52];
    generate_BCH_AFS_SF1(sb1, fid, toi);
    memcpy(frame + 68, sb1, 52);

    /* Subframe input buffers (data + 24-bit CRC) */
    uint8_t sb2_info[1200];
    uint8_t sb3_info[870];
    uint8_t sb4_info[870];

    /* Fill the 2868 input bits per the requested pattern */
    if (strcmp(pattern, "random") == 0) {
        fill_random_stream(sb2_info, sb3_info, sb4_info, seed);
    } else {
        fill_pattern(sb2_info, 1176, pattern);
        fill_pattern(sb3_info, 846, pattern);
        fill_pattern(sb4_info, 846, pattern);
    }

    /* Pattern-specific SB2 field overrides (after fill_pattern has run). */
    if (strcmp(pattern, "max_fields") == 0) {
        apply_itow_spec_max(sb2_info);
    }

    /* Subframe 2: 1176 data + 24 CRC = 1200 info -> 2400 coded.
     * LSIS-300: the 26-bit spare region at offset 1150 must carry the
     * alternating 0/1 pattern starting with 0 on the MSB. LuarSpace's
     * frame_build() rewrites this region unconditionally (G-18), so
     * this harness applies the same pattern post-fill to keep the
     * interop bit-exact comparison honest. */
    for (int i = 0; i < 26; i++) {
        sb2_info[1150 + i] = (uint8_t)(i % 2);
    }
    memset(sb2_info + 1176, 0, 24);
    append_CRC24(sb2_info, 1200);

    /* Subframe 3: 846 data + 24 CRC = 870 info -> 1740 coded */
    memset(sb3_info + 846, 0, 24);
    append_CRC24(sb3_info, 870);

    /* Subframe 4: same structure as SF3 */
    memset(sb4_info + 846, 0, 24);
    append_CRC24(sb4_info, 870);

    /* LDPC encode */
    uint8_t sb234_coded[5880];
    encode_LDPC_AFS_SF2(sb2_info, sb234_coded);          /* -> 2400 */
    encode_LDPC_AFS_SF3(sb3_info, sb234_coded + 2400);   /* -> 1740 */
    encode_LDPC_AFS_SF3(sb4_info, sb234_coded + 4140);   /* -> 1740 */

    /* Interleave */
    uint8_t sb234_interleaved[5880];
    interleave_AFS_SF234(sb234_coded, sb234_interleaved);

    memcpy(frame + 120, sb234_interleaved, 5880);

    /* --- Write output --- */
    char path[512];
    snprintf(path, sizeof(path), "%s/lans_frame_%s.bin", outdir, name);

    FILE* fp = fopen(path, "wb");
    if (!fp) {
        fprintf(stderr, "Cannot open %s\n", path);
        return 1;
    }
    fwrite(frame, 1, 6000, fp);
    fclose(fp);

    printf("Frame written: %s (6000 bytes)\n", path);
    if (strcmp(pattern, "random") == 0) {
        printf("  name=%s FID=%d TOI=%d pattern=%s seed=0x%08X\n",
               name, fid, toi, pattern, seed);
    } else {
        printf("  name=%s FID=%d TOI=%d pattern=%s\n",
               name, fid, toi, pattern);
    }

    return 0;
}
