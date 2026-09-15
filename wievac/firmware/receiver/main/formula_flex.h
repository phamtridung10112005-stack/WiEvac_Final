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

#ifdef __cplusplus
}
#endif

#endif /* WIEVAC_FORMULA_FLEX_H */
