/* Portable C encoder vector for EdgeResult schema 6 parity checks.
 * Compile with the ESP-IDF toolchain (no ESP headers are required):
 *   xtensa-esp32s3-elf-gcc -std=c11 -I firmware/receiver/main -c ...
 * The populated value intentionally uses the canonical textual identities
 * consumed by the Pi allow-list (link-1/rx-1/tx-1), not numeric projections.
 */
#include "../firmware/receiver/main/edge_result_v5_pipeline.h"
#include <string.h>

int edge_result_v6_vector(edge_result_v5_t *out, uint8_t *packet,
                          size_t capacity, size_t *length)
{
    if (out == NULL || packet == NULL || length == NULL) return -1;
    memset(out, 0, sizeof(*out));
    out->score_valid = true;
    out->local_passability_score = 72.5f;
    out->state = EDGE_RESULT_STATE_DEGRADED;
    out->quality_score = 91.0f;
    out->uncertainty_score = 12.5f;
    out->disagreement = false;
    out->reason_code = EDGE_RESULT_REASON_VALID;
    strcpy(out->formula_version, "formula-flex-v5.2-rx-median-mad");
    strcpy(out->model_version, "NOT_READY");
    out->model_hash[0] = '\0';
    strcpy(out->feature_schema_version, "7");
    strcpy(out->identity.device_id_text, "device-1");
    strcpy(out->identity.node_id_text, "rx-1");
    strcpy(out->identity.tx_id_text, "tx-1");
    strcpy(out->identity.rx_id_text, "rx-1");
    strcpy(out->identity.link_id_text, "link-1");
    strcpy(out->identity.corridor_id, "corridor-01");
    strcpy(out->identity.session_id, "parity-session");
    out->identity.boot_id = 7U;
    out->identity.window_seq = 9U;
    out->window_start_us = 1000000ULL;
    out->window_end_us = 2000000ULL;
    out->rx_timestamp_us = 2000000ULL;
    out->age_ms = 0U;
    out->sample_count = 8U;
    out->invalid_count = 0U;
    out->queue_drop_count = 0U;
    out->sequence_gap = 0U;
    out->packet_loss_ratio = 0.0f;
    out->jitter_ms = 1.25f;
    out->baseline_state = EDGE_RESULT_BASELINE_STABLE;
    out->baseline_version = 2U;
    strcpy(out->baseline_update_reason, "stable_window");
    out->baseline_confidence = 88.0f;
    out->drift_state = EDGE_RESULT_BASELINE_STABLE;
    strcpy(out->transition_state, "STABLE");
    out->model_state = EDGE_RESULT_MODEL_NOT_READY;
    out->raw_evidence_valid = true; out->raw_evidence_score = 70.0f;
    out->filtered_passability_valid = true; out->filtered_passability_score = 72.5f;
    out->occupancy_evidence_valid = true; out->occupancy_evidence = 10.0f;
    out->blocking_evidence_valid = true; out->blocking_evidence = 5.0f;
    return edge_result_v5_encode(out, packet, capacity, length);
}
