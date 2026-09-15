#include "formula_flex.h"

#include <math.h>
#include <string.h>

#define FORMULA_FLEX_SAFE_SCALE 0.0001f

static float clampf_local(float value, float lower, float upper)
{
    if (value < lower) return lower;
    if (value > upper) return upper;
    return value;
}

static void copy_text(char *destination, size_t capacity, const char *source)
{
    if (destination == NULL || capacity == 0U) return;
    if (source == NULL) source = "";
    size_t length = strlen(source);
    if (length >= capacity) length = capacity - 1U;
    memcpy(destination, source, length);
    destination[length] = '\0';
}

static void sort_values(float *values, uint16_t count)
{
    for (uint16_t i = 1U; i < count; ++i) {
        const float value = values[i];
        uint16_t j = i;
        while (j > 0U && values[j - 1U] > value) {
            values[j] = values[j - 1U];
            --j;
        }
        values[j] = value;
    }
}

static float median_values(const float *values, uint16_t count)
{
    if (values == NULL || count == 0U || count > 64U) return 0.0f;
    float copy[64];
    memcpy(copy, values, (size_t)count * sizeof(copy[0]));
    sort_values(copy, count);
    if ((count & 1U) != 0U) return copy[count / 2U];
    return 0.5f * (copy[count / 2U - 1U] + copy[count / 2U]);
}

float formula_flex_baseline_deviation(const edge_result_link_state_t *link,
                                      float amplitude)
{
    if (link == NULL || !link->baseline_ready || !isfinite((double)amplitude)) {
        return NAN;
    }
    /* Baseline MAD and temporal noise are both link-local CSI-amplitude
     * scales.  A tiny MAD alone would turn ordinary quantization drift into
     * an enormous z-score and collapse an otherwise clean score. */
    const float spread = fmaxf(1.4826f * link->baseline_mad,
                               fmaxf(link->temporal_noise_scale,
                                     FORMULA_FLEX_SAFE_SCALE));
    const float deviation = fabsf(amplitude - link->baseline_center) / spread;
    return isfinite((double)deviation) ? deviation : NAN;
}

float formula_flex_default_formula(const edge_result_features_t *features,
                                   const edge_result_link_state_t *link,
                                   void *context)
{
    (void)context;
    if (features == NULL || link == NULL || !link->baseline_ready ||
        !isfinite((double)features->common_amplitude) ||
        !isfinite((double)features->temporal_motion) ||
        !isfinite((double)features->robust_spread)) {
        return NAN;
    }
    const float deviation = formula_flex_baseline_deviation(link, features->common_amplitude);
    if (!isfinite((double)deviation)) {
        return NAN;
    }
    /* Link-local z-scores only: amplitude vs this link's MAD, motion vs this
     * link's temporal envelope.  The same rule moves with the kit.  Do not
     * penalize intra-packet subcarrier MAD; that is frequency-selective
     * fading of an empty hall, not occupancy, and is not a z-score of the
     * same quantity.  Quiet residual deviation is absorbed by rebase, not
     * by hiding it from the score. */
    const float excess_motion = fmaxf(0.0f, features->temporal_motion - 1.0f);
    const float combined_stress = fmaxf(0.0f, deviation) + 0.50f * excess_motion;
    /* A bounded ratio preserves the local 0..100 index without introducing
     * node-independent penalty gains or raw thresholds. */
    return clampf_local(100.0f / (1.0f + combined_stress), 0.0f, 100.0f);
}

void formula_flex_update_baseline(edge_result_link_state_t *link,
                                  const edge_result_pipeline_config_t *config,
                                  float amplitude,
                                  float delta)
{
    if (link == NULL || config == NULL || !link->baseline_learning_enabled ||
        !isfinite((double)amplitude) || !isfinite((double)delta)) return;
    if (link->baseline_ready) {
        /* Drift updates are only valid after a stable baseline. Callers add
         * window-level quality/evidence gates; this local guard prevents an
         * accidental future caller from absorbing a transition. */
        if (link->baseline_state != EDGE_RESULT_BASELINE_STABLE) {
            return;
        }
        const float deviation = formula_flex_baseline_deviation(link, amplitude);
        const float envelope = fmaxf(1.4826f * link->baseline_mad,
                                     fmaxf(link->temporal_noise_scale, FORMULA_FLEX_SAFE_SCALE));
        if (isfinite((double)deviation) && fabsf(delta) <= envelope) {
            /* Keep drift adaptation deliberately slow; confidence gates
             * eligibility, while the bounded rate prevents one window from
             * absorbing a persistent occupant or blocked corridor. */
            const float alpha = clampf_local(0.05f +
                                             0.15f * (1.0f -
                                                      link->baseline_confidence / 100.0f),
                                             FORMULA_FLEX_SAFE_SCALE, 0.20f);
            const float residual = fabsf(amplitude - link->baseline_center);
            link->baseline_center += alpha * (amplitude - link->baseline_center);
            link->baseline_mad = fmaxf(0.0001f,
                                        link->baseline_mad + alpha *
                                        (residual - link->baseline_mad));
            link->baseline_confidence = clampf_local(link->baseline_confidence + 0.05f,
                                                     0.0f, 100.0f);
            link->baseline_state = EDGE_RESULT_BASELINE_STABLE;
            link->baseline_last_update_us = link->window_last_timestamp_us;
            copy_text(link->baseline_update_reason, sizeof(link->baseline_update_reason),
                      "confidence_gated_drift_adaptation");
        }
        return;
    }
    if (link->baseline_count >= EDGE_RESULT_V5_MAX_BASELINE_SAMPLES) return;
    /* Bootstrap callers have already admitted only clean windows through
     * link-relative temporal, quality, invalid, gap, and queue gates. Do not
     * reapply a raw-amplitude delta reset here: before a candidate has a MAD,
     * its near-zero envelope makes normal lab variation erase every sample.
     * Median/MAD below remains the bounded robust estimator. */
    link->baseline_samples[link->baseline_count++] = amplitude;
    if (link->baseline_long_count < (uint16_t)(sizeof(link->baseline_long_samples) /
                                               sizeof(link->baseline_long_samples[0]))) {
        link->baseline_long_samples[link->baseline_long_count++] = amplitude;
    }
    if (link->baseline_short_count < (uint16_t)(sizeof(link->baseline_short_samples) /
                                                sizeof(link->baseline_short_samples[0]))) {
        link->baseline_short_samples[link->baseline_short_count++] = amplitude;
    }
    uint16_t required = config->baseline_min_samples;
    if (required < 2U) required = 2U;
    if (required > EDGE_RESULT_V5_MAX_BASELINE_SAMPLES) {
        required = EDGE_RESULT_V5_MAX_BASELINE_SAMPLES;
    }
    if (link->baseline_count < required) return;
    link->baseline_center = median_values(link->baseline_long_samples,
                                          link->baseline_long_count);
    float deviations[64];
    const uint16_t stats_count = link->baseline_long_count;
    for (uint16_t index = 0U; index < stats_count; ++index) {
        deviations[index] = fabsf(link->baseline_long_samples[index] - link->baseline_center);
    }
    link->baseline_mad = fmaxf(median_values(deviations, stats_count),
                               fmaxf(link->temporal_noise_scale, FORMULA_FLEX_SAFE_SCALE));
    link->baseline_ready = true;
    link->baseline_learning_enabled = true;
    link->baseline_state = link->baseline_version == 0U
                               ? EDGE_RESULT_BASELINE_STABLE
                               : EDGE_RESULT_BASELINE_UPDATED;
    copy_text(link->baseline_update_reason, sizeof(link->baseline_update_reason),
              link->baseline_version == 0U ? "automatic_stable_candidate" :
                                             "stable_environment_rebase");
    if (link->baseline_version < UINT32_MAX) {
        ++link->baseline_version;
    }
    link->baseline_confidence = clampf_local(25.0f +
                                             75.0f * ((float)stats_count /
                                                      (float)EDGE_RESULT_V5_MAX_BASELINE_SAMPLES),
                                             0.0f, 100.0f);
    link->baseline_last_update_us = link->window_last_timestamp_us;
}

void formula_flex_promote_rebase_candidate(edge_result_link_state_t *link)
{
    if (link == NULL || link->rebase_candidate_count < 3U ||
        link->rebase_candidate_pause_windows != 0U ||
        link->rebase_candidate_stable_windows < link->rebase_candidate_count ||
        link->temporal_noise_confidence <= 0.0f ||
        link->rebase_candidate_start_us == 0U) return;
    const uint16_t count = link->rebase_candidate_count;
    link->baseline_center = median_values(link->rebase_candidate_samples, count);
    float deviations[32];
    for (uint16_t i = 0U; i < count; ++i) {
        deviations[i] = fabsf(link->rebase_candidate_samples[i] - link->baseline_center);
    }
    link->baseline_mad = fmaxf(median_values(deviations, count),
                               fmaxf(link->temporal_noise_scale, FORMULA_FLEX_SAFE_SCALE));
    link->baseline_ready = true;
    link->baseline_learning_enabled = true;
    if (link->baseline_version < UINT32_MAX) {
        ++link->baseline_version;
    }
    const float count_confidence = 100.0f *
        ((float)count /
         (float)(sizeof(link->rebase_candidate_samples) /
                 sizeof(link->rebase_candidate_samples[0])));
    link->baseline_confidence = fminf(link->temporal_noise_confidence,
                                      fmaxf(25.0f, count_confidence));
    link->baseline_state = EDGE_RESULT_BASELINE_UPDATED;
    link->baseline_last_update_us = link->window_last_timestamp_us;
    copy_text(link->baseline_update_reason, sizeof(link->baseline_update_reason),
              "automatic_environment_rebase");
    link->rebase_candidate_count = 0U;
    link->rebase_candidate_windows = 0U;
    link->rebase_candidate_stable_windows = 0U;
    link->rebase_candidate_pause_windows = 0U;
    link->rebase_candidate_dirty_windows = 0U;
    link->rebase_candidate_start_us = 0U;
    link->rebase_candidate_sum_motion = 0.0f;
    link->rebase_candidate_sum_spread = 0.0f;
    link->shift_candidate_windows = 0U;
    link->shift_candidate_start_us = 0U;
    link->occupied_windows = 0U;
    link->occupied_start_us = 0U;
    link->recovery_windows = 0U;
}
