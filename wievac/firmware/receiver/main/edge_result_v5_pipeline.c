#include "edge_result_v5_pipeline.h"
#include "formula_flex.h"
#include "noise_filter.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#define EDGE_RESULT_V5_HEADER_SIZE 14U
#define EDGE_RESULT_V5_MAX_WIRE_PAYLOAD 700U
#define EDGE_RESULT_V5_MAX_WIRE_SIZE (EDGE_RESULT_V5_HEADER_SIZE + EDGE_RESULT_V5_MAX_WIRE_PAYLOAD)
#define EDGE_RESULT_V5_WIRE_PAYLOAD_SIZE EDGE_RESULT_V5_MAX_WIRE_PAYLOAD
#define EDGE_RESULT_V5_WIRE_SIZE EDGE_RESULT_V5_MAX_WIRE_SIZE
/* Keep the producer metadata byte-for-byte aligned with the active Pi
 * contract.  This is the Formula Flex implementation version, not a C
 * implementation label; changing it would make otherwise valid packets
 * appear to come from a different scoring contract. */
#define EDGE_RESULT_V5_FORMULA_DEFAULT "formula-flex-v5.3-rx-median-mad"
#define EDGE_RESULT_V5_MODEL_DEFAULT "NOT_READY"
#define EDGE_RESULT_V5_MODEL_TARGET "rx-tiny-ai"
#define EDGE_RESULT_V5_SCHEMA_DEFAULT "7"
#define EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR 0.0001f
#define EDGE_RESULT_V5_SEVERE_WINDOW_TOLERANCE 1U
#define EDGE_RESULT_V5_LIGHT_PAUSE_TOLERANCE 4U
#define EDGE_RESULT_V5_SCORE_FILTER_WALK_TAU 3.0f
#define EDGE_RESULT_V5_SCORE_FILTER_CROWD_TAU 7.0f
#define EDGE_RESULT_V5_SCORE_FILTER_RADIO_TAU 20.0f
#define EDGE_RESULT_V5_SCORE_FILTER_HOLD_WINDOWS 3U
#define EDGE_RESULT_V5_SCORE_FILTER_CORROBORATE_WINDOWS 2U
#define EDGE_RESULT_V5_OCCUPANCY_MEMORY_WINDOWS 25U
#define EDGE_RESULT_V5_STUCK_LOW_WINDOWS 12U
#define EDGE_RESULT_V5_STUCK_SCORE_LIMIT 35.0f
#define EDGE_RESULT_V5_REBASE_QUIET_WINDOWS 12U
#define EDGE_RESULT_V5_REBASE_DIRTY_RATIO 0.20f
#define EDGE_RESULT_V5_BOOTSTRAP_QUIET_WINDOWS 12U
/* This classifies a transport fault as severe for bounded cancellation. It
 * never makes a gapped window eligible for temporal or baseline learning. */
#define EDGE_RESULT_V5_BOOTSTRAP_MAX_LOSS_RATIO 0.10f
/* Compatibility aliases retained for contract/source checks only; active
 * decisions below use link-local envelopes and history instead. */
#define EDGE_RESULT_V5_C_REFERENCE_BASELINE_DELTA_MOTION FORMULA_FLEX_REFERENCE_BASELINE_DELTA_MOTION
#define EDGE_RESULT_V5_C_REFERENCE_SCORE_BLOCK FORMULA_FLEX_REFERENCE_SCORE_BLOCK

static const char *const k_default_corridor = "unknown-corridor";
static const char *const k_default_session = "software-only";
static uint32_t s_boot_counter = 1U;

static float clampf(float value, float lower, float upper)
{
    if (value < lower) {
        return lower;
    }
    if (value > upper) {
        return upper;
    }
    return value;
}

static bool finite_float(float value)
{
    return isfinite((double)value) != 0;
}

static uint32_t saturating_add_u32(uint32_t left, uint32_t right)
{
    return UINT32_MAX - left < right ? UINT32_MAX : left + right;
}

static void copy_text(char *destination, size_t capacity, const char *source)
{
    if (destination == NULL || capacity == 0U) {
        return;
    }
    if (source == NULL) {
        source = "";
    }
    size_t length = strlen(source);
    if (length >= capacity) {
        length = capacity - 1U;
    }
    memcpy(destination, source, length);
    destination[length] = '\0';
}

/* Link-local empty-spectrum envelope for r. Not a house/school Hz threshold. */
static void learn_doppler_ratio_scale(edge_result_link_state_t *link, float ratio, float alpha)
{
    if (link == NULL || !finite_float(ratio) || ratio < 0.0f || !finite_float(alpha)) {
        return;
    }
    if (link->doppler_ratio_scale <= 0.0f) {
        link->doppler_ratio_scale = fmaxf(ratio, EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR);
        return;
    }
    link->doppler_ratio_scale = fmaxf(EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR,
        link->doppler_ratio_scale + alpha * (ratio - link->doppler_ratio_scale));
}

static void reset_score_filter(edge_result_link_state_t *link)
{
    if (link == NULL) {
        return;
    }
    link->last_local_passability_score = 0.0f;
    link->last_local_passability_valid = false;
    link->score_filter_history_count = 0U;
    link->score_filter_history_next = 0U;
    link->score_filter_baseline_version = 0U;
    memset(link->score_filter_history, 0, sizeof(link->score_filter_history));
}

static float median_score_history(const edge_result_link_state_t *link)
{
    if (link == NULL || link->score_filter_history_count == 0U) {
        return NAN;
    }
    float values[EDGE_RESULT_V5_SCORE_HISTORY_CAPACITY];
    const uint8_t count = link->score_filter_history_count;
    memcpy(values, link->score_filter_history, (size_t)count * sizeof(values[0]));
    for (uint8_t i = 1U; i < count; ++i) {
        const float value = values[i];
        uint8_t j = i;
        while (j > 0U && values[j - 1U] > value) {
            values[j] = values[j - 1U];
            --j;
        }
        values[j] = value;
    }
    if ((count & 1U) != 0U) {
        return values[count / 2U];
    }
    return 0.5f * (values[count / 2U - 1U] + values[count / 2U]);
}

static float filter_passability_score(edge_result_link_state_t *link,
                                      float raw_score,
                                      uint32_t baseline_version,
                                      bool corroborated,
                                      bool occupied_memory)
{
    if (link == NULL || !finite_float(raw_score)) {
        return NAN;
    }
    if (link->score_filter_baseline_version != 0U &&
        link->score_filter_baseline_version != baseline_version) {
        reset_score_filter(link);
    }
    link->score_filter_baseline_version = baseline_version;
    link->score_filter_hold_windows = 0U;
    link->score_filter_history[link->score_filter_history_next] = raw_score;
    link->score_filter_history_next = (uint8_t)((link->score_filter_history_next + 1U) %
                                                EDGE_RESULT_V5_SCORE_HISTORY_CAPACITY);
    if (link->score_filter_history_count < EDGE_RESULT_V5_SCORE_HISTORY_CAPACITY) {
        ++link->score_filter_history_count;
    }
    if (corroborated) {
        if (link->score_corroborated_windows < UINT16_MAX) {
            ++link->score_corroborated_windows;
        }
    } else {
        link->score_corroborated_windows = 0U;
    }
    const float target = median_score_history(link);
    if (!finite_float(target)) {
        return NAN;
    }
    if (!link->last_local_passability_valid) {
        link->last_local_passability_score = target;
        link->last_local_passability_valid = true;
        return target;
    }
    /* Median rejects a one-window V-shape. The time constant then follows
     * pedestrian crossing (~3 s), crowd recovery (~7 s), or a slow radio
     * leak when only the level moved. This is not a 1-2 point clamp. */
    const float delta = target - link->last_local_passability_score;
    float tau = EDGE_RESULT_V5_SCORE_FILTER_WALK_TAU;
    if (!corroborated) {
        tau = link->score_filter_history_count < 3U
                  ? 0.0f
                  : EDGE_RESULT_V5_SCORE_FILTER_RADIO_TAU;
    } else if (occupied_memory && delta > 0.0f) {
        tau = EDGE_RESULT_V5_SCORE_FILTER_CROWD_TAU;
    } else if (link->score_corroborated_windows <
               EDGE_RESULT_V5_SCORE_FILTER_CORROBORATE_WINDOWS) {
        tau = EDGE_RESULT_V5_SCORE_FILTER_CROWD_TAU;
    }
    const float alpha = tau <= 0.0f ? 0.0f : clampf(1.0f / tau, 0.0f, 1.0f);
    link->last_local_passability_score = clampf(
        link->last_local_passability_score + alpha * delta, 0.0f, 100.0f);
    return link->last_local_passability_score;
}

static bool emit_held_passability(edge_result_link_state_t *link, edge_result_v5_t *result)
{
    if (link == NULL || result == NULL || !link->last_local_passability_valid) {
        return false;
    }
    if (link->score_filter_hold_windows >= EDGE_RESULT_V5_SCORE_FILTER_HOLD_WINDOWS) {
        return false;
    }
    ++link->score_filter_hold_windows;
    result->local_passability_score = link->last_local_passability_score;
    result->filtered_passability_score = link->last_local_passability_score;
    result->filtered_passability_valid = true;
    result->score_valid = true;
    result->state = EDGE_RESULT_STATE_DEGRADED;
    result->uncertainty_score = 100.0f;
    return true;
}

static bool publish_filtered_score(edge_result_link_state_t *link,
                                   edge_result_v5_t *result,
                                   bool corroborated,
                                   bool occupied_memory)
{
    if (link == NULL || result == NULL || !finite_float(result->local_passability_score)) {
        return false;
    }
    const float raw_score = clampf(result->local_passability_score, 0.0f, 100.0f);
    result->raw_evidence_score = finite_float(result->formula_score)
                                     ? result->formula_score : raw_score;
    result->raw_evidence_valid = true;
    const float filtered_score = filter_passability_score(link, raw_score,
                                                          link->baseline_version,
                                                          corroborated,
                                                          occupied_memory);
    if (!finite_float(filtered_score)) {
        return false;
    }
    result->local_passability_score = filtered_score;
    result->filtered_passability_score = filtered_score;
    result->filtered_passability_valid = true;
    result->score_valid = true;
    return true;
}

static edge_result_link_state_t *find_link(edge_result_pipeline_t *pipeline, uint32_t link_id)
{
    if (pipeline == NULL) {
        return NULL;
    }
    for (uint8_t index = 0U; index < pipeline->link_count; ++index) {
        if (pipeline->links[index].configured && pipeline->links[index].identity.link_id == link_id) {
            return &pipeline->links[index];
        }
    }
    return NULL;
}

static void attribute_invalid_frame(edge_result_link_state_t *link)
{
    if (link == NULL) {
        return;
    }
    /* A rejected frame breaks the adjacent-sample amplitude relationship. */
    link->previous_amplitude_valid = false;
    if (link->window_start_us != 0U) {
        ++link->window_sample_count;
        ++link->window_invalid_count;
        link->baseline_window_eligible = false;
    } else {
        ++link->pending_invalid_count;
    }
}

static const edge_result_link_state_t *find_link_const(const edge_result_pipeline_t *pipeline,
                                                        uint32_t link_id)
{
    if (pipeline == NULL) {
        return NULL;
    }
    for (uint8_t index = 0U; index < pipeline->link_count; ++index) {
        if (pipeline->links[index].configured && pipeline->links[index].identity.link_id == link_id) {
            return &pipeline->links[index];
        }
    }
    return NULL;
}

static bool queue_pop(edge_result_bounded_queue_t *queue, edge_result_csi_record_t *record)
{
    if (queue == NULL || record == NULL || queue->count == 0U) {
        return false;
    }
    *record = queue->records[queue->head];
    queue->head = (uint16_t)((queue->head + 1U) % queue->capacity);
    --queue->count;
    return true;
}

static bool queue_push(edge_result_bounded_queue_t *queue,
                       const edge_result_csi_record_t *record)
{
    if (queue == NULL || record == NULL || queue->capacity == 0U) {
        return false;
    }
    if (queue->count >= queue->capacity) {
        ++queue->drop_count;
        return false;
    }
    queue->records[queue->tail] = *record;
    queue->tail = (uint16_t)((queue->tail + 1U) % queue->capacity);
    ++queue->count;
    return true;
}

static void clear_rebase_candidate(edge_result_link_state_t *link)
{
    if (link == NULL) {
        return;
    }
    link->rebase_candidate_count = 0U;
    link->rebase_candidate_windows = 0U;
    link->rebase_candidate_stable_windows = 0U;
    link->rebase_candidate_pause_windows = 0U;
    link->rebase_candidate_dirty_windows = 0U;
    link->rebase_candidate_start_us = 0U;
    link->rebase_candidate_sum_motion = 0.0f;
    link->rebase_candidate_sum_spread = 0.0f;
}

static void pause_rebase_candidate(edge_result_link_state_t *link)
{
    if (link == NULL) {
        return;
    }
    /* A dirty window must not count as persistence, but it also must not
     * erase earlier quiet samples after one or two CSI blips. */
    if (link->rebase_candidate_pause_windows < UINT16_MAX) {
        ++link->rebase_candidate_pause_windows;
    }
    if (link->rebase_candidate_dirty_windows < UINT16_MAX) {
        ++link->rebase_candidate_dirty_windows;
    }
    copy_text(link->baseline_update_reason,
              sizeof(link->baseline_update_reason),
              "candidate_paused_quality_window");
}

static void reset_window(edge_result_link_state_t *link, uint64_t next_start_us)
{
    if (link == NULL) {
        return;
    }
    /* Pending counters belong to the next window. Including them here would
     * count one pre-window fault twice: once while pending and again after it
     * is transferred into that next window. */
    const uint32_t transport_total = saturating_add_u32(link->window_sample_count,
                                                         link->window_sequence_gap);
    const float transport_loss_ratio = transport_total == 0U ? 0.0f :
        (float)link->window_sequence_gap / (float)transport_total;
    const bool severe_window = link->window_invalid_count != 0U ||
                               link->window_queue_drop_count != 0U ||
                               transport_loss_ratio > EDGE_RESULT_V5_BOOTSTRAP_MAX_LOSS_RATIO;
    if (severe_window) {
        /* Invalid input, queue loss, or a large sequence discontinuity cannot
         * train or rebase. A bounded pause preserves earlier clean evidence
         * across one noisy window, but repeated severe windows cancel it. */
        if (link->consecutive_severe_windows < UINT16_MAX) {
            ++link->consecutive_severe_windows;
        }
        if (link->consecutive_severe_windows <= EDGE_RESULT_V5_SEVERE_WINDOW_TOLERANCE) {
            if (!link->baseline_ready) {
                copy_text(link->baseline_update_reason,
                          sizeof(link->baseline_update_reason),
                          "bootstrap_paused_severe_gap");
            } else {
                pause_rebase_candidate(link);
            }
        } else {
            if (!link->baseline_ready) {
                link->baseline_count = 0U;
                link->baseline_short_count = 0U;
                link->baseline_long_count = 0U;
            }
            clear_rebase_candidate(link);
            link->shift_candidate_windows = 0U;
            link->shift_candidate_start_us = 0U;
            link->occupied_windows = 0U;
            link->occupied_start_us = 0U;
            if (link->baseline_state == EDGE_RESULT_BASELINE_SHIFT_CANDIDATE ||
                link->baseline_state == EDGE_RESULT_BASELINE_REBASE_PENDING) {
                link->baseline_state = link->baseline_ready
                                           ? EDGE_RESULT_BASELINE_STABLE
                                           : EDGE_RESULT_BASELINE_CANDIDATE;
            }
            copy_text(link->baseline_update_reason,
                      sizeof(link->baseline_update_reason),
                      "candidate_cancelled_invalid_window");
        }
    } else if (link->window_sequence_gap > 0U) {
        link->consecutive_severe_windows = 0U;
        /* A bounded gap is still an invalid learning window. Preserve prior
         * clean bootstrap evidence, and pause an active rebase so gap time
         * cannot count as environmental persistence. */
        if (!link->baseline_ready) {
            copy_text(link->baseline_update_reason,
                      sizeof(link->baseline_update_reason),
                      "bootstrap_paused_sequence_gap");
        } else if (link->rebase_candidate_count > 0U ||
                   link->baseline_state == EDGE_RESULT_BASELINE_SHIFT_CANDIDATE ||
                   link->baseline_state == EDGE_RESULT_BASELINE_REBASE_PENDING) {
            pause_rebase_candidate(link);
        }
    } else {
        link->consecutive_severe_windows = 0U;
    }
    const uint32_t pending_invalid_count = link->pending_invalid_count;
    const uint32_t pending_queue_drop_count = link->pending_queue_drop_count;
    link->window_start_us = next_start_us;
    /* Preserve the accounting invariant for invalid frames submitted before
     * a valid record opens the window. */
    link->window_sample_count = pending_invalid_count;
    link->window_invalid_count = pending_invalid_count;
    link->pending_invalid_count = 0U;
    link->window_sequence_gap = 0U;
    link->window_queue_drop_count = pending_queue_drop_count;
    link->pending_queue_drop_count = 0U;
    link->baseline_window_eligible = link->window_invalid_count == 0U &&
                                    link->window_queue_drop_count == 0U;
    link->window_received_count = 0U;
    link->window_sum_amplitude = 0.0f;
    link->window_sum_delta = 0.0f;
    link->window_sum_spread = 0.0f;
    link->window_sum_motion = 0.0f;
    link->window_sum_rssi = 0.0f;
    link->window_sum_noise = 0.0f;
    link->window_last_timestamp_us = 0U;
    link->window_sum_interval_us = 0U;
    link->window_sum_interval_sq_us = 0.0;
    link->window_interval_count = 0U;
    link->doppler_count = 0U;
}

static void sync_baseline_metadata(edge_result_v5_t *result,
                                   const edge_result_link_state_t *link)
{
    if (result == NULL || link == NULL) {
        return;
    }
    /* Synchronize after transitions so metadata describes this result. */
    result->baseline_state = link->baseline_state;
    result->baseline_version = link->baseline_version;
    result->baseline_confidence = link->baseline_confidence;
    result->drift_state = link->baseline_state;
    copy_text(result->baseline_update_reason,
              sizeof(result->baseline_update_reason),
              link->baseline_update_reason);
}

static edge_result_v5_t make_result(edge_result_pipeline_t *pipeline,
                                    edge_result_link_state_t *link,
                                    uint64_t end_us)
{
    edge_result_v5_t result;
    memset(&result, 0, sizeof(result));
    if (pipeline == NULL || link == NULL) {
        result.state = EDGE_RESULT_STATE_UNKNOWN;
        result.reason_code = EDGE_RESULT_REASON_INVALID_CSI;
        return result;
    }
    result.identity = link->identity;
    sync_baseline_metadata(&result, link);
    result.identity.window_seq = link->next_window_seq == 0U ? 1U : link->next_window_seq;
    if (link->next_window_seq < UINT32_MAX) {
        ++link->next_window_seq;
    }
    result.window_start_us = link->window_start_us;
    result.window_end_us = end_us > link->window_start_us ? end_us : link->window_start_us + 1U;
    result.rx_timestamp_us = result.window_end_us;
    const uint64_t latest_sample_us = link->window_last_timestamp_us > 0U
                                          ? link->window_last_timestamp_us : result.window_end_us;
    const uint64_t age_us = result.window_end_us > latest_sample_us
                                ? result.window_end_us - latest_sample_us : 0U;
    result.age_ms = age_us > UINT32_MAX * 1000ULL ? UINT32_MAX : (uint32_t)(age_us / 1000ULL);
    result.sample_count = link->window_sample_count;
    result.invalid_count = link->window_invalid_count;
    result.queue_drop_count = link->window_queue_drop_count;
    result.sequence_gap = link->window_sequence_gap;
    result.features.sample_count = result.sample_count;
    result.features.invalid_count = result.invalid_count;
    result.features.queue_drop_count = result.queue_drop_count;
    result.features.sequence_gap = result.sequence_gap;
    const uint32_t valid_samples = result.sample_count >= result.invalid_count
                                       ? result.sample_count - result.invalid_count : 0U;
    if (valid_samples > 0U) {
        const float denominator = (float)valid_samples;
        result.features.common_amplitude = link->window_sum_amplitude / denominator;
        result.features.amplitude_delta = link->window_sum_delta / denominator;
        result.features.robust_spread = link->window_sum_spread / denominator;
        result.features.temporal_motion = link->window_sum_motion / denominator;
        result.features.rssi_dbm = link->window_sum_rssi / denominator;
        result.features.noise_floor_dbm = link->window_sum_noise / denominator;
    }
    /* Convert temporal motion to a link-local robust z-like measure.  The
     * learned scale is updated only after complete, uncontaminated windows. */
    const float temporal_scale = fmaxf(link->temporal_noise_scale, EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR);
    if (valid_samples > 0U && finite_float(result.features.temporal_motion)) {
        result.features.temporal_motion /= temporal_scale;
    }
    const uint32_t total = saturating_add_u32(
        saturating_add_u32(result.sample_count, result.invalid_count),
        result.queue_drop_count);
    const uint32_t quality_denominator = saturating_add_u32(result.sample_count,
                                                             result.queue_drop_count);
    result.quality_score = total == 0U || quality_denominator == 0U ? 0.0f :
                           clampf((float)valid_samples / (float)quality_denominator * 100.0f,
                                  0.0f, 100.0f);
    /* Keep the fixed V5 wire slot for compatibility, but bind it to one
     * source only: gaps between accepted CSI callbacks. Queue drops are
     * exposed separately and RX-to-Pi UDP loss is not measurable here. */
    const uint32_t csi_sequence_total = saturating_add_u32(result.sample_count,
                                                            result.sequence_gap);
    result.packet_loss_ratio = csi_sequence_total == 0U ? 0.0f :
                               clampf((float)result.sequence_gap /
                                      (float)csi_sequence_total, 0.0f, 1.0f);
    if (link->window_interval_count > 1U) {
        const double mean_interval = (double)link->window_sum_interval_us /
                                     (double)link->window_interval_count;
        const double variance = fmax(0.0, link->window_sum_interval_sq_us /
                                               (double)link->window_interval_count -
                                               mean_interval * mean_interval);
        result.jitter_ms = (float)(sqrt(variance) / 1000.0);
    } else {
        result.jitter_ms = 0.0f;
    }
    result.uncertainty_score = clampf(5.0f + (100.0f - result.quality_score) * 0.70f +
                                      result.packet_loss_ratio * 25.0f, 0.0f, 100.0f);
    copy_text(result.formula_version, sizeof(result.formula_version),
              pipeline->config.formula_version != NULL ? pipeline->config.formula_version : EDGE_RESULT_V5_FORMULA_DEFAULT);
    copy_text(result.model_version, sizeof(result.model_version),
              pipeline->config.model_version != NULL ? pipeline->config.model_version : EDGE_RESULT_V5_MODEL_DEFAULT);
    copy_text(result.model_hash, sizeof(result.model_hash), pipeline->config.model_hash);
    copy_text(result.feature_schema_version, sizeof(result.feature_schema_version),
              pipeline->config.feature_schema_version != NULL ? pipeline->config.feature_schema_version : EDGE_RESULT_V5_SCHEMA_DEFAULT);
    result.model_state = EDGE_RESULT_MODEL_NOT_READY;
    copy_text(result.transition_state, sizeof(result.transition_state), "STABLE");
    result.occupancy_evidence_valid = false;
    result.blocking_evidence_valid = false;
    result.raw_evidence_valid = false;
    result.filtered_passability_valid = false;
    result.doppler_valid = false;
    result.doppler_ratio = 0.0f;
    result.doppler_fs_hz = 0.0f;
    result.doppler_samples = 0U;
    {
        noise_filter_doppler_t doppler = {0};
        if (noise_filter_doppler_ratio(link->doppler_amp, link->doppler_t,
                                       link->doppler_count, &doppler) &&
            doppler.valid) {
            result.doppler_valid = true;
            result.doppler_ratio = doppler.ratio;
            result.doppler_fs_hz = doppler.fs_hz;
            result.doppler_samples = doppler.sample_count;
        }
    }
    /* Occupancy freeze uses Doppler z vs this link's learned r, not r as a
     * house-tuned multiplier on the displayed %. FFT-invalid does not lock
     * OCCUPIED so a new hall can rebase. */
    const float occupancy_motion =
        (result.doppler_valid && finite_float(result.doppler_ratio) &&
         finite_float(result.features.temporal_motion))
            ? result.features.temporal_motion * result.doppler_ratio
            : result.features.temporal_motion;
    result.state = EDGE_RESULT_STATE_UNKNOWN;
    result.reason_code = EDGE_RESULT_REASON_WARMING_UP;
    const uint64_t stale_limit_us = finite_float(pipeline->config.stale_after_ms) &&
                                    pipeline->config.stale_after_ms > 0.0f
                                        ? (uint64_t)(pipeline->config.stale_after_ms * 1000.0f)
                                        : 0U;
    /* Model-history timestamps describe estimator updates, not CSI transport
     * freshness. A stable baseline remains valid while fresh clean windows
     * arrive, even when no adaptation is needed. */
    const bool stale = finite_float(pipeline->config.stale_after_ms) &&
                       pipeline->config.stale_after_ms > 0.0f &&
                       (float)result.age_ms > pipeline->config.stale_after_ms;
    const bool bootstrap_window_eligible = !link->baseline_ready &&
        result.sample_count > 0U && result.invalid_count == 0U &&
        result.queue_drop_count == 0U &&
        result.sequence_gap == 0U && link->baseline_window_eligible &&
        finite_float(result.features.temporal_motion);
    if (stale || result.sample_count == 0U || result.invalid_count > 0U || result.queue_drop_count > 0U ||
        result.sequence_gap > 0U || !link->baseline_ready) {
        if (stale) {
            result.state = EDGE_RESULT_STATE_UNKNOWN;
            result.reason_code = EDGE_RESULT_REASON_STALE;
            result.score_valid = false;
            result.uncertainty_score = 100.0f;
            return result;
        }
        if (bootstrap_window_eligible && result.quality_score > 0.0f) {
            const float raw_motion = result.features.temporal_motion * temporal_scale;
            const float alpha = fmaxf(EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR,
                                     1.0f - fminf(link->temporal_noise_confidence / 100.0f, 1.0f));
            link->temporal_noise_scale = fmaxf(0.0001f,
                link->temporal_noise_scale + alpha * (raw_motion - link->temporal_noise_scale));
            link->temporal_noise_confidence = fminf(100.0f,
                link->temporal_noise_confidence + 2.0f);
            if (link->temporal_clean_windows < UINT16_MAX) ++link->temporal_clean_windows;
            link->temporal_noise_last_update_us = result.window_end_us;
            if (result.doppler_valid && finite_float(result.doppler_ratio)) {
                learn_doppler_ratio_scale(link, result.doppler_ratio, alpha);
            }
        }
        if (bootstrap_window_eligible && result.quality_score > 0.0f &&
            link->temporal_noise_confidence >= 50.0f &&
            link->temporal_clean_windows >= EDGE_RESULT_V5_BOOTSTRAP_QUIET_WINDOWS &&
            /* The motion feature is already normalized by this link's
             * learned temporal-noise scale. Keep the gate link-relative and
             * bounded so confidence cannot collapse it to zero. */
            /* Permit a bounded amount of motion relative to this link's
             * learned noise envelope. Tighten only from 1.5x to 1.0x as
             * confidence grows; the prior 1.0x-to-0.5x rule could make
             * automatic bootstrap self-block once confidence increased. */
            occupancy_motion <=
                fmaxf(EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR,
                      1.5f - 0.5f *
                          fminf(link->temporal_noise_confidence / 100.0f, 1.0f))) {
            formula_flex_update_baseline(link, &pipeline->config,
                                         result.features.common_amplitude,
                                         result.features.amplitude_delta);
        }
        if (result.queue_drop_count > 0U) {
            result.reason_code = EDGE_RESULT_REASON_QUEUE_DROP;
        } else if (result.sequence_gap > 0U) {
            result.reason_code = EDGE_RESULT_REASON_SEQUENCE_GAP;
        } else if (result.invalid_count > 0U || result.sample_count == 0U) {
            result.reason_code = EDGE_RESULT_REASON_INVALID_CSI;
        }
        if (link->baseline_ready && emit_held_passability(link, &result)) {
            sync_baseline_metadata(&result, link);
            return result;
        }
        result.score_valid = false;
        result.state = EDGE_RESULT_STATE_UNKNOWN;
        result.uncertainty_score = 100.0f;
        sync_baseline_metadata(&result, link);
        return result;
    }
    const edge_result_formula_fn formula = pipeline->config.formula != NULL
                                                ? pipeline->config.formula : formula_flex_default_formula;
    const float formula_score = formula(&result.features, link, pipeline->config.hook_context);
    if (!finite_float(formula_score)) {
        reset_score_filter(link);
        result.score_valid = false;
        result.state = EDGE_RESULT_STATE_UNKNOWN;
        result.reason_code = EDGE_RESULT_REASON_INVALID_CSI;
        result.uncertainty_score = 100.0f;
        sync_baseline_metadata(&result, link);
        return result;
    }
    /* Displayed % is Formula Flex, a link-local index. Do not keep scaling
     * it by r: one body does not grow occupied area, and r is not a
     * corridor-calibrated occupancy fraction. */
    result.formula_score = clampf(formula_score, 0.0f, 100.0f);
    result.raw_evidence_score = result.formula_score;
    result.filtered_passability_score = result.formula_score;
    result.raw_evidence_valid = true;
    result.filtered_passability_valid = true;
    /* Occupancy reflects robust level displacement; blocking must have an
     * independent temporal/spread signal so a stable environmental level
     * shift cannot become an occupancy conclusion by itself. */
    const float baseline_delta = formula_flex_baseline_deviation(link, result.features.common_amplitude);
    if (!finite_float(baseline_delta)) {
        reset_score_filter(link);
        result.score_valid = false;
        result.state = EDGE_RESULT_STATE_UNKNOWN;
        result.reason_code = EDGE_RESULT_REASON_INVALID_CSI;
        result.raw_evidence_valid = false;
        result.filtered_passability_valid = false;
        result.uncertainty_score = 100.0f;
        sync_baseline_metadata(&result, link);
        return result;
    }
    /* baseline_delta is already normalized by the link's MAD envelope. */
    const float normalized_level = baseline_delta;
    const float confidence_ratio = fminf(link->baseline_confidence / 100.0f, 1.0f);
    const float shift_z = 1.0f + (1.0f - confidence_ratio);
    const float occupancy_z = shift_z +
                              (1.0f - fminf(link->temporal_noise_confidence / 100.0f, 1.0f));
    const float shift_envelope = shift_z;
    /* r / this-link empty envelope. Same z idea as temporal_motion; the
     * scale is learned here so a school hall is not judged by the house. */
    const float doppler_motion =
        (result.doppler_valid && finite_float(result.doppler_ratio) &&
         link->doppler_ratio_scale > 0.0f)
            ? result.doppler_ratio /
                  fmaxf(link->doppler_ratio_scale, EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR)
            : NAN;
    const bool doppler_walking =
        finite_float(doppler_motion) && doppler_motion >= occupancy_z;
    /* Occupancy evidence is Doppler z vs this link, not a corridor area %. */
    const float excess_doppler = finite_float(doppler_motion)
                                     ? fmaxf(0.0f, doppler_motion - 1.0f) : 0.0f;
    result.occupancy_evidence = clampf(100.0f * excess_doppler /
                                       (1.0f + excess_doppler), 0.0f, 100.0f);
    const float blocking_stress =
        excess_doppler +
        fmaxf(0.0f, (100.0f - result.quality_score) / 100.0f);
    result.blocking_evidence = clampf(100.0f * blocking_stress /
                                      (1.0f + blocking_stress), 0.0f, 100.0f);
    result.occupancy_evidence_valid = true;
    result.blocking_evidence_valid = true;
    result.local_passability_score = result.formula_score;
    result.score_valid = true;
    /* Freeze OCCUPIED only when amplitude motion AND this link's spectrum
     * look like walking. Spectrum-typical-empty is a baseline error. */
    const float rebase_trigger_z = fmaxf(0.50f, 0.50f * shift_z);
    const bool level_shift = normalized_level >= shift_z;
    const bool rebase_candidate_shift = normalized_level >= rebase_trigger_z;
    const bool independent_blocking_signal =
        finite_float(result.features.temporal_motion) &&
        result.features.temporal_motion >= occupancy_z &&
        doppler_walking;
    if (independent_blocking_signal) {
        if (link->occupied_start_us == 0U) {
            link->occupied_start_us = result.window_start_us;
        }
        if (link->occupied_windows < UINT32_MAX) {
            ++link->occupied_windows;
        }
        if (link->occupied_windows >= 2U) {
            link->occupancy_memory_windows = EDGE_RESULT_V5_OCCUPANCY_MEMORY_WINDOWS;
        }
        link->recovery_windows = 0U;
        link->stuck_low_score_windows = 0U;
    } else if (link->occupancy_memory_windows > 0U) {
        --link->occupancy_memory_windows;
    }
    if (result.formula_score < EDGE_RESULT_V5_STUCK_SCORE_LIMIT &&
        !independent_blocking_signal) {
        if (link->stuck_low_score_windows < UINT16_MAX) {
            ++link->stuck_low_score_windows;
        }
    } else if (independent_blocking_signal ||
               result.formula_score >= EDGE_RESULT_V5_STUCK_SCORE_LIMIT) {
        link->stuck_low_score_windows = 0U;
    }
    const bool corroborated = independent_blocking_signal;
    const bool occupied_memory = link->occupancy_memory_windows > 0U ||
                                 link->occupied_windows > 0U;
    if (baseline_delta >= rebase_trigger_z && (rebase_candidate_shift || level_shift)) {
        if (link->shift_candidate_start_us == 0U) {
            link->shift_candidate_start_us = result.window_start_us;
        }
        ++link->shift_candidate_windows;
        link->baseline_state = EDGE_RESULT_BASELINE_SHIFT_CANDIDATE;
        link->baseline_confidence = fminf(link->baseline_confidence, 65.0f);
        const uint64_t shift_elapsed_us = result.window_end_us > link->shift_candidate_start_us
                                              ? result.window_end_us - link->shift_candidate_start_us : 0U;
        const uint64_t required_shift_us = stale_limit_us > 0U
                                                ? stale_limit_us
                                                : (uint64_t)pipeline->config.window_duration_us;
        if (shift_elapsed_us >= required_shift_us) {
            const uint64_t occupied_elapsed_us = result.window_end_us > link->occupied_start_us
                                                      ? result.window_end_us - link->occupied_start_us : 0U;
            if (link->occupied_start_us != 0U && occupied_elapsed_us >= required_shift_us &&
                independent_blocking_signal) {
                link->baseline_state = EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED;
                copy_text(link->baseline_update_reason, sizeof(link->baseline_update_reason), "persistent_occupancy_evidence");
            } else {
                link->baseline_state = EDGE_RESULT_BASELINE_REBASE_PENDING;
                /* Do not reuse aggregate blocking evidence here: it includes
                 * the same normalized baseline displacement that opened the
                 * shift/rebase path. Cancellation needs independent motion. */
                const bool strong_contradiction =
                    doppler_walking ||
                    link->occupancy_memory_windows > 0U;
                /* A large level error is exactly when rebase is needed.
                 * Occupancy is decided by motion, not by distance from the
                 * (possibly wrong) old baseline, and not by CSI frequency
                 * shape of an empty hall. */
                const bool clean_input = result.invalid_count == 0U &&
                    result.sequence_gap == 0U && result.queue_drop_count == 0U &&
                    result.quality_score > 0.0f && link->baseline_window_eligible &&
                    link->temporal_noise_confidence > 0.0f &&
                    !doppler_walking;

                if (strong_contradiction && link->occupancy_memory_windows > 0U &&
                    independent_blocking_signal) {
                    /* Strong occupancy/blocking evidence keeps the old
                     * baseline and invalidates this environmental candidate. */
                    clear_rebase_candidate(link);
                    copy_text(link->baseline_update_reason,
                              sizeof(link->baseline_update_reason),
                              "candidate_cancelled_contradictory_evidence");
                } else if (!clean_input) {
                    pause_rebase_candidate(link);
                    if (link->rebase_candidate_pause_windows >
                        EDGE_RESULT_V5_LIGHT_PAUSE_TOLERANCE) {
                        clear_rebase_candidate(link);
                        link->baseline_state = EDGE_RESULT_BASELINE_STABLE;
                        copy_text(link->baseline_update_reason,
                                  sizeof(link->baseline_update_reason),
                                  "candidate_cancelled_repeated_quality_window");
                    }
                } else {
                    const uint16_t prior_count = link->rebase_candidate_count;
                    float candidate_center = result.features.common_amplitude;
                    float candidate_spread = 0.0f;
                    if (prior_count > 0U) {
                        candidate_center = 0.0f;
                        for (uint16_t i = 0U; i < prior_count; ++i) {
                            candidate_center += link->rebase_candidate_samples[i];
                        }
                        candidate_center /= (float)prior_count;
                        for (uint16_t i = 0U; i < prior_count; ++i) {
                            candidate_spread += fabsf(link->rebase_candidate_samples[i] -
                                                      candidate_center);
                        }
                        candidate_spread /= (float)prior_count;
                    }
                    const float envelope = fmaxf(EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR,
                                                 fmaxf(link->temporal_noise_scale,
                                                       1.4826f * link->baseline_mad));
                    const float candidate_motion_mean = prior_count == 0U ? 0.0f :
                        link->rebase_candidate_sum_motion /
                        (float)link->rebase_candidate_windows;
                    const uint64_t required_elapsed_us =
                        (uint64_t)pipeline->config.window_duration_us *
                        (uint64_t)EDGE_RESULT_V5_REBASE_QUIET_WINDOWS;
                    const uint16_t required_candidate_count = EDGE_RESULT_V5_REBASE_QUIET_WINDOWS > 3U
                        ? (uint16_t)(EDGE_RESULT_V5_REBASE_QUIET_WINDOWS - 2U) : 3U;
                    const float candidate_spread_limit = envelope;
                    const float candidate_motion_limit = 0.75f * occupancy_z;
                    /* New-environment evidence is a quiet, stable amplitude
                     * center vs this link's envelope.  Do not require a small
                     * intra-packet CSI shape: that quantity is not occupancy
                     * and is not comparable to occupancy_z. */
                    const bool candidate_environment_evidence =
                        link->temporal_noise_confidence >= 50.0f &&
                        candidate_spread <= candidate_spread_limit &&
                        (prior_count == 0U ||
                         fabsf(result.features.common_amplitude - candidate_center) <=
                             candidate_spread_limit) &&
                        candidate_motion_mean <= candidate_motion_limit &&
                        occupancy_motion <= candidate_motion_limit &&
                        (link->occupancy_memory_windows == 0U ||
                         link->stuck_low_score_windows >= EDGE_RESULT_V5_STUCK_LOW_WINDOWS);
                    if (!candidate_environment_evidence) {
                        pause_rebase_candidate(link);
                        if (link->rebase_candidate_pause_windows >
                            EDGE_RESULT_V5_LIGHT_PAUSE_TOLERANCE) {
                            clear_rebase_candidate(link);
                            link->baseline_state = EDGE_RESULT_BASELINE_STABLE;
                            copy_text(link->baseline_update_reason,
                                      sizeof(link->baseline_update_reason),
                                      "candidate_cancelled_repeated_quality_window");
                        }
                    } else {
                        link->rebase_candidate_pause_windows = 0U;
                        if (link->rebase_candidate_start_us == 0U) {
                            link->rebase_candidate_start_us = result.window_start_us;
                        }
                        if (link->rebase_candidate_count <
                            (uint16_t)(sizeof(link->rebase_candidate_samples) /
                                       sizeof(link->rebase_candidate_samples[0]))) {
                            link->rebase_candidate_samples[link->rebase_candidate_count++] =
                                result.features.common_amplitude;
                        }
                        if (link->rebase_candidate_windows < UINT32_MAX) {
                            ++link->rebase_candidate_windows;
                        }
                        if (link->rebase_candidate_stable_windows < UINT32_MAX) {
                            ++link->rebase_candidate_stable_windows;
                        }
                        link->rebase_candidate_sum_motion += occupancy_motion;
                        link->rebase_candidate_sum_spread += result.features.robust_spread;
                        const uint64_t candidate_elapsed_us =
                            result.window_end_us > link->rebase_candidate_start_us
                                ? result.window_end_us - link->rebase_candidate_start_us : 0U;
                        const uint32_t observed = link->rebase_candidate_windows +
                            (uint32_t)link->rebase_candidate_dirty_windows;
                        const float dirty_ratio = observed == 0U ? 0.0f :
                            (float)link->rebase_candidate_dirty_windows / (float)observed;
                        const bool stuck_force =
                            link->stuck_low_score_windows >= EDGE_RESULT_V5_STUCK_LOW_WINDOWS &&
                            !doppler_walking;
                        if (candidate_elapsed_us >= required_elapsed_us &&
                            link->rebase_candidate_count >= required_candidate_count &&
                            link->rebase_candidate_stable_windows >= required_candidate_count &&
                            link->rebase_candidate_pause_windows == 0U &&
                            (link->occupancy_memory_windows == 0U || stuck_force) &&
                            (dirty_ratio <= EDGE_RESULT_V5_REBASE_DIRTY_RATIO || stuck_force)) {
                            formula_flex_promote_rebase_candidate(link);
                        }
                    }
                }
            }
        }
        const float blocking_gate = 100.0f *
            (1.0f - fminf((link->baseline_confidence + link->temporal_noise_confidence) / 200.0f, 1.0f));
        result.local_passability_score = result.formula_score;
        if (link->baseline_state == EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED &&
            normalized_level >= occupancy_z && result.blocking_evidence >=
                blocking_gate) {
            if (!publish_filtered_score(link, &result, corroborated, occupied_memory)) {
                result.score_valid = false;
                result.state = EDGE_RESULT_STATE_UNKNOWN;
                result.reason_code = EDGE_RESULT_REASON_INVALID_CSI;
            } else {
                result.state = EDGE_RESULT_STATE_BLOCKED;
                result.reason_code = EDGE_RESULT_REASON_BLOCKED;
            }
        } else if (emit_held_passability(link, &result)) {
            result.state = EDGE_RESULT_STATE_UNKNOWN;
            result.reason_code = EDGE_RESULT_REASON_ENVIRONMENT_SHIFT;
        } else {
            result.score_valid = false;
            result.state = EDGE_RESULT_STATE_UNKNOWN;
            result.reason_code = EDGE_RESULT_REASON_ENVIRONMENT_SHIFT;
            result.uncertainty_score = 100.0f;
        }
        copy_text(result.transition_state, sizeof(result.transition_state),
                  link->baseline_state == EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED
                      ? "OCCUPIED_OR_BLOCKED" : "ENVIRONMENT_SHIFT");
        sync_baseline_metadata(&result, link);
        return result;
    }
    if (link->baseline_state == EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED) {
        const bool recovery_window = result.invalid_count == 0U &&
                                     result.sequence_gap == 0U &&
                                     result.queue_drop_count == 0U &&
                                     result.quality_score > 0.0f &&
                                     !independent_blocking_signal;
        if (recovery_window) {
            if (link->recovery_windows < UINT16_MAX) ++link->recovery_windows;
            const uint16_t required_recovery = pipeline->config.baseline_min_samples > 3U
                                                   ? pipeline->config.baseline_min_samples
                                                   : 3U;
            if (link->recovery_windows >= required_recovery) {
                link->baseline_state = EDGE_RESULT_BASELINE_STABLE;
                copy_text(link->baseline_update_reason,
                          sizeof(link->baseline_update_reason),
                          "occupancy_recovery_confirmed");
                link->recovery_windows = 0U;
            }
        } else if (independent_blocking_signal) {
            link->recovery_windows = 0U;
        }
    }
    link->shift_candidate_windows = 0U;
    link->shift_candidate_start_us = 0U;
    if (!independent_blocking_signal &&
        link->baseline_state != EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED &&
        link->occupied_windows > 0U &&
        link->recovery_windows >= 3U) {
        link->occupied_windows = 0U;
        link->occupied_start_us = 0U;
        link->recovery_windows = 0U;
    } else if (!independent_blocking_signal &&
               link->baseline_state != EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED &&
               link->occupied_windows > 0U) {
        if (link->recovery_windows < UINT16_MAX) {
            ++link->recovery_windows;
        }
    }
    if (link->stuck_low_score_windows < EDGE_RESULT_V5_STUCK_LOW_WINDOWS) {
        link->rebase_candidate_windows = 0U;
        link->rebase_candidate_stable_windows = 0U;
        link->rebase_candidate_pause_windows = 0U;
        link->rebase_candidate_dirty_windows = 0U;
        link->rebase_candidate_count = 0U;
        link->rebase_candidate_start_us = 0U;
        link->rebase_candidate_sum_motion = 0.0f;
        link->rebase_candidate_sum_spread = 0.0f;
    }
    if (link->baseline_state == EDGE_RESULT_BASELINE_UPDATED) {
        link->baseline_state = EDGE_RESULT_BASELINE_STABLE;
    } else if ((link->baseline_state == EDGE_RESULT_BASELINE_SHIFT_CANDIDATE ||
                link->baseline_state == EDGE_RESULT_BASELINE_REBASE_PENDING) &&
               normalized_level < rebase_trigger_z &&
               link->stuck_low_score_windows < EDGE_RESULT_V5_STUCK_LOW_WINDOWS) {
        /* A candidate that has lost its displacement evidence is no longer a
         * transition; do not leave the link permanently labelled as shifting. */
        clear_rebase_candidate(link);
        link->baseline_state = EDGE_RESULT_BASELINE_STABLE;
        copy_text(link->baseline_update_reason,
                  sizeof(link->baseline_update_reason),
                  "candidate_below_rebase_trigger");
    }
    if (pipeline->config.tiny_ai != NULL) {
        const edge_result_ai_output_t ai = pipeline->config.tiny_ai(
            &result.features, result.formula_score, link, pipeline->config.hook_context);
        result.model_state = ai.state;
        const bool model_attested = pipeline->config.model_target != NULL &&
                                     strcmp(pipeline->config.model_target, EDGE_RESULT_V5_MODEL_TARGET) == 0 &&
                                     pipeline->config.model_version != NULL &&
                                     pipeline->config.model_version[0] != '\0' &&
                                     pipeline->config.model_hash != NULL &&
                                     pipeline->config.model_hash[0] != '\0' &&
                                     pipeline->config.feature_schema_version != NULL &&
                                     strcmp(pipeline->config.feature_schema_version, EDGE_RESULT_V5_SCHEMA_DEFAULT) == 0;
        if (ai.state == EDGE_RESULT_MODEL_READY && ai.accepted && model_attested &&
            !ai.ood && finite_float(ai.score)) {
            /* V5 defines tiny-AI output as a correction in score points, not
             * an absolute replacement.  The host reference uses the same
             * convention and applies this bound before combining scores. */
            float correction = ai.score;
            const float bound = pipeline->config.max_ai_correction > 0.0f
                                    ? pipeline->config.max_ai_correction : 10.0f;
            correction = clampf(correction, -bound, bound);
            result.ai_correction = correction;
            result.disagreement = ai.disagreement || fabsf(correction) >= bound * 0.80f;
            result.local_passability_score = clampf(result.formula_score + correction, 0.0f, 100.0f);
        } else if (ai.ood || ai.state == EDGE_RESULT_MODEL_OOD) {
            result.score_valid = false;
            result.state = EDGE_RESULT_STATE_UNKNOWN;
            result.reason_code = EDGE_RESULT_REASON_OOD;
            result.uncertainty_score = 100.0f;
            result.raw_evidence_valid = false;
            result.filtered_passability_valid = false;
            sync_baseline_metadata(&result, link);
            return result;
        } else if (ai.state == EDGE_RESULT_MODEL_REJECTED || ai.state == EDGE_RESULT_MODEL_READY) {
            result.score_valid = false;
            result.state = EDGE_RESULT_STATE_UNKNOWN;
            result.reason_code = EDGE_RESULT_REASON_MODEL_REJECTED;
            result.uncertainty_score = 100.0f;
            result.raw_evidence_valid = false;
            result.filtered_passability_valid = false;
            sync_baseline_metadata(&result, link);
            return result;
        } else {
            result.reason_code = EDGE_RESULT_REASON_MODEL_NOT_READY;
        }
    } else {
        result.reason_code = EDGE_RESULT_REASON_MODEL_NOT_READY;
    }
    if (result.score_valid) {
        if (!publish_filtered_score(link, &result, corroborated, occupied_memory)) {
            result.score_valid = false;
            result.state = EDGE_RESULT_STATE_UNKNOWN;
            result.reason_code = EDGE_RESULT_REASON_INVALID_CSI;
            result.uncertainty_score = 100.0f;
            result.raw_evidence_valid = false;
            result.filtered_passability_valid = false;
            sync_baseline_metadata(&result, link);
            return result;
        }
    }
    /* Every emitted result advances the limiter's result-to-result clock,
     * including UNKNOWN/DEGRADED results. */
    if (result.window_end_us > link->last_result_window_end_us) {
        link->last_result_window_end_us = result.window_end_us;
    }
    if (result.disagreement) {
        result.state = EDGE_RESULT_STATE_DEGRADED;
        result.reason_code = EDGE_RESULT_REASON_FORMULA_AI_DISAGREEMENT;
        result.uncertainty_score = clampf(result.uncertainty_score +
                                         100.0f * (1.0f -
                                         fminf(link->temporal_noise_confidence / 100.0f, 1.0f)),
                                         0.0f, 100.0f);
    } else if (result.quality_score <= 0.0f ||
               result.uncertainty_score > result.quality_score ||
               link->temporal_noise_confidence <= 0.0f) {
        result.state = EDGE_RESULT_STATE_DEGRADED;
    } else {
        const float blocking_gate = 100.0f *
            (1.0f - fminf((link->baseline_confidence + link->temporal_noise_confidence) / 200.0f, 1.0f));
        if (result.blocking_evidence >= blocking_gate && link->occupied_windows > 0U) {
            result.state = EDGE_RESULT_STATE_BLOCKED;
            result.reason_code = EDGE_RESULT_REASON_BLOCKED;
        } else if (link->occupied_windows > 0U) {
            result.state = EDGE_RESULT_STATE_DEGRADED;
            result.reason_code = EDGE_RESULT_REASON_QUALITY_LOW;
        } else {
            result.state = EDGE_RESULT_STATE_PASSABLE;
            result.reason_code = EDGE_RESULT_REASON_NONE;
        }
    }
    if (result.state == EDGE_RESULT_STATE_PASSABLE && result.quality_score > 0.0f &&
        link->baseline_window_eligible && link->temporal_noise_confidence > 0.0f &&
        link->baseline_state == EDGE_RESULT_BASELINE_STABLE &&
        link->recovery_windows == 0U &&
        result.uncertainty_score <= result.quality_score &&
        normalized_level < shift_envelope &&
        !doppler_walking) {
        /* Commit drift only after this complete window has been scored. */
        formula_flex_update_baseline(link, &pipeline->config,
                                     result.features.common_amplitude,
                                     result.features.amplitude_delta);
    }
    if (result.sample_count > 0U && result.invalid_count == 0U &&
        result.sequence_gap == 0U && result.queue_drop_count == 0U &&
        link->baseline_window_eligible && result.quality_score > 0.0f &&
        finite_float(result.features.temporal_motion) &&
        normalized_level < shift_envelope && link->occupied_windows == 0U) {
        const float raw_motion = result.features.temporal_motion * temporal_scale;
        const float alpha = fmaxf(EDGE_RESULT_V5_TEMPORAL_SAFE_FLOOR,
                                  1.0f - fminf(link->temporal_noise_confidence / 100.0f, 1.0f));
        link->temporal_noise_scale = fmaxf(0.0001f,
            link->temporal_noise_scale + alpha * (raw_motion - link->temporal_noise_scale));
        link->temporal_noise_confidence = fminf(100.0f,
            link->temporal_noise_confidence + (link->temporal_clean_windows < UINT16_MAX ? 2.0f : 0.0f));
        if (link->temporal_clean_windows < UINT16_MAX) ++link->temporal_clean_windows;
        link->temporal_noise_last_update_us = result.window_end_us;
        if (result.doppler_valid && finite_float(result.doppler_ratio)) {
            learn_doppler_ratio_scale(link, result.doppler_ratio, alpha);
        }
    }
    sync_baseline_metadata(&result, link);
    return result;
}

static void update_link(edge_result_pipeline_t *pipeline,
                        edge_result_link_state_t *link,
                        const edge_result_csi_record_t *record)
{
    if (pipeline == NULL || link == NULL || record == NULL) {
        return;
    }
    if (link->window_start_us == 0U) {
        reset_window(link, record->timestamp_us);
    }
    ++link->window_received_count;
    /* sample_count is the total number of admitted frames; invalid_count is
     * its subset, which keeps the V5 invariant invalid_count <= sample_count. */
    ++link->window_sample_count;
    if (link->last_tx_sequence != 0U) {
        const uint32_t delta = record->tx_sequence - link->last_tx_sequence;
        if (delta == 0U || delta >= UINT32_C(0x80000000)) {
            ++link->sequence_reject_total;
            ++link->window_sequence_gap;
            ++link->window_invalid_count;
            link->baseline_window_eligible = false;
            link->previous_amplitude_valid = false;
            return;
        }
        if (delta > 1U) {
            link->window_sequence_gap = saturating_add_u32(link->window_sequence_gap,
                                                            delta - 1U);
            link->baseline_window_eligible = false;
            /* Missing CSI breaks amplitude continuity. This window cannot
             * train, but bounded cancellation at the next boundary decides
             * whether it erases prior clean bootstrap evidence. */
            link->previous_amplitude_valid = false;
        }
    }
    if (link->last_timestamp_us != 0U && record->timestamp_us <= link->last_timestamp_us) {
        ++link->sequence_reject_total;
        ++link->window_invalid_count;
        link->baseline_window_eligible = false;
        link->previous_amplitude_valid = false;
        return;
    }
    link->last_tx_sequence = record->tx_sequence;
    link->last_timestamp_us = record->timestamp_us;
    link->last_rx_timestamp_us = (uint32_t)(record->timestamp_us > UINT32_MAX
                                               ? UINT32_MAX : record->timestamp_us);
    float amplitude = 0.0f;
    float spread = 0.0f;
    noise_filter_input_t filter_input = {
        .csi = record->csi,
        .csi_length = record->csi_length,
        .first_word_invalid = record->first_word_invalid,
    };
    noise_filter_output_t filter_output = {0};
    if (!noise_filter_process(&filter_input, &filter_output)) {
        ++link->invalid_total;
        ++link->window_invalid_count;
        link->baseline_window_eligible = false;
        link->previous_amplitude_valid = false;
        return;
    }
    amplitude = filter_output.amplitude;
    spread = filter_output.robust_spread;
    const float delta = link->previous_amplitude_valid ? amplitude - link->previous_amplitude : 0.0f;
    const float motion = fabsf(delta);
    if (link->previous_amplitude_valid && link->window_last_timestamp_us > 0U &&
        record->timestamp_us > link->window_last_timestamp_us) {
        const uint64_t interval = record->timestamp_us - link->window_last_timestamp_us;
        link->window_sum_interval_us += interval;
        link->window_sum_interval_sq_us += (double)interval * (double)interval;
        ++link->window_interval_count;
    }
    link->window_last_timestamp_us = record->timestamp_us;
    link->previous_amplitude = amplitude;
    link->previous_amplitude_valid = true;
    link->window_sum_amplitude += amplitude;
    link->window_sum_delta += delta;
    link->window_sum_spread += spread;
    link->window_sum_motion += motion;
    link->window_sum_rssi += (float)record->rssi_dbm;
    link->window_sum_noise += (float)record->noise_floor_dbm;
    if (link->doppler_count >= EDGE_RESULT_V5_DOPPLER_CAPACITY) {
        memmove(&link->doppler_amp[0], &link->doppler_amp[1],
                (size_t)(EDGE_RESULT_V5_DOPPLER_CAPACITY - 1U) * sizeof(link->doppler_amp[0]));
        memmove(&link->doppler_t[0], &link->doppler_t[1],
                (size_t)(EDGE_RESULT_V5_DOPPLER_CAPACITY - 1U) * sizeof(link->doppler_t[0]));
        link->doppler_count = EDGE_RESULT_V5_DOPPLER_CAPACITY - 1U;
    }
    link->doppler_amp[link->doppler_count] = amplitude;
    link->doppler_t[link->doppler_count] = record->timestamp_us;
    ++link->doppler_count;
}

void edge_result_pipeline_config_defaults(edge_result_pipeline_config_t *config)
{
    if (config == NULL) {
        return;
    }
    memset(config, 0, sizeof(*config));
    config->window_duration_us = 1000000U;
    config->queue_capacity = EDGE_RESULT_V5_MAX_QUEUE;
    config->baseline_min_samples = 8U;
    config->max_ai_correction = 10.0f;
    config->stale_after_ms = 2000.0f;
    config->corridor_id = k_default_corridor;
    config->session_id = k_default_session;
    config->formula_version = EDGE_RESULT_V5_FORMULA_DEFAULT;
    config->model_version = EDGE_RESULT_V5_MODEL_DEFAULT;
    config->model_target = EDGE_RESULT_V5_MODEL_TARGET;
    config->feature_schema_version = EDGE_RESULT_V5_SCHEMA_DEFAULT;
}

bool edge_result_pipeline_init(edge_result_pipeline_t *pipeline,
                               const edge_result_pipeline_config_t *config)
{
    if (pipeline == NULL) {
        return false;
    }
    edge_result_pipeline_config_t defaults;
    edge_result_pipeline_config_defaults(&defaults);
    pipeline->config = config != NULL ? *config : defaults;
    if (pipeline->config.boot_id == 0U) {
        /* Hardware callers should pass esp_random(); the portable fallback
         * still guarantees an encodable, nonzero stream identity. */
        ++s_boot_counter;
        if (s_boot_counter == 0U) {
            s_boot_counter = 1U;
        }
        pipeline->config.boot_id = s_boot_counter;
    }
    if (pipeline->config.window_duration_us == 0U) {
        pipeline->config.window_duration_us = defaults.window_duration_us;
    }
    if (pipeline->config.queue_capacity == 0U || pipeline->config.queue_capacity > EDGE_RESULT_V5_MAX_QUEUE) {
        pipeline->config.queue_capacity = defaults.queue_capacity;
    }
    if (pipeline->config.baseline_min_samples < 2U ||
        pipeline->config.baseline_min_samples > EDGE_RESULT_V5_MAX_BASELINE_SAMPLES) {
        pipeline->config.baseline_min_samples = defaults.baseline_min_samples;
    }
    if (pipeline->config.max_ai_correction <= 0.0f || !finite_float(pipeline->config.max_ai_correction)) {
        pipeline->config.max_ai_correction = defaults.max_ai_correction;
    }
    if (pipeline->config.corridor_id == NULL) {
        pipeline->config.corridor_id = defaults.corridor_id;
    }
    if (pipeline->config.session_id == NULL) {
        pipeline->config.session_id = defaults.session_id;
    }
    if (pipeline->config.formula_version == NULL) {
        pipeline->config.formula_version = defaults.formula_version;
    }
    if (pipeline->config.model_version == NULL) {
        pipeline->config.model_version = defaults.model_version;
    }
    if (pipeline->config.model_target == NULL) {
        pipeline->config.model_target = defaults.model_target;
    }
    if (pipeline->config.feature_schema_version == NULL) {
        pipeline->config.feature_schema_version = defaults.feature_schema_version;
    }
    memset(&pipeline->queue, 0, sizeof(pipeline->queue));
    pipeline->queue.capacity = pipeline->config.queue_capacity;
    pipeline->link_count = 0U;
    memset(pipeline->links, 0, sizeof(pipeline->links));
    return true;
}

bool edge_result_pipeline_register_link(edge_result_pipeline_t *pipeline,
                                        uint32_t link_id,
                                        uint32_t tx_id,
                                        uint32_t rx_id,
                                        const uint8_t expected_tx_mac[EDGE_RESULT_V5_MAC_BYTES])
{
    if (pipeline == NULL || link_id == 0U || tx_id == 0U || rx_id == 0U ||
        expected_tx_mac == NULL || pipeline->config.boot_id == 0U) {
        return false;
    }
    edge_result_link_state_t *link = find_link(pipeline, link_id);
    uint32_t prior_window_seq = 1U;
    char device_id_text[EDGE_RESULT_V5_MAX_TEXT] = {0};
    char node_id_text[EDGE_RESULT_V5_MAX_TEXT] = {0};
    char tx_id_text[EDGE_RESULT_V5_MAX_TEXT] = {0};
    char rx_id_text[EDGE_RESULT_V5_MAX_TEXT] = {0};
    char link_id_text[EDGE_RESULT_V5_MAX_TEXT] = {0};
    (void)snprintf(device_id_text, sizeof(device_id_text), "device-%" PRIu32, pipeline->config.device_id);
    (void)snprintf(node_id_text, sizeof(node_id_text), "rx-%" PRIu32, pipeline->config.node_id);
    (void)snprintf(tx_id_text, sizeof(tx_id_text), "tx-%" PRIu32, tx_id);
    (void)snprintf(rx_id_text, sizeof(rx_id_text), "rx-%" PRIu32, rx_id);
    (void)snprintf(link_id_text, sizeof(link_id_text), "link-%" PRIu32, link_id);
    if (link == NULL) {
        if (pipeline->link_count >= EDGE_RESULT_V5_MAX_LINKS) {
            return false;
        }
        link = &pipeline->links[pipeline->link_count++];
        memset(link, 0, sizeof(*link));
    } else if (link->next_window_seq != 0U) {
        /* Re-registration is an explicit reset, but never reuses the old
         * window sequence in the same persisted result domain. */
        prior_window_seq = link->next_window_seq;
        copy_text(device_id_text, sizeof(device_id_text), link->identity.device_id_text);
        copy_text(node_id_text, sizeof(node_id_text), link->identity.node_id_text);
        copy_text(tx_id_text, sizeof(tx_id_text), link->identity.tx_id_text);
        copy_text(rx_id_text, sizeof(rx_id_text), link->identity.rx_id_text);
        copy_text(link_id_text, sizeof(link_id_text), link->identity.link_id_text);
        memset(link, 0, sizeof(*link));
    }
    link->configured = true;
    link->identity.device_id = pipeline->config.device_id;
    link->identity.node_id = pipeline->config.node_id;
    link->identity.tx_id = tx_id;
    link->identity.rx_id = rx_id;
    link->identity.link_id = link_id;
    link->identity.boot_id = pipeline->config.boot_id;
    copy_text(link->identity.device_id_text, sizeof(link->identity.device_id_text), device_id_text);
    copy_text(link->identity.node_id_text, sizeof(link->identity.node_id_text), node_id_text);
    copy_text(link->identity.tx_id_text, sizeof(link->identity.tx_id_text), tx_id_text);
    copy_text(link->identity.rx_id_text, sizeof(link->identity.rx_id_text), rx_id_text);
    copy_text(link->identity.link_id_text, sizeof(link->identity.link_id_text), link_id_text);
    copy_text(link->identity.corridor_id, sizeof(link->identity.corridor_id), pipeline->config.corridor_id);
    copy_text(link->identity.session_id, sizeof(link->identity.session_id), pipeline->config.session_id);
    link->next_window_seq = prior_window_seq;
    /* Initial bootstrap is automatic; unresolved later shifts remain
     * fail-closed and are never promoted automatically. */
    link->baseline_learning_enabled = true;
    link->baseline_window_eligible = true;
    link->baseline_state = EDGE_RESULT_BASELINE_CANDIDATE;
    link->explicit_baseline_confirmed = false;
    copy_text(link->baseline_update_reason, sizeof(link->baseline_update_reason), "automatic_candidate");
    memcpy(link->expected_tx_mac, expected_tx_mac, EDGE_RESULT_V5_MAC_BYTES);
    link->expected_tx_mac_valid = true;
    return true;
}

bool edge_result_pipeline_reset_link_boot(edge_result_pipeline_t *pipeline,
                                          uint32_t link_id,
                                          uint32_t boot_id)
{
    edge_result_link_state_t *link = find_link(pipeline, link_id);
    if (link == NULL || boot_id == 0U) {
        return false;
    }
    const uint32_t next_seq = link->next_window_seq == 0U ? 1U : link->next_window_seq;
    const uint8_t expected_mac[EDGE_RESULT_V5_MAC_BYTES] = {
        link->expected_tx_mac[0], link->expected_tx_mac[1], link->expected_tx_mac[2],
        link->expected_tx_mac[3], link->expected_tx_mac[4], link->expected_tx_mac[5]};
    const bool expected_valid = link->expected_tx_mac_valid;
    const uint32_t link_id_copy = link->identity.link_id;
    const uint32_t tx_id = link->identity.tx_id;
    const uint32_t rx_id = link->identity.rx_id;
    char device_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char node_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char tx_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char rx_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char link_id_text[EDGE_RESULT_V5_MAX_TEXT];
    copy_text(device_id_text, sizeof(device_id_text), link->identity.device_id_text);
    copy_text(node_id_text, sizeof(node_id_text), link->identity.node_id_text);
    copy_text(tx_id_text, sizeof(tx_id_text), link->identity.tx_id_text);
    copy_text(rx_id_text, sizeof(rx_id_text), link->identity.rx_id_text);
    copy_text(link_id_text, sizeof(link_id_text), link->identity.link_id_text);
    memset(link, 0, sizeof(*link));
    link->configured = true;
    link->identity = (edge_result_identity_t){
        .device_id = pipeline->config.device_id, .node_id = pipeline->config.node_id,
        .tx_id = tx_id, .rx_id = rx_id, .link_id = link_id_copy,
        /* A TX/source reboot must never alter the RX result stream identity.
         * Pi orders the serialized tuple (node, link, RX boot, sequence). */
        .boot_id = pipeline->config.boot_id, .window_seq = 0U};
    copy_text(link->identity.device_id_text, sizeof(link->identity.device_id_text), device_id_text);
    copy_text(link->identity.node_id_text, sizeof(link->identity.node_id_text), node_id_text);
    copy_text(link->identity.tx_id_text, sizeof(link->identity.tx_id_text), tx_id_text);
    copy_text(link->identity.rx_id_text, sizeof(link->identity.rx_id_text), rx_id_text);
    copy_text(link->identity.link_id_text, sizeof(link->identity.link_id_text), link_id_text);
    copy_text(link->identity.corridor_id, sizeof(link->identity.corridor_id), pipeline->config.corridor_id);
    copy_text(link->identity.session_id, sizeof(link->identity.session_id), pipeline->config.session_id);
    link->next_window_seq = next_seq;
    link->source_boot_id = boot_id;
    link->baseline_learning_enabled = true;
    link->baseline_window_eligible = true;
    link->baseline_state = EDGE_RESULT_BASELINE_CANDIDATE;
    link->explicit_baseline_confirmed = false;
    copy_text(link->baseline_update_reason, sizeof(link->baseline_update_reason), "automatic_candidate");
    link->expected_tx_mac_valid = expected_valid;
    if (expected_valid) {
        memcpy(link->expected_tx_mac, expected_mac, EDGE_RESULT_V5_MAC_BYTES);
    }
    return true;
}

bool edge_result_pipeline_confirm_empty(edge_result_pipeline_t *pipeline, uint32_t link_id)
{
    edge_result_link_state_t *link = find_link(pipeline, link_id);
    if (link == NULL) {
        return false;
    }
    link->baseline_learning_enabled = true;
    link->baseline_window_eligible = true;
    link->baseline_ready = false;
    link->baseline_count = 0U;
    link->baseline_short_count = 0U;
    link->baseline_long_count = 0U;
    link->baseline_candidate_windows = 0U;
    link->rebase_candidate_windows = 0U;
    link->rebase_candidate_stable_windows = 0U;
    link->rebase_candidate_count = 0U;
    link->rebase_candidate_start_us = 0U;
    link->rebase_candidate_sum_motion = 0.0f;
    link->rebase_candidate_sum_spread = 0.0f;
    link->shift_candidate_windows = 0U;
    link->shift_candidate_start_us = 0U;
    link->occupied_windows = 0U;
    link->occupied_start_us = 0U;
    link->occupancy_memory_windows = 0U;
    link->stuck_low_score_windows = 0U;
    link->score_filter_hold_windows = 0U;
    link->rebase_candidate_dirty_windows = 0U;
    link->recovery_windows = 0U;
    link->baseline_state = EDGE_RESULT_BASELINE_CANDIDATE;
    link->explicit_baseline_confirmed = true;
    copy_text(link->baseline_update_reason, sizeof(link->baseline_update_reason), "explicit_test_or_recovery_confirmation");
    link->baseline_center = 0.0f;
    link->baseline_mad = 0.0f;
    return true;
}

bool edge_result_pipeline_revoke_empty(edge_result_pipeline_t *pipeline, uint32_t link_id)
{
    edge_result_link_state_t *link = find_link(pipeline, link_id);
    if (link == NULL) {
        return false;
    }
    link->baseline_learning_enabled = false;
    link->baseline_window_eligible = false;
    link->baseline_ready = false;
    link->baseline_state = EDGE_RESULT_BASELINE_UNKNOWN;
    link->explicit_baseline_confirmed = false;
    copy_text(link->baseline_update_reason, sizeof(link->baseline_update_reason), "baseline_revoked");
    link->baseline_count = 0U;
    link->baseline_center = 0.0f;
    link->baseline_mad = 0.0f;
    return true;
}

bool edge_result_pipeline_submit_csi(edge_result_pipeline_t *pipeline,
                                     const edge_result_csi_record_t *record)
{
    if (pipeline == NULL || record == NULL) {
        return false;
    }
    edge_result_link_state_t *link = find_link(pipeline, record->link_id);
    if (link == NULL) {
        return false;
    }
    /* Copy first so the caller's CSI buffer can be reused immediately. */
    edge_result_csi_record_t bounded;
    memset(&bounded, 0, sizeof(bounded));
    bounded = *record;
    if (bounded.boot_id == 0U || bounded.csi_length < 4U ||
        bounded.csi_length > EDGE_RESULT_V5_MAX_CSI_BYTES ||
        (bounded.csi_length & 1U) != 0U || bounded.first_word_invalid) {
        ++link->invalid_total;
        attribute_invalid_frame(link);
        return false;
    }
    if (!link->expected_tx_mac_valid ||
        memcmp(link->expected_tx_mac, bounded.source_mac, EDGE_RESULT_V5_MAC_BYTES) != 0) {
        ++link->source_reject_total;
        attribute_invalid_frame(link);
        return false;
    }
    if (bounded.timestamp_us == 0U || bounded.tx_sequence == 0U) {
        ++link->sequence_reject_total;
        attribute_invalid_frame(link);
        return false;
    }
    if (bounded.boot_id != link->source_boot_id) {
        /* A stream transition requires edge_result_pipeline_reset_link_boot;
         * accepting it inline would make delayed old packets destructive. */
        ++link->sequence_reject_total;
        attribute_invalid_frame(link);
        return false;
    }
    if (!queue_push(&pipeline->queue, &bounded)) {
        ++link->queue_drop_total;
        /* Dropped CSI cannot be adjacent to the next accepted amplitude. */
        link->previous_amplitude_valid = false;
        if (link->window_start_us != 0U) {
            ++link->window_queue_drop_count;
            link->baseline_window_eligible = false;
        } else {
            ++link->pending_queue_drop_count;
        }
        return false;
    }
    return true;
}

bool edge_result_pipeline_process_one(edge_result_pipeline_t *pipeline,
                                      edge_result_v5_t *result)
{
    if (pipeline == NULL || result == NULL) {
        return false;
    }
    edge_result_csi_record_t record;
    if (!queue_pop(&pipeline->queue, &record)) {
        return false;
    }
    edge_result_link_state_t *link = find_link(pipeline, record.link_id);
    if (link == NULL) {
        return false;
    }
    const uint64_t prior_start = link->window_start_us;
    const bool ordered = (link->last_tx_sequence == 0U || record.tx_sequence > link->last_tx_sequence) &&
                         (link->last_timestamp_us == 0U || record.timestamp_us > link->last_timestamp_us);
    const bool close = prior_start != 0U && link->window_received_count > 0U &&
                       ordered &&
                       record.timestamp_us >= prior_start &&
                       record.timestamp_us - prior_start >= pipeline->config.window_duration_us;
    if (close) {
        const uint64_t boundary = prior_start + pipeline->config.window_duration_us;
        *result = make_result(pipeline, link, boundary);
        reset_window(link, boundary);
        /* The boundary-crossing record belongs to the new window. */
        update_link(pipeline, link, &record);
        return true;
    }
    update_link(pipeline, link, &record);
    return false;
}

bool edge_result_pipeline_flush_link(edge_result_pipeline_t *pipeline,
                                     uint32_t link_id,
                                     edge_result_v5_t *result)
{
    if (pipeline == NULL || result == NULL) {
        return false;
    }
    edge_result_link_state_t *link = find_link(pipeline, link_id);
    if (link == NULL || link->window_start_us == 0U || link->window_received_count == 0U) {
        return false;
    }
    /* A one-sample flush still needs a positive wire window duration. */
    uint64_t flush_end_us = link->window_last_timestamp_us;
    if (flush_end_us <= link->window_start_us && flush_end_us < UINT64_MAX) {
        flush_end_us = link->window_start_us + 1U;
    }
    *result = make_result(pipeline, link, flush_end_us);
    const uint64_t next_start_us = link->window_last_timestamp_us < UINT64_MAX
                                       ? link->window_last_timestamp_us + 1U
                                       : link->window_last_timestamp_us;
    reset_window(link, next_start_us);
    return true;
}

const edge_result_link_state_t *edge_result_pipeline_link(const edge_result_pipeline_t *pipeline,
                                                          uint32_t link_id)
{
    return find_link_const(pipeline, link_id);
}

uint32_t edge_result_pipeline_queue_drops(const edge_result_pipeline_t *pipeline)
{
    return pipeline == NULL ? 0U : pipeline->queue.drop_count;
}

static void put_u16(uint8_t *bytes, uint16_t value)
{
    bytes[0] = (uint8_t)(value >> 8U);
    bytes[1] = (uint8_t)value;
}

static void put_u32(uint8_t *bytes, uint32_t value)
{
    bytes[0] = (uint8_t)(value >> 24U);
    bytes[1] = (uint8_t)(value >> 16U);
    bytes[2] = (uint8_t)(value >> 8U);
    bytes[3] = (uint8_t)value;
}

static void put_u64(uint8_t *bytes, uint64_t value)
{
    for (uint8_t index = 0U; index < 8U; ++index) {
        bytes[index] = (uint8_t)(value >> (56U - index * 8U));
    }
}

static void put_f32(uint8_t *bytes, float value)
{
    uint32_t raw = 0U;
    memcpy(&raw, &value, sizeof(raw));
    put_u32(bytes, raw);
}

static uint16_t get_u16(const uint8_t *bytes)
{
    return (uint16_t)(((uint16_t)bytes[0] << 8U) | bytes[1]);
}

static uint32_t get_u32(const uint8_t *bytes)
{
    return ((uint32_t)bytes[0] << 24U) | ((uint32_t)bytes[1] << 16U) |
           ((uint32_t)bytes[2] << 8U) | bytes[3];
}

static uint64_t get_u64(const uint8_t *bytes)
{
    uint64_t value = 0U;
    for (uint8_t index = 0U; index < 8U; ++index) {
        value = (value << 8U) | bytes[index];
    }
    return value;
}

static float get_f32(const uint8_t *bytes)
{
    const uint32_t raw = get_u32(bytes);
    float value = 0.0f;
    memcpy(&value, &raw, sizeof(value));
    return value;
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *bytes, size_t length)
{
    for (size_t index = 0U; index < length; ++index) {
        crc ^= bytes[index];
        for (uint8_t bit = 0U; bit < 8U; ++bit) {
            crc = (crc >> 1U) ^ (UINT32_C(0xEDB88320) & (0U - (crc & 1U)));
        }
    }
    return crc;
}

static uint32_t packet_crc(const uint8_t *prefix, size_t prefix_length,
                           const uint8_t *payload, size_t payload_length)
{
    uint32_t crc = UINT32_C(0xFFFFFFFF);
    crc = crc32_update(crc, prefix, prefix_length);
    crc = crc32_update(crc, payload, payload_length);
    return ~crc;
}

static bool valid_utf8_text(const uint8_t *bytes, size_t length);

static bool write_string(uint8_t *payload, size_t capacity, size_t *offset,
                         const char *value, size_t max_bytes)
{
    if (payload == NULL || offset == NULL || value == NULL) {
        return false;
    }
    size_t length = strlen(value);
    if (length > max_bytes || length > 255U || *offset + 1U + length > capacity ||
        !valid_utf8_text((const uint8_t *)value, length)) {
        return false;
    }
    payload[(*offset)++] = (uint8_t)length;
    memcpy(payload + *offset, value, length);
    *offset += length;
    return true;
}

static bool required_text(const char *value)
{
    return value != NULL && value[0] != '\0';
}

static bool valid_utf8_text(const uint8_t *bytes, size_t length)
{
    size_t index = 0U;
    while (index < length) {
        const uint8_t first = bytes[index++];
        if (first == 0U || first < 0x20U) {
            return false;
        }
        size_t continuation = 0U;
        uint32_t codepoint = 0U;
        if (first < 0x80U) {
            continue;
        } else if (first >= 0xC2U && first <= 0xDFU) {
            continuation = 1U; codepoint = first & 0x1FU;
        } else if (first >= 0xE0U && first <= 0xEFU) {
            continuation = 2U; codepoint = first & 0x0FU;
        } else if (first >= 0xF0U && first <= 0xF4U) {
            continuation = 3U; codepoint = first & 0x07U;
        } else {
            return false;
        }
        if (index + continuation > length) {
            return false;
        }
        for (size_t count = 0U; count < continuation; ++count) {
            const uint8_t next = bytes[index++];
            if ((next & 0xC0U) != 0x80U) {
                return false;
            }
            codepoint = (codepoint << 6U) | (next & 0x3FU);
        }
        if ((continuation == 2U && codepoint < 0x800U) ||
            (continuation == 3U && codepoint < 0x10000U) ||
            codepoint > 0x10FFFFU || (codepoint >= 0xD800U && codepoint <= 0xDFFFU)) {
            return false;
        }
    }
    return true;
}

static void format_id(char *output, size_t capacity, uint32_t numeric, const char *text)
{
    if (text != NULL && text[0] != '\0') {
        copy_text(output, capacity, text);
        return;
    }
    (void)snprintf(output, capacity, "%" PRIu32, numeric);
}

static const char *baseline_state_name(edge_result_baseline_state_t state)
{
    switch (state) {
    case EDGE_RESULT_BASELINE_NO_BASELINE: return "NO_BASELINE";
    case EDGE_RESULT_BASELINE_CANDIDATE: return "CANDIDATE";
    case EDGE_RESULT_BASELINE_STABLE: return "STABLE";
    case EDGE_RESULT_BASELINE_SHIFT_CANDIDATE: return "SHIFT_CANDIDATE";
    case EDGE_RESULT_BASELINE_REBASE_PENDING: return "REBASE_PENDING";
    case EDGE_RESULT_BASELINE_UPDATED: return "UPDATED";
    case EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED: return "OCCUPIED_OR_BLOCKED";
    case EDGE_RESULT_BASELINE_UNKNOWN: return "UNKNOWN";
    default: return "UNKNOWN";
    }
}

static const char *model_state_name(edge_result_model_state_t state)
{
    switch (state) {
    case EDGE_RESULT_MODEL_READY: return "READY";
    case EDGE_RESULT_MODEL_REJECTED: return "REJECTED";
    case EDGE_RESULT_MODEL_OOD: return "OOD";
    case EDGE_RESULT_MODEL_NOT_READY:
    default: return "NOT_READY";
    }
}

static bool write_nullable_score(uint8_t *payload, size_t capacity, size_t *offset,
                                 bool present, float value)
{
    if (payload == NULL || offset == NULL || *offset + 5U > capacity) {
        return false;
    }
    payload[(*offset)++] = present ? 1U : 0U;
    put_f32(payload + *offset, present ? value : -1.0f);
    *offset += 4U;
    return true;
}

static size_t write_payload(const edge_result_v5_t *result, uint8_t *payload, size_t capacity)
{
    size_t offset = 0U;
    char ids[7][EDGE_RESULT_V5_MAX_TEXT];
    format_id(ids[0], sizeof(ids[0]), result->identity.device_id, result->identity.device_id_text);
    format_id(ids[1], sizeof(ids[1]), result->identity.node_id, result->identity.node_id_text);
    format_id(ids[2], sizeof(ids[2]), result->identity.tx_id, result->identity.tx_id_text);
    format_id(ids[3], sizeof(ids[3]), result->identity.rx_id, result->identity.rx_id_text);
    format_id(ids[4], sizeof(ids[4]), result->identity.link_id, result->identity.link_id_text);
    copy_text(ids[5], sizeof(ids[5]), result->identity.corridor_id);
    copy_text(ids[6], sizeof(ids[6]), result->identity.session_id);
    for (size_t index = 0U; index < 7U; ++index) {
        if (!write_string(payload, capacity, &offset, ids[index], EDGE_RESULT_V5_MAX_TEXT - 1U)) {
            return 0U;
        }
    }
    if (offset + 36U > capacity) {
        return 0U;
    }
    put_u32(payload + offset, result->identity.boot_id); offset += 4U;
    put_u32(payload + offset, result->identity.window_seq); offset += 4U;
    put_u64(payload + offset, result->window_start_us); offset += 8U;
    put_u64(payload + offset, result->window_end_us); offset += 8U;
    put_u64(payload + offset, result->rx_timestamp_us); offset += 8U;
    put_u32(payload + offset, result->age_ms); offset += 4U;
    if (offset + 17U > capacity) {
        return 0U;
    }
    payload[offset++] = result->score_valid ? 1U : 0U;
    put_f32(payload + offset, result->score_valid ? result->local_passability_score : -1.0f); offset += 4U;
    payload[offset++] = (uint8_t)result->state;
    put_f32(payload + offset, result->quality_score); offset += 4U;
    put_f32(payload + offset, result->uncertainty_score); offset += 4U;
    payload[offset++] = result->disagreement ? 1U : 0U;
    put_u16(payload + offset, (uint16_t)result->reason_code); offset += 2U;
    const char *versions[] = {result->formula_version, result->model_version,
                              result->model_hash, result->feature_schema_version};
    for (size_t index = 0U; index < 4U; ++index) {
        const size_t max_bytes = index == 2U ? EDGE_RESULT_V5_MAX_MODEL_HASH : EDGE_RESULT_V5_MAX_TEXT - 1U;
        if (!write_string(payload, capacity, &offset, versions[index], max_bytes)) {
            return 0U;
        }
    }
    if (offset + 24U > capacity) {
        return 0U;
    }
    put_u32(payload + offset, result->sample_count); offset += 4U;
    put_u32(payload + offset, result->invalid_count); offset += 4U;
    put_u32(payload + offset, result->queue_drop_count); offset += 4U;
    put_u32(payload + offset, result->sequence_gap); offset += 4U;
    put_f32(payload + offset, result->packet_loss_ratio); offset += 4U;
    put_f32(payload + offset, result->jitter_ms); offset += 4U;
    const char *adaptive[] = {
        baseline_state_name(result->baseline_state),
        result->baseline_update_reason,
        baseline_state_name(result->drift_state),
    };
    for (size_t index = 0U; index < 3U; ++index) {
        if (!write_string(payload, capacity, &offset, adaptive[index], EDGE_RESULT_V5_MAX_TEXT - 1U)) {
            return 0U;
        }
    }
    if (offset + 8U > capacity) {
        return 0U;
    }
    put_u32(payload + offset, result->baseline_version); offset += 4U;
    put_f32(payload + offset, result->baseline_confidence); offset += 4U;
    if (!write_string(payload, capacity, &offset,
                      result->transition_state[0] != '\0' ? result->transition_state : "STABLE",
                      EDGE_RESULT_V5_MAX_TEXT - 1U) ||
        !write_string(payload, capacity, &offset, model_state_name(result->model_state),
                      EDGE_RESULT_V5_MAX_TEXT - 1U)) {
        return 0U;
    }
    if (!write_nullable_score(payload, capacity, &offset, result->raw_evidence_valid,
                              result->raw_evidence_score) ||
        !write_nullable_score(payload, capacity, &offset, result->filtered_passability_valid,
                              result->filtered_passability_score) ||
        !write_nullable_score(payload, capacity, &offset, result->occupancy_evidence_valid,
                              result->occupancy_evidence) ||
        !write_nullable_score(payload, capacity, &offset, result->blocking_evidence_valid,
                              result->blocking_evidence)) {
        return 0U;
    }
    if (offset + 11U > capacity) {
        return 0U;
    }
    payload[offset++] = result->doppler_valid ? 1U : 0U;
    put_f32(payload + offset, result->doppler_valid ? result->doppler_ratio : -1.0f);
    offset += 4U;
    put_f32(payload + offset, result->doppler_valid ? result->doppler_fs_hz : -1.0f);
    offset += 4U;
    put_u16(payload + offset, result->doppler_valid ? result->doppler_samples : 0U);
    offset += 2U;
    return offset;
}

int edge_result_v5_encode(const edge_result_v5_t *result,
                          uint8_t *packet,
                          size_t capacity,
                          size_t *encoded_length)
{
    if (result == NULL || packet == NULL || encoded_length == NULL ||
        capacity < EDGE_RESULT_V5_HEADER_SIZE ||
        result->identity.boot_id == 0U || result->identity.window_seq == 0U ||
        result->window_start_us == 0U || result->rx_timestamp_us == 0U ||
        result->window_end_us <= result->window_start_us ||
        result->rx_timestamp_us < result->window_end_us ||
        !finite_float(result->quality_score) || result->quality_score < 0.0f || result->quality_score > 100.0f ||
        !finite_float(result->uncertainty_score) || result->uncertainty_score < 0.0f || result->uncertainty_score > 100.0f ||
        (result->score_valid && (!finite_float(result->local_passability_score) ||
                                 result->local_passability_score < 0.0f || result->local_passability_score > 100.0f)) ||
        result->state > EDGE_RESULT_STATE_UNKNOWN ||
        (result->state == EDGE_RESULT_STATE_UNKNOWN && result->score_valid) ||
        (uint32_t)result->reason_code > UINT16_MAX ||
        !required_text(result->identity.device_id_text) || !required_text(result->identity.node_id_text) ||
        !required_text(result->identity.tx_id_text) || !required_text(result->identity.rx_id_text) ||
        !required_text(result->identity.link_id_text) || !required_text(result->identity.corridor_id) ||
        !required_text(result->identity.session_id) ||
        !required_text(result->formula_version) || !required_text(result->model_version) ||
        !required_text(result->feature_schema_version) ||
        /* The active encoder is schema 7.  A mismatched feature marker with
         * this header is rejected instead of emitting a packet the Pi codec
         * must discard.  Schema 5/6 remain decode-only compatibility. */
        strcmp(result->feature_schema_version, "7") != 0 ||
        !finite_float(result->packet_loss_ratio) || result->packet_loss_ratio < 0.0f || result->packet_loss_ratio > 1.0f ||
        !finite_float(result->jitter_ms) || result->jitter_ms < 0.0f ||
        result->invalid_count > result->sample_count) {
        return -1;
    }
    uint8_t payload[EDGE_RESULT_V5_MAX_WIRE_PAYLOAD];
    const size_t payload_length = write_payload(result, payload, sizeof(payload));
    if (payload_length == 0U || payload_length > UINT16_MAX ||
        capacity < EDGE_RESULT_V5_HEADER_SIZE + payload_length) {
        return -1;
    }
    put_u32(packet, EDGE_RESULT_V5_MAGIC);
    packet[4] = EDGE_RESULT_V5_PROTOCOL_VERSION;
    packet[5] = EDGE_RESULT_V5_MESSAGE_TYPE;
    put_u16(packet + 6U, (uint16_t)payload_length);
    put_u16(packet + 8U, EDGE_RESULT_V5_SCHEMA_VERSION);
    put_u32(packet + 10U, packet_crc(packet, 10U, payload, payload_length));
    memcpy(packet + EDGE_RESULT_V5_HEADER_SIZE, payload, payload_length);
    *encoded_length = EDGE_RESULT_V5_HEADER_SIZE + payload_length;
    return 0;
}

static bool read_string(const uint8_t *payload, size_t payload_length, size_t *offset,
                        char *output, size_t output_capacity, bool required)
{
    if (payload == NULL || offset == NULL || output == NULL || output_capacity == 0U ||
        *offset >= payload_length) {
        return false;
    }
    const size_t length = payload[(*offset)++];
    if (*offset + length > payload_length || length >= output_capacity ||
        (required && length == 0U) || !valid_utf8_text(payload + *offset, length)) {
        return false;
    }
    memcpy(output, payload + *offset, length);
    output[length] = '\0';
    *offset += length;
    return true;
}

static uint32_t parse_numeric_id(const char *text)
{
    uint32_t value = 0U;
    if (text == NULL || text[0] == '\0') {
        return 0U;
    }
    for (size_t index = 0U; text[index] != '\0'; ++index) {
        if (text[index] < '0' || text[index] > '9' || value > (UINT32_MAX / 10U)) {
            return 0U;
        }
        value = value * 10U + (uint32_t)(text[index] - '0');
    }
    return value;
}

static edge_result_baseline_state_t parse_baseline_state(const char *text)
{
    if (text == NULL) return EDGE_RESULT_BASELINE_UNKNOWN;
    if (strcmp(text, "NO_BASELINE") == 0) return EDGE_RESULT_BASELINE_NO_BASELINE;
    if (strcmp(text, "CANDIDATE") == 0) return EDGE_RESULT_BASELINE_CANDIDATE;
    if (strcmp(text, "STABLE") == 0) return EDGE_RESULT_BASELINE_STABLE;
    if (strcmp(text, "SHIFT_CANDIDATE") == 0) return EDGE_RESULT_BASELINE_SHIFT_CANDIDATE;
    if (strcmp(text, "REBASE_PENDING") == 0) return EDGE_RESULT_BASELINE_REBASE_PENDING;
    if (strcmp(text, "UPDATED") == 0) return EDGE_RESULT_BASELINE_UPDATED;
    if (strcmp(text, "OCCUPIED_OR_BLOCKED") == 0) return EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED;
    return EDGE_RESULT_BASELINE_UNKNOWN;
}

int edge_result_v5_decode(const uint8_t *packet,
                          size_t length,
                          edge_result_v5_t *result,
                          edge_result_reason_t *reject_reason)
{
    if (reject_reason != NULL) {
        *reject_reason = EDGE_RESULT_REASON_NONE;
    }
#define REJECT(reason) do { if (reject_reason != NULL) { *reject_reason = (reason); } return -1; } while (0)
    uint16_t schema_version = 0U;
    if (packet == NULL || result == NULL || length < EDGE_RESULT_V5_HEADER_SIZE ||
        get_u32(packet) != EDGE_RESULT_V5_MAGIC || packet[4] != EDGE_RESULT_V5_PROTOCOL_VERSION ||
        packet[5] != EDGE_RESULT_V5_MESSAGE_TYPE ||
        ((schema_version = get_u16(packet + 8U)) != EDGE_RESULT_V5_SCHEMA_V5 &&
         schema_version != EDGE_RESULT_V5_SCHEMA_V6 &&
         schema_version != EDGE_RESULT_V5_SCHEMA_V7)) {
        REJECT(EDGE_RESULT_REASON_MODEL_REJECTED);
    }
    const uint16_t payload_length = get_u16(packet + 6U);
    if (payload_length > EDGE_RESULT_V5_MAX_WIRE_PAYLOAD ||
        length > EDGE_RESULT_V5_MAX_WIRE_SIZE ||
        (size_t)payload_length != length - EDGE_RESULT_V5_HEADER_SIZE ||
        get_u32(packet + 10U) != packet_crc(packet, 10U, packet + EDGE_RESULT_V5_HEADER_SIZE,
                                             payload_length)) {
        REJECT(EDGE_RESULT_REASON_MODEL_REJECTED);
    }
    memset(result, 0, sizeof(*result));
    const uint8_t *payload = packet + EDGE_RESULT_V5_HEADER_SIZE;
    size_t offset = 0U;
    char *ids[] = {result->identity.device_id_text, result->identity.node_id_text,
                   result->identity.tx_id_text, result->identity.rx_id_text,
                   result->identity.link_id_text, result->identity.corridor_id,
                   result->identity.session_id};
    for (size_t index = 0U; index < 7U; ++index) {
        if (!read_string(payload, payload_length, &offset, ids[index], EDGE_RESULT_V5_MAX_TEXT, true)) {
            REJECT(EDGE_RESULT_REASON_INVALID_CSI);
        }
    }
    if (offset + 36U > payload_length) {
        REJECT(EDGE_RESULT_REASON_INVALID_CSI);
    }
    result->identity.device_id = parse_numeric_id(result->identity.device_id_text);
    result->identity.node_id = parse_numeric_id(result->identity.node_id_text);
    result->identity.tx_id = parse_numeric_id(result->identity.tx_id_text);
    result->identity.rx_id = parse_numeric_id(result->identity.rx_id_text);
    result->identity.link_id = parse_numeric_id(result->identity.link_id_text);
    result->identity.boot_id = get_u32(payload + offset); offset += 4U;
    result->identity.window_seq = get_u32(payload + offset); offset += 4U;
    result->window_start_us = get_u64(payload + offset); offset += 8U;
    result->window_end_us = get_u64(payload + offset); offset += 8U;
    result->rx_timestamp_us = get_u64(payload + offset); offset += 8U;
    result->age_ms = get_u32(payload + offset); offset += 4U;
    if (offset + 17U > payload_length) {
        REJECT(EDGE_RESULT_REASON_INVALID_CSI);
    }
    const uint8_t score_present = payload[offset++];
    result->local_passability_score = get_f32(payload + offset); offset += 4U;
    result->state = (edge_result_state_t)payload[offset++];
    result->quality_score = get_f32(payload + offset); offset += 4U;
    result->uncertainty_score = get_f32(payload + offset); offset += 4U;
    const uint8_t disagreement = payload[offset++];
    if (disagreement > 1U) {
        REJECT(EDGE_RESULT_REASON_INVALID_CSI);
    }
    result->disagreement = disagreement != 0U;
    result->reason_code = (edge_result_reason_t)get_u16(payload + offset); offset += 2U;
    if (score_present > 1U || result->state > EDGE_RESULT_STATE_UNKNOWN ||
        (score_present == 0U && result->local_passability_score != -1.0f)) {
        REJECT(EDGE_RESULT_REASON_INVALID_CSI);
    }
    result->score_valid = score_present != 0U;
    if (!read_string(payload, payload_length, &offset, result->formula_version,
                     EDGE_RESULT_V5_MAX_TEXT, true) ||
        !read_string(payload, payload_length, &offset, result->model_version,
                     EDGE_RESULT_V5_MAX_TEXT, true) ||
        !read_string(payload, payload_length, &offset, result->model_hash,
                     EDGE_RESULT_V5_MAX_MODEL_HASH + 1U, false) ||
        !read_string(payload, payload_length, &offset, result->feature_schema_version,
                     EDGE_RESULT_V5_MAX_TEXT, true) ||
        (strcmp(result->feature_schema_version, "5") != 0 &&
         strcmp(result->feature_schema_version, "6") != 0 &&
         strcmp(result->feature_schema_version, "7") != 0) ||
        offset + 24U > payload_length) {
        REJECT(EDGE_RESULT_REASON_INVALID_CSI);
    }
    result->sample_count = get_u32(payload + offset); offset += 4U;
    result->invalid_count = get_u32(payload + offset); offset += 4U;
    result->queue_drop_count = get_u32(payload + offset); offset += 4U;
    result->sequence_gap = get_u32(payload + offset); offset += 4U;
    result->packet_loss_ratio = get_f32(payload + offset); offset += 4U;
    result->jitter_ms = get_f32(payload + offset); offset += 4U;
    result->baseline_state = EDGE_RESULT_BASELINE_NO_BASELINE;
    result->baseline_version = 0U;
    copy_text(result->baseline_update_reason, sizeof(result->baseline_update_reason), "v5_compatibility_decode");
    result->baseline_confidence = 0.0f;
    result->drift_state = EDGE_RESULT_BASELINE_UNKNOWN;
    copy_text(result->transition_state, sizeof(result->transition_state), "STABLE");
    result->model_state = EDGE_RESULT_MODEL_NOT_READY;
    result->raw_evidence_valid = false;
    result->filtered_passability_valid = false;
    result->occupancy_evidence_valid = false;
    result->blocking_evidence_valid = false;
    if (schema_version == EDGE_RESULT_V5_SCHEMA_V6 ||
        schema_version == EDGE_RESULT_V5_SCHEMA_V7) {
        char baseline_text[EDGE_RESULT_V5_MAX_TEXT];
        char drift_text[EDGE_RESULT_V5_MAX_TEXT];
        char model_text[EDGE_RESULT_V5_MAX_TEXT];
        const size_t adaptive_tail = 20U +
            (schema_version == EDGE_RESULT_V5_SCHEMA_V7 ? 11U : 0U);
        if (!read_string(payload, payload_length, &offset, baseline_text, sizeof(baseline_text), true) ||
            !read_string(payload, payload_length, &offset, result->baseline_update_reason,
                         sizeof(result->baseline_update_reason), true) ||
            !read_string(payload, payload_length, &offset, drift_text, sizeof(drift_text), true) ||
            offset + 8U > payload_length) {
            REJECT(EDGE_RESULT_REASON_INVALID_CSI);
        }
        result->baseline_state = parse_baseline_state(baseline_text);
        result->drift_state = parse_baseline_state(drift_text);
        result->baseline_version = get_u32(payload + offset); offset += 4U;
        result->baseline_confidence = get_f32(payload + offset); offset += 4U;
        if (!read_string(payload, payload_length, &offset, result->transition_state,
                         sizeof(result->transition_state), true) ||
            !read_string(payload, payload_length, &offset, model_text, sizeof(model_text), true) ||
            offset + adaptive_tail != payload_length) {
            REJECT(EDGE_RESULT_REASON_INVALID_CSI);
        }
        if (strcmp(model_text, "READY") == 0) result->model_state = EDGE_RESULT_MODEL_READY;
        else if (strcmp(model_text, "REJECTED") == 0) result->model_state = EDGE_RESULT_MODEL_REJECTED;
        else if (strcmp(model_text, "OOD") == 0) result->model_state = EDGE_RESULT_MODEL_OOD;
        for (uint8_t index = 0U; index < 4U; ++index) {
            const uint8_t present = payload[offset++];
            const float score = get_f32(payload + offset); offset += 4U;
            if (present > 1U || (!present && score != -1.0f) ||
                (present && (!finite_float(score) || score < 0.0f || score > 100.0f))) {
                REJECT(EDGE_RESULT_REASON_INVALID_CSI);
            }
            if (index == 0U) { result->raw_evidence_valid = present != 0U; result->raw_evidence_score = score; }
            if (index == 1U) { result->filtered_passability_valid = present != 0U; result->filtered_passability_score = score; }
            if (index == 2U) { result->occupancy_evidence_valid = present != 0U; result->occupancy_evidence = score; }
            if (index == 3U) { result->blocking_evidence_valid = present != 0U; result->blocking_evidence = score; }
        }
        if (schema_version == EDGE_RESULT_V5_SCHEMA_V7) {
            const uint8_t present = payload[offset++];
            const float ratio = get_f32(payload + offset); offset += 4U;
            const float fs_hz = get_f32(payload + offset); offset += 4U;
            const uint16_t samples = get_u16(payload + offset); offset += 2U;
            if (present > 1U) {
                REJECT(EDGE_RESULT_REASON_INVALID_CSI);
            }
            if (present == 0U) {
                if (ratio != -1.0f || fs_hz != -1.0f) {
                    REJECT(EDGE_RESULT_REASON_INVALID_CSI);
                }
            } else if (!finite_float(ratio) || ratio < 0.0f || ratio > 1.0f ||
                       !finite_float(fs_hz) || fs_hz <= 0.0f) {
                REJECT(EDGE_RESULT_REASON_INVALID_CSI);
            } else {
                result->doppler_valid = true;
                result->doppler_ratio = ratio;
                result->doppler_fs_hz = fs_hz;
                result->doppler_samples = samples;
            }
        }
    } else if (offset != payload_length) {
        REJECT(EDGE_RESULT_REASON_INVALID_CSI);
    }
    if (result->identity.boot_id == 0U || result->identity.window_seq == 0U ||
        result->window_start_us == 0U || result->window_end_us <= result->window_start_us ||
        result->rx_timestamp_us < result->window_end_us ||
        !finite_float(result->quality_score) || result->quality_score < 0.0f || result->quality_score > 100.0f ||
        !finite_float(result->uncertainty_score) || result->uncertainty_score < 0.0f || result->uncertainty_score > 100.0f ||
        !finite_float(result->packet_loss_ratio) || result->packet_loss_ratio < 0.0f || result->packet_loss_ratio > 1.0f ||
        !finite_float(result->jitter_ms) || result->jitter_ms < 0.0f ||
        !finite_float(result->baseline_confidence) || result->baseline_confidence < 0.0f || result->baseline_confidence > 100.0f ||
        result->invalid_count > result->sample_count ||
        (result->state == EDGE_RESULT_STATE_UNKNOWN && result->score_valid) ||
        (result->score_valid && (!finite_float(result->local_passability_score) ||
                                 result->local_passability_score < 0.0f || result->local_passability_score > 100.0f))) {
        REJECT(EDGE_RESULT_REASON_INVALID_CSI);
    }
#undef REJECT
    return 0;
}

const char *edge_result_state_name(edge_result_state_t state)
{
    switch (state) {
    case EDGE_RESULT_STATE_PASSABLE: return "PASSABLE";
    case EDGE_RESULT_STATE_DEGRADED: return "DEGRADED";
    case EDGE_RESULT_STATE_BLOCKED: return "BLOCKED";
    case EDGE_RESULT_STATE_UNKNOWN: return "UNKNOWN";
    default: return "UNKNOWN";
    }
}

const char *edge_result_reason_name(edge_result_reason_t reason)
{
    switch (reason) {
    case EDGE_RESULT_REASON_NONE: return "none";
    case EDGE_RESULT_REASON_VALID: return "valid";
    case EDGE_RESULT_REASON_WARMING_UP: return "warming_up";
    case EDGE_RESULT_REASON_INVALID_CSI: return "invalid_csi";
    case EDGE_RESULT_REASON_WRONG_SOURCE: return "wrong_source";
    case EDGE_RESULT_REASON_TIMESTAMP_INVALID: return "timestamp_invalid";
    case EDGE_RESULT_REASON_SEQUENCE_GAP: return "sequence_gap";
    case EDGE_RESULT_REASON_PACKET_LOSS: return "packet_loss";
    case EDGE_RESULT_REASON_MODEL_NOT_READY: return "model_not_ready";
    case EDGE_RESULT_REASON_MODEL_REJECTED: return "model_rejected";
    case EDGE_RESULT_REASON_OOD: return "ood";
    case EDGE_RESULT_REASON_FORMULA_AI_DISAGREEMENT: return "formula_ai_disagreement";
    case EDGE_RESULT_REASON_QUALITY_LOW: return "quality_low";
    case EDGE_RESULT_REASON_STALE: return "stale";
    case EDGE_RESULT_REASON_QUEUE_DROP: return "queue_drop";
    case EDGE_RESULT_REASON_ENVIRONMENT_SHIFT: return "environment_shift";
    case EDGE_RESULT_REASON_PERSISTENT_OCCUPANCY: return "persistent_occupancy";
    case EDGE_RESULT_REASON_BLOCKED: return "blocked";
    default: return "invalid_reason";
    }
}
