/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * WiEvac ESP32-S3 receiver, Protocol V2.
 *
 * Data path:
 *   Wi-Fi CSI callback -> bounded copy queue -> DSP task -> real-time window
 *   -> Protocol V2 serializer -> UDP sender task.
 *
 * Protocol V2 uses network byte order (big-endian). Native C structures are
 * never transmitted. The fixed 68-byte header is:
 *
 *   0  u32 magic = 0x57495632 ("WIV2")
 *   4  u8  protocol_version = 2
 *   5  u8  message_type
 *   6  u16 flags
 *   8  u16 header_length = 68
 *  10  u16 payload_length
 *  12  u16 feature_schema_version (0 for non-feature messages)
 *  14  u16 reserved = 0
 *  16  u32 link_id
 *  20  u32 tx_id
 *  24  u32 rx_id
 *  28  u32 sequence (wraps modulo 2^32; consumers track each message stream)
 *  32  u64 sender monotonic timestamp, microseconds since sender boot
 *  40  u32 window_duration_ms
 *  44  u32 sample_count
 *  48  u32 invalid_csi_count, cumulative
 *  52  u32 wrong_source_count, cumulative
 *  56  u32 csi_queue_drop_count, cumulative
 *  60  u32 measurement_packet_loss_count, cumulative
 *  64  u32 CRC-32/ISO-HDLC over header bytes [0, 64) followed by payload
 *  68  payload
 *
 * FEATURE_RUN schema 2 is 64 bytes: twelve IEEE-754 binary32 values in
 * big-endian order (delta_amp, std_dev, spectral_roughness, rssi_mean,
 * rssi_std, noise_floor_mean, agc_mean, fft_mean, packet_rate_hz,
 * packet_loss_ratio, jitter_ms, valid_subcarrier_mean), followed by four
 * big-endian u32 values (measurement_rx_window,
 * first_word_invalid_cumulative, tx_firmware_version, rx_firmware_version).
 * CSI_SNAPSHOT schema 2 begins with 32 bytes of metadata: the original
 * 16-byte layout descriptor, then source MAC, channel/bandwidth, RSSI,
 * noise-floor, AGC/FFT gain, rx_state, sig_mode, secondary channel, and
 * temporal packet decimation.
 *
 * The timestamp is useful for ordering on its originating device. It is not a
 * wall-clock timestamp and must not be subtracted from a Raspberry Pi clock.
 * PI_ALIVE (0x30) is header-only: Pi echoes a FEATURE_RUN sequence from its
 * bound UDP service socket so RX can distinguish IP connectivity from a live
 * ingest process. CAPTURE_CONTROL (0x31) has a one-byte START/STOP payload;
 * CAPTURE_ACK (0x32) echoes that command and its sequence with RX's actual
 * runtime mode. Both use the normal V2 header, identity fields, and CRC.
 */

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_csi_gain_ctrl.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_idf_version.h"
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
#include "nvs.h"
#include "nvs_flash.h"

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 1, 0) || \
    ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
#error "WiEvac receiver currently supports ESP-IDF >= 5.1 and < 5.5; the project lock is 5.4.4"
#endif

/* -------------------------------------------------------------------------- */
/* Deployment and development configuration                                   */
/* -------------------------------------------------------------------------- */

/*
 * Production credentials and deployment IDs should be provisioned in the
 * "wievac_cfg" NVS namespace. These macros are development fallbacks because
 * this task is intentionally limited to one source file. The password is never
 * logged. Define them from the build environment to avoid source defaults.
 */
#ifndef WIEVAC_DEV_WIFI_SSID
#define WIEVAC_DEV_WIFI_SSID              "WiEvac_Pi5"
#endif
#ifndef WIEVAC_DEV_WIFI_PASSWORD
#define WIEVAC_DEV_WIFI_PASSWORD          "12345678"
#endif
#ifndef WIEVAC_DEV_LINK_ID
#define WIEVAC_DEV_LINK_ID                UINT32_C(1)
#endif
#ifndef WIEVAC_DEV_RX_ID
#define WIEVAC_DEV_RX_ID                  UINT32_C(0) /* 0: hash full STA MAC. */
#endif

#define WIEVAC_WIFI_CHANNEL               11U
#define WIEVAC_WIFI_BANDWIDTH             WIFI_BW_HT20
#define WIEVAC_ESPNOW_PHYMODE             WIFI_PHY_MODE_HT20
#define WIEVAC_ESPNOW_RATE                WIFI_PHY_RATE_MCS0_LGI
#define WIEVAC_PI_UDP_PORT                8888U
#define WIEVAC_PI_GATEWAY_IP              "10.42.0.1"
#define WIEVAC_PI_LIVENESS_TIMEOUT_MS     5000U

#define WIEVAC_MODE_RUN                   0U
#define WIEVAC_MODE_CAPTURE               1U
#ifndef WIEVAC_OPERATING_MODE
#define WIEVAC_OPERATING_MODE             WIEVAC_MODE_RUN
#endif

#define WIEVAC_WINDOW_DURATION_MS         1000U
#define WIEVAC_CSI_QUEUE_DEPTH            16U
#define WIEVAC_UDP_QUEUE_DEPTH            12U
#define WIEVAC_ESPNOW_QUEUE_DEPTH         16U
#define WIEVAC_PAIR_DISCOVERY_MS          3000U
#define WIEVAC_PAIR_SESSION_TIMEOUT_MS    10000U
#define WIEVAC_PAIR_RESET_GPIO            GPIO_NUM_0
#define WIEVAC_PAIR_RESET_HOLD_MS         3000U

#define WIEVAC_MEDIAN_WINDOW              5U
#define WIEVAC_EMA_FAST_ALPHA             0.8f
#define WIEVAC_EMA_SLOW_BASE_ALPHA        0.005f
#define WIEVAC_EMA_SLOW_FLUCTUATION_GAIN  20.0f
#define WIEVAC_GAIN_BASELINE_SAMPLES      100U

/* RUN and CAPTURE both acquire one HT-LTF from an HT20 measurement frame. */
#define WIEVAC_HTLTF_BYTES                128U
#define WIEVAC_HTLTF_COMPLEX              (WIEVAC_HTLTF_BYTES / 2U)
#define WIEVAC_CAPTURE_PACKET_DECIMATION  25U /* Emit one bounded snapshot per 25 CSI records. */
#define WIEVAC_CAPTURE_SAMPLE_DECIMATION  1U  /* Raw payload keeps every selected complex sample. */
#define WIEVAC_CAPTURE_MAX_COMPLEX        WIEVAC_HTLTF_COMPLEX

#define WIEVAC_RX_FIRMWARE_VERSION        UINT32_C(0x00020000)
#define WIEVAC_MAX_SEQUENCE_GAP           UINT32_C(10000)

_Static_assert(sizeof(float) == 4, "Protocol V2 requires 32-bit IEEE-754 float");
_Static_assert(WIEVAC_OPERATING_MODE == WIEVAC_MODE_RUN ||
               WIEVAC_OPERATING_MODE == WIEVAC_MODE_CAPTURE,
               "WIEVAC_OPERATING_MODE must be RUN or CAPTURE");
_Static_assert(WIEVAC_PI_LIVENESS_TIMEOUT_MS > 2U * WIEVAC_WINDOW_DURATION_MS,
               "Pi liveness timeout must tolerate at least two feature windows");
_Static_assert(WIEVAC_CAPTURE_PACKET_DECIMATION > 0U &&
               WIEVAC_CAPTURE_PACKET_DECIMATION <= UINT8_MAX,
               "Capture packet decimation must fit the snapshot metadata");

/* -------------------------------------------------------------------------- */
/* Protocol constants and flags                                               */
/* -------------------------------------------------------------------------- */

#define V2_MAGIC                          UINT32_C(0x57495632)
#define V2_VERSION                        2U
#define V2_HEADER_SIZE                    68U
#define V2_FEATURE_SCHEMA                 2U
#define V2_FEATURE_PAYLOAD_SIZE           64U
#define V2_SNAPSHOT_META_SIZE             32U
#define V2_MAX_SNAPSHOT_PAYLOAD           (V2_SNAPSHOT_META_SIZE + 2U * WIEVAC_CAPTURE_MAX_COMPLEX)
#define V2_MAX_PACKET_SIZE                (V2_HEADER_SIZE + V2_MAX_SNAPSHOT_PAYLOAD)

enum {
    V2_MSG_PAIR_HELLO  = 0x01,
    V2_MSG_PAIR_OFFER  = 0x02,
    V2_MSG_PAIR_CONFIRM = 0x03,
    V2_MSG_PAIR_ACK    = 0x04,
    V2_MSG_MEASUREMENT = 0x10,
    V2_MSG_FEATURE_RUN = 0x20,
    V2_MSG_CSI_SNAPSHOT = 0x21,
    V2_MSG_PI_ALIVE    = 0x30,
    V2_MSG_CAPTURE_CONTROL = 0x31,
    V2_MSG_CAPTURE_ACK = 0x32,
};

enum {
    V2_FLAG_PAIRED                  = 0x0001,
    V2_FLAG_RUN                     = 0x0002,
    V2_FLAG_CAPTURE                 = 0x0004,
    V2_FLAG_WINDOW_VALID            = 0x0008,
    V2_FLAG_CHANNEL_FILTER_ENABLED  = 0x0010,
    V2_FLAG_FIRST_WORD_INVALID_SEEN = 0x0020,
    V2_FLAG_GAIN_METADATA_VALID     = 0x0040,
    V2_FLAG_SYSTEM_UNAVAILABLE      = 0x0080,
};

enum {
    V2_CAP_ESPNOW_UNICAST = 0x00000001,
    V2_CAP_CONFIRM_ACK    = 0x00000002,
    V2_CAP_CRC32          = 0x00000004,
};

#define V2_REQUIRED_PAIR_CAPS (V2_CAP_ESPNOW_UNICAST | V2_CAP_CONFIRM_ACK | V2_CAP_CRC32)
#define V2_LOCAL_CAPS         (V2_CAP_ESPNOW_UNICAST | V2_CAP_CONFIRM_ACK | V2_CAP_CRC32)

enum {
    V2_LTF_LLTF = 1,
    V2_LTF_HTLTF = 2,
    V2_LTF_STBC_HTLTF = 3,
};

enum {
    V2_SAMPLE_FORMAT_INT8_IMAG_REAL = 1,
    V2_CAPTURE_FLAG_TRUNCATED = 0x01,
    V2_CAPTURE_FLAG_FIRST_WORD_SKIPPED = 0x02,
};

/* MEASUREMENT payload from TX, all fields big-endian except one-byte fields. */
#define V2_MEASUREMENT_PAYLOAD_SIZE       16U
#define V2_PAIR_HELLO_PAYLOAD_SIZE        8U
#define V2_PAIR_OFFER_PAYLOAD_SIZE        12U
#define V2_PAIR_CONFIRM_PAYLOAD_SIZE      8U
#define V2_CAPTURE_CONTROL_PAYLOAD_SIZE   1U
#define V2_CAPTURE_ACK_PAYLOAD_SIZE       6U

enum {
    V2_CAPTURE_COMMAND_START = 1,
    V2_CAPTURE_COMMAND_STOP = 2,
};

/* ESP32-S3 rx_ctrl.sig_mode value documented by ESP-IDF for HT frames. */
#define WIEVAC_SIG_MODE_HT                1U

/* -------------------------------------------------------------------------- */
/* Runtime types and state                                                    */
/* -------------------------------------------------------------------------- */

typedef struct {
    uint8_t message_type;
    uint16_t flags;
    uint16_t payload_length;
    uint16_t feature_schema;
    uint32_t link_id;
    uint32_t tx_id;
    uint32_t rx_id;
    uint32_t sequence;
    uint64_t timestamp_us;
    uint32_t window_duration_ms;
    uint32_t sample_count;
    uint32_t invalid_csi;
    uint32_t wrong_source;
    uint32_t queue_drop;
    uint32_t packet_loss;
} v2_header_t;

typedef struct {
    uint32_t link_id;
    uint32_t rx_id;
    char wifi_ssid[33];
    char wifi_password[65];
} app_config_t;

typedef struct {
    bool valid;
    uint8_t tx_mac[ESP_NOW_ETH_ALEN];
    uint32_t tx_id;
} pairing_state_t;

typedef struct {
    uint64_t arrival_us;
    uint16_t len;
    bool first_word_invalid;
    uint8_t source_mac[ESP_NOW_ETH_ALEN];
    wifi_pkt_rx_ctrl_t rx_ctrl;
    int8_t csi[WIEVAC_HTLTF_BYTES];
} csi_record_t;

typedef struct {
    uint16_t len;
    uint8_t bytes[V2_MAX_PACKET_SIZE];
} udp_datagram_t;

#define WIEVAC_ESPNOW_FRAME_MAX (V2_HEADER_SIZE + V2_MEASUREMENT_PAYLOAD_SIZE)
typedef struct {
    uint64_t arrival_us;
    uint8_t source_mac[ESP_NOW_ETH_ALEN];
    uint16_t len;
    uint8_t bytes[WIEVAC_ESPNOW_FRAME_MAX];
} espnow_rx_event_t;

typedef struct {
    uint32_t count;
    double mean;
    double m2;
} running_stats_t;

typedef struct {
    bool initialized;
    uint32_t last_sequence;
    uint64_t last_sender_timestamp_us;
    uint64_t last_arrival_us;
    uint32_t received_window;
    uint32_t lost_window;
    running_stats_t interarrival_ms;
} measurement_stats_t;

typedef struct {
    float history[WIEVAC_MEDIAN_WINDOW];
    uint8_t history_index;
    float ema_fast;
    float ema_slow;
    bool initialized;
} subcarrier_filter_t;

typedef struct {
    subcarrier_filter_t subcarrier[57]; /* signed k = -28..+28 maps to k+28 */
    running_stats_t delta;
    running_stats_t roughness;
    running_stats_t rssi;
    running_stats_t noise_floor;
    running_stats_t agc;
    running_stats_t fft;
    running_stats_t valid_subcarriers;
    uint32_t sample_count;
    uint32_t gain_metadata_count;
    uint32_t gain_compensation_ready_count;
    uint32_t gain_samples_recorded;
    bool gain_baseline_requested;
    uint32_t capture_counter;
    uint32_t start_invalid_csi;
    uint32_t start_wrong_source;
    uint32_t start_queue_drop;
    uint32_t start_packet_loss;
    uint32_t start_first_word_invalid;
    uint32_t start_udp_error;
} dsp_state_t;

typedef struct {
    bool used;
    uint8_t mac[ESP_NOW_ETH_ALEN];
    uint32_t tx_id;
    uint32_t hello_nonce;
    uint32_t capabilities;
} pair_candidate_t;

typedef struct {
    bool active;
    bool completed;
    uint8_t mac[ESP_NOW_ETH_ALEN];
    uint32_t tx_id;
    uint32_t hello_nonce;
    uint32_t offer_nonce;
    uint64_t expires_us;
} pair_offer_session_t;

static const char *TAG = "wievac_rx_v2";
static app_config_t g_config;
static pairing_state_t g_pairing;
static uint8_t g_rx_mac[ESP_NOW_ETH_ALEN];

static QueueHandle_t g_csi_queue;
static QueueHandle_t g_udp_queue;
static QueueHandle_t g_espnow_queue;
static EventGroupHandle_t g_network_events;

#define NETWORK_GOT_IP_BIT BIT0

static portMUX_TYPE g_pairing_mux = portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE g_measurement_mux = portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE g_pi_address_mux = portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE g_pi_alive_mux = portMUX_INITIALIZER_UNLOCKED;
static portMUX_TYPE g_capture_control_mux = portMUX_INITIALIZER_UNLOCKED;
static measurement_stats_t g_measurement_stats;
static struct sockaddr_in g_pi_address;

static uint32_t g_invalid_csi_count;
static uint32_t g_wrong_source_count;
static uint32_t g_csi_queue_drop_count;
static uint32_t g_measurement_loss_count;
static uint32_t g_first_word_invalid_count;
static uint32_t g_espnow_queue_drop_count;
static uint32_t g_udp_queue_drop_count;
static uint32_t g_udp_send_error_count;
static uint32_t g_espnow_send_success_count;
static uint32_t g_espnow_send_failure_count;
static uint32_t g_feature_sequence;
static uint32_t g_snapshot_sequence;
static uint32_t g_pair_sequence;
static uint32_t g_capture_ack_sequence;
typedef struct {
    uint32_t mode;
    uint32_t last_sequence;
    uint32_t last_command;
    uint64_t last_sender_timestamp_us;
    bool initialized;
} capture_control_state_t;
static capture_control_state_t g_capture_control = {.mode = WIEVAC_MODE_RUN};
static uint32_t g_tx_firmware_version;
static uint32_t g_dsp_reset_generation;
static uint64_t g_pi_last_alive_us;
/* Owned by udp_sender_task; PI_ALIVE may echo either recent FEATURE_RUN. */
static uint32_t g_last_feature_sent_sequence;
static uint32_t g_previous_feature_sent_sequence;
static uint32_t g_feature_sent_count;

/* -------------------------------------------------------------------------- */
/* Small, allocation-free protocol helpers                                    */
/* -------------------------------------------------------------------------- */

static uint16_t read_u16_be(const uint8_t *p)
{
    return (uint16_t)(((uint16_t)p[0] << 8) | (uint16_t)p[1]);
}

static uint32_t read_u32_be(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static uint64_t read_u64_be(const uint8_t *p)
{
    return ((uint64_t)read_u32_be(p) << 32) | (uint64_t)read_u32_be(p + 4);
}

static void write_u16_be(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)(value >> 8);
    p[1] = (uint8_t)value;
}

static void write_u32_be(uint8_t *p, uint32_t value)
{
    p[0] = (uint8_t)(value >> 24);
    p[1] = (uint8_t)(value >> 16);
    p[2] = (uint8_t)(value >> 8);
    p[3] = (uint8_t)value;
}

static void write_u64_be(uint8_t *p, uint64_t value)
{
    write_u32_be(p, (uint32_t)(value >> 32));
    write_u32_be(p + 4, (uint32_t)value);
}

static void write_f32_be(uint8_t *p, float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    write_u32_be(p, bits);
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t length)
{
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            const uint32_t mask = (uint32_t)-(int32_t)(crc & 1U);
            crc = (crc >> 1) ^ (UINT32_C(0xEDB88320) & mask);
        }
    }
    return crc;
}

static uint32_t protocol_crc(const uint8_t *packet, uint16_t payload_length)
{
    uint32_t crc = UINT32_C(0xFFFFFFFF);
    crc = crc32_update(crc, packet, 64U);
    crc = crc32_update(crc, packet + V2_HEADER_SIZE, payload_length);
    return crc ^ UINT32_C(0xFFFFFFFF);
}

static bool protocol_decode(const uint8_t *packet, size_t length,
                            v2_header_t *header, const uint8_t **payload)
{
    if (packet == NULL || header == NULL || payload == NULL || length < V2_HEADER_SIZE) {
        return false;
    }
    if (read_u32_be(packet) != V2_MAGIC || packet[4] != V2_VERSION ||
        read_u16_be(packet + 8) != V2_HEADER_SIZE || read_u16_be(packet + 14) != 0U) {
        return false;
    }

    const uint16_t payload_length = read_u16_be(packet + 10);
    if (length != (size_t)V2_HEADER_SIZE + payload_length) {
        return false;
    }
    if (read_u32_be(packet + 64) != protocol_crc(packet, payload_length)) {
        return false;
    }

    header->message_type = packet[5];
    header->flags = read_u16_be(packet + 6);
    header->payload_length = payload_length;
    header->feature_schema = read_u16_be(packet + 12);
    header->link_id = read_u32_be(packet + 16);
    header->tx_id = read_u32_be(packet + 20);
    header->rx_id = read_u32_be(packet + 24);
    header->sequence = read_u32_be(packet + 28);
    header->timestamp_us = read_u64_be(packet + 32);
    header->window_duration_ms = read_u32_be(packet + 40);
    header->sample_count = read_u32_be(packet + 44);
    header->invalid_csi = read_u32_be(packet + 48);
    header->wrong_source = read_u32_be(packet + 52);
    header->queue_drop = read_u32_be(packet + 56);
    header->packet_loss = read_u32_be(packet + 60);
    *payload = packet + V2_HEADER_SIZE;
    return true;
}

static bool protocol_encode(uint8_t *packet, size_t capacity,
                            const v2_header_t *header, const uint8_t *payload)
{
    if (packet == NULL || header == NULL ||
        capacity < (size_t)V2_HEADER_SIZE + header->payload_length ||
        (header->payload_length > 0U && payload == NULL)) {
        return false;
    }

    const size_t total = (size_t)V2_HEADER_SIZE + header->payload_length;
    memset(packet, 0, total);
    write_u32_be(packet, V2_MAGIC);
    packet[4] = V2_VERSION;
    packet[5] = header->message_type;
    write_u16_be(packet + 6, header->flags);
    write_u16_be(packet + 8, V2_HEADER_SIZE);
    write_u16_be(packet + 10, header->payload_length);
    write_u16_be(packet + 12, header->feature_schema);
    write_u16_be(packet + 14, 0U);
    write_u32_be(packet + 16, header->link_id);
    write_u32_be(packet + 20, header->tx_id);
    write_u32_be(packet + 24, header->rx_id);
    write_u32_be(packet + 28, header->sequence);
    write_u64_be(packet + 32, header->timestamp_us);
    write_u32_be(packet + 40, header->window_duration_ms);
    write_u32_be(packet + 44, header->sample_count);
    write_u32_be(packet + 48, header->invalid_csi);
    write_u32_be(packet + 52, header->wrong_source);
    write_u32_be(packet + 56, header->queue_drop);
    write_u32_be(packet + 60, header->packet_loss);
    if (header->payload_length > 0U) {
        memcpy(packet + V2_HEADER_SIZE, payload, header->payload_length);
    }
    write_u32_be(packet + 64, protocol_crc(packet, header->payload_length));
    return true;
}

static uint32_t atomic_load_u32(const uint32_t *value)
{
    return __atomic_load_n(value, __ATOMIC_RELAXED);
}

static void atomic_increment_u32(uint32_t *value)
{
    (void)__atomic_add_fetch(value, 1U, __ATOMIC_RELAXED);
}

static uint64_t pi_alive_load_us(void)
{
    uint64_t value;
    portENTER_CRITICAL(&g_pi_alive_mux);
    value = g_pi_last_alive_us;
    portEXIT_CRITICAL(&g_pi_alive_mux);
    return value;
}

static void pi_alive_store_us(uint64_t value)
{
    portENTER_CRITICAL(&g_pi_alive_mux);
    g_pi_last_alive_us = value;
    portEXIT_CRITICAL(&g_pi_alive_mux);
}

static uint32_t next_sequence(uint32_t *value)
{
    return __atomic_add_fetch(value, 1U, __ATOMIC_RELAXED);
}

static capture_control_state_t capture_control_snapshot(void)
{
    capture_control_state_t snapshot;
    portENTER_CRITICAL(&g_capture_control_mux);
    snapshot = g_capture_control;
    portEXIT_CRITICAL(&g_capture_control_mux);
    return snapshot;
}

static void capture_control_reset_to_run(const char *reason)
{
    bool transitioned;
    portENTER_CRITICAL(&g_capture_control_mux);
    transitioned = g_capture_control.mode == WIEVAC_MODE_CAPTURE;
    g_capture_control.mode = WIEVAC_MODE_RUN;
    g_capture_control.last_sequence = 0U;
    g_capture_control.last_command = 0U;
    g_capture_control.last_sender_timestamp_us = 0U;
    g_capture_control.initialized = false;
    portEXIT_CRITICAL(&g_capture_control_mux);
    if (transitioned) {
        ESP_LOGW(TAG, "Capture fail-safe switched RX to RUN: %s", reason);
    }
}

/* -------------------------------------------------------------------------- */
/* Configuration, identity, and pairing persistence                           */
/* -------------------------------------------------------------------------- */

static uint32_t stable_id_from_mac(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    uint32_t hash = UINT32_C(2166136261);
    const uint8_t domain[] = {'R', 'X', ':'};
    for (size_t i = 0; i < sizeof(domain); ++i) {
        hash = (hash ^ domain[i]) * UINT32_C(16777619);
    }
    for (size_t i = 0; i < ESP_NOW_ETH_ALEN; ++i) {
        hash = (hash ^ mac[i]) * UINT32_C(16777619);
    }
    return hash == 0U ? UINT32_C(1) : hash;
}

static bool mac_is_unicast(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    static const uint8_t zero[ESP_NOW_ETH_ALEN] = {0};
    return memcmp(mac, zero, ESP_NOW_ETH_ALEN) != 0 && (mac[0] & 0x01U) == 0U;
}

static void load_app_config(void)
{
    memset(&g_config, 0, sizeof(g_config));
    g_config.link_id = WIEVAC_DEV_LINK_ID;
    g_config.rx_id = WIEVAC_DEV_RX_ID != 0U ? WIEVAC_DEV_RX_ID : stable_id_from_mac(g_rx_mac);
    (void)snprintf(g_config.wifi_ssid, sizeof(g_config.wifi_ssid), "%s", WIEVAC_DEV_WIFI_SSID);
    (void)snprintf(g_config.wifi_password, sizeof(g_config.wifi_password), "%s",
                   WIEVAC_DEV_WIFI_PASSWORD);

    nvs_handle_t handle;
    esp_err_t err = nvs_open("wievac_cfg", NVS_READONLY, &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGW(TAG, "NVS deployment config absent; using documented development defaults");
        return;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Cannot open NVS deployment config: %s", esp_err_to_name(err));
        return;
    }

    uint32_t value;
    err = nvs_get_u32(handle, "link_id", &value);
    if (err == ESP_OK && value != 0U) {
        g_config.link_id = value;
    } else if (err != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGE(TAG, "Invalid link_id in NVS: %s", esp_err_to_name(err));
    }
    err = nvs_get_u32(handle, "rx_id", &value);
    if (err == ESP_OK && value != 0U) {
        g_config.rx_id = value;
    } else if (err != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGE(TAG, "Invalid rx_id in NVS: %s", esp_err_to_name(err));
    }

    size_t string_length = sizeof(g_config.wifi_ssid);
    err = nvs_get_str(handle, "wifi_ssid", g_config.wifi_ssid, &string_length);
    if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGE(TAG, "Cannot read Wi-Fi SSID from NVS: %s", esp_err_to_name(err));
    }
    string_length = sizeof(g_config.wifi_password);
    err = nvs_get_str(handle, "wifi_pass", g_config.wifi_password, &string_length);
    if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGE(TAG, "Cannot read Wi-Fi password from NVS: %s", esp_err_to_name(err));
    }
    nvs_close(handle);
}

static pairing_state_t pairing_snapshot(void)
{
    pairing_state_t result;
    portENTER_CRITICAL(&g_pairing_mux);
    result = g_pairing;
    portEXIT_CRITICAL(&g_pairing_mux);
    return result;
}

static void set_pairing_state(const pairing_state_t *state)
{
    if (!state->valid) {
        capture_control_reset_to_run("pairing_unavailable");
    }
    portENTER_CRITICAL(&g_pairing_mux);
    g_pairing = *state;
    portEXIT_CRITICAL(&g_pairing_mux);
    (void)__atomic_add_fetch(&g_dsp_reset_generation, 1U, __ATOMIC_RELEASE);
}

static void load_pairing(void)
{
    pairing_state_t loaded = {0};
    nvs_handle_t handle;
    esp_err_t err = nvs_open("wievac_pair", NVS_READONLY, &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        set_pairing_state(&loaded);
        return;
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Cannot open pairing NVS: %s", esp_err_to_name(err));
        set_pairing_state(&loaded);
        return;
    }

    uint8_t valid = 0U;
    uint32_t stored_link_id = 0U;
    size_t mac_length = ESP_NOW_ETH_ALEN;
    esp_err_t valid_err = nvs_get_u8(handle, "valid", &valid);
    esp_err_t mac_err = nvs_get_blob(handle, "tx_mac", loaded.tx_mac, &mac_length);
    esp_err_t tx_err = nvs_get_u32(handle, "tx_id", &loaded.tx_id);
    esp_err_t link_err = nvs_get_u32(handle, "link_id", &stored_link_id);
    nvs_close(handle);

    loaded.valid = valid_err == ESP_OK && valid == 1U && mac_err == ESP_OK &&
                   mac_length == ESP_NOW_ETH_ALEN && tx_err == ESP_OK &&
                   link_err == ESP_OK && stored_link_id == g_config.link_id &&
                   loaded.tx_id != 0U && mac_is_unicast(loaded.tx_mac) &&
                   memcmp(loaded.tx_mac, g_rx_mac, ESP_NOW_ETH_ALEN) != 0;
    if (!loaded.valid) {
        if (valid_err != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGW(TAG, "Ignoring incomplete or deployment-mismatched pairing record");
        }
        memset(&loaded, 0, sizeof(loaded));
    }
    set_pairing_state(&loaded);
}

static esp_err_t save_pairing(const uint8_t tx_mac[ESP_NOW_ETH_ALEN], uint32_t tx_id)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open("wievac_pair", NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return err;
    }
    if ((err = nvs_set_blob(handle, "tx_mac", tx_mac, ESP_NOW_ETH_ALEN)) == ESP_OK &&
        (err = nvs_set_u32(handle, "tx_id", tx_id)) == ESP_OK &&
        (err = nvs_set_u32(handle, "link_id", g_config.link_id)) == ESP_OK &&
        (err = nvs_set_u8(handle, "valid", 1U)) == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}

static esp_err_t erase_pairing(void)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open("wievac_pair", NVS_READWRITE, &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;
    }
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_erase_all(handle);
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}

static bool reset_button_held(void)
{
    if (gpio_get_level(WIEVAC_PAIR_RESET_GPIO) != 0) {
        return false;
    }
    const uint32_t checks = WIEVAC_PAIR_RESET_HOLD_MS / 20U;
    for (uint32_t i = 0; i < checks; ++i) {
        vTaskDelay(pdMS_TO_TICKS(20));
        if (gpio_get_level(WIEVAC_PAIR_RESET_GPIO) != 0) {
            return false;
        }
    }
    return true;
}

static void pairing_reset_button_task(void *argument)
{
    (void)argument;
    for (;;) {
        if (reset_button_held()) {
            capture_control_reset_to_run("pairing_reset_requested");
            esp_err_t err = erase_pairing();
            if (err == ESP_OK) {
                ESP_LOGW(TAG, "Pairing erased by physical reset button; restarting receiver");
                vTaskDelay(pdMS_TO_TICKS(100));
                esp_restart();
            }
            ESP_LOGE(TAG, "Failed to erase pairing: %s", esp_err_to_name(err));
            while (gpio_get_level(WIEVAC_PAIR_RESET_GPIO) == 0) {
                vTaskDelay(pdMS_TO_TICKS(100));
            }
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

/* -------------------------------------------------------------------------- */
/* Measurement quality tracking                                               */
/* -------------------------------------------------------------------------- */

static void running_stats_add(running_stats_t *stats, double value)
{
    ++stats->count;
    const double delta = value - stats->mean;
    stats->mean += delta / (double)stats->count;
    stats->m2 += delta * (value - stats->mean);
}

static double running_stats_std(const running_stats_t *stats)
{
    if (stats->count == 0U) {
        return 0.0;
    }
    const double variance = stats->m2 / (double)stats->count;
    return sqrt(variance > 0.0 ? variance : 0.0);
}

static void measurement_tracker_reset(void)
{
    portENTER_CRITICAL(&g_measurement_mux);
    memset(&g_measurement_stats, 0, sizeof(g_measurement_stats));
    portEXIT_CRITICAL(&g_measurement_mux);
}

static void measurement_note(uint32_t sequence, uint64_t sender_timestamp_us,
                             uint64_t arrival_us)
{
    uint32_t lost = 0U;
    portENTER_CRITICAL(&g_measurement_mux);
    measurement_stats_t *stats = &g_measurement_stats;
    if (!stats->initialized || sender_timestamp_us < stats->last_sender_timestamp_us) {
        stats->initialized = true;
        stats->last_sequence = sequence;
        stats->last_sender_timestamp_us = sender_timestamp_us;
        stats->last_arrival_us = arrival_us;
        ++stats->received_window;
        portEXIT_CRITICAL(&g_measurement_mux);
        return;
    }

    const int32_t signed_delta = (int32_t)(sequence - stats->last_sequence);
    if (signed_delta > 0 && (uint32_t)signed_delta <= WIEVAC_MAX_SEQUENCE_GAP) {
        if (signed_delta > 1) {
            lost = (uint32_t)signed_delta - 1U;
            stats->lost_window += lost;
        }
        if (stats->last_arrival_us != 0U && arrival_us > stats->last_arrival_us) {
            running_stats_add(&stats->interarrival_ms,
                              (double)(arrival_us - stats->last_arrival_us) / 1000.0);
        }
        stats->last_sequence = sequence;
        stats->last_sender_timestamp_us = sender_timestamp_us;
        stats->last_arrival_us = arrival_us;
        ++stats->received_window;
    } else if (signed_delta > 0) {
        /* Treat a large jump as a stream discontinuity, not millions of losses. */
        stats->last_sequence = sequence;
        stats->last_sender_timestamp_us = sender_timestamp_us;
        stats->last_arrival_us = arrival_us;
        ++stats->received_window;
    }
    /* Duplicate and reordered packets are ignored for loss and jitter. */
    portEXIT_CRITICAL(&g_measurement_mux);

    if (lost > 0U) {
        (void)__atomic_add_fetch(&g_measurement_loss_count, lost, __ATOMIC_RELAXED);
    }
}

static void measurement_take_window(uint32_t *received, uint32_t *lost, float *jitter_ms)
{
    running_stats_t intervals;
    portENTER_CRITICAL(&g_measurement_mux);
    *received = g_measurement_stats.received_window;
    *lost = g_measurement_stats.lost_window;
    intervals = g_measurement_stats.interarrival_ms;
    g_measurement_stats.received_window = 0U;
    g_measurement_stats.lost_window = 0U;
    memset(&g_measurement_stats.interarrival_ms, 0,
           sizeof(g_measurement_stats.interarrival_ms));
    portEXIT_CRITICAL(&g_measurement_mux);
    *jitter_ms = (float)running_stats_std(&intervals);
}

/* -------------------------------------------------------------------------- */
/* UDP transport                                                              */
/* -------------------------------------------------------------------------- */

static bool enqueue_udp_packet(const uint8_t *packet, uint16_t length)
{
    if (length > V2_MAX_PACKET_SIZE) {
        return false;
    }
    udp_datagram_t datagram = {.len = length};
    memcpy(datagram.bytes, packet, length);
    if (xQueueSend(g_udp_queue, &datagram, 0) != pdTRUE) {
        atomic_increment_u32(&g_udp_queue_drop_count);
        return false;
    }
    return true;
}

static bool pi_liveness_available(void)
{
    if ((xEventGroupGetBits(g_network_events) & NETWORK_GOT_IP_BIT) == 0) {
        return false;
    }
    const uint64_t last_alive_us = pi_alive_load_us();
    const uint64_t now_us = (uint64_t)esp_timer_get_time();
    return last_alive_us != 0U && now_us >= last_alive_us &&
           now_us - last_alive_us <=
               (uint64_t)WIEVAC_PI_LIVENESS_TIMEOUT_MS * 1000U;
}

static bool capture_transport_available(void)
{
    if (!pairing_snapshot().valid || !pi_liveness_available()) {
        return false;
    }
    struct sockaddr_in address;
    portENTER_CRITICAL(&g_pi_address_mux);
    address = g_pi_address;
    portEXIT_CRITICAL(&g_pi_address_mux);
    return address.sin_family == AF_INET &&
           address.sin_port == htons(WIEVAC_PI_UDP_PORT) &&
           address.sin_addr.s_addr == inet_addr(WIEVAC_PI_GATEWAY_IP);
}

static void enforce_capture_fail_safe(void)
{
    if (!capture_transport_available()) {
        capture_control_reset_to_run("Pi liveness/network/pairing unavailable");
    }
}

static bool enqueue_capture_ack(uint8_t command, uint32_t command_sequence,
                                uint32_t actual_mode)
{
    const pairing_state_t pairing = pairing_snapshot();
    if (!pairing.valid) {
        return false;
    }
    const uint16_t flags = actual_mode == WIEVAC_MODE_CAPTURE
                               ? V2_FLAG_CAPTURE : V2_FLAG_RUN;
    uint8_t payload[V2_CAPTURE_ACK_PAYLOAD_SIZE];
    payload[0] = command;
    payload[1] = (uint8_t)actual_mode;
    write_u32_be(payload + 2, command_sequence);
    const v2_header_t header = {
        .message_type = V2_MSG_CAPTURE_ACK,
        .flags = flags,
        .payload_length = V2_CAPTURE_ACK_PAYLOAD_SIZE,
        .feature_schema = 0U,
        .link_id = g_config.link_id,
        .tx_id = pairing.tx_id,
        .rx_id = g_config.rx_id,
        .sequence = next_sequence(&g_capture_ack_sequence),
        .timestamp_us = (uint64_t)esp_timer_get_time(),
        .window_duration_ms = 0U,
        .sample_count = 0U,
        .invalid_csi = 0U,
        .wrong_source = 0U,
        .queue_drop = 0U,
        .packet_loss = 0U,
    };
    uint8_t packet[V2_HEADER_SIZE + V2_CAPTURE_ACK_PAYLOAD_SIZE];
    return protocol_encode(packet, sizeof(packet), &header, payload) &&
           enqueue_udp_packet(packet, sizeof(packet));
}

static void drain_pi_messages(int socket_fd)
{
    for (unsigned attempt = 0; attempt < 4U; ++attempt) {
        uint8_t packet[V2_MAX_PACKET_SIZE + 1U];
        struct sockaddr_in source = {0};
        socklen_t source_length = sizeof(source);
        const int received = recvfrom(socket_fd, packet, sizeof(packet), MSG_DONTWAIT,
                                      (struct sockaddr *)&source, &source_length);
        if (received < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK) {
                ESP_LOGW(TAG, "UDP Pi control receive failed: errno=%d", errno);
            }
            return;
        }

        struct sockaddr_in expected_source;
        portENTER_CRITICAL(&g_pi_address_mux);
        expected_source = g_pi_address;
        portEXIT_CRITICAL(&g_pi_address_mux);
        if (source_length < sizeof(source) || source.sin_family != AF_INET ||
            source.sin_addr.s_addr != expected_source.sin_addr.s_addr ||
            source.sin_port != expected_source.sin_port) {
            continue;
        }

        v2_header_t header;
        const uint8_t *payload = NULL;
        pairing_state_t pairing = pairing_snapshot();
        if (!pairing.valid ||
            !protocol_decode(packet, (size_t)received, &header, &payload) ||
            header.link_id != g_config.link_id || header.tx_id != pairing.tx_id ||
            header.rx_id != g_config.rx_id || header.window_duration_ms != 0U ||
            header.sample_count != 0U || header.invalid_csi != 0U ||
            header.wrong_source != 0U || header.queue_drop != 0U ||
            header.packet_loss != 0U) {
            continue;
        }
        if (header.message_type == V2_MSG_PI_ALIVE && header.flags == 0U &&
            header.payload_length == 0U && header.feature_schema == 0U) {
            const bool recent_sequence =
                g_feature_sent_count > 0U &&
                (header.sequence == g_last_feature_sent_sequence ||
                 (g_feature_sent_count > 1U &&
                  header.sequence == g_previous_feature_sent_sequence));
            if (recent_sequence) {
                pi_alive_store_us((uint64_t)esp_timer_get_time());
            }
            continue;
        }
        if (header.message_type != V2_MSG_CAPTURE_CONTROL || header.flags != 0U ||
            header.payload_length != V2_CAPTURE_CONTROL_PAYLOAD_SIZE ||
            header.feature_schema != 0U ||
            (payload[0] != V2_CAPTURE_COMMAND_START &&
             payload[0] != V2_CAPTURE_COMMAND_STOP)) {
            continue;
        }
        bool accepted = false;
        bool duplicate = false;
        uint32_t actual_mode = WIEVAC_MODE_RUN;
        portENTER_CRITICAL(&g_capture_control_mux);
        if (g_capture_control.initialized &&
            header.sequence == g_capture_control.last_sequence &&
            (uint32_t)payload[0] == g_capture_control.last_command &&
            header.timestamp_us == g_capture_control.last_sender_timestamp_us) {
            duplicate = true;
            actual_mode = g_capture_control.mode;
        } else {
            const bool new_pi_session =
                g_capture_control.initialized && header.sequence == 1U &&
                header.timestamp_us > g_capture_control.last_sender_timestamp_us;
            const bool normal_newer =
                !g_capture_control.initialized ||
                ((int32_t)(header.sequence - g_capture_control.last_sequence) > 0 &&
                 header.timestamp_us > g_capture_control.last_sender_timestamp_us);
            if (new_pi_session || normal_newer) {
                if (new_pi_session) {
                    g_capture_control.mode = WIEVAC_MODE_RUN;
                    g_capture_control.last_sequence = 0U;
                    g_capture_control.last_command = 0U;
                    g_capture_control.last_sender_timestamp_us = 0U;
                    g_capture_control.initialized = false;
                }
                g_capture_control.mode =
                    payload[0] == V2_CAPTURE_COMMAND_START
                        ? WIEVAC_MODE_CAPTURE : WIEVAC_MODE_RUN;
                g_capture_control.last_sequence = header.sequence;
                g_capture_control.last_command = payload[0];
                g_capture_control.last_sender_timestamp_us = header.timestamp_us;
                g_capture_control.initialized = true;
                actual_mode = g_capture_control.mode;
                accepted = true;
            }
        }
        portEXIT_CRITICAL(&g_capture_control_mux);
        if (!accepted && !duplicate) {
            continue;
        }
        pi_alive_store_us((uint64_t)esp_timer_get_time());
        if (!enqueue_capture_ack(payload[0], header.sequence, actual_mode)) {
            ESP_LOGW(TAG, "%s capture command ACK enqueue failed",
                     duplicate ? "Duplicate" : "Accepted");
        }
    }
}

static void udp_sender_task(void *argument)
{
    (void)argument;
    int socket_fd = -1;
    udp_datagram_t datagram;

    for (;;) {
        enforce_capture_fail_safe();
        if (socket_fd >= 0) {
            drain_pi_messages(socket_fd);
        }
        if (xQueueReceive(g_udp_queue, &datagram, pdMS_TO_TICKS(500)) != pdTRUE) {
            if ((xEventGroupGetBits(g_network_events) & NETWORK_GOT_IP_BIT) == 0 &&
                socket_fd >= 0) {
                close(socket_fd);
                socket_fd = -1;
                pi_alive_store_us(0U);
            }
            continue;
        }

        if ((xEventGroupGetBits(g_network_events) & NETWORK_GOT_IP_BIT) == 0) {
            atomic_increment_u32(&g_udp_send_error_count);
            continue;
        }
        const capture_control_state_t capture_state = capture_control_snapshot();
        if (datagram.len >= V2_HEADER_SIZE &&
            datagram.bytes[5] == V2_MSG_CSI_SNAPSHOT &&
            capture_state.mode != WIEVAC_MODE_CAPTURE) {
            continue;
        }
        if (datagram.len == V2_HEADER_SIZE + V2_CAPTURE_ACK_PAYLOAD_SIZE &&
            datagram.bytes[5] == V2_MSG_CAPTURE_ACK &&
            (!capture_state.initialized ||
             read_u32_be(datagram.bytes + V2_HEADER_SIZE + 2U) !=
                 capture_state.last_sequence ||
             datagram.bytes[V2_HEADER_SIZE] !=
                 (uint8_t)capture_state.last_command)) {
            continue;
        }
        if (socket_fd < 0) {
            socket_fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
            if (socket_fd < 0) {
                atomic_increment_u32(&g_udp_send_error_count);
                ESP_LOGE(TAG, "UDP socket creation failed: errno=%d", errno);
                continue;
            }
        }

        struct sockaddr_in destination;
        portENTER_CRITICAL(&g_pi_address_mux);
        destination = g_pi_address;
        portEXIT_CRITICAL(&g_pi_address_mux);
        if (destination.sin_addr.s_addr == 0U) {
            atomic_increment_u32(&g_udp_send_error_count);
            continue;
        }

        const int sent = sendto(socket_fd, datagram.bytes, datagram.len, 0,
                                (struct sockaddr *)&destination, sizeof(destination));
        if (sent != (int)datagram.len) {
            atomic_increment_u32(&g_udp_send_error_count);
            ESP_LOGE(TAG, "UDP send failed: sent=%d expected=%u errno=%d",
                     sent, datagram.len, errno);
            close(socket_fd);
            socket_fd = -1;
            pi_alive_store_us(0U);
        } else if (datagram.bytes[5] == V2_MSG_FEATURE_RUN &&
                   datagram.len >= V2_HEADER_SIZE) {
            g_previous_feature_sent_sequence = g_last_feature_sent_sequence;
            g_last_feature_sent_sequence = read_u32_be(datagram.bytes + 28);
            if (g_feature_sent_count < 2U) {
                ++g_feature_sent_count;
            }
        }
    }
}

/* -------------------------------------------------------------------------- */
/* ESP-NOW pairing and measurement receive path                               */
/* -------------------------------------------------------------------------- */

static esp_err_t ensure_espnow_peer(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    if (esp_now_is_peer_exist(mac)) {
        return ESP_OK;
    }
    esp_now_peer_info_t peer = {0};
    memcpy(peer.peer_addr, mac, ESP_NOW_ETH_ALEN);
    peer.channel = 0U; /* Follow the STA/AP channel negotiated by Wi-Fi. */
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;
    esp_err_t err = esp_now_add_peer(&peer);
    if (err != ESP_OK) {
        return err;
    }

    esp_now_rate_config_t rate_config = {
        .phymode = WIEVAC_ESPNOW_PHYMODE,
        .rate = WIEVAC_ESPNOW_RATE,
        .ersu = false,
        .dcm = false,
    };
    err = esp_now_set_peer_rate_config(mac, &rate_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Cannot configure ESP-NOW peer rate: %s", esp_err_to_name(err));
    }
    return err;
}

static void espnow_send_callback(const uint8_t *mac_addr, esp_now_send_status_t status)
{
    (void)mac_addr;
    if (status == ESP_NOW_SEND_SUCCESS) {
        atomic_increment_u32(&g_espnow_send_success_count);
    } else {
        atomic_increment_u32(&g_espnow_send_failure_count);
    }
}

static void espnow_receive_callback(const esp_now_recv_info_t *receive_info,
                                    const uint8_t *data, int length)
{
    if (receive_info == NULL || receive_info->src_addr == NULL || data == NULL ||
        length <= 0 || length > (int)WIEVAC_ESPNOW_FRAME_MAX) {
        atomic_increment_u32(&g_espnow_queue_drop_count);
        return;
    }
    espnow_rx_event_t event = {
        .arrival_us = (uint64_t)esp_timer_get_time(),
        .len = (uint16_t)length,
    };
    memcpy(event.source_mac, receive_info->src_addr, ESP_NOW_ETH_ALEN);
    memcpy(event.bytes, data, (size_t)length);
    if (xQueueSend(g_espnow_queue, &event, 0) != pdTRUE) {
        atomic_increment_u32(&g_espnow_queue_drop_count);
    }
}

static bool send_pair_packet(uint8_t message_type,
                             const uint8_t destination[ESP_NOW_ETH_ALEN],
                             uint32_t tx_id, const uint8_t *payload,
                             uint16_t payload_length, bool paired_flag)
{
    esp_err_t err = ensure_espnow_peer(destination);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Cannot add pairing peer: %s", esp_err_to_name(err));
        return false;
    }

    uint8_t packet[V2_HEADER_SIZE + V2_PAIR_OFFER_PAYLOAD_SIZE];
    v2_header_t header = {
        .message_type = message_type,
        .flags = paired_flag ? V2_FLAG_PAIRED : 0U,
        .payload_length = payload_length,
        .feature_schema = 0U,
        .link_id = g_config.link_id,
        .tx_id = tx_id,
        .rx_id = g_config.rx_id,
        .sequence = next_sequence(&g_pair_sequence),
        .timestamp_us = (uint64_t)esp_timer_get_time(),
    };
    if (!protocol_encode(packet, sizeof(packet), &header, payload)) {
        return false;
    }
    err = esp_now_send(destination, packet, V2_HEADER_SIZE + payload_length);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ESP-NOW pairing send failed: %s", esp_err_to_name(err));
        return false;
    }
    return true;
}

static int find_candidate(pair_candidate_t *candidates, size_t count,
                          const uint8_t mac[ESP_NOW_ETH_ALEN], uint32_t tx_id)
{
    for (size_t i = 0; i < count; ++i) {
        if (candidates[i].used && candidates[i].tx_id == tx_id &&
            memcmp(candidates[i].mac, mac, ESP_NOW_ETH_ALEN) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static void clear_candidates(pair_candidate_t *candidates, size_t count)
{
    /* Candidate discovery does not add peers. The selected peer is retained so
     * the asynchronous OFFER can finish and the following CONFIRM can arrive. */
    memset(candidates, 0, count * sizeof(candidates[0]));
}

static void remove_peer_if_temporary(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    const pairing_state_t paired = pairing_snapshot();
    if (paired.valid && memcmp(paired.tx_mac, mac, ESP_NOW_ETH_ALEN) == 0) {
        return;
    }
    if (esp_now_is_peer_exist(mac)) {
        const esp_err_t err = esp_now_del_peer(mac);
        if (err != ESP_OK && err != ESP_ERR_ESPNOW_NOT_FOUND) {
            ESP_LOGW(TAG, "Cannot remove temporary pairing peer " MACSTR ": %s",
                     MAC2STR(mac), esp_err_to_name(err));
        }
    }
}

static void abort_offer_session(pair_offer_session_t *session)
{
    if (session->active) {
        remove_peer_if_temporary(session->mac);
    }
    memset(session, 0, sizeof(*session));
}

static bool begin_offer(pair_offer_session_t *session, const pair_candidate_t *candidate)
{
    uint32_t offer_nonce = esp_random();
    if (offer_nonce == 0U) {
        offer_nonce = 1U;
    }
    uint8_t payload[V2_PAIR_OFFER_PAYLOAD_SIZE];
    write_u32_be(payload, candidate->hello_nonce);
    write_u32_be(payload + 4, offer_nonce);
    write_u32_be(payload + 8, V2_LOCAL_CAPS);
    if (!send_pair_packet(V2_MSG_PAIR_OFFER, candidate->mac, candidate->tx_id,
                          payload, sizeof(payload), false)) {
        remove_peer_if_temporary(candidate->mac);
        return false;
    }
    memset(session, 0, sizeof(*session));
    session->active = true;
    memcpy(session->mac, candidate->mac, ESP_NOW_ETH_ALEN);
    session->tx_id = candidate->tx_id;
    session->hello_nonce = candidate->hello_nonce;
    session->offer_nonce = offer_nonce;
    session->expires_us = (uint64_t)esp_timer_get_time() +
                          (uint64_t)WIEVAC_PAIR_SESSION_TIMEOUT_MS * 1000U;
    return true;
}

static bool measurement_header_valid(const v2_header_t *header,
                                     const pairing_state_t *pairing,
                                     const uint8_t source_mac[ESP_NOW_ETH_ALEN])
{
    return pairing->valid && header->message_type == V2_MSG_MEASUREMENT &&
           (header->flags & (V2_FLAG_PAIRED | V2_FLAG_RUN)) ==
               (V2_FLAG_PAIRED | V2_FLAG_RUN) &&
           header->feature_schema == 0U &&
           header->payload_length == V2_MEASUREMENT_PAYLOAD_SIZE &&
           header->link_id == g_config.link_id && header->tx_id == pairing->tx_id &&
           header->rx_id == g_config.rx_id &&
           memcmp(source_mac, pairing->tx_mac, ESP_NOW_ETH_ALEN) == 0;
}

static void espnow_receiver_task(void *argument)
{
    (void)argument;
    pair_candidate_t candidates[4] = {0};
    pair_offer_session_t session = {0};
    uint64_t discovery_deadline_us = 0U;
    espnow_rx_event_t event;

    for (;;) {
        const bool received = xQueueReceive(g_espnow_queue, &event,
                                            pdMS_TO_TICKS(100)) == pdTRUE;
        const uint64_t now_us = (uint64_t)esp_timer_get_time();
        if (session.active && now_us >= session.expires_us) {
            if (!session.completed) {
                ESP_LOGW(TAG, "Pairing confirmation timed out; returning to discovery");
            }
            abort_offer_session(&session);
            clear_candidates(candidates, 4U);
            discovery_deadline_us = 0U;
        }

        size_t candidate_count = 0U;
        for (size_t i = 0; i < 4U; ++i) {
            candidate_count += candidates[i].used ? 1U : 0U;
        }
        if (!session.active && candidate_count > 0U &&
            now_us >= discovery_deadline_us) {
            if (candidate_count == 1U) {
                for (size_t i = 0; i < 4U; ++i) {
                    if (candidates[i].used) {
                        if (!begin_offer(&session, &candidates[i])) {
                            ESP_LOGW(TAG, "Pair offer could not be sent; discovery will retry");
                        }
                        break;
                    }
                }
            } else {
                ESP_LOGW(TAG, "Pairing ambiguous: %u compatible TX devices detected; none selected",
                         (unsigned)candidate_count);
            }
            clear_candidates(candidates, 4U);
            discovery_deadline_us = 0U;
        }

        if (!received) {
            continue;
        }

        v2_header_t header;
        const uint8_t *payload;
        if (!protocol_decode(event.bytes, event.len, &header, &payload)) {
            continue;
        }

        pairing_state_t paired = pairing_snapshot();
        if (header.message_type == V2_MSG_MEASUREMENT) {
            if (!measurement_header_valid(&header, &paired, event.source_mac)) {
                continue;
            }
            const uint32_t tx_firmware = read_u32_be(payload);
            const uint8_t channel = payload[12];
            const uint8_t bandwidth_mhz = payload[13];
            const uint8_t mcs = payload[14];
            if (channel != WIEVAC_WIFI_CHANNEL || bandwidth_mhz != 20U || mcs != 0U) {
                /* The CSI layout contract is HT20/MCS0 on the configured channel. */
                continue;
            }
            __atomic_store_n(&g_tx_firmware_version, tx_firmware, __ATOMIC_RELAXED);
            measurement_note(header.sequence, header.timestamp_us, event.arrival_us);
            continue;
        }

        if (header.message_type == V2_MSG_PAIR_HELLO) {
            if (header.feature_schema != 0U ||
                header.payload_length != V2_PAIR_HELLO_PAYLOAD_SIZE ||
                header.link_id != g_config.link_id || header.tx_id == 0U ||
                (header.rx_id != 0U && header.rx_id != g_config.rx_id) ||
                !mac_is_unicast(event.source_mac)) {
                continue;
            }
            const uint32_t hello_nonce = read_u32_be(payload);
            const uint32_t capabilities = read_u32_be(payload + 4);
            if (hello_nonce == 0U || (capabilities & V2_REQUIRED_PAIR_CAPS) !=
                                     V2_REQUIRED_PAIR_CAPS) {
                continue;
            }

            if (paired.valid) {
                if (paired.tx_id == header.tx_id &&
                    memcmp(paired.tx_mac, event.source_mac, ESP_NOW_ETH_ALEN) == 0) {
                    if (!session.active) {
                        pair_candidate_t known = {
                            .used = true,
                            .tx_id = header.tx_id,
                            .hello_nonce = hello_nonce,
                            .capabilities = capabilities,
                        };
                        memcpy(known.mac, event.source_mac, ESP_NOW_ETH_ALEN);
                        (void)begin_offer(&session, &known);
                    }
                }
                continue;
            }

            if (session.active) {
                if (session.tx_id != header.tx_id ||
                    memcmp(session.mac, event.source_mac, ESP_NOW_ETH_ALEN) != 0) {
                    ESP_LOGW(TAG, "Additional TX appeared during pairing; aborting automatic selection");
                    abort_offer_session(&session);
                    clear_candidates(candidates, 4U);
                    discovery_deadline_us = 0U;
                }
                continue;
            }

            int index = find_candidate(candidates, 4U, event.source_mac, header.tx_id);
            if (index < 0) {
                for (size_t i = 0; i < 4U; ++i) {
                    if (!candidates[i].used) {
                        index = (int)i;
                        candidates[i].used = true;
                        memcpy(candidates[i].mac, event.source_mac, ESP_NOW_ETH_ALEN);
                        candidates[i].tx_id = header.tx_id;
                        break;
                    }
                }
            }
            if (index >= 0) {
                candidates[index].hello_nonce = hello_nonce;
                candidates[index].capabilities = capabilities;
                if (discovery_deadline_us == 0U) {
                    discovery_deadline_us = now_us +
                                            (uint64_t)WIEVAC_PAIR_DISCOVERY_MS * 1000U;
                }
            }
            continue;
        }

        if (header.message_type == V2_MSG_PAIR_CONFIRM && session.active &&
            header.feature_schema == 0U &&
            header.payload_length == V2_PAIR_CONFIRM_PAYLOAD_SIZE &&
            header.link_id == g_config.link_id && header.tx_id == session.tx_id &&
            header.rx_id == g_config.rx_id &&
            memcmp(event.source_mac, session.mac, ESP_NOW_ETH_ALEN) == 0 &&
            read_u32_be(payload) == session.hello_nonce &&
            read_u32_be(payload + 4) == session.offer_nonce) {
            if (!session.completed) {
                esp_err_t err = save_pairing(session.mac, session.tx_id);
                if (err != ESP_OK) {
                    ESP_LOGE(TAG, "Pairing not committed to NVS: %s", esp_err_to_name(err));
                    continue;
                }

                pairing_state_t new_pairing = {.valid = true, .tx_id = session.tx_id};
                memcpy(new_pairing.tx_mac, session.mac, ESP_NOW_ETH_ALEN);
                set_pairing_state(&new_pairing);
                measurement_tracker_reset();
                __atomic_store_n(&g_tx_firmware_version, 0U, __ATOMIC_RELAXED);
                ESP_LOGI(TAG, "Paired link=%" PRIu32 " tx_id=%" PRIu32 " tx_mac=" MACSTR,
                         g_config.link_id, session.tx_id, MAC2STR(session.mac));
            }

            uint8_t ack[V2_PAIR_CONFIRM_PAYLOAD_SIZE];
            write_u32_be(ack, session.hello_nonce);
            write_u32_be(ack + 4, session.offer_nonce);
            (void)send_pair_packet(V2_MSG_PAIR_ACK, session.mac, session.tx_id,
                                   ack, sizeof(ack), true);
            session.completed = true;
            session.expires_us = now_us + 5000000U; /* ACK retry grace period. */
            clear_candidates(candidates, 4U);
            discovery_deadline_us = 0U;
        }
    }
}

/* -------------------------------------------------------------------------- */
/* CSI callback and DSP                                                       */
/* -------------------------------------------------------------------------- */

static bool is_paired_source(const uint8_t source[ESP_NOW_ETH_ALEN])
{
    bool result;
    portENTER_CRITICAL(&g_pairing_mux);
    result = g_pairing.valid &&
             memcmp(g_pairing.tx_mac, source, ESP_NOW_ETH_ALEN) == 0;
    portEXIT_CRITICAL(&g_pairing_mux);
    return result;
}

static void wifi_csi_receive_callback(void *context, wifi_csi_info_t *info)
{
    (void)context;
    if (info == NULL) {
        atomic_increment_u32(&g_invalid_csi_count);
        return;
    }
    if (!pairing_snapshot().valid) {
        return;
    }
    if (!is_paired_source(info->mac)) {
        atomic_increment_u32(&g_wrong_source_count);
        return;
    }
    if (info->buf == NULL || info->len == 0U ||
        info->len > WIEVAC_HTLTF_BYTES) {
        atomic_increment_u32(&g_invalid_csi_count);
        return;
    }

    csi_record_t record = {
        .arrival_us = (uint64_t)esp_timer_get_time(),
        .len = info->len,
        .first_word_invalid = info->first_word_invalid,
        .rx_ctrl = info->rx_ctrl,
    };
    memcpy(record.source_mac, info->mac, ESP_NOW_ETH_ALEN);
    memcpy(record.csi, info->buf, info->len);
    if (record.first_word_invalid) {
        atomic_increment_u32(&g_first_word_invalid_count);
    }
    if (xQueueSend(g_csi_queue, &record, 0) != pdTRUE) {
        atomic_increment_u32(&g_csi_queue_drop_count);
    }
}

static bool is_pilot_subcarrier(int k)
{
    return k == -21 || k == -7 || k == 7 || k == 21;
}

static float median_five(const float values[WIEVAC_MEDIAN_WINDOW])
{
    float copy[WIEVAC_MEDIAN_WINDOW];
    memcpy(copy, values, sizeof(copy));
    for (size_t i = 1; i < WIEVAC_MEDIAN_WINDOW; ++i) {
        const float value = copy[i];
        size_t j = i;
        while (j > 0U && copy[j - 1U] > value) {
            copy[j] = copy[j - 1U];
            --j;
        }
        copy[j] = value;
    }
    return copy[WIEVAC_MEDIAN_WINDOW / 2U];
}

static float filter_subcarrier(subcarrier_filter_t *filter, float amplitude,
                               float *slow_value)
{
    if (!filter->initialized) {
        for (size_t i = 0; i < WIEVAC_MEDIAN_WINDOW; ++i) {
            filter->history[i] = amplitude;
        }
        filter->ema_fast = amplitude;
        filter->ema_slow = amplitude;
        filter->initialized = true;
    } else {
        filter->history[filter->history_index] = amplitude;
        filter->history_index = (uint8_t)((filter->history_index + 1U) %
                                          WIEVAC_MEDIAN_WINDOW);
        const float median = median_five(filter->history);
        filter->ema_fast = WIEVAC_EMA_FAST_ALPHA * median +
                           (1.0f - WIEVAC_EMA_FAST_ALPHA) * filter->ema_fast;
        const float fluctuation = fabsf(median - filter->ema_fast) /
                                  (fabsf(filter->ema_fast) + 1.0f);
        const float alpha_slow = WIEVAC_EMA_SLOW_BASE_ALPHA /
                                 (1.0f + fluctuation * fluctuation *
                                           WIEVAC_EMA_SLOW_FLUCTUATION_GAIN);
        filter->ema_slow = alpha_slow * median +
                           (1.0f - alpha_slow) * filter->ema_slow;
    }
    *slow_value = filter->ema_slow;
    return fabsf(filter->ema_fast - filter->ema_slow);
}

static bool csi_layout_valid(const csi_record_t *record)
{
    return record->len == WIEVAC_HTLTF_BYTES &&
           record->rx_ctrl.sig_mode == WIEVAC_SIG_MODE_HT &&
           record->rx_ctrl.cwb == 0U &&
           record->rx_ctrl.secondary_channel == WIFI_SECOND_CHAN_NONE &&
           record->rx_ctrl.rx_state == 0U;
}

static bool enqueue_snapshot(const csi_record_t *record, uint16_t common_flags,
                             uint8_t agc_gain, int8_t fft_gain)
{
    const uint16_t skipped_bytes = record->first_word_invalid ? 4U : 0U;
    if (record->len < skipped_bytes || ((record->len - skipped_bytes) & 1U) != 0U) {
        return false;
    }
    const uint16_t original_complex = record->len / 2U;
    const uint16_t available_complex = (record->len - skipped_bytes) / 2U;
    const uint16_t captured_complex = available_complex > WIEVAC_CAPTURE_MAX_COMPLEX
                                          ? WIEVAC_CAPTURE_MAX_COMPLEX
                                          : available_complex;
    uint8_t payload[V2_MAX_SNAPSHOT_PAYLOAD];
    memset(payload, 0, sizeof(payload));
    write_u32_be(payload, WIEVAC_RX_FIRMWARE_VERSION);
    payload[4] = V2_LTF_HTLTF;
    payload[5] = V2_SAMPLE_FORMAT_INT8_IMAG_REAL;
    payload[6] = WIEVAC_CAPTURE_SAMPLE_DECIMATION;
    if (captured_complex < available_complex) {
        payload[7] |= V2_CAPTURE_FLAG_TRUNCATED;
    }
    if (skipped_bytes != 0U) {
        payload[7] |= V2_CAPTURE_FLAG_FIRST_WORD_SKIPPED;
    }
    write_u16_be(payload + 8, original_complex);
    write_u16_be(payload + 10, captured_complex);
    const int16_t first_subcarrier = skipped_bytes == 0U ? 0 : 2;
    write_u16_be(payload + 12, (uint16_t)first_subcarrier);
    write_u16_be(payload + 14, 0U);
    memcpy(payload + 16, record->source_mac, ESP_NOW_ETH_ALEN);
    payload[22] = record->rx_ctrl.channel;
    payload[23] = record->rx_ctrl.cwb == 0U ? 20U : 40U;
    payload[24] = (uint8_t)record->rx_ctrl.rssi;
    payload[25] = (uint8_t)record->rx_ctrl.noise_floor;
    payload[26] = agc_gain;
    payload[27] = (uint8_t)fft_gain;
    payload[28] = record->rx_ctrl.rx_state;
    payload[29] = record->rx_ctrl.sig_mode;
    payload[30] = record->rx_ctrl.secondary_channel;
    payload[31] = WIEVAC_CAPTURE_PACKET_DECIMATION;
    memcpy(payload + V2_SNAPSHOT_META_SIZE, record->csi + skipped_bytes,
           (size_t)captured_complex * 2U);

    pairing_state_t pairing = pairing_snapshot();
    const uint16_t payload_length = V2_SNAPSHOT_META_SIZE + captured_complex * 2U;
    v2_header_t header = {
        .message_type = V2_MSG_CSI_SNAPSHOT,
        .flags = (uint16_t)(common_flags | V2_FLAG_CAPTURE),
        .payload_length = payload_length,
        .feature_schema = V2_FEATURE_SCHEMA,
        .link_id = g_config.link_id,
        .tx_id = pairing.tx_id,
        .rx_id = g_config.rx_id,
        .sequence = next_sequence(&g_snapshot_sequence),
        .timestamp_us = record->arrival_us,
        .window_duration_ms = 0U,
        .sample_count = 1U,
        .invalid_csi = atomic_load_u32(&g_invalid_csi_count),
        .wrong_source = atomic_load_u32(&g_wrong_source_count),
        .queue_drop = atomic_load_u32(&g_csi_queue_drop_count),
        .packet_loss = atomic_load_u32(&g_measurement_loss_count),
    };
    uint8_t packet[V2_MAX_PACKET_SIZE];
    if (!protocol_encode(packet, sizeof(packet), &header, payload)) {
        return false;
    }
    return enqueue_udp_packet(packet, V2_HEADER_SIZE + payload_length);
}

static bool process_csi_record(dsp_state_t *state, const csi_record_t *record)
{
    if (!csi_layout_valid(record)) {
        atomic_increment_u32(&g_invalid_csi_count);
        return false;
    }

    uint8_t agc_gain = 0U;
    int8_t fft_gain = 0;
    float gain_compensation = 1.0f;
    bool gain_compensation_ready = false;
    esp_csi_gain_ctrl_get_rx_gain(&record->rx_ctrl, &agc_gain, &fft_gain);
    ++state->gain_metadata_count;

    if (state->gain_samples_recorded < WIEVAC_GAIN_BASELINE_SAMPLES) {
        esp_err_t err = esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
        if (err == ESP_OK) {
            ++state->gain_samples_recorded;
        } else {
            ESP_LOGW(TAG, "Gain baseline sample rejected: %s", esp_err_to_name(err));
        }
    }
    if (state->gain_samples_recorded >= WIEVAC_GAIN_BASELINE_SAMPLES &&
        !state->gain_baseline_requested) {
        uint8_t baseline_agc;
        int8_t baseline_fft;
        esp_err_t err = esp_csi_gain_ctrl_get_rx_gain_baseline(&baseline_agc,
                                                               &baseline_fft);
        if (err == ESP_OK) {
            state->gain_baseline_requested = true;
            ESP_LOGI(TAG, "Gain compensation baseline ready: agc=%u fft=%d",
                     baseline_agc, baseline_fft);
        } else if (err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "Gain baseline unavailable: %s", esp_err_to_name(err));
        }
    }
    if (esp_csi_gain_ctrl_get_gain_compensation(&gain_compensation,
                                                 agc_gain, fft_gain) == ESP_OK &&
        isfinite(gain_compensation) && gain_compensation > 0.0f) {
        gain_compensation_ready = true;
        ++state->gain_compensation_ready_count;
    } else {
        gain_compensation = 1.0f;
    }

    float delta_by_slot[57] = {0};
    bool present_by_slot[57] = {0};
    float sum_absolute_delta = 0.0f;
    float sum_slow = 0.0f;
    uint32_t valid_subcarriers = 0U;

    /*
     * HT20 buffer order on ESP32-S3 is k=0..31 followed by k=-32..-1.
     * Each pair is [imaginary, real]. Process in physical frequency order and
     * exclude guard/DC/pilot tones. If first_word_invalid is set, exactly the
     * first four driver bytes (two complex values) are skipped.
     */
    for (int k = -28; k <= 28; ++k) {
        if (k == 0 || is_pilot_subcarrier(k)) {
            continue;
        }
        const uint16_t complex_index = (uint16_t)(k >= 0 ? k : k + 64);
        const uint16_t byte_index = complex_index * 2U;
        if (record->first_word_invalid && byte_index < 4U) {
            continue;
        }
        if (byte_index + 1U >= record->len) {
            atomic_increment_u32(&g_invalid_csi_count);
            return false;
        }
        const int8_t imaginary = record->csi[byte_index];
        const int8_t real = record->csi[byte_index + 1U];
        const float amplitude = hypotf((float)real, (float)imaginary) *
                                gain_compensation;
        const size_t slot = (size_t)(k + 28);
        float slow;
        const float absolute_delta = filter_subcarrier(&state->subcarrier[slot],
                                                        amplitude, &slow);
        delta_by_slot[slot] = absolute_delta;
        present_by_slot[slot] = true;
        sum_absolute_delta += absolute_delta;
        sum_slow += slow;
        ++valid_subcarriers;
    }

    if (valid_subcarriers == 0U || !isfinite(sum_absolute_delta) || !isfinite(sum_slow)) {
        atomic_increment_u32(&g_invalid_csi_count);
        return false;
    }

    const float delta_amp = sum_absolute_delta / (sum_slow + 1.0f);
    const float mean_slow = sum_slow / (float)valid_subcarriers;
    float roughness_sum = 0.0f;
    uint32_t roughness_pairs = 0U;
    int previous_k = -100;
    float previous_normalized_delta = 0.0f;
    for (int k = -28; k <= 28; ++k) {
        const size_t slot = (size_t)(k + 28);
        if (!present_by_slot[slot]) {
            continue;
        }
        const float normalized_delta = delta_by_slot[slot] / (mean_slow + 1.0f);
        if (k == previous_k + 1) {
            roughness_sum += fabsf(normalized_delta - previous_normalized_delta);
            ++roughness_pairs;
        }
        previous_k = k;
        previous_normalized_delta = normalized_delta;
    }
    const float spectral_roughness = roughness_pairs > 0U
                                         ? roughness_sum / (float)roughness_pairs
                                         : 0.0f;
    if (!isfinite(delta_amp) || !isfinite(spectral_roughness)) {
        atomic_increment_u32(&g_invalid_csi_count);
        return false;
    }

    running_stats_add(&state->delta, delta_amp);
    running_stats_add(&state->roughness, spectral_roughness);
    running_stats_add(&state->rssi, record->rx_ctrl.rssi);
    running_stats_add(&state->noise_floor, record->rx_ctrl.noise_floor);
    running_stats_add(&state->agc, agc_gain);
    running_stats_add(&state->fft, fft_gain);
    running_stats_add(&state->valid_subcarriers, valid_subcarriers);
    ++state->sample_count;

    enforce_capture_fail_safe();
    if (capture_control_snapshot().mode == WIEVAC_MODE_CAPTURE) {
        ++state->capture_counter;
        if ((state->capture_counter % WIEVAC_CAPTURE_PACKET_DECIMATION) == 0U) {
            uint16_t flags = V2_FLAG_PAIRED | V2_FLAG_CAPTURE;
            if (record->first_word_invalid) {
                flags |= V2_FLAG_FIRST_WORD_INVALID_SEEN;
            }
            if (state->gain_metadata_count > 0U) {
                flags |= V2_FLAG_GAIN_METADATA_VALID;
            }
            if (!pi_liveness_available()) {
                flags |= V2_FLAG_SYSTEM_UNAVAILABLE;
            }
            (void)enqueue_snapshot(record, flags, agc_gain, fft_gain);
        }
    }
    (void)gain_compensation_ready;
    return true;
}

static void dsp_window_counters_begin(dsp_state_t *state)
{
    state->start_invalid_csi = atomic_load_u32(&g_invalid_csi_count);
    state->start_wrong_source = atomic_load_u32(&g_wrong_source_count);
    state->start_queue_drop = atomic_load_u32(&g_csi_queue_drop_count);
    state->start_packet_loss = atomic_load_u32(&g_measurement_loss_count);
    state->start_first_word_invalid = atomic_load_u32(&g_first_word_invalid_count);
    state->start_udp_error = atomic_load_u32(&g_udp_send_error_count);
}

static void dsp_window_reset(dsp_state_t *state)
{
    memset(&state->delta, 0, sizeof(state->delta));
    memset(&state->roughness, 0, sizeof(state->roughness));
    memset(&state->rssi, 0, sizeof(state->rssi));
    memset(&state->noise_floor, 0, sizeof(state->noise_floor));
    memset(&state->agc, 0, sizeof(state->agc));
    memset(&state->fft, 0, sizeof(state->fft));
    memset(&state->valid_subcarriers, 0, sizeof(state->valid_subcarriers));
    state->sample_count = 0U;
    state->gain_metadata_count = 0U;
    state->gain_compensation_ready_count = 0U;
    dsp_window_counters_begin(state);
}

static void dsp_full_reset(dsp_state_t *state)
{
    memset(state, 0, sizeof(*state));
    /* This resets only library/software gain-baseline statistics, not RF AGC. */
    esp_csi_gain_ctrl_reset_rx_gain_baseline();
    dsp_window_counters_begin(state);
}

static void emit_feature_window(dsp_state_t *state, uint64_t start_us, uint64_t end_us)
{
    pairing_state_t pairing = pairing_snapshot();
    uint32_t measurement_received;
    uint32_t measurement_lost;
    float jitter_ms;
    measurement_take_window(&measurement_received, &measurement_lost, &jitter_ms);
    if (!pairing.valid) {
        return;
    }

    const uint64_t duration_us = end_us > start_us ? end_us - start_us : 0U;
    const uint32_t duration_ms = (uint32_t)((duration_us + 500U) / 1000U);
    const float duration_seconds = duration_us > 0U
                                       ? (float)duration_us / 1000000.0f
                                       : 0.0f;
    const float packet_rate_hz = duration_seconds > 0.0f
                                     ? (float)state->sample_count / duration_seconds
                                     : 0.0f;
    const uint32_t expected_measurements = measurement_received + measurement_lost;
    const float packet_loss_ratio = expected_measurements > 0U
                                        ? (float)measurement_lost /
                                              (float)expected_measurements
                                        : 0.0f;

    uint8_t payload[V2_FEATURE_PAYLOAD_SIZE];
    write_f32_be(payload + 0, (float)state->delta.mean);
    write_f32_be(payload + 4, (float)running_stats_std(&state->delta));
    write_f32_be(payload + 8, (float)state->roughness.mean);
    write_f32_be(payload + 12, (float)state->rssi.mean);
    write_f32_be(payload + 16, (float)running_stats_std(&state->rssi));
    write_f32_be(payload + 20, (float)state->noise_floor.mean);
    write_f32_be(payload + 24, (float)state->agc.mean);
    write_f32_be(payload + 28, (float)state->fft.mean);
    write_f32_be(payload + 32, packet_rate_hz);
    write_f32_be(payload + 36, packet_loss_ratio);
    write_f32_be(payload + 40, jitter_ms);
    write_f32_be(payload + 44, (float)state->valid_subcarriers.mean);
    write_u32_be(payload + 48, measurement_received);
    write_u32_be(payload + 52, atomic_load_u32(&g_first_word_invalid_count));
    write_u32_be(payload + 56, atomic_load_u32(&g_tx_firmware_version));
    write_u32_be(payload + 60, WIEVAC_RX_FIRMWARE_VERSION);

    uint16_t flags = V2_FLAG_PAIRED;
    flags |= capture_control_snapshot().mode == WIEVAC_MODE_CAPTURE
                 ? V2_FLAG_CAPTURE : V2_FLAG_RUN;
    if (state->sample_count >= 2U && state->delta.count == state->sample_count) {
        flags |= V2_FLAG_WINDOW_VALID;
    }
    if (atomic_load_u32(&g_first_word_invalid_count) != state->start_first_word_invalid) {
        flags |= V2_FLAG_FIRST_WORD_INVALID_SEEN;
    }
    if (state->gain_metadata_count == state->sample_count && state->sample_count > 0U) {
        flags |= V2_FLAG_GAIN_METADATA_VALID;
    }
    if (!pi_liveness_available() ||
        atomic_load_u32(&g_udp_send_error_count) != state->start_udp_error) {
        flags |= V2_FLAG_SYSTEM_UNAVAILABLE;
    }

    v2_header_t header = {
        .message_type = V2_MSG_FEATURE_RUN,
        .flags = flags,
        .payload_length = V2_FEATURE_PAYLOAD_SIZE,
        .feature_schema = V2_FEATURE_SCHEMA,
        .link_id = g_config.link_id,
        .tx_id = pairing.tx_id,
        .rx_id = g_config.rx_id,
        .sequence = next_sequence(&g_feature_sequence),
        .timestamp_us = end_us,
        .window_duration_ms = duration_ms,
        .sample_count = state->sample_count,
        .invalid_csi = atomic_load_u32(&g_invalid_csi_count),
        .wrong_source = atomic_load_u32(&g_wrong_source_count),
        .queue_drop = atomic_load_u32(&g_csi_queue_drop_count),
        .packet_loss = atomic_load_u32(&g_measurement_loss_count),
    };
    uint8_t packet[V2_HEADER_SIZE + V2_FEATURE_PAYLOAD_SIZE];
    if (protocol_encode(packet, sizeof(packet), &header, payload)) {
        (void)enqueue_udp_packet(packet, sizeof(packet));
    }
}

static TickType_t ticks_until(uint64_t deadline_us)
{
    const uint64_t now_us = (uint64_t)esp_timer_get_time();
    if (now_us >= deadline_us) {
        return 0;
    }
    uint64_t remaining_ms = (deadline_us - now_us + 999U) / 1000U;
    if (remaining_ms > UINT32_MAX) {
        remaining_ms = UINT32_MAX;
    }
    TickType_t ticks = pdMS_TO_TICKS((uint32_t)remaining_ms);
    return ticks == 0 ? 1 : ticks;
}

static void dsp_task(void *argument)
{
    (void)argument;
    dsp_state_t state;
    dsp_full_reset(&state);
    uint32_t reset_generation = atomic_load_u32(&g_dsp_reset_generation);
    uint64_t window_start_us = (uint64_t)esp_timer_get_time();
    uint64_t window_end_us = window_start_us +
                             (uint64_t)WIEVAC_WINDOW_DURATION_MS * 1000U;
    csi_record_t record;

    for (;;) {
        const uint32_t current_generation =
            __atomic_load_n(&g_dsp_reset_generation, __ATOMIC_ACQUIRE);
        if (current_generation != reset_generation) {
            dsp_full_reset(&state);
            reset_generation = current_generation;
            window_start_us = (uint64_t)esp_timer_get_time();
            window_end_us = window_start_us +
                            (uint64_t)WIEVAC_WINDOW_DURATION_MS * 1000U;
        }

        if (xQueueReceive(g_csi_queue, &record, ticks_until(window_end_us)) == pdTRUE) {
            while (record.arrival_us >= window_end_us) {
                emit_feature_window(&state, window_start_us, window_end_us);
                dsp_window_reset(&state);
                window_start_us = window_end_us;
                window_end_us += (uint64_t)WIEVAC_WINDOW_DURATION_MS * 1000U;
            }
            (void)process_csi_record(&state, &record);
        } else {
            const uint64_t now_us = (uint64_t)esp_timer_get_time();
            while (now_us >= window_end_us) {
                emit_feature_window(&state, window_start_us, window_end_us);
                dsp_window_reset(&state);
                window_start_us = window_end_us;
                window_end_us += (uint64_t)WIEVAC_WINDOW_DURATION_MS * 1000U;
            }
        }
    }
}

/* -------------------------------------------------------------------------- */
/* Wi-Fi, CSI, ESP-NOW, and application initialization                        */
/* -------------------------------------------------------------------------- */

static void wifi_event_handler(void *argument, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    (void)argument;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        if (g_config.wifi_ssid[0] == '\0') {
            esp_err_t channel_err = esp_wifi_set_channel(WIEVAC_WIFI_CHANNEL,
                                                         WIFI_SECOND_CHAN_NONE);
            if (channel_err != ESP_OK) {
                ESP_LOGE(TAG, "Cannot set standalone pairing channel: %s",
                         esp_err_to_name(channel_err));
            } else {
                ESP_LOGW(TAG, "Wi-Fi credentials absent; Pi transport unavailable, pairing stays on channel %u",
                         WIEVAC_WIFI_CHANNEL);
            }
            return;
        }
        esp_err_t err = esp_wifi_connect();
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Wi-Fi connect start failed: %s", esp_err_to_name(err));
        }
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED) {
        ESP_LOGI(TAG, "Wi-Fi associated; waiting for DHCP");
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;
        const uint32_t expected_gateway = inet_addr(WIEVAC_PI_GATEWAY_IP);
        if (event->ip_info.gw.addr != expected_gateway) {
            capture_control_reset_to_run("unexpected_DHCP_gateway");
            xEventGroupClearBits(g_network_events, NETWORK_GOT_IP_BIT);
            pi_alive_store_us(0U);
            portENTER_CRITICAL(&g_pi_address_mux);
            memset(&g_pi_address, 0, sizeof(g_pi_address));
            portEXIT_CRITICAL(&g_pi_address_mux);
            ESP_LOGE(TAG, "DHCP gateway=" IPSTR " does not match required Pi gateway %s; system unavailable, UDP disabled",
                     IP2STR(&event->ip_info.gw), WIEVAC_PI_GATEWAY_IP);
            return;
        }
        capture_control_reset_to_run("network_reconnected_requires_new_START");
        portENTER_CRITICAL(&g_pi_address_mux);
        memset(&g_pi_address, 0, sizeof(g_pi_address));
        g_pi_address.sin_family = AF_INET;
        g_pi_address.sin_port = htons(WIEVAC_PI_UDP_PORT);
        /* In the supported Pi-hotspot deployment, the DHCP gateway is the Pi. */
        g_pi_address.sin_addr.s_addr = event->ip_info.gw.addr;
        portEXIT_CRITICAL(&g_pi_address_mux);
        pi_alive_store_us(0U);
        xEventGroupSetBits(g_network_events, NETWORK_GOT_IP_BIT);

        uint8_t primary = 0U;
        wifi_second_chan_t secondary = WIFI_SECOND_CHAN_NONE;
        esp_err_t err = esp_wifi_get_channel(&primary, &secondary);
        if (err == ESP_OK) {
            ESP_LOGI(TAG, "Network ready: IP=" IPSTR " gateway=" IPSTR
                     " (expected %s) channel=%u secondary=%d",
                     IP2STR(&event->ip_info.ip), IP2STR(&event->ip_info.gw),
                     WIEVAC_PI_GATEWAY_IP, primary, secondary);
            if (primary != WIEVAC_WIFI_CHANNEL || secondary != WIFI_SECOND_CHAN_NONE) {
                ESP_LOGE(TAG, "Radio channel/layout differs from configured HT20 measurement link");
            }
        } else {
            ESP_LOGE(TAG, "Cannot read negotiated Wi-Fi channel: %s", esp_err_to_name(err));
        }
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event =
            (const wifi_event_sta_disconnected_t *)event_data;
        capture_control_reset_to_run("WiFi_disconnected");
        xEventGroupClearBits(g_network_events, NETWORK_GOT_IP_BIT);
        pi_alive_store_us(0U);
        portENTER_CRITICAL(&g_pi_address_mux);
        memset(&g_pi_address, 0, sizeof(g_pi_address));
        portEXIT_CRITICAL(&g_pi_address_mux);
        ESP_LOGW(TAG, "Wi-Fi disconnected, reason=%u; Formula state must become unavailable upstream",
                 event != NULL ? event->reason : 0U);
        esp_err_t err = esp_wifi_connect();
        if (err != ESP_OK && err != ESP_ERR_WIFI_NOT_STARTED) {
            ESP_LOGE(TAG, "Wi-Fi reconnect request failed: %s", esp_err_to_name(err));
        }
    }
}

static void wifi_initialize(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_create_default_wifi_sta() != NULL ? ESP_OK : ESP_ERR_NO_MEM);

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIEVAC_WIFI_BANDWIDTH));

    wifi_config_t wifi_config = {0};
    const size_t ssid_length = strnlen(g_config.wifi_ssid, sizeof(g_config.wifi_ssid));
    const size_t pass_length = strnlen(g_config.wifi_password,
                                       sizeof(g_config.wifi_password));
    if (ssid_length > sizeof(wifi_config.sta.ssid)) {
        ESP_LOGE(TAG, "Wi-Fi SSID exceeds ESP-IDF limit");
        ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
    }
    memcpy(wifi_config.sta.ssid, g_config.wifi_ssid, ssid_length);
    if (pass_length >= sizeof(wifi_config.sta.password)) {
        ESP_LOGE(TAG, "Wi-Fi password exceeds ESP-IDF limit");
        ESP_ERROR_CHECK(ESP_ERR_INVALID_ARG);
    }
    memcpy(wifi_config.sta.password, g_config.wifi_password, pass_length);
    wifi_config.sta.channel = WIEVAC_WIFI_CHANNEL;
    wifi_config.sta.pmf_cfg.capable = true;
    wifi_config.sta.pmf_cfg.required = false;
    if (ssid_length > 0U) {
        ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    }
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

static void espnow_initialize(void)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(espnow_receive_callback));
    ESP_ERROR_CHECK(esp_now_register_send_cb(espnow_send_callback));
    pairing_state_t pairing = pairing_snapshot();
    if (pairing.valid) {
        ESP_ERROR_CHECK(ensure_espnow_peer(pairing.tx_mac));
    }
}

static void csi_initialize(void)
{
    /*
     * One LTF only removes ambiguity: RUN and CAPTURE accept an HT20 HT-LTF
     * of exactly 128 signed bytes. Adjacent-channel filtering is deliberately
     * disabled because it changes the adjacent-subcarrier roughness feature.
     */
    wifi_csi_config_t csi_config = {
        .lltf_en = false,
        .htltf_en = true,
        .stbc_htltf2_en = false,
        .ltf_merge_en = false,
        .channel_filter_en = false,
        .manu_scale = false,
        .shift = 0,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_receive_callback, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

static void create_task_checked(TaskFunction_t function, const char *name,
                                uint32_t stack_depth, UBaseType_t priority)
{
    if (xTaskCreate(function, name, stack_depth, NULL, priority, NULL) != pdPASS) {
        ESP_LOGE(TAG, "Cannot create task %s", name);
        ESP_ERROR_CHECK(ESP_ERR_NO_MEM);
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS partition requires reinitialization; stored local config will be erased");
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_read_mac(g_rx_mac, ESP_MAC_WIFI_STA));
    load_app_config();

    gpio_config_t button_config = {
        .pin_bit_mask = UINT64_C(1) << WIEVAC_PAIR_RESET_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&button_config));
    if (reset_button_held()) {
        ESP_ERROR_CHECK(erase_pairing());
        ESP_LOGW(TAG, "Pairing erased by boot-time physical reset request");
    }
    load_pairing();

    g_network_events = xEventGroupCreate();
    g_csi_queue = xQueueCreate(WIEVAC_CSI_QUEUE_DEPTH, sizeof(csi_record_t));
    g_udp_queue = xQueueCreate(WIEVAC_UDP_QUEUE_DEPTH, sizeof(udp_datagram_t));
    g_espnow_queue = xQueueCreate(WIEVAC_ESPNOW_QUEUE_DEPTH, sizeof(espnow_rx_event_t));
    ESP_ERROR_CHECK(g_network_events != NULL && g_csi_queue != NULL &&
                    g_udp_queue != NULL && g_espnow_queue != NULL
                        ? ESP_OK
                        : ESP_ERR_NO_MEM);

    create_task_checked(udp_sender_task, "wievac_udp", 4096U, 5U);
    create_task_checked(dsp_task, "wievac_dsp", 8192U, 6U);
    create_task_checked(espnow_receiver_task, "wievac_pair_rx", 6144U, 7U);
    create_task_checked(pairing_reset_button_task, "wievac_pair_btn", 3072U, 3U);

    wifi_initialize();
    espnow_initialize();
    csi_initialize();

    const pairing_state_t pairing = pairing_snapshot();
    ESP_LOGI(TAG, "RX startup: mac=" MACSTR " rx_id=%" PRIu32
             " link_id=%" PRIu32 " channel=%u bandwidth=HT20 mode=%s paired=%s"
             " pi_liveness_timeout_ms=%u",
             MAC2STR(g_rx_mac), g_config.rx_id, g_config.link_id,
             WIEVAC_WIFI_CHANNEL,
             capture_control_snapshot().mode == WIEVAC_MODE_CAPTURE ? "CAPTURE" : "RUN",
             pairing.valid ? "yes" : "no", WIEVAC_PI_LIVENESS_TIMEOUT_MS);
    if (pairing.valid) {
        ESP_LOGI(TAG, "Stored peer: tx_id=%" PRIu32 " tx_mac=" MACSTR,
                 pairing.tx_id, MAC2STR(pairing.tx_mac));
    } else {
        ESP_LOGW(TAG, "No paired TX in NVS; entering non-RSSI pairing discovery");
    }
}
