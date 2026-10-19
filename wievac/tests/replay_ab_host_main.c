#include "edge_result_v5_pipeline.h"
#include "tiny_ai_gatekeeper.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

/* Replays recorded CSI (52 tone amplitudes + applied gain scale) through the
 * real RX pipeline from a cold boot, exactly as firmware would see it, and
 * prints one line per emitted window. Built twice by evaluate_ab_replay.py:
 * once with the NOT_READY weights (AI off) and once with a trained model.
 *
 * stdin frame (little endian, 225 bytes):
 *   u64 ts_us, u32 tx_sequence, f32 applied_scale, f32 amp[52], u8 label
 * label: 0 EMPTY, 1 OCCUPIED, 2 EXCLUDED (operator label at that frame). */

static const uint8_t k_tone[52] = {
    6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26,
    27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47,
    48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58
};
static const uint8_t k_mac[EDGE_RESULT_V5_MAC_BYTES] = {2U, 0U, 0U, 0U, 0U, 3U};

static int read_frame(uint64_t *ts, uint32_t *seq, float *scale, float amp[52], uint8_t *label)
{
    unsigned char buf[225];
    if (fread(buf, 1, sizeof(buf), stdin) != sizeof(buf)) {
        return 0;
    }
    memcpy(ts, buf, 8);
    memcpy(seq, buf + 8, 4);
    memcpy(scale, buf + 12, 4);
    memcpy(amp, buf + 16, 52 * 4);
    *label = buf[224];
    return 1;
}

int main(int argc, char **argv)
{
    static edge_result_pipeline_t pipeline;
    edge_result_pipeline_config_t config;
    edge_result_v5_t result;
    uint64_t ts;
    uint32_t seq;
    float scale;
    float amp[52];
    uint8_t label;
    uint8_t last_label = 2U;
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
#endif
    memset(&config, 0, sizeof(config));
    edge_result_pipeline_config_defaults(&config);
    config.device_id = 1U;
    config.node_id = 3U;
    config.boot_id = 1U;
    config.window_duration_us = 1000000U;
    config.stale_after_ms = 10000.0f;
    /* argv[1]: boot settle seconds, as CONFIG_WIEVAC_BOOT_SETTLE_S. */
    config.boot_settle_us = argc > 1 ? (uint64_t)atoi(argv[1]) * 1000000ULL : 0U;
    if (!edge_result_pipeline_init(&pipeline, &config) ||
        !edge_result_pipeline_register_link(&pipeline, 3U, 1U, 3U, k_mac) ||
        !edge_result_pipeline_reset_link_boot(&pipeline, 3U, 7U)) {
        fprintf(stderr, "setup failed\n");
        return 1;
    }
    printf("MODEL %s\n", tiny_ai_gate_model_version() ? tiny_ai_gate_model_version() : "NOT_READY");
    while (read_frame(&ts, &seq, &scale, amp, &label)) {
        edge_result_csi_record_t record;
        unsigned i;
        memset(&record, 0, sizeof(record));
        record.link_id = 3U;
        record.boot_id = 7U;
        record.timestamp_us = ts;
        record.tx_sequence = seq;
        memcpy(record.source_mac, k_mac, sizeof(k_mac));
        record.csi_length = 128U;
        record.gain_scale = scale > 0.0f ? scale : 1.0f;
        record.channel = 11U;
        for (i = 0; i < 52U; ++i) {
            float raw = amp[i] / record.gain_scale;
            int value = (int)(raw + 0.5f);
            if (value > 127) {
                value = 127;
            }
            if (value < 0) {
                value = 0;
            }
            /* imag, real pairs: put the magnitude on the real part. */
            record.csi[k_tone[i] * 2U + 1U] = (uint8_t)(int8_t)value;
        }
        last_label = label;
        if (!edge_result_pipeline_submit_csi(&pipeline, &record)) {
            continue;
        }
        while (edge_result_pipeline_process_one(&pipeline, &result)) {
            const edge_result_link_state_t *link = edge_result_pipeline_link(&pipeline, 3U);
            printf("W %" PRIu64 " %u %d %d %.2f %u %.4f %d %d %d %.2f %d %d %s %.3f\n",
                   result.window_end_us, (unsigned)last_label,
                   (int)result.state, result.score_valid ? 1 : 0,
                   result.score_valid ? (double)result.local_passability_score : -1.0,
                   (unsigned)link->baseline_version, (double)link->baseline_center,
                   link->baseline_ready ? 1 : 0, (int)link->baseline_state,
                   link->cap_locked ? 1 : 0, (double)link->width_score,
                   (int)result.reason_code, (int)result.model_state,
                   result.transition_state[0] ? result.transition_state : "-",
                   (double)result.ai_correction);
        }
    }
    return 0;
}
