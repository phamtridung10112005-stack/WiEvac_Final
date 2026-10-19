#include "edge_result_v5_pipeline.h"

#include <stdio.h>
#include <string.h>

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
    link->baseline_mad = 1.0f;
    link->baseline_confidence = 80.0f;
    link->baseline_version = 9U;
    link->temporal_noise_scale = 1.0f;
    link->temporal_noise_confidence = 80.0f;
    link->temporal_noise_last_update_us = 42U;
    link->cap_locked = true;
    link->cap_base = 1200.0f;
    link->width_score = 100.0f;
    link->width_valid = true;
    link->cap_quiet_windows = 7U;
    for (bin = 0U; bin < EDGE_RESULT_V5_REF_BINS; ++bin) {
        link->cap_ref_bins[bin] = 10.0f;
    }
}

static int push(edge_result_pipeline_t *pipeline, uint32_t seq, uint64_t timestamp_us,
                edge_result_v5_t *out, uint8_t amplitude)
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

static void run_case(const char *name, uint32_t missing, uint64_t extra_hole_us)
{
    static edge_result_pipeline_t pipeline;
    edge_result_v5_t result;
    const edge_result_link_state_t *link;
    const uint64_t t0 = 5000000ULL;
    uint64_t timestamp = t0;
    uint32_t seq = 1U;
    int index;
    int closed = 0;
    int learned;
    memset(&result, 0, sizeof(result));
    prepare(&pipeline);
    for (index = 0; index < 90 && !closed; ++index) {
        if (index == 20) {
            seq += missing;
            timestamp += extra_hole_us;
        }
        if (timestamp >= t0 + 1000000ULL) {
            break;
        }
        if (push(&pipeline, seq, timestamp, &result, 10U) < 0) {
            printf("CASE %s submit_fail\n", name);
            return;
        }
        ++seq;
        timestamp += 10000ULL;
    }
    if (push(&pipeline, seq, t0 + 1000000ULL, &result, 10U) < 0) {
        printf("CASE %s close_fail\n", name);
        return;
    }
    link = &pipeline.links[0];
    learned = link->temporal_noise_last_update_us != 42U ||
              link->baseline_version != 9U ||
              link->cap_quiet_windows != 7U;
    printf("CASE %s valid=%d state=%d reason=%d score=%.3f learned=%d gap=%u\n",
           name, result.score_valid ? 1 : 0, (int)result.state, (int)result.reason_code,
           result.local_passability_score, learned, result.sequence_gap);
}

static int feed_window(edge_result_pipeline_t *pipeline, uint64_t *timestamp, uint32_t *seq,
                      uint32_t missing, uint64_t extra_hole_us, edge_result_v5_t *out)
{
    const uint64_t start = *timestamp;
    int index;
    for (index = 0; index < 80; ++index) {
        if (index == 10 && missing > 0U) {
            *seq += missing;
            *timestamp += extra_hole_us;
        }
        if (push(pipeline, *seq, *timestamp, out, 20U) < 0) {
            return -1;
        }
        *seq += 1U;
        *timestamp += 10000ULL;
    }
    if (!edge_result_pipeline_flush_link(pipeline, 1U, out)) {
        return -1;
    }
    *timestamp = start + 1000000ULL;
    return 0;
}

/* Amplitude sits well above the old baseline so the shift/rebase path runs.
 * boot_unverified holds the static-profile flag at STABLE; this test only
 * watches rebase counters. */
static void run_rebase(const char *name, int windows, int mild_every, int heavy_index)
{
    static edge_result_pipeline_t pipeline;
    edge_result_v5_t result;
    edge_result_link_state_t *link;
    uint64_t timestamp = 8000000ULL;
    uint32_t seq = 1U;
    int index;
    prepare(&pipeline);
    pipeline.config.stale_after_ms = 1000.0f;
    link = &pipeline.links[0];
    link->baseline_version = 3U;
    link->boot_unverified = true;
    link->static_flag = EDGE_RESULT_STATIC_STABLE;
    for (index = 0; index < windows; ++index) {
        uint32_t missing = 0U;
        uint64_t hole = 0U;
        if (index == heavy_index) {
            missing = 5U;
        } else if (mild_every > 0 && ((index + 1) % mild_every) == 0) {
            missing = 1U;
            hole = 10000ULL;
        }
        if (feed_window(&pipeline, &timestamp, &seq, missing, hole, &result) != 0) {
            printf("REBASE %s feed_fail\n", name);
            return;
        }
    }
    link = &pipeline.links[0];
    printf("REBASE %s version=%u state=%d pause=%u dirty=%u count=%u stuck=%u flag=%u\n",
           name, link->baseline_version, (int)link->baseline_state,
           link->rebase_candidate_pause_windows, link->rebase_candidate_dirty_windows,
           link->rebase_candidate_count, link->stuck_low_score_windows, link->static_flag);
}

int main(void)
{
    run_case("mild1", 1U, 10000ULL);
    run_case("mild2", 2U, 20000ULL);
    run_case("loss5", 5U, 0ULL);
    run_case("gap200", 1U, 190000ULL);
    /* Longer than the 30 s person-free rebase requirement. */
    /* 25 quiet windows are not enough under the 30 s rule. */
    run_rebase("short", 25, 0, -1);
    run_rebase("clean", 40, 0, -1);
    run_rebase("mixed", 40, 3, -1);
    run_rebase("heavy", 7, 0, 6);
    return 0;
}
