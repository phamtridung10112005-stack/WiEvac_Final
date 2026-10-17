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

/* Path loss P_norm = P_rx * (d / d0)^gamma lives on the Pi, once, from
 * topology nominal_distance_m. Firmware always emits the d0 = 1 m score. */
#define FORMULA_FLEX_PATH_GAMMA 1.8f
#define FORMULA_FLEX_PATH_D0_M 1.0f
/* Quiet windows required before the first W_base lock. Motion resets it. */
#define FORMULA_FLEX_CAP_HOLD_US 5000000ULL
#define FORMULA_FLEX_MOTION_GATE 1.5f

/* Sum of |H_k|^2 at d0 = 1 m (gain = 1). distance_m is ignored.
 * NAN when power is not usable. */
float formula_flex_capacity_base(float power, float distance_m);

/* W_cap = W_base * (1 - max(N_null / N_total, SCI)). */
float formula_flex_capacity_score(float base, float sci, uint16_t n_null,
                                  uint16_t n_total);

/* People moving through the link. Static attenuation stays in W_cap.
 * Kept for metadata and older tests. Displayed scores use the knife-edge. */
float formula_flex_dynamic_passability(const edge_result_features_t *features,
                                       const edge_result_link_state_t *link);

/* RTI link noise is about 1 dB (RSSI std ~0.97 dB). A tone inside this
 * band counts as an open path so an empty hall reads near 100%. */
#define FORMULA_FLEX_KE_DEADBAND_DB 1.0f

/* Fraction (0..1) of the link's first Fresnel zone blocked, from one tone's
 * attenuation in dB (ITU-R P.526 knife-edge, closed-form inverse).
 * A person occluding the 2.4 GHz ray is about +10 dB (Januszkiewicz, Sensors
 * 2018). b is the blocked fraction of THIS link's Fresnel path (about 0.8 m
 * across at the middle of a 5 m link), not a fraction of corridor width. */
float formula_flex_knife_edge_blocked(float attenuation_db);

/* Mean blocked fraction over tones: A_k = 20·log10(ref_k / now_k) − deadband,
 * clamped at 0 (a stronger tone counts as 0 dB). Skips NaN / ≤0 bins.
 * Returns NAN if fewer than 4 usable bins. */
float formula_flex_blocked_fraction(const float *now, const float *ref, uint16_t count);

/* Knife-edge: below ~5 dB the obstacle edge stays outside the clear
 * Fresnel core (v < ~0), i.e. ≤30% of the zone blocked → path open. */
#define FORMULA_FLEX_KE_BLOCKED_PCT 70.0f

/* One AGC step is a fraction of a dB; a person on the link is ~10 dB.
 * 3 dB drops the step from the regression without eating a real AGC step.
 * 30 events is enough for a one-parameter slope to be tested. */
#define FORMULA_FLEX_GAIN_RESP_MIN_EVENTS 30U
#define FORMULA_FLEX_GAIN_RESP_OUTLIER_DB 3.0f

/* Through-origin regression of raw CSI steps on library gain steps.
 * a ∝ g^(-s). s = 1 is full compensation, s = 0 is none. */
typedef struct {
    double sxx, sxy, syy;
    uint32_t events;
    uint32_t frames;
} formula_flex_gain_response_t;

void formula_flex_gain_response_reset(formula_flex_gain_response_t *st);
void formula_flex_gain_response_add(formula_flex_gain_response_t *st, float x_db, float y_db);
float formula_flex_gain_response_s(const formula_flex_gain_response_t *st);

#ifdef __cplusplus
}
#endif

#endif /* WIEVAC_FORMULA_FLEX_H */
