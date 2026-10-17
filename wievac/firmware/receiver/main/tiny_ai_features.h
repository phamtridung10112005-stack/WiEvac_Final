#ifndef WIEVAC_TINY_AI_FEATURES_H
#define WIEVAC_TINY_AI_FEATURES_H

/*
 * Dynamic 42-vector. Static multipath gain is not an input.
 * Layout: var[8], roughness[8], ac1[8], ac5[8], lowband[8], xcorr(0,7), xcorr(3,4).
 * Short window 2 s at 20 Hz buckets. Long window 30 s at 8 Hz (240 buckets).
 * Operation order is the Python parity contract in pi/app/tiny_ai_features.py.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TINY_AI_BANDS 8
#define TINY_AI_TONES 52
#define TINY_AI_FEATURES 42
#define TINY_AI_FEATURE_SCHEMA "dyn-42-v1"
#define TINY_AI_SHORT_BUCKET_US 50000ULL
#define TINY_AI_LONG_BUCKET_US 125000ULL
#define TINY_AI_LONG_N 240U
#define TINY_AI_SHORT_CAP 48U
#define TINY_AI_EMIT_STEP_US 1000000ULL
/* The noise reference is the median of the first N short-window variances
 * (one per emit, about N s). A single 2 s window can land far below the
 * typical level, and the reference never rises afterwards, which locked the
 * gate at "not empty" for a whole boot. Features are withheld until ready. */
#define TINY_AI_NOISE_INIT_N 31U

typedef struct {
    uint64_t key;
    float sum[TINY_AI_BANDS];
    uint16_t count;
    bool open;
} tiny_ai_acc_t;

typedef struct {
    uint64_t key;
    float mean[TINY_AI_BANDS];
} tiny_ai_bucket_t;

/* About 13 KB. BSS or static only; not a small-task stack local. */
typedef struct tiny_ai_extractor {
    tiny_ai_bucket_t short_q[TINY_AI_SHORT_CAP];
    uint16_t short_n;
    tiny_ai_acc_t short_acc;
    float long_q[TINY_AI_LONG_N][TINY_AI_BANDS];
    uint16_t long_n;
    tiny_ai_acc_t long_acc;
    float noise[TINY_AI_BANDS];
    bool noise_ready[TINY_AI_BANDS];
    float noise_init[TINY_AI_NOISE_INIT_N][TINY_AI_BANDS];
    uint16_t noise_init_n;
    float features[TINY_AI_FEATURES];
    uint64_t feature_ts;
    uint64_t last_emit_ts;
    uint64_t last_ts;
    bool have_ts;
    bool have_features;
    bool emit_started;
} tiny_ai_extractor_t;

void tiny_ai_extractor_init(tiny_ai_extractor_t *extractor);

/* HT20 CSI: imag, real int8 pairs. Index 32 is DC, matching noise_filter. */
bool tiny_ai_amplitudes_from_csi(const uint8_t *csi, uint16_t csi_length, float out[TINY_AI_TONES]);

/* Returns true when a new 42-vector is emitted. out may be NULL. */
bool tiny_ai_extractor_push(tiny_ai_extractor_t *extractor, uint64_t timestamp_us,
                            const float amplitudes[TINY_AI_TONES], float out[TINY_AI_FEATURES]);

bool tiny_ai_extractor_latest(const tiny_ai_extractor_t *extractor, float out[TINY_AI_FEATURES],
                              uint64_t *timestamp_us);

#ifdef __cplusplus
}
#endif

#endif /* WIEVAC_TINY_AI_FEATURES_H */
