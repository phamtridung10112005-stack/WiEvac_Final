#include "edge_result_v5_pipeline.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Boot settle: for the first settle_s seconds after the first frame the RX
 * must publish UNKNOWN / WARMING_UP and learn nothing, even while someone is
 * moving next to the node; afterwards the formula learns from settled frames.
 * argv[1] = settle seconds. One line per window:
 *   W <end_s> <state> <reason> <score_valid> <baseline_ready> <baseline_version> <transition> */

static const uint8_t k_mac[EDGE_RESULT_V5_MAC_BYTES] = {2U, 0U, 0U, 0U, 0U, 1U};

int main(int argc, char **argv)
{
    static edge_result_pipeline_t pipeline;
    edge_result_pipeline_config_t config;
    edge_result_v5_t result;
    const unsigned settle_s = argc > 1 ? (unsigned)atoi(argv[1]) : 0U;
    uint64_t timestamp = 5000000ULL;
    uint32_t seq = 1U;
    uint32_t rng = 12345U;
    int frame;
    memset(&config, 0, sizeof(config));
    edge_result_pipeline_config_defaults(&config);
    config.device_id = 1U;
    config.node_id = 1U;
    config.boot_id = 1U;
    config.window_duration_us = 1000000U;
    config.stale_after_ms = 10000.0f;
    config.boot_settle_us = (uint64_t)settle_s * 1000000ULL;
    if (!edge_result_pipeline_init(&pipeline, &config) ||
        !edge_result_pipeline_register_link(&pipeline, 1U, 1U, 1U, k_mac) ||
        !edge_result_pipeline_reset_link_boot(&pipeline, 1U, 9U)) {
        return 1;
    }
    /* 0-120 s: large swings (a person walking by the node); then 240 s of a
     * steady empty corridor with small noise. 100 frames/s. */
    for (frame = 0; frame < 36000; ++frame) {
        edge_result_csi_record_t record;
        uint16_t pair;
        int amplitude;
        rng = rng * 1103515245U + 12345U;
        if (frame < 12000) {
            amplitude = 10 + (int)((rng >> 16) % 13U) - 6 + ((frame / 70) % 2 ? 8 : 0);
        } else {
            amplitude = 20 + (int)((rng >> 16) % 3U) - 1;
        }
        memset(&record, 0, sizeof(record));
        record.link_id = 1U;
        record.boot_id = 9U;
        record.timestamp_us = timestamp;
        record.tx_sequence = seq;
        memcpy(record.source_mac, k_mac, sizeof(k_mac));
        record.csi_length = 128U;
        record.gain_scale = 1.0f;
        for (pair = 0U; pair < 64U; ++pair) {
            record.csi[pair * 2U + 1U] = (uint8_t)amplitude;
        }
        if (!edge_result_pipeline_submit_csi(&pipeline, &record)) {
            return 2;
        }
        while (edge_result_pipeline_process_one(&pipeline, &result)) {
            const edge_result_link_state_t *link = edge_result_pipeline_link(&pipeline, 1U);
            printf("W %.1f %d %d %d %d %u %s\n",
                   (double)(result.window_end_us - 5000000ULL) / 1e6,
                   (int)result.state, (int)result.reason_code, result.score_valid ? 1 : 0,
                   link->baseline_ready ? 1 : 0, (unsigned)link->baseline_version,
                   result.transition_state);
        }
        ++seq;
        timestamp += 10000ULL;
    }
    return 0;
}
