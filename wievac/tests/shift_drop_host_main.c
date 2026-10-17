#include "edge_result_v5_pipeline.h"

#include <stdio.h>
#include <string.h>

/* Regression for dropped windows: during an environment-shift transition the
 * pipeline held the last score but set UNKNOWN, the encoder rejects UNKNOWN
 * with a score, and up to three windows were never sent (Pi showed STALE). */

static const uint8_t k_mac[EDGE_RESULT_V5_MAC_BYTES] = {2U, 0U, 0U, 0U, 0U, 1U};

static void prepare(edge_result_pipeline_t *pipeline)
{
    edge_result_pipeline_config_t config;
    edge_result_link_state_t *link;
    uint16_t bin;
    memset(&config, 0, sizeof(config));
    edge_result_pipeline_config_defaults(&config);
    config.device_id = 1U;
    config.node_id = 1U;
    config.boot_id = 1U;
    config.window_duration_us = 1000000U;
    config.stale_after_ms = 10000.0f;
    edge_result_pipeline_init(pipeline, &config);
    edge_result_pipeline_register_link(pipeline, 1U, 1U, 1U, k_mac);
    edge_result_pipeline_reset_link_boot(pipeline, 1U, 9U);
    link = &pipeline->links[0];
    link->baseline_ready = true;
    link->baseline_learning_enabled = true;
    link->baseline_state = EDGE_RESULT_BASELINE_STABLE;
    link->baseline_center = 10.0f;
    link->baseline_mad = 0.2f;
    link->baseline_confidence = 80.0f;
    link->baseline_version = 3U;
    link->temporal_noise_scale = 1.0f;
    link->temporal_noise_confidence = 80.0f;
    link->temporal_noise_last_update_us = 42U;
    link->cap_locked = true;
    link->cap_base = 1200.0f;
    link->width_score = 100.0f;
    link->width_valid = true;
    for (bin = 0U; bin < EDGE_RESULT_V5_REF_BINS; ++bin) {
        link->cap_ref_bins[bin] = 10.0f;
    }
}

static int push(edge_result_pipeline_t *pipeline, uint32_t seq, uint64_t timestamp_us,
                uint8_t amplitude, edge_result_v5_t *out)
{
    edge_result_csi_record_t record;
    uint16_t pair;
    memset(&record, 0, sizeof(record));
    record.link_id = 1U;
    record.boot_id = 9U;
    record.timestamp_us = timestamp_us;
    record.tx_sequence = seq;
    memcpy(record.source_mac, k_mac, sizeof(k_mac));
    record.csi_length = 104U;
    record.gain_scale = 1.0f;
    for (pair = 0U; pair < 52U; ++pair) {
        record.csi[pair * 2U + 1U] = amplitude;
    }
    if (!edge_result_pipeline_submit_csi(pipeline, &record)) {
        return -1;
    }
    return edge_result_pipeline_process_one(pipeline, out) ? 1 : 0;
}

int main(void)
{
    static edge_result_pipeline_t pipeline;
    edge_result_v5_t result;
    uint64_t timestamp = 5000000ULL;
    uint32_t seq = 1U;
    int frame;
    int emitted = 0;
    int unknown_with_score = 0;
    int shift_windows = 0;
    int held_degraded = 0;
    prepare(&pipeline);
    /* 20 s at the baseline level, then 40 s at a quiet higher level that the
     * formula treats as an environment shift (candidate / rebase path). */
    for (frame = 0; frame < 6000; ++frame) {
        const uint8_t amplitude = frame < 2000 ? 10U : 14U;
        const int rc = push(&pipeline, seq, timestamp, amplitude, &result);
        if (rc < 0) {
            printf("SUBMIT_FAIL frame=%d\n", frame);
            return 1;
        }
        if (rc > 0) {
            ++emitted;
            if (result.state == EDGE_RESULT_STATE_UNKNOWN && result.score_valid) {
                ++unknown_with_score;
            }
            if (result.reason_code == EDGE_RESULT_REASON_ENVIRONMENT_SHIFT) {
                ++shift_windows;
                if (result.state == EDGE_RESULT_STATE_DEGRADED && result.score_valid) {
                    ++held_degraded;
                }
            }
        }
        ++seq;
        timestamp += 10000ULL;
    }
    printf("SHIFT emitted=%d unknown_with_score=%d shift_windows=%d held_degraded=%d\n",
           emitted, unknown_with_score, shift_windows, held_degraded);
    return 0;
}
