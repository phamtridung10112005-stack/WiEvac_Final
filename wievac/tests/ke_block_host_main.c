#include "edge_result_v5_pipeline.h"
#include "noise_filter.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static const uint8_t k_mac[EDGE_RESULT_V5_MAC_BYTES] = {2U, 0U, 0U, 0U, 0U, 1U};

static edge_result_ai_output_t disagree_ai(const edge_result_features_t *features,
                                          float formula_score,
                                          const edge_result_link_state_t *link,
                                          void *context)
{
    edge_result_ai_output_t out;
    (void)features;
    (void)formula_score;
    (void)link;
    (void)context;
    memset(&out, 0, sizeof(out));
    out.score = 0.0f;
    out.accepted = true;
    out.disagreement = true;
    out.state = EDGE_RESULT_MODEL_READY;
    return out;
}

static void prepare(edge_result_pipeline_t *pipeline, int cap_locked, float cap_ref, int disagree)
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
    if (disagree) {
        config.tiny_ai = disagree_ai;
        config.model_version = "host";
        config.model_hash = "host";
        config.model_target = "rx-tiny-ai";
        config.feature_schema_version = "8";
    }
    edge_result_pipeline_init(pipeline, &config);
    edge_result_pipeline_register_link(pipeline, 1U, 1U, 1U, k_mac);
    edge_result_pipeline_reset_link_boot(pipeline, 1U, 9U);
    noise_filter_reset();
    link = &pipeline->links[0];
    link->baseline_ready = true;
    link->baseline_learning_enabled = true;
    link->baseline_state = EDGE_RESULT_BASELINE_STABLE;
    link->baseline_center = 40.0f;
    link->baseline_mad = 5.0f;
    link->baseline_confidence = 90.0f;
    link->baseline_version = 9U;
    link->temporal_noise_scale = 0.15f;
    link->temporal_noise_confidence = 90.0f;
    link->doppler_ratio_scale = 0.05f;
    link->cap_locked = cap_locked != 0;
    link->cap_base = 1200.0f;
    link->width_score = 100.0f;
    link->width_valid = true;
    for (bin = 0U; bin < EDGE_RESULT_V5_REF_BINS; ++bin) {
        link->cap_ref_bins[bin] = cap_ref;
    }
}

static int push(edge_result_pipeline_t *pipeline, uint32_t seq, uint64_t timestamp_us,
                int8_t amplitude, edge_result_v5_t *out)
{
    edge_result_csi_record_t record;
    uint16_t pair;
    memset(&record, 0, sizeof(record));
    record.link_id = 1U;
    record.boot_id = 9U;
    record.timestamp_us = timestamp_us;
    record.tx_sequence = seq;
    memcpy(record.source_mac, k_mac, sizeof(k_mac));
    record.csi_length = 128U;
    record.gain_scale = 1.0f;
    for (pair = 0U; pair < 64U; ++pair) {
        record.csi[pair * 2U + 1U] = (uint8_t)amplitude;
    }
    if (!edge_result_pipeline_submit_csi(pipeline, &record)) {
        return -1;
    }
    return edge_result_pipeline_process_one(pipeline, out) ? 1 : 0;
}

static void run_case(const char *name, int cap_locked, float cap_ref, int disagree)
{
    static edge_result_pipeline_t pipeline;
    edge_result_v5_t result;
    const edge_result_link_state_t *link;
    const uint64_t t0 = 5000000ULL;
    int index;
    int closed = 0;
    memset(&result, 0, sizeof(result));
    prepare(&pipeline, cap_locked, cap_ref, disagree);
    for (index = 0; index <= 100 && !closed; ++index) {
        const float phase = 6.28318530718f * 2.0f * (float)index / 100.0f;
        const int8_t amplitude = (int8_t)lroundf(40.0f + 8.0f * sinf(phase));
        const int rc = push(&pipeline, (uint32_t)(index + 1), t0 + (uint64_t)index * 10000ULL,
                            amplitude, &result);
        if (rc < 0) {
            printf("CASE %s submit_fail\n", name);
            return;
        }
        closed = rc;
    }
    link = &pipeline.links[0];
    printf("CASE %s state=%d reason=%d score=%.3f ke=%.3f kev=%d occ=%u block=%.2f dop=%.3f tm=%.3f closed=%d\n",
           name, (int)result.state, (int)result.reason_code, result.local_passability_score,
           link->ke_last_p, link->ke_last_valid ? 1 : 0, link->occupied_windows,
           result.blocking_evidence, result.doppler_ratio, result.features.temporal_motion, closed);
}

int main(void)
{
    run_case("open", 1, 20.0f, 0);
    run_case("blocked", 1, 141.9f, 0);
    run_case("unlocked", 0, 141.9f, 0);
    run_case("disagree", 1, 20.0f, 1);
    return 0;
}
