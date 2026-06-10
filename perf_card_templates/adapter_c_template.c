/* Adapter template for the LSIS-AFS perf-card harness — C.
 *
 * Replace the three TODO blocks (handshake identity strings + decoder
 * bodies) with your decoder's specifics. Everything else (protocol I/O,
 * SB1 info-bit convention) is boilerplate.
 *
 * Build (single .c, no dependencies beyond libc):
 *
 *     cc -O2 -std=c11 -Wall -o my_adapter adapter_c_template.c
 *
 * Run via the harness:
 *
 *     python ../perf_card.py run --decoder ./my_adapter
 *
 * Compatibility notes:
 *   - All integers on the wire are little-endian. This template targets
 *     little-endian hosts (x86_64, AArch64); on big-endian add byte-swap.
 *   - On Windows, set stdin/stdout to binary mode before any I/O:
 *         _setmode(_fileno(stdin),  _O_BINARY);
 *         _setmode(_fileno(stdout), _O_BINARY);
 *   - The handshake JSON is hand-emitted (no JSON library needed). If
 *     you change identity strings, escape any embedded quotes/backslashes.
 */

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define PROTOCOL_VERSION "1.0"

/* Info-bit counts per wire code_id. */
static const uint32_t N_INFO[3] = {9, 1200, 870};   /* SB1 / SF2 / SF3 */

/* ── TODO 1: adapter identity ─────────────────────────────────────────
 * Edit these strings to describe your decoder. They land in the algo
 * card's identity block and are what `perf-card compare` reports.
 */
#define ADAPTER_NAME      "my-decoder"
#define ADAPTER_VERSION   "0.1.0"
/* `supports_codes` array (any subset of SB1, SF2, SF3): edit below. */
#define LDPC_ALGORITHM    "TODO: e.g., min-sum, sum-product BP, …"
#define LDPC_EARLY_TERM   "TODO: e.g., syndrome check every iter, …"
#define SB1_DECODER_NAME  "my-bch-decoder"
#define SB1_DECODER_CLASS "soft_ML"     /* hard_ML | soft_ML | BDD | other */
#define SB1_ALGORITHM     "TODO: e.g., exhaustive ML over LLR, BMA, …"

static void emit_handshake_ack(FILE *out) {
    /* Construct HandshakeAck JSON. Length-prefixed; format must match
     * perf_card.py's _recv_length_prefixed_json. */
    char buf[1024];
    int n = snprintf(buf, sizeof(buf),
        "{"
          "\"type\":\"handshake_ack\","
          "\"protocol_version\":\"" PROTOCOL_VERSION "\","
          "\"adapter\":{"
            "\"name\":\"" ADAPTER_NAME "\","
            "\"version\":\"" ADAPTER_VERSION "\","
            "\"supports_codes\":[\"SB1\",\"SF2\",\"SF3\"],"
            "\"ldpc\":{"
              "\"algorithm\":\"" LDPC_ALGORITHM "\","
              "\"early_termination\":\"" LDPC_EARLY_TERM "\""
            "},"
            "\"sb1\":{"
              "\"name\":\"" SB1_DECODER_NAME "\","
              "\"decoder_class\":\"" SB1_DECODER_CLASS "\","
              "\"algorithm\":\"" SB1_ALGORITHM "\""
            "}"
          "}"
        "}");
    if (n < 0 || (size_t)n >= sizeof(buf)) {
        fprintf(stderr, "[adapter] handshake JSON too long\n");
        exit(1);
    }
    uint32_t len = (uint32_t)n;
    fwrite(&len, 4, 1, out);
    fwrite(buf, 1, n, out);
    fflush(out);
}

static void do_handshake(FILE *in, FILE *out) {
    uint32_t n;
    if (fread(&n, 4, 1, in) != 1) {
        fprintf(stderr, "[adapter] no handshake from harness\n");
        exit(1);
    }
    /* Read and discard request body — we don't act on its fields here. */
    char discard[4096];
    while (n > 0) {
        size_t take = n < sizeof(discard) ? n : sizeof(discard);
        if (fread(discard, 1, take, in) != take) {
            fprintf(stderr, "[adapter] short handshake request\n");
            exit(1);
        }
        n -= (uint32_t)take;
    }
    emit_handshake_ack(out);
}

/* ── TODO 2: decoder implementations ──────────────────────────────────
 * Replace each function body with your real decoder. Each writes the
 * decoded info bits into out_info[] and reports a status code:
 *     0 = ok, 1 = not_converged (decoder produced bits but didn't
 *     internally satisfy convergence), 2 = error (decoder problem;
 *     harness will surface and abort).
 * `iters_used` is informational — pass 0 for non-iterative codes.
 */

static uint8_t decode_sb1(const float *llrs, uint16_t max_iters,
                          uint8_t *out_info /* 9 bytes */,
                          uint16_t *iters_used) {
    (void)llrs; (void)max_iters;
    /* TODO: implement. Placeholder returns all-zero info bits. */
    memset(out_info, 0, 9);
    *iters_used = 0;
    return 0;  /* status = ok */
}

static uint8_t decode_sf2(const float *llrs, uint16_t max_iters,
                          uint8_t *out_info /* 1200 bytes */,
                          uint16_t *iters_used) {
    (void)llrs;
    /* TODO: implement. Placeholder. */
    memset(out_info, 0, 1200);
    *iters_used = max_iters;
    return 0;
}

static uint8_t decode_sf3(const float *llrs, uint16_t max_iters,
                          uint8_t *out_info /* 870 bytes */,
                          uint16_t *iters_used) {
    (void)llrs;
    /* TODO: implement. Placeholder. */
    memset(out_info, 0, 870);
    *iters_used = max_iters;
    return 0;
}

/* ── TODO 3 (optional): SB1 info-bit packing ──────────────────────────
 * The standard pins this layout — DO NOT change. Available for decoders
 * that recover (FID, TOI) directly rather than 9 raw bits.
 */
static void pack_sb1_info(uint8_t fid_val, uint8_t toi_val,
                          uint8_t out_info[9]) {
    out_info[0] = (fid_val >> 1) & 1;
    out_info[1] = fid_val & 1;
    for (int i = 0; i < 7; ++i) {
        out_info[2 + i] = (toi_val >> (6 - i)) & 1;
    }
}

/* ── Protocol I/O — boilerplate ────────────────────────────────────── */

int main(void) {
    setvbuf(stdout, NULL, _IOFBF, 65536);
    do_handshake(stdin, stdout);

    uint8_t code_id;
    while (fread(&code_id, 1, 1, stdin) == 1) {
        uint16_t max_iters;
        float    sigma_sq;
        uint32_t n_bits;
        if (fread(&max_iters, 2, 1, stdin) != 1 ||
            fread(&sigma_sq,  4, 1, stdin) != 1 ||
            fread(&n_bits,    4, 1, stdin) != 1) {
            fprintf(stderr, "[adapter] short request header\n");
            return 1;
        }
        (void)sigma_sq;

        float *llrs = (float *)malloc((size_t)n_bits * sizeof(float));
        if (!llrs) { fprintf(stderr, "[adapter] OOM\n"); return 1; }
        if (fread(llrs, sizeof(float), n_bits, stdin) != n_bits) {
            fprintf(stderr, "[adapter] short LLR payload\n");
            free(llrs);
            return 1;
        }

        uint32_t n_info = N_INFO[code_id];
        uint8_t *info = (uint8_t *)malloc(n_info);
        if (!info) { fprintf(stderr, "[adapter] OOM\n"); free(llrs); return 1; }

        uint16_t iters_used = 0;
        uint8_t  status;
        switch (code_id) {
            case 0: status = decode_sb1(llrs, max_iters, info, &iters_used); break;
            case 1: status = decode_sf2(llrs, max_iters, info, &iters_used); break;
            case 2: status = decode_sf3(llrs, max_iters, info, &iters_used); break;
            default:
                fprintf(stderr, "[adapter] unknown code_id %u\n", code_id);
                free(llrs); free(info);
                return 1;
        }

        fwrite(&status,     1, 1, stdout);
        fwrite(&iters_used, 2, 1, stdout);
        fwrite(&n_info,     4, 1, stdout);
        fwrite(info,        1, n_info, stdout);
        fflush(stdout);

        free(llrs);
        free(info);
    }

    return 0;
}
