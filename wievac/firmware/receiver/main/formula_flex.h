#ifndef WIEVAC_FORMULA_FLEX_H
#define WIEVAC_FORMULA_FLEX_H

#include "edge_result_v5_pipeline.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Legacy contract names retained for source compatibility only.  They are
 * not used as active occupancy, blocking, or score decisions. */
#define FORMULA_FLEX_REFERENCE_BASELINE_DELTA_MOTION 8.0f
#define FORMULA_FLEX_REFERENCE_BASELINE_DELTA_BLOCK 4.0f
#define FORMULA_FLEX_REFERENCE_SCORE_BLOCK 20.0f
#define FORMULA_FLEX_REFERENCE_UNCERTAINTY_PENALTY 20.0f

float formula_flex_baseline_deviation(const edge_result_link_state_t *link,
                                      float amplitude);

float formula_flex_default_formula(const edge_result_features_t *features,
                                   const edge_result_link_state_t *link,
                                   void *context);

void formula_flex_update_baseline(edge_result_link_state_t *link,
                                  const edge_result_pipeline_config_t *config,
                                  float amplitude,
                                  float delta);

void formula_flex_promote_rebase_candidate(edge_result_link_state_t *link);

/* True when the quiet rebase candidate is an attenuation of the empty-corridor
 * reference (or of the current baseline if no calibration is stored). That
 * candidate must not become the new baseline. */
bool formula_flex_rebase_absorbs_attenuation(const edge_result_link_state_t *link);

/* 1 - cosine of mean-normalized amplitudes. Gain is removed. This is a shape
 * change, not a width in metres. */
float formula_flex_shape_change(const float *now, const float *ref, uint16_t count);

/* Bins where now < alpha * ref. This is a count, not a width in metres. */
uint16_t formula_flex_null_count(const float *now, const float *ref,
                                 uint16_t count, float alpha);

#define FORMULA_FLEX_NULL_ALPHA 0.5f

/* Cosine dissimilarity above this holds a quiet rebase. Knob: raise if an
 * empty hall's ordinary multipath drift is marked static_change. */
#define FORMULA_FLEX_SCI_HOLD 0.25f
#define FORMULA_FLEX_SCORE_K 0.55f
/* dB of attenuation that maps to stress 1. 6 dB stays near the old index 33. */
#define FORMULA_FLEX_ATTEN_STRESS_DB 5.45f

#ifdef __cplusplus
}
#endif

#endif /* WIEVAC_FORMULA_FLEX_H */
