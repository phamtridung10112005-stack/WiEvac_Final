#ifndef WIEVAC_EDGE_RESULT_V5_PIPELINE_H
#define WIEVAC_EDGE_RESULT_V5_PIPELINE_H

/*
 * Portable EdgeResult V5 reference pipeline.
 *
 * This module is the active V6 receiver scoring/encoding core. The legacy V4
 * app remains available as a compatibility path, but active V6 integration
 * must use this API and its protocol-5/schema-6 encoder.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define EDGE_RESULT_V5_MAGIC UINT32_C(0x57495635) /* "WIV5" */
#define EDGE_RESULT_V5_PROTOCOL_VERSION UINT8_C(5)
#define EDGE_RESULT_V5_SCHEMA_VERSION UINT16_C(7)
#define EDGE_RESULT_V5_SCHEMA_V5 UINT16_C(5)
#define EDGE_RESULT_V5_SCHEMA_V6 UINT16_C(6)
#define EDGE_RESULT_V5_SCHEMA_V7 UINT16_C(7)
#define EDGE_RESULT_V5_MESSAGE_TYPE UINT8_C(0x30)

/* Machine-readable lifecycle markers.  Keep these in the reference module so
 * tooling cannot mistake source presence for a production firmware claim. */
#define EDGE_RESULT_V5_C_FORMULA_STATUS "REFERENCE_ONLY"
#define EDGE_RESULT_V5_C_PARITY_STATUS "UNVERIFIED"
#define EDGE_RESULT_V5_C_ACTIVE_FIRMWARE_STATUS "NOT_READY"

#define EDGE_RESULT_V5_MAC_BYTES 6U
#define EDGE_RESULT_V5_MAX_CSI_BYTES 128U
#define EDGE_RESULT_V5_MAX_QUEUE 32U
#define EDGE_RESULT_V5_MAX_LINKS 8U
#define EDGE_RESULT_V5_MAX_BASELINE_SAMPLES 32U
#define EDGE_RESULT_V5_DOPPLER_CAPACITY 64U
#define EDGE_RESULT_V5_SCORE_HISTORY_CAPACITY 5U
#define EDGE_RESULT_V5_MAX_TEXT 32U
#define EDGE_RESULT_V5_MAX_MODEL_HASH 64U
/* Allocation capacity; the codec enforces a 700-byte payload / 714-byte wire
 * packet limit in the C implementation, matching the Python codec. */
#define EDGE_RESULT_V5_MAX_PACKET 768U

typedef enum {
    EDGE_RESULT_STATE_PASSABLE = 0,
    EDGE_RESULT_STATE_DEGRADED = 1,
    EDGE_RESULT_STATE_BLOCKED = 2,
    EDGE_RESULT_STATE_UNKNOWN = 3,
} edge_result_state_t;

typedef enum {
    EDGE_RESULT_REASON_NONE = 0,
    EDGE_RESULT_REASON_VALID = 1,
    EDGE_RESULT_REASON_WARMING_UP = 2,
    EDGE_RESULT_REASON_INVALID_CSI = 3,
    EDGE_RESULT_REASON_STALE = 4,
    EDGE_RESULT_REASON_OOD = 5,
    EDGE_RESULT_REASON_FORMULA_AI_DISAGREEMENT = 6,
    EDGE_RESULT_REASON_QUALITY_LOW = 7,
    EDGE_RESULT_REASON_WRONG_SOURCE = 100,
    EDGE_RESULT_REASON_TIMESTAMP_INVALID = 101,
    EDGE_RESULT_REASON_SEQUENCE_GAP = 102,
    EDGE_RESULT_REASON_PACKET_LOSS = 103,
    EDGE_RESULT_REASON_MODEL_NOT_READY = 104,
    EDGE_RESULT_REASON_MODEL_REJECTED = 105,
    EDGE_RESULT_REASON_QUEUE_DROP = 106,
    EDGE_RESULT_REASON_ENVIRONMENT_SHIFT = 107,
    EDGE_RESULT_REASON_PERSISTENT_OCCUPANCY = 108,
    EDGE_RESULT_REASON_BLOCKED = 109,
} edge_result_reason_t;

typedef enum {
    EDGE_RESULT_MODEL_NOT_READY = 0,
    EDGE_RESULT_MODEL_READY = 1,
    EDGE_RESULT_MODEL_REJECTED = 2,
    EDGE_RESULT_MODEL_OOD = 3,
} edge_result_model_state_t;

typedef enum {
    EDGE_RESULT_BASELINE_NO_BASELINE = 0,
    EDGE_RESULT_BASELINE_CANDIDATE = 1,
    EDGE_RESULT_BASELINE_STABLE = 2,
    EDGE_RESULT_BASELINE_SHIFT_CANDIDATE = 3,
    EDGE_RESULT_BASELINE_REBASE_PENDING = 4,
    EDGE_RESULT_BASELINE_UPDATED = 5,
    EDGE_RESULT_BASELINE_OCCUPIED_OR_BLOCKED = 6,
    EDGE_RESULT_BASELINE_UNKNOWN = 7,
} edge_result_baseline_state_t;

typedef struct {
    /* *_text fields are the canonical identity representation on the wire;
     * numeric fields are compatibility projections and may be zero for IDs
     * such as "link-1". */
    uint32_t device_id;
    uint32_t node_id;
    uint32_t tx_id;
    uint32_t rx_id;
    uint32_t link_id;
    char device_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char node_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char tx_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char rx_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char link_id_text[EDGE_RESULT_V5_MAX_TEXT];
    char corridor_id[EDGE_RESULT_V5_MAX_TEXT];
    char session_id[EDGE_RESULT_V5_MAX_TEXT];
    uint32_t boot_id;
    uint32_t window_seq;
} edge_result_identity_t;

typedef struct {
    uint32_t link_id;
    /* TX stream identity copied from the beacon/CSI metadata.  A sequence
     * number is only meaningful inside this boot domain. */
    uint32_t boot_id;
    uint64_t timestamp_us;
    uint32_t tx_sequence;
    uint8_t source_mac[EDGE_RESULT_V5_MAC_BYTES];
    uint16_t csi_length;
    uint8_t csi[EDGE_RESULT_V5_MAX_CSI_BYTES];
    bool first_word_invalid;
    int8_t rssi_dbm;
    int8_t noise_floor_dbm;
    uint8_t channel;
    uint8_t bandwidth;
    uint8_t sig_mode;
    uint8_t htltf_layout;
} edge_result_csi_record_t;

typedef struct {
    float common_amplitude;
    float amplitude_delta;
    float robust_spread;
    float temporal_motion;
    float rssi_dbm;
    float noise_floor_dbm;
    uint32_t sample_count;
    uint32_t invalid_count;
    uint32_t queue_drop_count;
    uint32_t sequence_gap;
    /* Legacy wire slot: RX-side CSI callback sequence-gap ratio only.
     * It is not UDP loss from RX to Pi and must not include queue drops. */
    float packet_loss_ratio;
    float jitter_ms;
} edge_result_features_t;

typedef struct {
    /* score is a bounded correction in score points, never an absolute score. */
    float score;
    float uncertainty;
    bool accepted;
    bool ood;
    bool disagreement;
    edge_result_model_state_t state;
} edge_result_ai_output_t;

struct edge_result_pipeline;
struct edge_result_link_state;

typedef float (*edge_result_formula_fn)(
    const edge_result_features_t *features,
    const struct edge_result_link_state *link,
    void *context);

typedef edge_result_ai_output_t (*edge_result_tiny_ai_fn)(
    const edge_result_features_t *features,
    float formula_score,
    const struct edge_result_link_state *link,
    void *context);

typedef struct {
    uint32_t device_id;
    uint32_t node_id;
    uint32_t tx_id;
    uint32_t rx_id;
    uint32_t link_id;
    const char *corridor_id;
    const char *session_id;
    uint32_t boot_id;
    uint32_t window_duration_us;
    uint16_t queue_capacity;
    uint16_t baseline_min_samples;
    float max_ai_correction;
    float stale_after_ms;
    const char *formula_version;
    const char *model_version;
    const char *model_hash;
    const char *model_target;
    const char *feature_schema_version;
    edge_result_formula_fn formula;
    edge_result_tiny_ai_fn tiny_ai;
    void *hook_context;
} edge_result_pipeline_config_t;

typedef struct edge_result_link_state {
    bool configured;
    edge_result_identity_t identity;
    uint8_t expected_tx_mac[EDGE_RESULT_V5_MAC_BYTES];
    bool expected_tx_mac_valid;
    /* CSI source boot is distinct from the boot identity of the result
     * producer.  Only the latter is serialized to Pi as stream identity. */
    uint32_t source_boot_id;
    uint32_t last_tx_sequence;
    uint64_t last_timestamp_us;
    uint64_t window_start_us;
    uint32_t window_sample_count;
    uint32_t window_invalid_count;
    uint32_t pending_invalid_count;
    uint32_t window_sequence_gap;
    uint32_t window_queue_drop_count;
    uint32_t pending_queue_drop_count;
    uint32_t window_received_count;
    uint64_t previous_sample_us;
    float previous_amplitude;
    bool previous_amplitude_valid;
    float window_sum_amplitude;
    float window_sum_delta;
    float window_sum_spread;
    float window_sum_motion;
    float window_sum_rssi;
    float window_sum_noise;
    uint64_t window_last_timestamp_us;
    uint64_t window_sum_interval_us;
    double window_sum_interval_sq_us;
    uint32_t window_interval_count;
    float baseline_samples[EDGE_RESULT_V5_MAX_BASELINE_SAMPLES];
    float baseline_short_samples[16];
    float baseline_long_samples[64];
    /* Bounded candidate buffer used to learn a relocated, quiet environment
     * without overwriting the active baseline prematurely. */
    float rebase_candidate_samples[32];
    uint16_t rebase_candidate_count;
    uint16_t baseline_count;
    uint16_t baseline_short_count;
    uint16_t baseline_long_count;
    bool baseline_ready;
    bool baseline_learning_enabled;
    /* A window containing invalid, lossy, or gapped input cannot train or
     * rebase the baseline. */
    bool baseline_window_eligible;
    bool explicit_baseline_confirmed;
    edge_result_baseline_state_t baseline_state;
    uint32_t baseline_version;
    uint32_t baseline_candidate_windows;
    uint32_t shift_candidate_windows;
    uint32_t rebase_candidate_windows;
    uint32_t rebase_candidate_stable_windows;
    uint16_t rebase_candidate_pause_windows;
    uint32_t occupied_windows;
    float baseline_confidence;
    char baseline_update_reason[EDGE_RESULT_V5_MAX_TEXT];
    float previous_baseline_center;
    float previous_baseline_mad;
    float baseline_center;
    float baseline_mad;
    /* Internal link-local temporal envelope; not serialized on the wire. */
    float temporal_noise_scale;
    float temporal_noise_confidence;
    /* Learned typical r for THIS link's empty spectrum. Occupancy freeze
     * compares current r to this envelope, so a school hall can relearn. */
    float doppler_ratio_scale;
    uint64_t temporal_noise_last_update_us;
    uint64_t baseline_last_update_us;
    uint64_t rebase_candidate_start_us;
    float rebase_candidate_sum_motion;
    float rebase_candidate_sum_spread;
    uint64_t shift_candidate_start_us;
    uint64_t occupied_start_us;
    uint16_t recovery_windows;
    uint16_t temporal_clean_windows;
    uint16_t temporal_shift_windows;
    /* One dirty window may pause learning; consecutive severe windows still
     * cancel it so invalid input never becomes baseline evidence. */
    uint16_t consecutive_severe_windows;
    uint32_t next_window_seq;
    uint32_t invalid_total;
    uint32_t queue_drop_total;
    uint32_t source_reject_total;
    uint32_t sequence_reject_total;
    uint32_t stale_reject_total;
    uint32_t last_rx_timestamp_us;
    /* Keep score transitions stable without discarding the raw evidence. */
    float last_local_passability_score;
    bool last_local_passability_valid;
    uint64_t last_result_window_end_us;
    float score_filter_history[EDGE_RESULT_V5_SCORE_HISTORY_CAPACITY];
    uint8_t score_filter_history_count;
    uint8_t score_filter_history_next;
    uint32_t score_filter_baseline_version;
    /* Recent corroborated occupancy, used to block rebase and slow recovery. */
    uint16_t occupancy_memory_windows;
    uint16_t stuck_low_score_windows;
    uint16_t score_filter_hold_windows;
    uint16_t score_corroborated_windows;
    uint16_t rebase_candidate_dirty_windows;
    /* Shadow Doppler buffer: last amplitudes in this window. Not on WIV5. */
    float doppler_amp[EDGE_RESULT_V5_DOPPLER_CAPACITY];
    uint64_t doppler_t[EDGE_RESULT_V5_DOPPLER_CAPACITY];
    uint16_t doppler_count;
} edge_result_link_state_t;

typedef struct {
    edge_result_csi_record_t records[EDGE_RESULT_V5_MAX_QUEUE];
    uint16_t capacity;
    uint16_t head;
    uint16_t tail;
    uint16_t count;
    uint32_t drop_count;
} edge_result_bounded_queue_t;

typedef struct {
    edge_result_pipeline_config_t config;
    edge_result_bounded_queue_t queue;
    edge_result_link_state_t links[EDGE_RESULT_V5_MAX_LINKS];
    uint8_t link_count;
} edge_result_pipeline_t;

typedef struct {
    bool score_valid;
    float local_passability_score;
    edge_result_state_t state;
    float quality_score;
    float uncertainty_score;
    bool disagreement;
    edge_result_reason_t reason_code;
    char formula_version[EDGE_RESULT_V5_MAX_TEXT];
    char model_version[EDGE_RESULT_V5_MAX_TEXT];
    char model_hash[EDGE_RESULT_V5_MAX_MODEL_HASH + 1U];
    char feature_schema_version[EDGE_RESULT_V5_MAX_TEXT];
    edge_result_identity_t identity;
    uint64_t window_start_us;
    uint64_t window_end_us;
    uint64_t rx_timestamp_us;
    uint32_t age_ms;
    uint32_t sample_count;
    uint32_t invalid_count;
    uint32_t queue_drop_count;
    uint32_t sequence_gap;
    /* Legacy wire slot: RX-side CSI callback sequence-gap ratio only.
     * It is not UDP loss from RX to Pi and must not include queue drops. */
    float packet_loss_ratio;
    float jitter_ms;
    edge_result_features_t features;
    float formula_score;
    float raw_evidence_score;
    float filtered_passability_score;
    edge_result_baseline_state_t baseline_state;
    uint32_t baseline_version;
    char baseline_update_reason[EDGE_RESULT_V5_MAX_TEXT];
    float baseline_confidence;
    edge_result_baseline_state_t drift_state;
    float ai_correction;
    edge_result_model_state_t model_state;
    char transition_state[EDGE_RESULT_V5_MAX_TEXT];
    float occupancy_evidence;
    float blocking_evidence;
    bool occupancy_evidence_valid;
    bool blocking_evidence_valid;
    bool raw_evidence_valid;
    bool filtered_passability_valid;
    /* Schema 7 shadow Doppler. r is walking-band energy vs this link's
     * learned empty spectrum, not P(person) and not a house-tuned %. */
    bool doppler_valid;
    float doppler_ratio;
    float doppler_fs_hz;
    uint16_t doppler_samples;
} edge_result_v5_t;

void edge_result_pipeline_config_defaults(edge_result_pipeline_config_t *config);

bool edge_result_pipeline_init(edge_result_pipeline_t *pipeline,
                               const edge_result_pipeline_config_t *config);

bool edge_result_pipeline_register_link(edge_result_pipeline_t *pipeline,
                                        uint32_t link_id,
                                        uint32_t tx_id,
                                        uint32_t rx_id,
                                        const uint8_t expected_tx_mac[EDGE_RESULT_V5_MAC_BYTES]);

/* Atomically acknowledge a new TX boot/stream epoch.  Delayed packets from
 * the old epoch are rejected until this handshake is performed. */
bool edge_result_pipeline_reset_link_boot(edge_result_pipeline_t *pipeline,
                                          uint32_t link_id,
                                          uint32_t boot_id);

/* Manual confirmation is optional; automatic bootstrap remains fail-closed
 * until its clean-history and quality gates are satisfied. */
bool edge_result_pipeline_confirm_empty(edge_result_pipeline_t *pipeline, uint32_t link_id);
bool edge_result_pipeline_revoke_empty(edge_result_pipeline_t *pipeline, uint32_t link_id);

/* This is the CSI callback boundary: bounded copy, constant-time checks and
 * a non-blocking queue admission. No DSP, model inference, I/O or logging is
 * performed here. */
bool edge_result_pipeline_submit_csi(edge_result_pipeline_t *pipeline,
                                     const edge_result_csi_record_t *record);

/* Run one bounded worker step. Returns true when an EdgeResult window closes. */
bool edge_result_pipeline_process_one(edge_result_pipeline_t *pipeline,
                                      edge_result_v5_t *result);

/* Force-close the current time window for one link (useful on shutdown). */
bool edge_result_pipeline_flush_link(edge_result_pipeline_t *pipeline,
                                     uint32_t link_id,
                                     edge_result_v5_t *result);

const edge_result_link_state_t *edge_result_pipeline_link(
    const edge_result_pipeline_t *pipeline, uint32_t link_id);

uint32_t edge_result_pipeline_queue_drops(const edge_result_pipeline_t *pipeline);

int edge_result_v5_encode(const edge_result_v5_t *result,
                          uint8_t *packet,
                          size_t capacity,
                          size_t *encoded_length);

int edge_result_v5_decode(const uint8_t *packet,
                          size_t length,
                          edge_result_v5_t *result,
                          edge_result_reason_t *reject_reason);

const char *edge_result_state_name(edge_result_state_t state);
const char *edge_result_reason_name(edge_result_reason_t reason);

#ifdef __cplusplus
}
#endif

#endif /* WIEVAC_EDGE_RESULT_V5_PIPELINE_H */
