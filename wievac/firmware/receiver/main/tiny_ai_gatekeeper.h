#ifndef WIEVAC_TINY_AI_GATEKEEPER_H
#define WIEVAC_TINY_AI_GATEKEEPER_H

/*
 * Fail-closed novelty gate. NOT_READY (no attested weights) is a bypass:
 * the existing MAD z-score path is unchanged. A ready model may only lower
 * the published score, force DEGRADED, or refuse a new baseline. It cannot
 * raise a score to PASSABLE.
 */

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TINY_AI_SCORE_PENALTY 15.0f

typedef struct {
    bool ready;
    bool attested;
    bool empty_like;
    float empty_score;
} tiny_ai_gate_status_t;

void tiny_ai_gate_reset(void);
void tiny_ai_gate_ingest(uint32_t link_id, uint64_t timestamp_us,
                         const uint8_t *csi, uint16_t csi_length);
void tiny_ai_gate_status(uint64_t now_us, tiny_ai_gate_status_t *status);
/* True when a new baseline / STATIC_CHANGE is allowed. */
bool tiny_ai_gate_allows_baseline(uint64_t now_us);
/* True when boot does not look dynamically occupied. */
bool tiny_ai_gate_boot_is_quiet(uint64_t now_us);
bool tiny_ai_gate_score_features(const float features[42], float *empty_score, bool *empty_like);
const char *tiny_ai_gate_model_version(void);
const char *tiny_ai_gate_model_hash(void);

#ifdef __cplusplus
}
#endif

#endif /* WIEVAC_TINY_AI_GATEKEEPER_H */
