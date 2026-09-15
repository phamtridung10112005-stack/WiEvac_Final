/* WiEvac receiver: V6 compact EdgeResult active path with V4 ESP-NOW control compatibility. */

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "esp_check.h"
#include "esp_csi_gain_ctrl.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "edge_result_v5_pipeline.h"

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 0, 0) || \
    ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
#error "WiEvac receiver requires ESP-IDF >= 5.0 and < 5.5"
#endif

/* Per-device deployment values come from sdkconfig/Kconfig. */
#ifndef CONFIG_WIEVAC_WIFI_SSID
#define CONFIG_WIEVAC_WIFI_SSID           ""
#endif
#ifndef CONFIG_WIEVAC_WIFI_PASSWORD
#define CONFIG_WIEVAC_WIFI_PASSWORD       ""
#endif
#ifndef CONFIG_WIEVAC_PI_IP
#define CONFIG_WIEVAC_PI_IP               ""
#endif
#ifndef CONFIG_WIEVAC_PI_UDP_PORT
#define CONFIG_WIEVAC_PI_UDP_PORT         8888
#endif
#ifndef CONFIG_WIEVAC_EXPECTED_TX_MAC
#define CONFIG_WIEVAC_EXPECTED_TX_MAC     ""
#endif
#ifndef CONFIG_WIEVAC_RX_ID
#define CONFIG_WIEVAC_RX_ID               1
#endif
#ifndef CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
#define CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE 1
#endif
#define WIEVAC_RX_ID                     ((uint32_t)CONFIG_WIEVAC_RX_ID)
#define WIEVAC_LINK_ID                   WIEVAC_RX_ID
#define WIEVAC_TX_ID                     UINT32_C(1)
#define WIEVAC_WIFI_SSID                 CONFIG_WIEVAC_WIFI_SSID
#define WIEVAC_WIFI_PASSWORD             CONFIG_WIEVAC_WIFI_PASSWORD
#define WIEVAC_PI_IP                     CONFIG_WIEVAC_PI_IP
#define WIEVAC_PI_UDP_PORT               ((uint16_t)CONFIG_WIEVAC_PI_UDP_PORT)
#define WIEVAC_WIFI_CHANNEL              11U
#define WIEVAC_WIFI_BANDWIDTH            WIFI_BW_HT20
#define WIEVAC_ESPNOW_RATE               WIFI_PHY_RATE_MCS0_LGI
#define WIEVAC_MCS_INDEX                 0U
#define WIEVAC_RATE_CODE                 ((uint32_t)WIEVAC_ESPNOW_RATE & 0xffU)
#define WIEVAC_STATUS_LOG_INTERVAL_US    10000000ULL
#define WIEVAC_WINDOW_MS                 1000U
#define WIEVAC_SNAPSHOT_RATE_HZ          2U
#define WIEVAC_DYNAMIC_FRAME_RATE_HZ     25U
#define WIEVAC_GAIN_STATS_CAPACITY       64U
#define WIEVAC_GAIN_STATS_MIN_SAMPLES    16U
#define WIEVAC_GAIN_TRAINING_SAMPLES     200U
#define WIEVAC_INVALID_CSI_RETRAIN_DEBOUNCE 5U
#define WIEVAC_MAX_CLIPPED_IQ_RATIO      0.20f
#define WIEVAC_MAX_ZERO_IQ_RATIO         0.60f
#define WIEVAC_CSI_QUEUE_DEPTH           24U
#define WIEVAC_CONTROL_QUEUE_DEPTH       16U
#define WIEVAC_UDP_QUEUE_DEPTH           24U
#define WIEVAC_WINDOW_CAPACITY           128U
#define WIEVAC_SHAPE_BINS                8U
#define WIEVAC_HTLTF_BYTES               128U
#define WIEVAC_SNAPSHOT_METADATA_BYTES   16U
#define WIEVAC_SNAPSHOT_FLAG_RF_LEVEL_CHANGE   0x01U
#define WIEVAC_SNAPSHOT_FLAG_GAIN_DIAGNOSTIC   0x02U
#define WIEVAC_SNAPSHOT_BYTES            (WIEVAC_SNAPSHOT_METADATA_BYTES + WIEVAC_HTLTF_BYTES)
#define WIEVAC_LOG_EPSILON               0.001f
#define WIEVAC_FAST_TIME_CONSTANT_US     150000.0f
#define WIEVAC_SLOW_TIME_CONSTANT_US     1500000.0f
#define WIEVAC_GAIN_FAILURE_DEBOUNCE     5U

#define WIEVAC_STRINGIFY_INNER(value)    #value
#define WIEVAC_STRINGIFY(value)          WIEVAC_STRINGIFY_INNER(value)
/* Keep the configured identity discoverable in the active V6 image. */
static const char *const WIEVAC_BINARY_ID_MARKER __attribute__((used)) =
    "V6 RX protocol=5 schema=7 rx_id=" WIEVAC_STRINGIFY(CONFIG_WIEVAC_RX_ID);

#define V4_MAGIC                         UINT32_C(0x57495634) /* WIV4 */
#define V4_VERSION                       4U
#define V4_SCHEMA                        4U
#define V4_HEADER_SIZE                   48U
#define V4_CRC_OFFSET                    44U
#define V4_FEATURE_FLOATS                20U
#define V4_FEATURE_COUNTERS              20U
#define V4_FEATURE_PAYLOAD_SIZE          (V4_FEATURE_FLOATS * 4U + V4_FEATURE_COUNTERS * 4U + 4U * 8U)
#define V4_DYNAMIC_FLOATS                (WIEVAC_SHAPE_BINS + 4U)
#define V4_DYNAMIC_COUNTERS              15U
#define V4_DYNAMIC_PAYLOAD_SIZE          (V4_DYNAMIC_FLOATS * 4U + V4_DYNAMIC_COUNTERS * 4U + 2U * 8U)
#define V4_MAX_PACKET_SIZE               EDGE_RESULT_V5_MAX_PACKET

#define V4_MSG_HELLO                     0x01U
#define V4_MSG_PAIR_REPLY                0x02U
#define V4_MSG_MEASUREMENT               0x10U
#define V4_MSG_FEATURE                   0x20U
#define V4_MSG_SNAPSHOT                  0x21U
#define V4_MSG_DYNAMIC_FRAME             0x22U

#define NETWORK_READY_BIT                BIT0
#define WIFI_CONNECTED_BIT               BIT1

typedef struct {
    uint8_t type;
    uint16_t payload_length;
    uint16_t schema;
    uint32_t link_id;
    uint32_t tx_id;
    uint32_t rx_id;
    uint32_t boot_id;
    uint32_t gain_epoch;
    uint32_t sequence;
    uint64_t timestamp_us;
} v4_header_t;

typedef struct {
    uint8_t source_mac[ESP_NOW_ETH_ALEN];
    uint16_t length;
    uint8_t bytes[V4_HEADER_SIZE];
    uint64_t arrival_us;
} control_event_t;

typedef struct {
    uint8_t source_mac[ESP_NOW_ETH_ALEN];
    uint8_t csi[WIEVAC_HTLTF_BYTES];
    uint16_t length;
    bool first_word_invalid;
    wifi_pkt_rx_ctrl_t rx_ctrl;
    uint64_t arrival_us;
    bool tx_context_valid;
    uint32_t tx_boot_id;
    uint32_t tx_sequence;
    uint64_t tx_timestamp_us;
    uint32_t rx_sequence;
    uint32_t sequence_gap;
} csi_record_t;

typedef struct {
    uint16_t length;
    uint8_t bytes[V4_MAX_PACKET_SIZE];
} udp_packet_t;

typedef struct {
    bool valid;
    uint8_t tx_mac[ESP_NOW_ETH_ALEN];
    uint32_t tx_boot_id;
} pair_state_t;

typedef enum {
    GAIN_NOT_READY = 0,
    GAIN_READY = 1,
} gain_state_t;

typedef struct {
    bool initialized;
    uint32_t tx_boot_id;
    uint32_t last_sequence;
    uint64_t last_timestamp_us;
    uint32_t sequence_gap;
    uint32_t latest_tx_sequence;
    uint64_t latest_tx_timestamp_us;
    uint64_t latest_rx_arrival_us;
    uint32_t last_csi_tx_boot_id;
    uint32_t last_csi_tx_sequence;
    uint64_t last_csi_tx_timestamp_us;
    uint32_t received;
    uint32_t lost;
} measurement_tracker_t;

typedef struct {
    gain_state_t gain_state;
    bool gain_baseline_ready;
    uint32_t gain_training_samples;
    uint32_t gain_epoch;
    uint32_t gain_diagnostic_samples;
    uint32_t consecutive_invalid_csi;
    size_t gain_stats_count;
    size_t gain_stats_next;
    float gain_agc_stats[WIEVAC_GAIN_STATS_CAPACITY];
    float gain_fft_stats[WIEVAC_GAIN_STATS_CAPACITY];
    float gain_rssi_stats[WIEVAC_GAIN_STATS_CAPACITY];
    float gain_common_stats[WIEVAC_GAIN_STATS_CAPACITY];
    bool trend_initialized;
    float fast_level;
    float slow_level;
    uint64_t window_start_us;
    uint64_t last_snapshot_us;
    uint64_t last_dynamic_frame_us;
    bool pending_shape_invalid;
    uint64_t last_sample_us;
    uint32_t consecutive_gain_failures;
    uint32_t start_csi_queue_drops;
    uint32_t start_invalid_csi;
    uint32_t start_control_drops;
    uint32_t start_udp_drops;
    uint32_t start_stale_record_drops;
    uint32_t start_gain_compensation_failures;
    uint32_t start_gain_baseline_retrains;
    uint32_t start_gain_epoch_changes;
    uint32_t pending_gain_compensation_failures;
    uint32_t pending_gain_baseline_retrains;
    uint32_t pending_gain_epoch_changes;
    uint32_t window_gain_compensation_failures;
    uint32_t pending_feature_counters[V4_FEATURE_COUNTERS];
    uint64_t window_start_tx_us;
    uint32_t last_tx_boot_id;
    uint64_t last_tx_timestamp_us;
    uint32_t last_tx_sequence;
    uint32_t last_rx_sequence;
    uint32_t valid_samples;
    uint32_t discarded_samples;
    uint32_t gain_ready_samples;
    uint32_t gain_not_ready_samples;
    float common[WIEVAC_WINDOW_CAPACITY];
    float shape_spread[WIEVAC_WINDOW_CAPACITY];
    float temporal_motion[WIEVAC_WINDOW_CAPACITY];
    float rssi[WIEVAC_WINDOW_CAPACITY];
    float noise_floor[WIEVAC_WINDOW_CAPACITY];
    float agc[WIEVAC_WINDOW_CAPACITY];
    float fft[WIEVAC_WINDOW_CAPACITY];
    float shape_bins[WIEVAC_SHAPE_BINS][WIEVAC_WINDOW_CAPACITY];
} dsp_state_t;

static const char *TAG = "wievac_rx_v6";
static portMUX_TYPE g_pair_mux = portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE g_measurement_mux = portMUX_INITIALIZER_UNLOCKED;
static QueueHandle_t g_control_queue;
static QueueHandle_t g_csi_queue;
static QueueHandle_t g_udp_queue;
static EventGroupHandle_t g_network_events;
static pair_state_t g_pair;
static measurement_tracker_t g_measurements;
static uint8_t g_rx_mac[ESP_NOW_ETH_ALEN];
static uint8_t g_expected_tx_mac[ESP_NOW_ETH_ALEN];
static bool g_expected_tx_mac_configured;
static bool g_expected_tx_mac_invalid;
static uint32_t g_rx_boot_id;
static uint32_t g_feature_sequence;
static uint32_t g_snapshot_sequence;
static uint32_t g_dynamic_sequence;
static uint32_t g_rx_sequence;
static uint32_t g_pair_sequence;
static uint32_t g_csi_queue_drops;
static uint32_t g_invalid_csi;
static uint32_t g_csi_shape_rejects;
static uint32_t g_csi_mac_rejects;
static uint32_t g_csi_header_rejects;
static uint32_t g_csi_context_rejects;
static uint32_t g_csi_sequence_gaps;
static uint32_t g_csi_foreign_drops;
static uint32_t g_control_drops;
static uint32_t g_udp_drops;
/* Split UDP loss by queue admission versus socket/network delivery. */
static uint32_t g_udp_queue_drops;
static uint32_t g_udp_send_failures;
static uint32_t g_v6_encode_failures;
static uint32_t g_stale_record_drops;
static uint32_t g_gain_compensation_failures;
static uint32_t g_gain_baseline_retrains;
static uint32_t g_gain_epoch_changes;
static uint32_t g_gain_transitions;
static uint32_t g_rf_level_changes;
static uint32_t g_dynamic_frame_drops;
static uint32_t g_measurement_duplicates;
static uint32_t g_measurement_reorders;
static bool g_logged_csi_shape_reject;
static bool g_logged_csi_mac_reject;
static bool g_logged_csi_header_reject;
static bool g_logged_csi_context_reject;
static int g_first_csi_len = -1;
static int g_first_csi_payload_len = -1;
static uint8_t g_first_csi_src[ESP_NOW_ETH_ALEN];
static uint8_t g_first_csi_dst[ESP_NOW_ETH_ALEN];
static uint8_t g_first_csi_payload[4];
static uint32_t g_dsp_reset_generation;
static uint32_t g_wifi_reconnect_count;
static struct sockaddr_in g_pi_address;
static dsp_state_t g_dsp_state;
static csi_record_t g_drain_record;
#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
static edge_result_pipeline_t g_v6_pipeline;
static bool g_v6_pipeline_ready;
static uint32_t g_v6_tx_boot_id;
#endif

static void drain_csi_queue(void);
static bool enqueue_udp(const uint8_t *packet, uint16_t length);
static bool csi_layout_valid(const csi_record_t *record);

#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
static void v6_pipeline_start_for_pair(const pair_state_t *pair)
{
    if (pair == NULL || !pair->valid) return;
    if (g_v6_pipeline_ready && g_v6_tx_boot_id == pair->tx_boot_id) return;
    if (g_v6_pipeline_ready) {
        /* A TX boot change resets only CSI-source state. Preserve the RX
         * result sequence so Pi never sees a lower sequence in the same RX
         * boot stream. */
        if (!edge_result_pipeline_reset_link_boot(&g_v6_pipeline, WIEVAC_LINK_ID,
                                                   pair->tx_boot_id)) {
            ESP_LOGE(TAG, "v6_pipeline_source_boot_reset_failed");
            return;
        }
        g_v6_tx_boot_id = pair->tx_boot_id;
        ESP_LOGI(TAG, "edge_result_v6 source_boot_reset tx_boot=%" PRIu32
                 " result_boot=%" PRIu32 " link=link-%" PRIu32,
                 pair->tx_boot_id, g_rx_boot_id, WIEVAC_LINK_ID);
        return;
    }
    edge_result_pipeline_config_t config;
    edge_result_pipeline_config_defaults(&config);
    /* Corridor topology owns the physical device identity. Both receivers
     * are nodes on device-1; node/link/rx IDs remain link-specific below. */
    config.device_id = UINT32_C(1);
    config.node_id = WIEVAC_RX_ID;
    config.tx_id = WIEVAC_TX_ID;
    config.rx_id = WIEVAC_RX_ID;
    config.boot_id = g_rx_boot_id;
    config.corridor_id = "corridor-01";
    config.session_id = "runtime";
    config.formula_version = "formula-flex-v5.3-rx-median-mad";
    config.model_version = "NOT_READY";
    config.feature_schema_version = "7";
    if (!edge_result_pipeline_init(&g_v6_pipeline, &config) ||
        !edge_result_pipeline_register_link(&g_v6_pipeline, WIEVAC_LINK_ID,
                                            WIEVAC_TX_ID, WIEVAC_RX_ID, pair->tx_mac)) {
        ESP_LOGE(TAG, "v6_pipeline_init_failed");
        g_v6_pipeline_ready = false;
        return;
    }
    (void)edge_result_pipeline_reset_link_boot(&g_v6_pipeline, WIEVAC_LINK_ID, pair->tx_boot_id);
    g_v6_tx_boot_id = pair->tx_boot_id;
    g_v6_pipeline_ready = true;
    ESP_LOGI(TAG, "edge_result_v6 active protocol=5 schema=7 link=link-%" PRIu32 " node=rx-%" PRIu32,
             WIEVAC_LINK_ID, WIEVAC_RX_ID);
}

static void v6_submit_and_emit(const csi_record_t *record)
{
    if (!g_v6_pipeline_ready || record == NULL) return;
    if (!csi_layout_valid(record)) {
        __atomic_add_fetch(&g_invalid_csi, 1U, __ATOMIC_RELAXED);
        return;
    }
    edge_result_csi_record_t input = {0};
    input.link_id = WIEVAC_LINK_ID;
    input.boot_id = record->tx_boot_id;
    /* The TX timestamp restarts at each TX boot. Results are ordered by the
     * RX producer boot, so put the RX monotonic arrival time on the wire;
     * TX boot/sequence remain the source-ordering context above. */
    input.timestamp_us = record->arrival_us;
    input.tx_sequence = record->tx_sequence;
    memcpy(input.source_mac, record->source_mac, EDGE_RESULT_V5_MAC_BYTES);
    if (record->length == 0U || record->length > EDGE_RESULT_V5_MAX_CSI_BYTES) {
        /* The compact pipeline owns a bounded CSI copy; never advertise or
         * copy bytes beyond its fixed storage. */
        __atomic_add_fetch(&g_invalid_csi, 1U, __ATOMIC_RELAXED);
        return;
    }
    input.csi_length = record->length;
    memcpy(input.csi, record->csi, input.csi_length);
    input.first_word_invalid = record->first_word_invalid;
    input.rssi_dbm = record->rx_ctrl.rssi;
    input.noise_floor_dbm = record->rx_ctrl.noise_floor;
    input.channel = record->rx_ctrl.channel;
    input.bandwidth = record->rx_ctrl.cwb;
    input.sig_mode = record->rx_ctrl.sig_mode;
    input.htltf_layout = WIEVAC_HTLTF_BYTES;
    if (!edge_result_pipeline_submit_csi(&g_v6_pipeline, &input)) return;
    edge_result_v5_t result;
    while (edge_result_pipeline_process_one(&g_v6_pipeline, &result)) {
        ESP_LOGI(TAG,
                 "doppler valid=%d fs=%.1f r=%.3f n=%u score=%.1f raw=%.1f occ=%.1f",
                 result.doppler_valid ? 1 : 0, (double)result.doppler_fs_hz,
                 (double)result.doppler_ratio, (unsigned)result.doppler_samples,
                 result.score_valid ? (double)result.formula_score : -1.0,
                 result.raw_evidence_valid ? (double)result.raw_evidence_score : -1.0,
                 result.occupancy_evidence_valid ? (double)result.occupancy_evidence : 0.0);
        uint8_t packet[EDGE_RESULT_V5_MAX_PACKET];
        size_t length = 0U;
        const int encode_rc = edge_result_v5_encode(&result, packet, sizeof(packet), &length);
        if (encode_rc == 0 && length <= UINT16_MAX) {
            (void)enqueue_udp(packet, (uint16_t)length);
        } else {
            __atomic_add_fetch(&g_v6_encode_failures, 1U, __ATOMIC_RELAXED);
            ESP_LOGW(TAG, "v6_encode_failed rc=%d length=%u", encode_rc, (unsigned)length);
        }
    }
}
#endif

static int hex_nibble(char value)
{
    if (value >= '0' && value <= '9') {
        return value - '0';
    }
    if (value >= 'a' && value <= 'f') {
        return value - 'a' + 10;
    }
    if (value >= 'A' && value <= 'F') {
        return value - 'A' + 10;
    }
    return -1;
}

static bool parse_mac_text(const char *text, uint8_t output[ESP_NOW_ETH_ALEN])
{
    if (text == NULL || output == NULL || strlen(text) != 17U) {
        return false;
    }
    for (size_t index = 0; index < ESP_NOW_ETH_ALEN; ++index) {
        const size_t offset = index * 3U;
        if (index > 0U && text[offset - 1U] != ':') {
            return false;
        }
        const int high = hex_nibble(text[offset]);
        const int low = hex_nibble(text[offset + 1U]);
        if (high < 0 || low < 0) {
            return false;
        }
        output[index] = (uint8_t)((high << 4) | low);
    }
    return true;
}

static bool expected_tx_mac_allows(const uint8_t source_mac[ESP_NOW_ETH_ALEN])
{
    if (g_expected_tx_mac_invalid) {
        return false;
    }
    return !g_expected_tx_mac_configured ||
           memcmp(g_expected_tx_mac, source_mac, ESP_NOW_ETH_ALEN) == 0;
}

static void put_u16(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)(value >> 8);
    dst[1] = (uint8_t)value;
}

static void put_u32(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)(value >> 24);
    dst[1] = (uint8_t)(value >> 16);
    dst[2] = (uint8_t)(value >> 8);
    dst[3] = (uint8_t)value;
}

static void put_u64(uint8_t *dst, uint64_t value)
{
    put_u32(dst, (uint32_t)(value >> 32));
    put_u32(dst + 4, (uint32_t)value);
}

static void put_f32(uint8_t *dst, float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    put_u32(dst, bits);
}

static uint16_t get_u16(const uint8_t *src)
{
    return ((uint16_t)src[0] << 8) | src[1];
}

static uint32_t get_u32(const uint8_t *src)
{
    return ((uint32_t)src[0] << 24) | ((uint32_t)src[1] << 16) |
           ((uint32_t)src[2] << 8) | src[3];
}

static uint64_t get_u64(const uint8_t *src)
{
    return ((uint64_t)get_u32(src) << 32) | get_u32(src + 4);
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *bytes, size_t length)
{
    for (size_t i = 0; i < length; ++i) {
        crc ^= bytes[i];
        for (unsigned bit = 0; bit < 8U; ++bit) {
            crc = (crc >> 1) ^ ((crc & 1U) ? UINT32_C(0xedb88320) : 0U);
        }
    }
    return crc;
}

static uint32_t crc32_packet(const uint8_t *prefix, const uint8_t *payload, size_t payload_length)
{
    uint32_t crc = crc32_update(UINT32_C(0xffffffff), prefix, V4_CRC_OFFSET);
    if (payload_length > 0U) {
        crc = crc32_update(crc, payload, payload_length);
    }
    return ~crc;
}

static bool protocol_decode(const uint8_t *packet, size_t length, v4_header_t *header,
                            const uint8_t **payload)
{
    if (packet == NULL || header == NULL || payload == NULL || length < V4_HEADER_SIZE ||
        get_u32(packet) != V4_MAGIC || packet[4] != V4_VERSION ||
        get_u16(packet + 6) != V4_HEADER_SIZE || get_u16(packet + 10) != V4_SCHEMA) {
        return false;
    }
    const uint16_t payload_length = get_u16(packet + 8);
    if ((size_t)payload_length != length - V4_HEADER_SIZE ||
        get_u32(packet + V4_CRC_OFFSET) != crc32_packet(packet, packet + V4_HEADER_SIZE,
                                                         payload_length)) {
        return false;
    }
    header->type = packet[5];
    header->payload_length = payload_length;
    header->schema = get_u16(packet + 10);
    header->link_id = get_u32(packet + 12);
    header->tx_id = get_u32(packet + 16);
    header->rx_id = get_u32(packet + 20);
    header->boot_id = get_u32(packet + 24);
    header->gain_epoch = get_u32(packet + 28);
    header->sequence = get_u32(packet + 32);
    header->timestamp_us = get_u64(packet + 36);
    *payload = packet + V4_HEADER_SIZE;
    return true;
}

static bool protocol_encode(uint8_t *packet, size_t capacity, const v4_header_t *header,
                            const uint8_t *payload)
{
    if (packet == NULL || header == NULL ||
        capacity < (size_t)V4_HEADER_SIZE + header->payload_length) {
        return false;
    }
    put_u32(packet + 0, V4_MAGIC);
    packet[4] = V4_VERSION;
    packet[5] = header->type;
    put_u16(packet + 6, V4_HEADER_SIZE);
    put_u16(packet + 8, header->payload_length);
    put_u16(packet + 10, V4_SCHEMA);
    put_u32(packet + 12, header->link_id);
    put_u32(packet + 16, header->tx_id);
    put_u32(packet + 20, header->rx_id);
    put_u32(packet + 24, header->boot_id);
    put_u32(packet + 28, header->gain_epoch);
    put_u32(packet + 32, header->sequence);
    put_u64(packet + 36, header->timestamp_us);
    if (header->payload_length > 0U) {
        memcpy(packet + V4_HEADER_SIZE, payload, header->payload_length);
    }
    put_u32(packet + V4_CRC_OFFSET,
            crc32_packet(packet, packet + V4_HEADER_SIZE, header->payload_length));
    return true;
}

static pair_state_t pairing_snapshot(void)
{
    pair_state_t copy;
    portENTER_CRITICAL(&g_pair_mux);
    copy = g_pair;
    portEXIT_CRITICAL(&g_pair_mux);
    return copy;
}

static bool set_pair(const uint8_t source_mac[ESP_NOW_ETH_ALEN], uint32_t tx_boot_id)
{
    if (!expected_tx_mac_allows(source_mac)) {
        return false;
    }
    portENTER_CRITICAL(&g_pair_mux);
    /* Do not let a new source MAC hijack an already paired receiver. */
    if (g_pair.valid && memcmp(g_pair.tx_mac, source_mac, ESP_NOW_ETH_ALEN) != 0) {
        portEXIT_CRITICAL(&g_pair_mux);
        return false;
    }
    const bool changed = !g_pair.valid || g_pair.tx_boot_id != tx_boot_id ||
                         memcmp(g_pair.tx_mac, source_mac, ESP_NOW_ETH_ALEN) != 0;
    g_pair.valid = true;
    g_pair.tx_boot_id = tx_boot_id;
    memcpy(g_pair.tx_mac, source_mac, ESP_NOW_ETH_ALEN);
    portEXIT_CRITICAL(&g_pair_mux);
    if (changed) {
        __atomic_add_fetch(&g_dsp_reset_generation, 1U, __ATOMIC_RELEASE);
        portENTER_CRITICAL(&g_measurement_mux);
        memset(&g_measurements, 0, sizeof(g_measurements));
        portEXIT_CRITICAL(&g_measurement_mux);
        /* Discard records queued under the previous TX boot context before
           the DSP task can reuse them after the pairing transition. */
        drain_csi_queue();
    }
#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
    pair_state_t active_pair = {0};
    active_pair.valid = true;
    active_pair.tx_boot_id = tx_boot_id;
    memcpy(active_pair.tx_mac, source_mac, ESP_NOW_ETH_ALEN);
    v6_pipeline_start_for_pair(&active_pair);
#endif
    return true;
}

static esp_err_t ensure_peer(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    if (esp_now_is_peer_exist(mac)) {
        return ESP_OK;
    }
    esp_now_peer_info_t peer = {0};
    memcpy(peer.peer_addr, mac, ESP_NOW_ETH_ALEN);
    peer.channel = WIEVAC_WIFI_CHANNEL;
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;
    ESP_RETURN_ON_ERROR(esp_now_add_peer(&peer), TAG, "add ESP-NOW peer");
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 1, 0)
    esp_now_rate_config_t rate = {
        .phymode = WIFI_PHY_MODE_HT20,
        .rate = WIEVAC_ESPNOW_RATE,
        .ersu = false,
        .dcm = false,
    };
    return esp_now_set_peer_rate_config(mac, &rate);
#else
    return esp_wifi_config_espnow_rate(WIFI_IF_STA, WIEVAC_ESPNOW_RATE);
#endif
}

static void send_pair_reply(const uint8_t destination[ESP_NOW_ETH_ALEN])
{
    if (ensure_peer(destination) != ESP_OK) {
        ESP_LOGE(TAG, "pair_reply peer_setup_failed dest=" MACSTR, MAC2STR(destination));
        return;
    }
    v4_header_t header = {
        .type = V4_MSG_PAIR_REPLY,
        .payload_length = 0U,
        .schema = V4_SCHEMA,
        .link_id = WIEVAC_LINK_ID,
        .tx_id = WIEVAC_TX_ID,
        .rx_id = WIEVAC_RX_ID,
        .boot_id = g_rx_boot_id,
        .gain_epoch = 0U,
        .sequence = ++g_pair_sequence,
        .timestamp_us = (uint64_t)esp_timer_get_time(),
    };
    uint8_t packet[V4_HEADER_SIZE];
    if (protocol_encode(packet, sizeof(packet), &header, NULL)) {
        const esp_err_t send_err = esp_now_send(destination, packet, sizeof(packet));
        ESP_LOGI(TAG, "pair_reply_send rx_id=%" PRIu32 " dest=" MACSTR " result=%s",
                 WIEVAC_RX_ID, MAC2STR(destination), esp_err_to_name(send_err));
    } else {
        ESP_LOGE(TAG, "pair_reply_encode_failed rx_id=%" PRIu32, WIEVAC_RX_ID);
    }
}

static bool measurement_note(uint32_t tx_boot_id, uint32_t sequence, uint64_t timestamp_us,
                             uint64_t arrival_us)
{
    if (tx_boot_id == 0U || sequence == 0U || timestamp_us == 0U) {
        __atomic_add_fetch(&g_control_drops, 1U, __ATOMIC_RELAXED);
        return false;
    }
    portENTER_CRITICAL(&g_measurement_mux);
    if (!g_measurements.initialized) {
        g_measurements.initialized = true;
        g_measurements.tx_boot_id = tx_boot_id;
        g_measurements.last_sequence = sequence;
        g_measurements.last_timestamp_us = timestamp_us;
        g_measurements.latest_tx_sequence = sequence;
        g_measurements.latest_tx_timestamp_us = timestamp_us;
        g_measurements.latest_rx_arrival_us = arrival_us;
        g_measurements.sequence_gap = 0U;
        ++g_measurements.received;
    } else if (tx_boot_id != g_measurements.tx_boot_id) {
        /* A TX boot transition must be announced by HELLO/set_pair first. */
        portEXIT_CRITICAL(&g_measurement_mux);
        __atomic_add_fetch(&g_measurement_reorders, 1U, __ATOMIC_RELAXED);
        return false;
    } else {
        if (timestamp_us <= g_measurements.last_timestamp_us) {
            if (sequence == g_measurements.last_sequence) {
                __atomic_add_fetch(&g_measurement_duplicates, 1U, __ATOMIC_RELAXED);
            } else {
                __atomic_add_fetch(&g_measurement_reorders, 1U, __ATOMIC_RELAXED);
            }
            portEXIT_CRITICAL(&g_measurement_mux);
            return false;
        }
        const int32_t delta = (int32_t)(sequence - g_measurements.last_sequence);
        if (delta <= 0) {
            __atomic_add_fetch(&g_measurement_reorders, 1U, __ATOMIC_RELAXED);
            portEXIT_CRITICAL(&g_measurement_mux);
            return false;
        }
        if ((uint32_t)delta < 10000U) {
            g_measurements.lost += (uint32_t)delta - 1U;
            g_measurements.sequence_gap += (uint32_t)delta - 1U;
        }
        g_measurements.last_sequence = sequence;
        g_measurements.last_timestamp_us = timestamp_us;
        g_measurements.latest_tx_sequence = sequence;
        g_measurements.latest_tx_timestamp_us = timestamp_us;
        g_measurements.latest_rx_arrival_us = arrival_us;
        ++g_measurements.received;
    }
    portEXIT_CRITICAL(&g_measurement_mux);
    return true;
}

static void measurement_take(uint32_t *received, uint32_t *lost, uint32_t *latest_sequence,
                             uint64_t *latest_timestamp, uint32_t *latest_boot_id,
                             uint32_t *sequence_gap)
{
    portENTER_CRITICAL(&g_measurement_mux);
    *received = g_measurements.received;
    *lost = g_measurements.lost;
    *latest_sequence = g_measurements.latest_tx_sequence;
    *latest_timestamp = g_measurements.latest_tx_timestamp_us;
    *latest_boot_id = g_measurements.tx_boot_id;
    *sequence_gap = g_measurements.sequence_gap;
    g_measurements.received = 0U;
    g_measurements.lost = 0U;
    g_measurements.sequence_gap = 0U;
    portEXIT_CRITICAL(&g_measurement_mux);
}

/* Read the ESP-NOW measurement counters without consuming the window totals. */
static void measurement_peek(uint32_t *received, uint32_t *lost, uint32_t *latest_sequence,
                             uint32_t *latest_boot_id, uint32_t *sequence_gap)
{
    portENTER_CRITICAL(&g_measurement_mux);
    if (received != NULL) {
        *received = g_measurements.received;
    }
    if (lost != NULL) {
        *lost = g_measurements.lost;
    }
    if (latest_sequence != NULL) {
        *latest_sequence = g_measurements.latest_tx_sequence;
    }
    if (latest_boot_id != NULL) {
        *latest_boot_id = g_measurements.tx_boot_id;
    }
    if (sequence_gap != NULL) {
        *sequence_gap = g_measurements.sequence_gap;
    }
    portEXIT_CRITICAL(&g_measurement_mux);
}

static bool measurement_context_admissible(uint32_t tx_boot_id, uint32_t sequence,
                                           uint64_t timestamp_us, uint32_t *sequence_gap)
{
    bool admissible = true;
    uint32_t gap_before = 0U;
    portENTER_CRITICAL(&g_measurement_mux);
    /* CSI delivery is asynchronous and can lag the fast measurement callback.
       Validate boot identity here, then compare ordering only against the
       previous CSI sample; comparing with the newest measurement rejects
       legitimate delayed CSI frames. */
    if (g_measurements.initialized && tx_boot_id != g_measurements.tx_boot_id) {
        admissible = false;
    }
    if (admissible) {
        if (g_measurements.last_csi_tx_sequence != 0U) {
            if (tx_boot_id != g_measurements.last_csi_tx_boot_id ||
                sequence <= g_measurements.last_csi_tx_sequence ||
                timestamp_us <= g_measurements.last_csi_tx_timestamp_us) {
                admissible = false;
            } else {
                const uint32_t delta = sequence - g_measurements.last_csi_tx_sequence;
                if (delta < 10000U) {
                    gap_before = delta - 1U;
                }
            }
        }
    }
    if (admissible) {
        g_measurements.last_csi_tx_boot_id = tx_boot_id;
        g_measurements.last_csi_tx_sequence = sequence;
        g_measurements.last_csi_tx_timestamp_us = timestamp_us;
    }
    portEXIT_CRITICAL(&g_measurement_mux);
    if (sequence_gap != NULL) {
        *sequence_gap = admissible ? gap_before : 0U;
    }
    if (admissible && gap_before > 0U) {
        /* This is a gap between accepted CSI callbacks, not a Pi UDP gap. */
        __atomic_add_fetch(&g_csi_sequence_gaps, gap_before, __ATOMIC_RELAXED);
    }
    if (!admissible) {
        if (!g_logged_csi_context_reject) {
            g_logged_csi_context_reject = true;
            ESP_LOGW(TAG, "csi_context_reject boot=%" PRIu32 " seq=%" PRIu32
                     " last_csi_boot=%" PRIu32 " last_csi_seq=%" PRIu32
                     " latest_measurement_seq=%" PRIu32,
                     tx_boot_id, sequence, g_measurements.last_csi_tx_boot_id,
                     g_measurements.last_csi_tx_sequence,
                     g_measurements.latest_tx_sequence);
        }
    }
    return admissible;
}

static void espnow_receive_callback(const esp_now_recv_info_t *info, const uint8_t *data, int length)
{
    if (info == NULL || info->src_addr == NULL || data == NULL || length != V4_HEADER_SIZE) {
        __atomic_add_fetch(&g_control_drops, 1U, __ATOMIC_RELAXED);
        return;
    }
    /* Track measurement loss independently; CSI associates from its own
       packet payload rather than guessing from the latest measurement. */
    v4_header_t fast_header;
    const uint8_t *fast_payload;
    if (protocol_decode(data, (size_t)length, &fast_header, &fast_payload) &&
        fast_header.type == V4_MSG_MEASUREMENT && fast_header.payload_length == 0U &&
        fast_header.link_id == WIEVAC_LINK_ID && fast_header.tx_id == WIEVAC_TX_ID &&
        fast_header.rx_id == WIEVAC_RX_ID && fast_header.boot_id != 0U &&
        fast_header.sequence != 0U && fast_header.timestamp_us != 0U) {
        const pair_state_t pair = pairing_snapshot();
        if (pair.valid && pair.tx_boot_id == fast_header.boot_id &&
            memcmp(pair.tx_mac, info->src_addr, ESP_NOW_ETH_ALEN) == 0) {
            (void)measurement_note(fast_header.boot_id, fast_header.sequence,
                                    fast_header.timestamp_us,
                                    (uint64_t)esp_timer_get_time());
        }
    }
    control_event_t event = {
        .length = (uint16_t)length,
        .arrival_us = (uint64_t)esp_timer_get_time(),
    };
    memcpy(event.source_mac, info->src_addr, ESP_NOW_ETH_ALEN);
    memcpy(event.bytes, data, (size_t)length);
    if (xQueueSend(g_control_queue, &event, 0) != pdTRUE) {
        __atomic_add_fetch(&g_control_drops, 1U, __ATOMIC_RELAXED);
    }
}

static void espnow_control_task(void *argument)
{
    (void)argument;
    control_event_t event;
    for (;;) {
        if (xQueueReceive(g_control_queue, &event, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        v4_header_t header;
        const uint8_t *payload;
        if (!protocol_decode(event.bytes, event.length, &header, &payload)) {
            continue;
        }
        (void)payload;
        if (header.type == V4_MSG_HELLO && header.payload_length == 0U &&
            header.link_id == 0U && header.tx_id == WIEVAC_TX_ID &&
            header.rx_id == 0U && header.gain_epoch == 0U && header.boot_id != 0U &&
            header.sequence != 0U && header.timestamp_us != 0U) {
            if (set_pair(event.source_mac, header.boot_id)) {
                ESP_LOGI(TAG, "hello_rx tx_boot=%" PRIu32 " source=" MACSTR,
                         header.boot_id, MAC2STR(event.source_mac));
                send_pair_reply(event.source_mac);
            } else {
                ESP_LOGW(TAG, "reject HELLO tx_boot=%" PRIu32 " source=" MACSTR,
                         header.boot_id, MAC2STR(event.source_mac));
            }
            continue;
        }
        const pair_state_t pair = pairing_snapshot();
        if (header.type == V4_MSG_MEASUREMENT && header.payload_length == 0U &&
            pair.valid && header.link_id == WIEVAC_LINK_ID && header.tx_id == WIEVAC_TX_ID &&
            header.rx_id == WIEVAC_RX_ID && header.boot_id == pair.tx_boot_id &&
            memcmp(pair.tx_mac, event.source_mac, ESP_NOW_ETH_ALEN) == 0) {
            /* The fast callback already records this measurement before CSI
               association. Avoid counting the same radio frame twice. */
            continue;
        }
    }
}

static bool csi_layout_valid(const csi_record_t *record)
{
    return record->tx_context_valid && record->tx_boot_id != 0U &&
           record->tx_sequence != 0U && record->tx_timestamp_us != 0U &&
           record->length >= 2U && record->length <= WIEVAC_HTLTF_BYTES &&
           (record->length & 1U) == 0U &&
           record->rx_ctrl.sig_mode == 1U &&
           record->rx_ctrl.channel == WIEVAC_WIFI_CHANNEL &&
           record->rx_ctrl.cwb == 0U &&
           record->rx_ctrl.secondary_channel == WIFI_SECOND_CHAN_NONE &&
           record->rx_ctrl.rx_state == 0U && !record->first_word_invalid;
}

static void wifi_csi_callback(void *context, wifi_csi_info_t *info)
{
    (void)context;
    if (info != NULL && g_first_csi_len < 0) {
        g_first_csi_len = info->len;
        g_first_csi_payload_len = info->payload_len;
        memcpy(g_first_csi_src, info->mac, ESP_NOW_ETH_ALEN);
        memcpy(g_first_csi_dst, info->dmac, ESP_NOW_ETH_ALEN);
        memset(g_first_csi_payload, 0, sizeof(g_first_csi_payload));
        if (info->payload != NULL && info->payload_len > 0U) {
            const size_t diagnostic_length = info->payload_len < sizeof(g_first_csi_payload)
                                                 ? info->payload_len : sizeof(g_first_csi_payload);
            memcpy(g_first_csi_payload, info->payload, diagnostic_length);
        }
    }
    if (info == NULL || info->buf == NULL || info->len < 2U ||
        info->len > WIEVAC_HTLTF_BYTES || (info->len & 1U) != 0U ||
        info->payload == NULL || info->payload_len < V4_HEADER_SIZE) {
        if (!g_logged_csi_shape_reject && info != NULL) {
            g_logged_csi_shape_reject = true;
            ESP_LOGW(TAG, "csi_shape_reject len=%d payload_len=%d expected_len=%u expected_payload=%u",
                     info->len, info->payload_len, WIEVAC_HTLTF_BYTES, V4_HEADER_SIZE);
        }
        __atomic_add_fetch(&g_invalid_csi, 1U, __ATOMIC_RELAXED);
        __atomic_add_fetch(&g_csi_shape_rejects, 1U, __ATOMIC_RELAXED);
        return;
    }
    const pair_state_t pair = pairing_snapshot();
    /* CSI is enabled in promiscuous mode. Require both MAC directions so a
       HELLO broadcast or another TX frame cannot become a measurement. */
    if (!pair.valid || memcmp(pair.tx_mac, info->mac, ESP_NOW_ETH_ALEN) != 0 ||
        memcmp(g_rx_mac, info->dmac, ESP_NOW_ETH_ALEN) != 0) {
        if (!g_logged_csi_mac_reject) {
            g_logged_csi_mac_reject = true;
            ESP_LOGW(TAG, "csi_mac_reject pair_valid=%d src=" MACSTR " dst=" MACSTR
                     " expected_src=" MACSTR " expected_dst=" MACSTR,
                     pair.valid, MAC2STR(info->mac), MAC2STR(info->dmac),
                     MAC2STR(pair.tx_mac), MAC2STR(g_rx_mac));
        }
        __atomic_add_fetch(&g_csi_mac_rejects, 1U, __ATOMIC_RELAXED);
        /* Promiscuous CSI also observes the other link. This is an expected
           foreign-frame drop, not a defect in the local CSI stream. */
        __atomic_add_fetch(&g_csi_foreign_drops, 1U, __ATOMIC_RELAXED);
        return;
    }
    v4_header_t measurement_header = {0};
    const uint8_t *measurement_payload;
    /* ESP-IDF reports the enclosing 802.11/ESP-NOW payload (91 bytes here).
       Locate the embedded 48-byte V4 packet instead of assuming offset zero. */
    const uint8_t *v4_packet = NULL;
    for (int offset = 0; offset <= info->payload_len - V4_HEADER_SIZE; ++offset) {
        if (get_u32(info->payload + offset) == V4_MAGIC) {
            v4_packet = info->payload + offset;
            break;
        }
    }
    const bool header_ok = v4_packet != NULL &&
                           protocol_decode(v4_packet, V4_HEADER_SIZE,
                                           &measurement_header, &measurement_payload);
    if (!header_ok ||
        measurement_header.type != V4_MSG_MEASUREMENT ||
        measurement_header.payload_length != 0U ||
        measurement_header.link_id != WIEVAC_LINK_ID ||
        measurement_header.tx_id != WIEVAC_TX_ID ||
        measurement_header.rx_id != WIEVAC_RX_ID ||
        measurement_header.boot_id != pair.tx_boot_id ||
        measurement_header.sequence == 0U || measurement_header.timestamp_us == 0U) {
        if (!g_logged_csi_header_reject) {
            g_logged_csi_header_reject = true;
            ESP_LOGW(TAG, "csi_header_reject decode=%d type=%u link=%" PRIu32
                     " tx=%" PRIu32 " rx=%" PRIu32 " boot=%" PRIu32
                     " expected_boot=%" PRIu32 " seq=%" PRIu32,
                     header_ok,
                     measurement_header.type, measurement_header.link_id,
                     measurement_header.tx_id, measurement_header.rx_id,
                     measurement_header.boot_id, pair.tx_boot_id,
                     measurement_header.sequence);
        }
        __atomic_add_fetch(&g_invalid_csi, 1U, __ATOMIC_RELAXED);
        __atomic_add_fetch(&g_csi_header_rejects, 1U, __ATOMIC_RELAXED);
        return;
    }
    uint32_t sequence_gap = 0U;
    if (!measurement_context_admissible(measurement_header.boot_id,
                                        measurement_header.sequence,
                                        measurement_header.timestamp_us,
                                        &sequence_gap)) {
        __atomic_add_fetch(&g_invalid_csi, 1U, __ATOMIC_RELAXED);
        __atomic_add_fetch(&g_csi_context_rejects, 1U, __ATOMIC_RELAXED);
        return;
    }
    const uint64_t arrival_us = (uint64_t)esp_timer_get_time();
    csi_record_t record = {
        .length = info->len,
        .first_word_invalid = info->first_word_invalid,
        .rx_ctrl = info->rx_ctrl,
        .arrival_us = arrival_us,
        .tx_context_valid = true,
        .tx_boot_id = measurement_header.boot_id,
        .tx_sequence = measurement_header.sequence,
        .tx_timestamp_us = measurement_header.timestamp_us,
        .sequence_gap = sequence_gap,
    };
    /* Keep a local CSI sequence independent of the TX sequence. */
    record.rx_sequence = ++g_rx_sequence;
    memcpy(record.source_mac, info->mac, ESP_NOW_ETH_ALEN);
    memcpy(record.csi, info->buf, info->len);
    if (xQueueSend(g_csi_queue, &record, 0) != pdTRUE) {
        __atomic_add_fetch(&g_csi_queue_drops, 1U, __ATOMIC_RELAXED);
    }
}

static void sort_values(float *values, size_t count)
{
    for (size_t i = 1; i < count; ++i) {
        const float value = values[i];
        size_t j = i;
        while (j > 0U && values[j - 1U] > value) {
            values[j] = values[j - 1U];
            --j;
        }
        values[j] = value;
    }
}

static float percentile(const float *values, size_t count, float fraction)
{
    if (count == 0U) {
        return NAN;
    }
    float copy[WIEVAC_WINDOW_CAPACITY];
    memcpy(copy, values, count * sizeof(float));
    sort_values(copy, count);
    const float position = fraction * (float)(count - 1U);
    const size_t lower = (size_t)floorf(position);
    const size_t upper = (size_t)ceilf(position);
    const float weight = position - (float)lower;
    return copy[lower] * (1.0f - weight) + copy[upper] * weight;
}

static float mad_scale(const float *values, size_t count)
{
    const float center = percentile(values, count, 0.5f);
    float absolute[WIEVAC_WINDOW_CAPACITY];
    for (size_t i = 0; i < count; ++i) {
        absolute[i] = fabsf(values[i] - center);
    }
    return 1.4826f * percentile(absolute, count, 0.5f);
}

static bool is_pilot(int subcarrier)
{
    return subcarrier == -21 || subcarrier == -7 || subcarrier == 7 || subcarrier == 21;
}

static void reset_window(dsp_state_t *state, uint64_t start_us)
{
    state->window_start_us = start_us;
    state->window_start_tx_us = 0U;
    state->last_tx_boot_id = 0U;
    state->last_tx_timestamp_us = 0U;
    state->last_tx_sequence = 0U;
    state->last_rx_sequence = 0U;
    state->start_csi_queue_drops = __atomic_load_n(&g_csi_queue_drops, __ATOMIC_RELAXED);
    state->start_invalid_csi = __atomic_load_n(&g_invalid_csi, __ATOMIC_RELAXED);
    state->start_control_drops = __atomic_load_n(&g_control_drops, __ATOMIC_RELAXED);
    state->start_udp_drops = __atomic_load_n(&g_udp_drops, __ATOMIC_RELAXED);
    state->start_stale_record_drops = __atomic_load_n(&g_stale_record_drops, __ATOMIC_RELAXED);
    state->start_gain_compensation_failures =
        __atomic_load_n(&g_gain_compensation_failures, __ATOMIC_RELAXED);
    state->start_gain_baseline_retrains =
        __atomic_load_n(&g_gain_baseline_retrains, __ATOMIC_RELAXED);
    state->start_gain_epoch_changes =
        __atomic_load_n(&g_gain_epoch_changes, __ATOMIC_RELAXED);
    state->valid_samples = 0U;
    state->discarded_samples = 0U;
    state->gain_ready_samples = 0U;
    state->gain_not_ready_samples = 0U;
}

static void drain_csi_queue(void)
{
    while (g_csi_queue != NULL && xQueueReceive(g_csi_queue, &g_drain_record, 0) == pdTRUE) {
        __atomic_add_fetch(&g_stale_record_drops, 1U, __ATOMIC_RELAXED);
    }
}

static void start_gain_epoch(dsp_state_t *state, uint64_t now_us)
{
    ++state->gain_epoch;
    if (state->gain_epoch == 0U) {
        state->gain_epoch = 1U;
    }
    state->trend_initialized = false;
    state->last_sample_us = 0U;
    state->gain_diagnostic_samples = 0U;
    state->gain_stats_count = 0U;
    state->gain_stats_next = 0U;
    state->gain_state = GAIN_READY;
    __atomic_add_fetch(&g_gain_epoch_changes, 1U, __ATOMIC_RELAXED);
    ++state->pending_gain_epoch_changes;
    reset_window(state, now_us);
}

static void dsp_reset(dsp_state_t *state, uint64_t now_us)
{
    const uint32_t previous_epoch = state->gain_epoch;
    memset(state, 0, sizeof(*state));
    /* A pairing or TX-boot change must be visible before its CSI can be reused. */
    state->gain_epoch = previous_epoch + 1U;
    if (state->gain_epoch == 0U) {
        state->gain_epoch = 1U;
    }
    state->gain_state = GAIN_NOT_READY;
    esp_csi_gain_ctrl_reset_rx_gain_baseline();
    __atomic_add_fetch(&g_gain_epoch_changes, 1U, __ATOMIC_RELAXED);
    ++state->pending_gain_epoch_changes;
    drain_csi_queue();
    reset_window(state, now_us);
}

static void restart_gain_training(dsp_state_t *state, uint64_t now_us)
{
    state->gain_baseline_ready = false;
    state->gain_training_samples = 0U;
    state->consecutive_gain_failures = 0U;
    state->trend_initialized = false;
    state->last_sample_us = 0U;
    state->gain_diagnostic_samples = 0U;
    state->gain_stats_count = 0U;
    state->gain_stats_next = 0U;
    state->gain_state = GAIN_NOT_READY;
    esp_csi_gain_ctrl_reset_rx_gain_baseline();
    __atomic_add_fetch(&g_gain_baseline_retrains, 1U, __ATOMIC_RELAXED);
    ++state->pending_gain_baseline_retrains;
    state->pending_gain_compensation_failures += state->window_gain_compensation_failures;
    state->window_gain_compensation_failures = 0U;
    drain_csi_queue();
    reset_window(state, now_us);
}

static void gain_stats_add(dsp_state_t *state, float agc_gain, float fft_gain,
                           float rssi, float common_level)
{
    const size_t index = state->gain_stats_next;
    state->gain_agc_stats[index] = agc_gain;
    state->gain_fft_stats[index] = fft_gain;
    state->gain_rssi_stats[index] = rssi;
    state->gain_common_stats[index] = common_level;
    state->gain_stats_next = (index + 1U) % WIEVAC_GAIN_STATS_CAPACITY;
    if (state->gain_stats_count < WIEVAC_GAIN_STATS_CAPACITY) {
        ++state->gain_stats_count;
    }
}

static bool gain_stat_is_outlier(const float *samples, size_t count, float value)
{
    if (count < WIEVAC_GAIN_STATS_MIN_SAMPLES) {
        return false;
    }
    const float center = percentile(samples, count, 0.5f);
    float deviations[WIEVAC_WINDOW_CAPACITY];
    for (size_t i = 0; i < count; ++i) {
        deviations[i] = fabsf(samples[i] - center);
    }
    /* The boundary follows the current stable distribution, not corridor amplitude. */
    const float boundary = percentile(deviations, count, 0.95f) +
                           fmaxf(mad_scale(samples, count), 0.001f);
    return fabsf(value - center) > boundary;
}

static bool gain_metadata_change_detected(const dsp_state_t *state, float agc_gain, float fft_gain)
{
    return gain_stat_is_outlier(state->gain_agc_stats, state->gain_stats_count, agc_gain) ||
           gain_stat_is_outlier(state->gain_fft_stats, state->gain_stats_count, fft_gain);
}

static bool rf_level_change_detected(const dsp_state_t *state, float rssi, float common_level)
{
    return gain_stat_is_outlier(state->gain_rssi_stats, state->gain_stats_count, rssi) ||
           gain_stat_is_outlier(state->gain_common_stats, state->gain_stats_count, common_level);
}

static bool enqueue_udp(const uint8_t *packet, uint16_t length)
{
    if (length > V4_MAX_PACKET_SIZE) {
        return false;
    }
    udp_packet_t datagram = {.length = length};
    memcpy(datagram.bytes, packet, length);
    if (xQueueSend(g_udp_queue, &datagram, 0) != pdTRUE) {
        __atomic_add_fetch(&g_udp_queue_drops, 1U, __ATOMIC_RELAXED);
        __atomic_add_fetch(&g_udp_drops, 1U, __ATOMIC_RELAXED);
        return false;
    }
    return true;
}

static void enqueue_dynamic_frame(const csi_record_t *record, dsp_state_t *state,
                                  const float shape_bins[WIEVAC_SHAPE_BINS], uint8_t agc_gain,
                                  int8_t fft_gain, bool shape_valid, bool rf_level_change)
{
#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
    (void)record; (void)state; (void)shape_bins; (void)agc_gain; (void)fft_gain;
    (void)shape_valid; (void)rf_level_change;
    return;
#else
    const uint64_t interval_us = 1000000ULL / WIEVAC_DYNAMIC_FRAME_RATE_HZ;
    if (state->last_dynamic_frame_us != 0U && record->arrival_us >= state->last_dynamic_frame_us &&
        record->arrival_us - state->last_dynamic_frame_us < interval_us) {
        state->pending_shape_invalid = state->pending_shape_invalid || !shape_valid;
        return;
    }
    state->last_dynamic_frame_us = record->arrival_us;
    shape_valid = shape_valid && !state->pending_shape_invalid;
    state->pending_shape_invalid = false;
    const pair_state_t pair = pairing_snapshot();
    if (!pair.valid) {
        return;
    }
    uint8_t payload[V4_DYNAMIC_PAYLOAD_SIZE] = {0};
    for (size_t i = 0; i < WIEVAC_SHAPE_BINS; ++i) {
        put_f32(payload + i * 4U, isfinite(shape_bins[i]) ? shape_bins[i] : 0.0f);
    }
    put_f32(payload + WIEVAC_SHAPE_BINS * 4U, (float)agc_gain);
    put_f32(payload + (WIEVAC_SHAPE_BINS + 1U) * 4U, (float)fft_gain);
    put_f32(payload + (WIEVAC_SHAPE_BINS + 2U) * 4U, (float)record->rx_ctrl.rssi);
    put_f32(payload + (WIEVAC_SHAPE_BINS + 3U) * 4U, (float)record->rx_ctrl.noise_floor);
    const size_t counters_offset = V4_DYNAMIC_FLOATS * 4U;
    const uint32_t flags = (shape_valid ? 1U : 0U) |
                           (record->first_word_invalid ? 2U : 0U) |
                           (csi_layout_valid(record) ? 4U : 0U) |
                           (rf_level_change ? (1U << 4U) : 0U) |
                           ((WIEVAC_MCS_INDEX & 0xffU) << 8U) |
                           ((WIEVAC_RATE_CODE & 0xffU) << 16U);
    const uint32_t counters[V4_DYNAMIC_COUNTERS] = {
        flags, 1U, shape_valid ? 0U : 1U,
        __atomic_load_n(&g_csi_queue_drops, __ATOMIC_RELAXED) -
            state->start_csi_queue_drops,
        record->sequence_gap, record->tx_sequence, record->rx_sequence, record->tx_boot_id,
        record->length,
        /* The current CSI configuration has one HT-LTF layout: its 128-byte length. */
        WIEVAC_WIFI_CHANNEL, 20U, record->rx_ctrl.sig_mode, WIEVAC_HTLTF_BYTES,
        /* Preserve the physical layout even when the shape is invalid.  The
         * Pi must decode the frame and surface SIGNAL_INVALID/DEGRADED rather
         * than silently losing the gain-transition/CSI-quality event. */
        record->length / 2U, 0U,
    };
    for (size_t i = 0; i < V4_DYNAMIC_COUNTERS; ++i) {
        put_u32(payload + counters_offset + i * 4U, counters[i]);
    }
    put_u64(payload + counters_offset + V4_DYNAMIC_COUNTERS * 4U, record->tx_timestamp_us);
    put_u64(payload + counters_offset + V4_DYNAMIC_COUNTERS * 4U + 8U, record->arrival_us);
    const v4_header_t header = {
        .type = V4_MSG_DYNAMIC_FRAME,
        .payload_length = V4_DYNAMIC_PAYLOAD_SIZE,
        .schema = V4_SCHEMA,
        .link_id = WIEVAC_LINK_ID,
        .tx_id = WIEVAC_TX_ID,
        .rx_id = WIEVAC_RX_ID,
        .boot_id = g_rx_boot_id,
        .gain_epoch = state->gain_epoch,
        .sequence = ++g_dynamic_sequence,
        .timestamp_us = record->arrival_us,
    };
    uint8_t packet[V4_HEADER_SIZE + V4_DYNAMIC_PAYLOAD_SIZE];
    if (!protocol_encode(packet, sizeof(packet), &header, payload) ||
        !enqueue_udp(packet, sizeof(packet))) {
        __atomic_add_fetch(&g_dynamic_frame_drops, 1U, __ATOMIC_RELAXED);
    }
#endif
}

static void reject_invalid_shape(dsp_state_t *state, const csi_record_t *record,
                                 uint8_t agc_gain, int8_t fft_gain)
{
    const float empty_shape[WIEVAC_SHAPE_BINS] = {0};
    ++state->discarded_samples;
    ++state->consecutive_invalid_csi;
    state->gain_state = GAIN_NOT_READY;
    __atomic_add_fetch(&g_invalid_csi, 1U, __ATOMIC_RELAXED);
    enqueue_dynamic_frame(record, state, empty_shape, agc_gain, fft_gain, false, false);
    if (state->consecutive_invalid_csi >= WIEVAC_INVALID_CSI_RETRAIN_DEBOUNCE) {
        restart_gain_training(state, record->arrival_us);
    }
}

static void enqueue_snapshot(const csi_record_t *record, const dsp_state_t *state, float compensation,
                             uint8_t agc_gain, int8_t fft_gain, uint8_t diagnostic_flags)
{
#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
    (void)record; (void)state; (void)compensation; (void)agc_gain; (void)fft_gain; (void)diagnostic_flags;
    return;
#else
    const pair_state_t pair = pairing_snapshot();
    if (!pair.valid) {
        return;
    }
    v4_header_t header = {
        .type = V4_MSG_SNAPSHOT,
        .payload_length = WIEVAC_SNAPSHOT_BYTES,
        .schema = V4_SCHEMA,
        .link_id = WIEVAC_LINK_ID,
        .tx_id = WIEVAC_TX_ID,
        .rx_id = WIEVAC_RX_ID,
        .boot_id = g_rx_boot_id,
        .gain_epoch = state->gain_epoch,
        .sequence = ++g_snapshot_sequence,
        .timestamp_us = record->arrival_us,
    };
    uint8_t payload[WIEVAC_SNAPSHOT_BYTES] = {0};
    put_u16(payload, record->length);
    payload[2] = record->first_word_invalid ? 1U : 0U;
    payload[3] = (uint8_t)record->rx_ctrl.rssi;
    payload[4] = (uint8_t)record->rx_ctrl.noise_floor;
    payload[5] = agc_gain;
    payload[6] = (uint8_t)fft_gain;
    payload[7] = diagnostic_flags;
    put_f32(payload + 8, compensation);
    put_u32(payload + 12, record->tx_boot_id);
    memcpy(payload + WIEVAC_SNAPSHOT_METADATA_BYTES, record->csi, WIEVAC_HTLTF_BYTES);
    uint8_t packet[V4_HEADER_SIZE + WIEVAC_SNAPSHOT_BYTES];
    if (protocol_encode(packet, sizeof(packet), &header, payload)) {
        (void)enqueue_udp(packet, sizeof(packet));
    }
#endif
}

static void process_csi(dsp_state_t *state, const csi_record_t *record)
{
#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
    v6_submit_and_emit(record);
    return;
#else
    if (state->window_start_tx_us == 0U && record->tx_timestamp_us != 0U) {
        state->window_start_tx_us = record->tx_timestamp_us;
    }
    state->last_tx_timestamp_us = record->tx_timestamp_us;
    state->last_tx_boot_id = record->tx_boot_id;
    state->last_tx_sequence = record->tx_sequence;
    state->last_rx_sequence = record->rx_sequence;
    uint8_t agc_gain = 0U;
    int8_t fft_gain = 0;
    esp_csi_gain_ctrl_get_rx_gain(&record->rx_ctrl, &agc_gain, &fft_gain);
    if (!csi_layout_valid(record)) {
        reject_invalid_shape(state, record, agc_gain, fft_gain);
        return;
    }

    float power[52];
    float raw_log_power[52];
    float shape[52];
    float bin_sum[WIEVAC_SHAPE_BINS] = {0};
    uint32_t bin_count[WIEVAC_SHAPE_BINS] = {0};
    size_t count = 0U;
    float total_power = 0.0f;
    uint32_t clipped_components = 0U;
    uint32_t zero_iq_pairs = 0U;
    for (int k = -28; k <= 28; ++k) {
        if (k == 0 || is_pilot(k)) {
            continue;
        }
        const uint16_t complex_index = (uint16_t)(k >= 0 ? k : k + 64);
        const uint16_t byte_index = complex_index * 2U;
        const int8_t imaginary = record->csi[byte_index];
        const int8_t real = record->csi[byte_index + 1U];
        clipped_components += (imaginary == INT8_MIN || imaginary == INT8_MAX) ? 1U : 0U;
        clipped_components += (real == INT8_MIN || real == INT8_MAX) ? 1U : 0U;
        zero_iq_pairs += (imaginary == 0 && real == 0) ? 1U : 0U;
        power[count] = (float)real * (float)real + (float)imaginary * (float)imaginary;
        raw_log_power[count] = 0.5f * logf(power[count] + WIEVAC_LOG_EPSILON);
        total_power += power[count];
        ++count;
    }
    if (count != 52U || !isfinite(total_power) || total_power <= 0.0f ||
        (float)clipped_components / (2.0f * (float)count) > WIEVAC_MAX_CLIPPED_IQ_RATIO ||
        (float)zero_iq_pairs / (float)count > WIEVAC_MAX_ZERO_IQ_RATIO) {
        reject_invalid_shape(state, record, agc_gain, fft_gain);
        return;
    }
    float log_normalized_power[52];
    for (size_t i = 0; i < count; ++i) {
        log_normalized_power[i] = logf(power[i] / total_power + WIEVAC_LOG_EPSILON);
    }
    const float common_level = percentile(raw_log_power, count, 0.5f);
    const float shape_center = percentile(log_normalized_power, count, 0.5f);
    for (size_t i = 0; i < count; ++i) {
        shape[i] = log_normalized_power[i] - shape_center;
        const size_t bin = (i * WIEVAC_SHAPE_BINS) / count;
        bin_sum[bin] += shape[i];
        ++bin_count[bin];
    }
    float shape_bins[WIEVAC_SHAPE_BINS] = {0};
    for (size_t bin = 0; bin < WIEVAC_SHAPE_BINS; ++bin) {
        shape_bins[bin] = bin_count[bin] > 0U ? bin_sum[bin] / (float)bin_count[bin] : 0.0f;
        if (!isfinite(shape_bins[bin])) {
            reject_invalid_shape(state, record, agc_gain, fft_gain);
            return;
        }
    }
    state->consecutive_invalid_csi = 0U;

    if (!state->gain_baseline_ready) {
        if (esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain) == ESP_OK) {
            ++state->gain_training_samples;
        }
        uint8_t ignored_agc;
        int8_t ignored_fft;
        if (state->gain_training_samples >= WIEVAC_GAIN_TRAINING_SAMPLES &&
            esp_csi_gain_ctrl_get_rx_gain_baseline(&ignored_agc, &ignored_fft) == ESP_OK) {
            state->gain_baseline_ready = true;
            start_gain_epoch(state, record->arrival_us);
        }
    }
    float compensation = 0.0f;
    const bool gain_compensation_valid = state->gain_baseline_ready &&
        esp_csi_gain_ctrl_get_gain_compensation(&compensation, agc_gain, fft_gain) == ESP_OK &&
        isfinite(compensation) && compensation > 0.0f;
    if (!gain_compensation_valid) {
        state->gain_state = GAIN_NOT_READY;
        ++state->gain_not_ready_samples;
        if (state->gain_baseline_ready) {
            ++state->consecutive_gain_failures;
            ++state->window_gain_compensation_failures;
            __atomic_add_fetch(&g_gain_compensation_failures, 1U, __ATOMIC_RELAXED);
            if (state->consecutive_gain_failures >= WIEVAC_GAIN_FAILURE_DEBOUNCE) {
                restart_gain_training(state, record->arrival_us);
            }
        }
    } else {
        state->consecutive_gain_failures = 0U;
    }
    const bool gain_metadata_change = gain_metadata_change_detected(state, (float)agc_gain,
                                                                      (float)fft_gain);
    if (gain_metadata_change) {
        if (state->gain_diagnostic_samples == 0U) {
            __atomic_add_fetch(&g_gain_transitions, 1U, __ATOMIC_RELAXED);
        }
        ++state->gain_diagnostic_samples;
    } else {
        state->gain_diagnostic_samples = 0U;
    }
    const bool rf_level_change = rf_level_change_detected(state, (float)record->rx_ctrl.rssi,
                                                           common_level);
    if (rf_level_change) {
        __atomic_add_fetch(&g_rf_level_changes, 1U, __ATOMIC_RELAXED);
    }
    gain_stats_add(state, (float)agc_gain, (float)fft_gain, (float)record->rx_ctrl.rssi, common_level);
    if (gain_compensation_valid) {
        state->gain_state = GAIN_READY;
    }
    /* Keep telemetry flowing, but never offer a training or transition frame to Pi learning. */
    const bool dynamic_shape_valid = state->gain_baseline_ready && gain_compensation_valid &&
                                     state->gain_state == GAIN_READY &&
                                     state->gain_diagnostic_samples == 0U;
    enqueue_dynamic_frame(record, state, shape_bins, agc_gain, fft_gain, dynamic_shape_valid,
                          rf_level_change);
    if (state->valid_samples >= WIEVAC_WINDOW_CAPACITY) {
        ++state->discarded_samples;
        return;
    }

    const float shape_iqr = percentile(shape, count, 0.75f) - percentile(shape, count, 0.25f);
    if (!state->trend_initialized) {
        state->fast_level = common_level;
        state->slow_level = common_level;
        state->trend_initialized = true;
    } else {
        const uint64_t elapsed_us = record->arrival_us >= state->last_sample_us
                                        ? record->arrival_us - state->last_sample_us : 0U;
        const float fast_alpha = 1.0f - expf(-(float)elapsed_us / WIEVAC_FAST_TIME_CONSTANT_US);
        const float slow_alpha = 1.0f - expf(-(float)elapsed_us / WIEVAC_SLOW_TIME_CONSTANT_US);
        state->fast_level = fast_alpha * common_level + (1.0f - fast_alpha) * state->fast_level;
        state->slow_level = slow_alpha * common_level + (1.0f - slow_alpha) * state->slow_level;
    }
    const size_t index = state->valid_samples;
    state->common[index] = common_level;
    state->shape_spread[index] = shape_iqr;
    state->temporal_motion[index] = fabsf(state->fast_level - state->slow_level);
    state->rssi[index] = (float)record->rx_ctrl.rssi;
    state->noise_floor[index] = (float)record->rx_ctrl.noise_floor;
    state->agc[index] = (float)agc_gain;
    state->fft[index] = (float)fft_gain;
    for (size_t bin = 0; bin < WIEVAC_SHAPE_BINS; ++bin) {
        state->shape_bins[bin][index] = shape_bins[bin];
    }
    ++state->valid_samples;
    if (gain_compensation_valid) {
        ++state->gain_ready_samples;
    }
    state->last_sample_us = record->arrival_us;
    const uint64_t snapshot_interval = 1000000ULL / WIEVAC_SNAPSHOT_RATE_HZ;
    if (gain_compensation_valid && (state->last_snapshot_us == 0U ||
        (record->arrival_us >= state->last_snapshot_us &&
         record->arrival_us - state->last_snapshot_us >= snapshot_interval))) {
        const uint8_t diagnostic_flags = (rf_level_change ? WIEVAC_SNAPSHOT_FLAG_RF_LEVEL_CHANGE : 0U) |
                                         (gain_metadata_change ? WIEVAC_SNAPSHOT_FLAG_GAIN_DIAGNOSTIC : 0U);
        enqueue_snapshot(record, state, compensation, agc_gain, fft_gain, diagnostic_flags);
        state->last_snapshot_us = record->arrival_us;
    }
#endif
}

static float finite_or_zero(float value)
{
    return isfinite(value) ? value : 0.0f;
}

static uint32_t saturating_add_u32(uint32_t left, uint32_t right)
{
    return UINT32_MAX - left < right ? UINT32_MAX : left + right;
}

static void emit_feature(dsp_state_t *state, uint64_t end_us)
{
#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
    /* V6 window boundaries and emission belong to edge_result_v5_pipeline.
     * Keep only the legacy scheduler clock moving here; flushing would emit
     * a duplicate partial result after process_one closes a real window. */
    reset_window(state, end_us);
    return;
#else
    const pair_state_t pair = pairing_snapshot();
    if (!pair.valid) {
        reset_window(state, end_us);
        return;
    }
    uint32_t measurement_received;
    uint32_t measurement_lost;
    uint32_t latest_tx_sequence;
    uint64_t latest_tx_timestamp;
    uint32_t latest_tx_boot_id;
    uint32_t measurement_sequence_gap;
    measurement_take(&measurement_received, &measurement_lost, &latest_tx_sequence,
                     &latest_tx_timestamp, &latest_tx_boot_id, &measurement_sequence_gap);
    /* The measurement tracker sees every TX packet, while CSI can be delayed
       or missing.  Feature timing must describe the last CSI record actually
       included in this window; using a newer measurement-only timestamp would
       put tx_timestamp_us outside window_end_tx_us and create a malformed
       feature frame. */
    if (state->valid_samples + state->discarded_samples == 0U ||
        state->last_tx_sequence == 0U || state->last_tx_timestamp_us == 0U ||
        state->last_tx_boot_id == 0U) {
        /* Do not emit a feature window without a real CSI timing context. */
        reset_window(state, end_us);
        return;
    }
    latest_tx_sequence = state->last_tx_sequence;
    latest_tx_timestamp = state->last_tx_timestamp_us;
    latest_tx_boot_id = state->last_tx_boot_id;
    const uint32_t queue_drops = __atomic_load_n(&g_csi_queue_drops, __ATOMIC_RELAXED) -
                                 state->start_csi_queue_drops;
    const uint32_t total_seen = state->valid_samples + state->discarded_samples + queue_drops;
    const uint32_t csi_seen = state->valid_samples + state->discarded_samples;
    const uint32_t gain_seen = state->gain_ready_samples + state->gain_not_ready_samples;
    /* Feature duration is defined in the TX clock domain.  The RX scheduler
       may wake late (or CSI callbacks may be delayed), so using end_us here
       can disagree with window_start_tx_us/window_end_tx_us and make a valid
       feature frame fail the Pi contract. */
    const uint64_t tx_window_duration_us = latest_tx_timestamp > state->window_start_tx_us
                                               ? latest_tx_timestamp - state->window_start_tx_us : 0U;
    const float duration_s = tx_window_duration_us > 0U
                                 ? (float)tx_window_duration_us / 1000000.0f : 0.0f;
    float payload[V4_FEATURE_FLOATS] = {0};
    if (state->valid_samples > 0U) {
        payload[0] = percentile(state->common, state->valid_samples, 0.5f);
        payload[1] = percentile(state->shape_spread, state->valid_samples, 0.5f);
        payload[2] = percentile(state->temporal_motion, state->valid_samples, 0.5f);
        payload[3] = mad_scale(state->common, state->valid_samples);
        payload[5] = percentile(state->rssi, state->valid_samples, 0.5f);
        payload[6] = percentile(state->noise_floor, state->valid_samples, 0.5f);
        payload[7] = percentile(state->agc, state->valid_samples, 0.5f);
        payload[8] = percentile(state->fft, state->valid_samples, 0.5f);
        for (size_t bin = 0; bin < WIEVAC_SHAPE_BINS; ++bin) {
            payload[12U + bin] = percentile(state->shape_bins[bin], state->valid_samples, 0.5f);
        }
    }
    payload[4] = total_seen > 0U ? (float)state->valid_samples / (float)total_seen : 0.0f;
    payload[9] = duration_s > 0.0f ? (float)state->valid_samples / duration_s : 0.0f;
    payload[10] = gain_seen > 0U ? (float)state->gain_ready_samples / (float)gain_seen : 0.0f;
    payload[11] = csi_seen > 0U ? (float)state->valid_samples / (float)csi_seen : 0.0f;

    uint8_t bytes[V4_FEATURE_PAYLOAD_SIZE];
    for (size_t i = 0; i < V4_FEATURE_FLOATS; ++i) {
        put_f32(bytes + i * 4U, finite_or_zero(payload[i]));
    }
    uint32_t counters[V4_FEATURE_COUNTERS] = {
        state->valid_samples, state->discarded_samples, queue_drops, measurement_received,
        measurement_lost, latest_tx_boot_id,
        __atomic_load_n(&g_gain_epoch_changes, __ATOMIC_RELAXED) -
            state->start_gain_epoch_changes + state->pending_gain_epoch_changes,
        (uint32_t)(tx_window_duration_us > 0U
                       ? ((tx_window_duration_us + 500U) / 1000U) : 0U),
        __atomic_load_n(&g_invalid_csi, __ATOMIC_RELAXED) - state->start_invalid_csi,
        __atomic_load_n(&g_control_drops, __ATOMIC_RELAXED) - state->start_control_drops,
        __atomic_load_n(&g_udp_drops, __ATOMIC_RELAXED) - state->start_udp_drops,
        __atomic_load_n(&g_stale_record_drops, __ATOMIC_RELAXED) - state->start_stale_record_drops,
        __atomic_load_n(&g_gain_compensation_failures, __ATOMIC_RELAXED) -
            state->start_gain_compensation_failures + state->pending_gain_compensation_failures,
        __atomic_load_n(&g_gain_baseline_retrains, __ATOMIC_RELAXED) -
            state->start_gain_baseline_retrains + state->pending_gain_baseline_retrains,
        latest_tx_sequence, csi_seen, state->discarded_samples, 0U,
        measurement_sequence_gap, state->last_rx_sequence,
    };
    for (size_t i = 0; i < V4_FEATURE_COUNTERS; ++i) {
        if (i == 2U || i == 3U || i == 4U || i == 8U || i == 9U ||
            i == 10U || i == 11U) {
            /* Only carry event totals; samples and duration describe this window. */
            counters[i] = saturating_add_u32(counters[i], state->pending_feature_counters[i]);
        }
    }
    for (size_t i = 0; i < V4_FEATURE_COUNTERS; ++i) {
        put_u32(bytes + (V4_FEATURE_FLOATS + i) * 4U, counters[i]);
    }
    const size_t timing_offset = (V4_FEATURE_FLOATS + V4_FEATURE_COUNTERS) * 4U;
    const uint64_t window_start_tx_us = state->window_start_tx_us != 0U
                                            ? state->window_start_tx_us : latest_tx_timestamp;
    const uint64_t window_end_tx_us = state->last_tx_timestamp_us != 0U
                                          ? state->last_tx_timestamp_us : latest_tx_timestamp;
    put_u64(bytes + timing_offset, window_start_tx_us);
    put_u64(bytes + timing_offset + 8U, window_end_tx_us);
    put_u64(bytes + timing_offset + 16U, latest_tx_timestamp);
    put_u64(bytes + timing_offset + 24U, end_us);
    v4_header_t header = {
        .type = V4_MSG_FEATURE,
        .payload_length = V4_FEATURE_PAYLOAD_SIZE,
        .schema = V4_SCHEMA,
        .link_id = WIEVAC_LINK_ID,
        .tx_id = WIEVAC_TX_ID,
        .rx_id = WIEVAC_RX_ID,
        .boot_id = g_rx_boot_id,
        .gain_epoch = state->gain_epoch,
        .sequence = ++g_feature_sequence,
        .timestamp_us = end_us,
    };
    uint8_t packet[V4_HEADER_SIZE + V4_FEATURE_PAYLOAD_SIZE];
    bool enqueued = false;
    if (protocol_encode(packet, sizeof(packet), &header, bytes)) {
        enqueued = enqueue_udp(packet, sizeof(packet));
    }
    if (!enqueued) {
        for (size_t i = 0; i < V4_FEATURE_COUNTERS; ++i) {
            if (i == 2U || i == 3U || i == 4U || i == 8U || i == 9U ||
                i == 10U || i == 11U) {
                state->pending_feature_counters[i] = counters[i];
            }
        }
        state->pending_gain_epoch_changes = counters[6U];
        state->pending_gain_compensation_failures = counters[12U];
        state->pending_gain_baseline_retrains = counters[13U];
        /* The queue rejection happened after the counters were sampled. */
        state->pending_feature_counters[10U] =
            saturating_add_u32(state->pending_feature_counters[10U], 1U);
    } else {
        memset(state->pending_feature_counters, 0, sizeof(state->pending_feature_counters));
        state->pending_gain_compensation_failures = 0U;
        state->pending_gain_baseline_retrains = 0U;
        state->pending_gain_epoch_changes = 0U;
    }
    state->window_gain_compensation_failures = 0U;
    reset_window(state, end_us);
#endif
}

static void log_receiver_status(const dsp_state_t *state)
{
    const EventBits_t network_bits = xEventGroupGetBits(g_network_events);
    const pair_state_t pair = pairing_snapshot();
    const uint32_t udp_queue_depth = (uint32_t)uxQueueMessagesWaiting(g_udp_queue);
    const uint32_t udp_drops = __atomic_load_n(&g_udp_drops, __ATOMIC_RELAXED);
    const uint32_t udp_queue_drops = __atomic_load_n(&g_udp_queue_drops, __ATOMIC_RELAXED);
    const uint32_t udp_send_failures = __atomic_load_n(&g_udp_send_failures, __ATOMIC_RELAXED);
    const uint32_t rf_level_changes = __atomic_load_n(&g_rf_level_changes, __ATOMIC_RELAXED);
    const uint32_t dynamic_drops = __atomic_load_n(&g_dynamic_frame_drops, __ATOMIC_RELAXED);
    const uint32_t csi_queue_drops = __atomic_load_n(&g_csi_queue_drops, __ATOMIC_RELAXED);
    const uint32_t invalid_csi = __atomic_load_n(&g_invalid_csi, __ATOMIC_RELAXED);
    const uint32_t csi_shape_rejects = __atomic_load_n(&g_csi_shape_rejects, __ATOMIC_RELAXED);
    const uint32_t csi_mac_rejects = __atomic_load_n(&g_csi_mac_rejects, __ATOMIC_RELAXED);
    const uint32_t csi_header_rejects = __atomic_load_n(&g_csi_header_rejects, __ATOMIC_RELAXED);
    const uint32_t csi_context_rejects = __atomic_load_n(&g_csi_context_rejects, __ATOMIC_RELAXED);
    const uint32_t csi_foreign_drops = __atomic_load_n(&g_csi_foreign_drops, __ATOMIC_RELAXED);
    const uint32_t stale_record_drops = __atomic_load_n(&g_stale_record_drops, __ATOMIC_RELAXED);
    const uint32_t measurement_duplicates =
        __atomic_load_n(&g_measurement_duplicates, __ATOMIC_RELAXED);
    const uint32_t measurement_reorders =
        __atomic_load_n(&g_measurement_reorders, __ATOMIC_RELAXED);
    uint32_t measurement_received = 0U;
    uint32_t measurement_lost = 0U;
    uint32_t latest_measurement_sequence = 0U;
    uint32_t measurement_boot_id = 0U;
    uint32_t measurement_sequence_gap = 0U;
    measurement_peek(&measurement_received, &measurement_lost,
                     &latest_measurement_sequence, &measurement_boot_id,
                     &measurement_sequence_gap);
    const uint32_t csi_sequence_gaps =
        __atomic_load_n(&g_csi_sequence_gaps, __ATOMIC_RELAXED);
    ESP_LOGI(TAG,
             "status wifi_connected=%d network_ready=%d paired=%d gain_state=%u gain_epoch=%" PRIu32
             " udp_queue=%" PRIu32 " udp_drops=%" PRIu32
             " udp_queue_drops=%" PRIu32 " udp_send_failures=%" PRIu32
             " rf_level_changes=%" PRIu32
             " dynamic_drops=%" PRIu32 " csi_queue_drops=%" PRIu32
             " invalid_csi=%" PRIu32 " stale_record_drops=%" PRIu32
             " csi_reject_shape=%" PRIu32 " csi_reject_mac=%" PRIu32
             " csi_reject_header=%" PRIu32 " csi_reject_context=%" PRIu32
             " csi_foreign_drops=%" PRIu32
             " first_csi_len=%d first_csi_payload_len=%d first_csi_src=" MACSTR
             " first_csi_dst=" MACSTR " first_csi_b0=%02x%02x%02x%02x"
             " measurement_received=%" PRIu32 " measurement_lost=%" PRIu32
             " measurement_sequence_gap=%" PRIu32
             " latest_measurement_sequence=%" PRIu32
             " measurement_boot_id=%" PRIu32
             " csi_sequence_gaps=%" PRIu32
             " measurement_duplicates=%" PRIu32 " measurement_reorders=%" PRIu32,
             (network_bits & WIFI_CONNECTED_BIT) != 0U,
             (network_bits & NETWORK_READY_BIT) != 0U, pair.valid,
             (unsigned)state->gain_state, state->gain_epoch, udp_queue_depth, udp_drops,
             udp_queue_drops, udp_send_failures, rf_level_changes,
             dynamic_drops, csi_queue_drops, invalid_csi,
             stale_record_drops, csi_shape_rejects, csi_mac_rejects,
             csi_header_rejects, csi_context_rejects,
             csi_foreign_drops,
             g_first_csi_len, g_first_csi_payload_len,
             MAC2STR(g_first_csi_src), MAC2STR(g_first_csi_dst),
             g_first_csi_payload[0], g_first_csi_payload[1],
             g_first_csi_payload[2], g_first_csi_payload[3],
             measurement_received, measurement_lost, measurement_sequence_gap,
             latest_measurement_sequence, measurement_boot_id, csi_sequence_gaps,
             measurement_duplicates, measurement_reorders);
#if CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE
    /* This is local serial telemetry only: keep the WIV5/schema-6 packet
     * contract unchanged while exposing the exact bootstrap gate progress. */
    const edge_result_link_state_t *link = g_v6_pipeline_ready
        ? edge_result_pipeline_link(&g_v6_pipeline, WIEVAC_LINK_ID) : NULL;
    if (link != NULL) {
        ESP_LOGI(TAG,
                  "baseline link=link-%" PRIu32 " ready=%d version=%" PRIu32
                  " samples=%u clean=%u candidate=%u/%u severe_streak=%u"
                  " temporal_conf=%.1f state=%u eligible=%d"
                 " reason=%s invalid=%" PRIu32 " seq_reject=%" PRIu32
                 " queue_drops=%" PRIu32 " source_boot=%" PRIu32
                 " temporal_at=%" PRIu64 " window=[%" PRIu64 ",%" PRIu64 "]",
                  WIEVAC_LINK_ID, link->baseline_ready, link->baseline_version,
                  (unsigned)link->baseline_count, (unsigned)link->temporal_clean_windows,
                  (unsigned)link->rebase_candidate_count,
                  (unsigned)link->rebase_candidate_windows,
                  (unsigned)link->consecutive_severe_windows,
                  (double)link->temporal_noise_confidence, (unsigned)link->baseline_state,
                 link->baseline_window_eligible, link->baseline_update_reason,
                 link->invalid_total, link->sequence_reject_total,
                 edge_result_pipeline_queue_drops(&g_v6_pipeline), link->source_boot_id,
                 link->temporal_noise_last_update_us, link->window_start_us,
                 link->window_last_timestamp_us);
        ESP_LOGI(TAG, "baseline_pair tx_boot=%" PRIu32 " pipeline_tx_boot=%" PRIu32
                 " reset_generation=%" PRIu32,
                 pair.tx_boot_id, g_v6_tx_boot_id,
                 __atomic_load_n(&g_dsp_reset_generation, __ATOMIC_ACQUIRE));
    }
#endif
}

static void dsp_task(void *argument)
{
    (void)argument;
    dsp_state_t *state = &g_dsp_state;
    uint64_t now_us = (uint64_t)esp_timer_get_time();
    uint64_t last_status_log_us = 0U;
    dsp_reset(state, now_us);
    uint32_t reset_generation = __atomic_load_n(&g_dsp_reset_generation, __ATOMIC_ACQUIRE);
    csi_record_t record;
    for (;;) {
        const uint32_t current_generation = __atomic_load_n(&g_dsp_reset_generation, __ATOMIC_ACQUIRE);
        if (current_generation != reset_generation) {
            dsp_reset(state, (uint64_t)esp_timer_get_time());
            reset_generation = current_generation;
        }
        const TickType_t timeout = pdMS_TO_TICKS(100);
        if (xQueueReceive(g_csi_queue, &record, timeout) == pdTRUE) {
            const uint32_t received_generation =
                __atomic_load_n(&g_dsp_reset_generation, __ATOMIC_ACQUIRE);
            if (received_generation != reset_generation) {
                dsp_reset(state, (uint64_t)esp_timer_get_time());
                reset_generation = received_generation;
                __atomic_add_fetch(&g_stale_record_drops, 1U, __ATOMIC_RELAXED);
                continue;
            }
            const pair_state_t pair = pairing_snapshot();
            if (!pair.valid || record.tx_boot_id != pair.tx_boot_id ||
                memcmp(pair.tx_mac, record.source_mac, ESP_NOW_ETH_ALEN) != 0) {
                __atomic_add_fetch(&g_stale_record_drops, 1U, __ATOMIC_RELAXED);
                continue;
            }
            if (record.arrival_us < state->window_start_us) {
                __atomic_add_fetch(&g_stale_record_drops, 1U, __ATOMIC_RELAXED);
                ++state->discarded_samples;
                continue;
            }
            while (record.arrival_us >= state->window_start_us &&
                   record.arrival_us - state->window_start_us >= (uint64_t)WIEVAC_WINDOW_MS * 1000U) {
                const uint64_t end_us = state->window_start_us + (uint64_t)WIEVAC_WINDOW_MS * 1000U;
                emit_feature(state, end_us);
            }
            process_csi(state, &record);
        }
        now_us = (uint64_t)esp_timer_get_time();
        if (last_status_log_us == 0U ||
            now_us - last_status_log_us >= WIEVAC_STATUS_LOG_INTERVAL_US) {
            log_receiver_status(state);
            last_status_log_us = now_us;
        }
        while (now_us >= state->window_start_us &&
               now_us - state->window_start_us >= (uint64_t)WIEVAC_WINDOW_MS * 1000U) {
            const uint64_t end_us = state->window_start_us + (uint64_t)WIEVAC_WINDOW_MS * 1000U;
            emit_feature(state, end_us);
        }
    }
}

static void udp_sender_task(void *argument)
{
    (void)argument;
    int socket_fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (socket_fd < 0) {
        ESP_LOGE(TAG, "cannot create UDP socket");
        vTaskDelete(NULL);
        return;
    }
    /* Bind the source port so Pi endpoint allow-lists can validate both the
       receiver address and the configured UDP port instead of an ephemeral
       kernel-selected port. */
    struct sockaddr_in local_address = {0};
    local_address.sin_family = AF_INET;
    local_address.sin_port = htons(WIEVAC_PI_UDP_PORT);
    local_address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(socket_fd, (struct sockaddr *)&local_address, sizeof(local_address)) != 0) {
        ESP_LOGE(TAG, "cannot bind UDP source port=%u: errno=%d",
                 (unsigned)WIEVAC_PI_UDP_PORT, errno);
        close(socket_fd);
        vTaskDelete(NULL);
        return;
    }
    udp_packet_t datagram;
    for (;;) {
        if (xQueueReceive(g_udp_queue, &datagram, pdMS_TO_TICKS(250)) != pdTRUE) {
            continue;
        }
        /* Wi-Fi association is only a local precondition.  Mark the Pi route
         * ready after a real UDP send attempt, and clear it on send failure. */
        if ((xEventGroupGetBits(g_network_events) & WIFI_CONNECTED_BIT) == 0U) {
            xEventGroupClearBits(g_network_events, NETWORK_READY_BIT);
            __atomic_add_fetch(&g_udp_send_failures, 1U, __ATOMIC_RELAXED);
            __atomic_add_fetch(&g_udp_drops, 1U, __ATOMIC_RELAXED);
            continue;
        }
        const int sent = sendto(socket_fd, datagram.bytes, datagram.length, 0,
                                (struct sockaddr *)&g_pi_address, sizeof(g_pi_address));
        if (sent != (int)datagram.length) {
            xEventGroupClearBits(g_network_events, NETWORK_READY_BIT);
            __atomic_add_fetch(&g_udp_send_failures, 1U, __ATOMIC_RELAXED);
            __atomic_add_fetch(&g_udp_drops, 1U, __ATOMIC_RELAXED);
        } else {
            xEventGroupSetBits(g_network_events, NETWORK_READY_BIT);
        }
    }
}

static void wifi_event_handler(void *argument, esp_event_base_t base, int32_t event_id, void *event_data)
{
    (void)argument;
    if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *disconnected = event_data;
        const unsigned int reason = disconnected != NULL ? (unsigned int)disconnected->reason : 0U;
        const uint32_t reconnect_count = ++g_wifi_reconnect_count;
        xEventGroupClearBits(g_network_events, WIFI_CONNECTED_BIT | NETWORK_READY_BIT);
        ESP_LOGW(TAG, "Wi-Fi disconnected: reason=%u ssid=%s reconnect=%" PRIu32,
                 reason, WIEVAC_WIFI_SSID, reconnect_count);
        const esp_err_t reconnect_err = esp_wifi_connect();
        if (reconnect_err != ESP_OK) {
            ESP_LOGW(TAG, "Wi-Fi reconnect request failed: %s", esp_err_to_name(reconnect_err));
        }
    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *got_ip = event_data;
        if (got_ip == NULL) {
            xEventGroupClearBits(g_network_events, NETWORK_READY_BIT);
            ESP_LOGE(TAG, "Wi-Fi got-IP event has no address data");
            return;
        }
        uint8_t primary_channel = 0U;
        wifi_second_chan_t secondary_channel = WIFI_SECOND_CHAN_NONE;
        const esp_err_t channel_err = esp_wifi_get_channel(&primary_channel, &secondary_channel);
        const in_addr_t pi_addr = inet_addr(WIEVAC_PI_IP);
        const bool pi_configured = pi_addr != INADDR_NONE && pi_addr != 0U;
        const bool channel_ok = channel_err == ESP_OK && primary_channel == WIEVAC_WIFI_CHANNEL;
        xEventGroupSetBits(g_network_events, WIFI_CONNECTED_BIT);
        /* The Pi may be behind a router; gateway_ip need not equal Pi IP.
           NETWORK_READY remains clear until the UDP sender gets a successful
           send result, so Wi-Fi association is not misreported as Pi reachability. */
        xEventGroupClearBits(g_network_events, NETWORK_READY_BIT);
        if (pi_configured && channel_ok) {
            ESP_LOGI(TAG, "Wi-Fi connected: ip=" IPSTR " gateway=" IPSTR
                     " channel=%u network_ready=0 awaiting_udp_probe",
                     IP2STR(&got_ip->ip_info.ip), IP2STR(&got_ip->ip_info.gw), primary_channel);
        } else {
            xEventGroupClearBits(g_network_events, NETWORK_READY_BIT);
            ESP_LOGE(TAG,
                     "Wi-Fi network rejected: ip=" IPSTR " gateway=" IPSTR
                     " channel=%u configured_pi=%s expected_channel=%u channel_err=%s",
                     IP2STR(&got_ip->ip_info.ip), IP2STR(&got_ip->ip_info.gw), primary_channel,
                     WIEVAC_PI_IP, WIEVAC_WIFI_CHANNEL, esp_err_to_name(channel_err));
        }
    }
}

static void wifi_initialize(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_create_default_wifi_sta() == NULL ? ESP_FAIL : ESP_OK);
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                         wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                         wifi_event_handler, NULL, NULL));
    wifi_config_t wifi = {0};
    const size_t ssid_length = strlen(WIEVAC_WIFI_SSID);
    const size_t password_length = strlen(WIEVAC_WIFI_PASSWORD);
    if (ssid_length >= sizeof(wifi.sta.ssid) || password_length >= sizeof(wifi.sta.password)) {
        ESP_LOGE(TAG, "Wi-Fi credentials exceed ESP-IDF limits (ssid=%u password=%u)",
                 (unsigned)ssid_length, (unsigned)password_length);
        ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
    }
    memcpy(wifi.sta.ssid, WIEVAC_WIFI_SSID, ssid_length);
    memcpy(wifi.sta.password, WIEVAC_WIFI_PASSWORD, password_length);
    wifi.sta.channel = WIEVAC_WIFI_CHANNEL;
    wifi.sta.pmf_cfg.capable = true;
    wifi.sta.pmf_cfg.required = false;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_protocol(WIFI_IF_STA,
                                          WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G |
                                              WIFI_PROTOCOL_11N));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIEVAC_WIFI_BANDWIDTH));
    ESP_ERROR_CHECK(esp_wifi_connect());
}

static void csi_initialize(void)
{
    wifi_csi_config_t config = {
        .lltf_en = false,
        .htltf_en = true,
        .stbc_htltf2_en = false,
        .ltf_merge_en = false,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_callback, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&config));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_read_mac(g_rx_mac, ESP_MAC_WIFI_STA));
    if (CONFIG_WIEVAC_EXPECTED_TX_MAC[0] == '\0') {
        ESP_LOGW(TAG, "pairing_auth=trusted_closed_lab expected_tx_mac=unset");
    } else if (parse_mac_text(CONFIG_WIEVAC_EXPECTED_TX_MAC, g_expected_tx_mac)) {
        g_expected_tx_mac_configured = true;
        ESP_LOGI(TAG, "pairing_auth=tx_mac_allowlist");
    } else {
        g_expected_tx_mac_invalid = true;
        ESP_LOGE(TAG, "invalid CONFIG_WIEVAC_EXPECTED_TX_MAC; pairing disabled");
    }
    g_rx_boot_id = esp_random();
    if (g_rx_boot_id == 0U) {
        g_rx_boot_id = 1U;
    }
    g_network_events = xEventGroupCreate();
    g_control_queue = xQueueCreate(WIEVAC_CONTROL_QUEUE_DEPTH, sizeof(control_event_t));
    g_csi_queue = xQueueCreate(WIEVAC_CSI_QUEUE_DEPTH, sizeof(csi_record_t));
    g_udp_queue = xQueueCreate(WIEVAC_UDP_QUEUE_DEPTH, sizeof(udp_packet_t));
    ESP_ERROR_CHECK(g_network_events != NULL && g_control_queue != NULL &&
                    g_csi_queue != NULL && g_udp_queue != NULL ? ESP_OK : ESP_ERR_NO_MEM);
    memset(&g_pi_address, 0, sizeof(g_pi_address));
    g_pi_address.sin_family = AF_INET;
    g_pi_address.sin_port = htons(WIEVAC_PI_UDP_PORT);
    g_pi_address.sin_addr.s_addr = inet_addr(WIEVAC_PI_IP);

    wifi_initialize();
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(espnow_receive_callback));
    csi_initialize();
    ESP_ERROR_CHECK(xTaskCreate(espnow_control_task, "wievac_ctrl", 4096U, NULL, 6U, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(dsp_task, "wievac_dsp", 8192U, NULL, 5U, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(xTaskCreate(udp_sender_task, "wievac_udp", 4096U, NULL, 4U, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_LOGI(TAG, "%s", WIEVAC_BINARY_ID_MARKER);
    ESP_LOGI(TAG, "EDGE_RESULT_V6 boot=%" PRIu32 " link=link-%" PRIu32 " rx_id=rx-%" PRIu32
             " channel=%u bandwidth=HT20 mcs=%u phy_rate=%u Pi=%s:%u snapshots=%uHz",
             g_rx_boot_id, WIEVAC_LINK_ID, WIEVAC_RX_ID, WIEVAC_WIFI_CHANNEL,
             WIEVAC_MCS_INDEX, (unsigned)WIEVAC_ESPNOW_RATE,
             WIEVAC_PI_IP, WIEVAC_PI_UDP_PORT, WIEVAC_SNAPSHOT_RATE_HZ);
}
