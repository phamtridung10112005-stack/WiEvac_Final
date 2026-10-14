#ifndef WIEVAC_TINY_AI_MODEL_WEIGHTS_H
#define WIEVAC_TINY_AI_MODEL_WEIGHTS_H

/*
 * Default artifact. No dataset has been trained.
 * train_tiny_ai.py overwrites this header only after a real LOCO winner.
 * PRESENT 0 keeps the firmware on the existing MAD z-score path.
 */

#define TINY_AI_MODEL_PRESENT 0
#define TINY_AI_MODEL_RUNG 0
#define TINY_AI_MODEL_VERSION "NOT_READY"
#define TINY_AI_MODEL_HASH ""

static const unsigned tiny_ai_weight_blob_len = 0U;
static const unsigned char tiny_ai_weight_blob[1] = {0};

#endif /* WIEVAC_TINY_AI_MODEL_WEIGHTS_H */
