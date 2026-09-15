/*
 * WiEvac ESP32-S3 transmitter -- Protocol V2 measurement source.
 *
 * This firmware creates measurement traffic only.  It does not infer human
 * presence, congestion, or passability.
 */

#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_check.h"
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
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 0, 0) || \
    ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
#error "WiEvac transmitter currently supports ESP-IDF >= 5.0 and < 5.5; the project lock is 5.4.4"
#endif

/* --------------------------------------------------------------------------
 * Deployment/radio configuration.
 *
 * These are radio and resource settings, not sensing thresholds.  link_id can
 * is fixed at 1 for the single-node corridor trial.
 * TX power uses ESP-IDF quarter-dBm units; 52 means 13 dBm and is deliberately
 * below the ESP32-S3 maximum.  Its suitability still requires RF measurement.
 * -------------------------------------------------------------------------- */
#define WIEVAC_FIRMWARE_VERSION             UINT32_C(0x00020000)
#define WIEVAC_DEFAULT_LINK_ID              UINT32_C(1)
#define WIEVAC_WIFI_CHANNEL                 11U
#define WIEVAC_WIFI_BANDWIDTH               WIFI_BW_HT20
#define WIEVAC_WIFI_BANDWIDTH_MHZ           20U
#define WIEVAC_ESPNOW_PHY_MODE              WIFI_PHY_MODE_HT20
#define WIEVAC_ESPNOW_RATE                  WIFI_PHY_RATE_MCS0_LGI
#define WIEVAC_MCS_INDEX                    0U
#define WIEVAC_TX_POWER_QDBM                52
#define WIEVAC_MEASUREMENT_RATE_HZ          100U
#define WIEVAC_PAIR_BUTTON_GPIO             GPIO_NUM_0
#define WIEVAC_PAIR_BUTTON_HOLD_MS          3000U
#define WIEVAC_PAIR_DISCOVERY_MS            3000U
#define WIEVAC_PAIR_HELLO_REPETITIONS       3U
#define WIEVAC_PAIR_HELLO_SPACING_MS        250U
#define WIEVAC_SEND_CALLBACK_TIMEOUT_MS     250U
#define WIEVAC_STATS_LOG_PERIOD_MS          10000U
#define WIEVAC_RX_EVENT_QUEUE_DEPTH         12U
#define WIEVAC_MAX_PAIR_CANDIDATES          8U

_Static_assert(WIEVAC_MEASUREMENT_RATE_HZ > 0U &&
                   WIEVAC_MEASUREMENT_RATE_HZ <= configTICK_RATE_HZ,
               "measurement rate must fit the FreeRTOS tick rate");
_Static_assert((configTICK_RATE_HZ % WIEVAC_MEASUREMENT_RATE_HZ) == 0U,
               "measurement rate must divide the FreeRTOS tick rate exactly");

/* --------------------------------------------------------------------------
 * WiEvac Protocol V2 wire contract (all integer fields are big-endian).
 *
 * The C code serializes fields explicitly; a packed C struct is never sent.
 * IEEE-754 binary32 values, when present in other V2 payloads, are serialized
 * as their 32-bit bit pattern in big-endian order.
 *
 * Fixed header, 68 bytes:
 *   0  u32 magic = 0x57495632 (ASCII "WIV2")
 *   4  u8  protocol_version = 2
 *   5  u8  message_type
 *   6  u16 flags
 *   8  u16 header_length = 68
 *  10  u16 payload_length
 *  12  u16 feature_schema_version (0 for pairing/measurement)
 *  14  u16 reserved = 0
 *  16  u32 link_id
 *  20  u32 tx_id
 *  24  u32 rx_id (0 until a receiver is selected)
 *  28  u32 sequence (wraps modulo 2^32)
 *  32  u64 sender monotonic timestamp, microseconds since sender boot
 *  40  u32 window_duration_ms (0 outside feature/snapshot windows)
 *  44  u32 sample_count
 *  48  u32 cumulative invalid-CSI count
 *  52  u32 cumulative wrong-source count
 *  56  u32 cumulative queue-drop count
 *  60  u32 cumulative measurement-packet loss count
 *  64  u32 CRC-32/ISO-HDLC over header bytes [0,64), then payload
 *  68  payload bytes
 *
 * CRC parameters: poly 0x04C11DB7 (reflected 0xEDB88320), init/xorout
 * 0xFFFFFFFF, refin/refout true.  The CRC field itself is excluded.
 * Pair payloads: HELLO={u32 hello_nonce,u32 capabilities};
 * OFFER={u32 hello_nonce,u32 offer_nonce,u32 capabilities};
 * CONFIRM/ACK={u32 hello_nonce,u32 offer_nonce}.
 * Measurement payload (16 bytes): u32 TX firmware version, u32 target rate in
 * millihertz, u32 requested-send counter, u8 channel, u8 bandwidth MHz,
 * u8 MCS index, i8 configured TX power in quarter-dBm.
 * -------------------------------------------------------------------------- */
#define WIEVAC_PROTOCOL_MAGIC               UINT32_C(0x57495632)
#define WIEVAC_PROTOCOL_VERSION             2U
#define WIEVAC_PROTOCOL_HEADER_LEN          68U
#define WIEVAC_PROTOCOL_CRC_OFFSET          64U
#define WIEVAC_PROTOCOL_MAX_PACKET_LEN      128U
#define WIEVAC_PAIR_HELLO_PAYLOAD_LEN       8U
#define WIEVAC_PAIR_OFFER_PAYLOAD_LEN       12U
#define WIEVAC_PAIR_CONFIRM_PAYLOAD_LEN     8U
#define WIEVAC_MEASUREMENT_PAYLOAD_LEN      16U

#define WIEVAC_MSG_PAIR_HELLO               0x01U
#define WIEVAC_MSG_PAIR_OFFER               0x02U
#define WIEVAC_MSG_PAIR_CONFIRM             0x03U
#define WIEVAC_MSG_PAIR_ACK                 0x04U
#define WIEVAC_MSG_MEASUREMENT              0x10U
#define WIEVAC_MSG_PI_ALIVE                 0x30U /* Header-only RX<->Pi liveness ACK. */

#define WIEVAC_FLAG_PAIRED                  UINT16_C(0x0001)
#define WIEVAC_FLAG_RUN                     UINT16_C(0x0002)
#define WIEVAC_FLAG_CAPTURE                 UINT16_C(0x0004)
#define WIEVAC_FLAG_WINDOW_VALID            UINT16_C(0x0008)
#define WIEVAC_FLAG_CHANNEL_FILTER_ENABLED  UINT16_C(0x0010)
#define WIEVAC_FLAG_FIRST_WORD_INVALID_SEEN UINT16_C(0x0020)
#define WIEVAC_FLAG_GAIN_METADATA_VALID     UINT16_C(0x0040)
#define WIEVAC_FLAG_SYSTEM_UNAVAILABLE      UINT16_C(0x0080)

#define WIEVAC_PAIR_CAP_ESPNOW_UNICAST      UINT32_C(0x00000001)
#define WIEVAC_PAIR_CAP_CONFIRM_ACK         UINT32_C(0x00000002)
#define WIEVAC_PAIR_CAP_CRC32               UINT32_C(0x00000004)
#define WIEVAC_PAIR_LOCAL_CAPS               (WIEVAC_PAIR_CAP_ESPNOW_UNICAST | \
                                               WIEVAC_PAIR_CAP_CONFIRM_ACK | \
                                               WIEVAC_PAIR_CAP_CRC32)
#define WIEVAC_PAIR_REQUIRED_CAPS            WIEVAC_PAIR_LOCAL_CAPS
#define WIEVAC_NVS_NAMESPACE                "wievac_tx"
#define WIEVAC_NVS_PAIR_VERSION             2U
#define WIEVAC_NOTIFY_CLEAR_PAIRING          UINT32_C(0x00000001)

typedef struct {
    uint8_t type;
    uint16_t flags;
    uint16_t payload_len;
    uint16_t feature_schema;
    uint32_t link_id;
    uint32_t tx_id;
    uint32_t rx_id;
    uint32_t sequence;
    uint64_t timestamp_us;
    uint32_t window_duration_ms;
    uint32_t sample_count;
    uint32_t invalid_csi_count;
    uint32_t wrong_source_count;
    uint32_t queue_drop_count;
    uint32_t packet_loss_count;
} wievac_header_t;

typedef struct {
    uint8_t source_mac[ESP_NOW_ETH_ALEN];
    uint16_t length;
    uint8_t bytes[WIEVAC_PROTOCOL_MAX_PACKET_LEN];
} espnow_rx_event_t;

typedef struct {
    uint8_t mac[ESP_NOW_ETH_ALEN];
    uint32_t rx_id;
    uint32_t offer_nonce;
} pair_candidate_t;

typedef struct {
    bool valid;
    uint8_t rx_mac[ESP_NOW_ETH_ALEN];
    uint32_t rx_id;
} pairing_record_t;

static const char *TAG = "wievac_tx_v2";
static const uint8_t BROADCAST_MAC[ESP_NOW_ETH_ALEN] = {
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff
};

static nvs_handle_t s_nvs;
static QueueHandle_t s_rx_queue;
static SemaphoreHandle_t s_send_done;
static TaskHandle_t s_owner_task;
static portMUX_TYPE s_counter_mux = portMUX_INITIALIZER_UNLOCKED;

static uint8_t s_tx_mac[ESP_NOW_ETH_ALEN];
static uint32_t s_link_id;
static uint32_t s_tx_id;
static uint32_t s_protocol_sequence;
static pairing_record_t s_pairing;

static bool s_send_in_flight;
static bool s_send_timed_out;
static esp_now_send_status_t s_send_status = ESP_NOW_SEND_FAIL;
static uint32_t s_send_requested;
static uint32_t s_send_succeeded;
static uint32_t s_send_failed;
static uint32_t s_send_timeouts;
static uint32_t s_late_callbacks;
static uint32_t s_rx_queue_drops;

static void put_u16_be(uint8_t *dst, uint16_t value)
{
    dst[0] = (uint8_t)(value >> 8);
    dst[1] = (uint8_t)value;
}

static void put_u32_be(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)(value >> 24);
    dst[1] = (uint8_t)(value >> 16);
    dst[2] = (uint8_t)(value >> 8);
    dst[3] = (uint8_t)value;
}

static void put_u64_be(uint8_t *dst, uint64_t value)
{
    put_u32_be(dst, (uint32_t)(value >> 32));
    put_u32_be(dst + 4, (uint32_t)value);
}

static uint16_t get_u16_be(const uint8_t *src)
{
    return (uint16_t)(((uint16_t)src[0] << 8) | src[1]);
}

static uint32_t get_u32_be(const uint8_t *src)
{
    return ((uint32_t)src[0] << 24) | ((uint32_t)src[1] << 16) |
           ((uint32_t)src[2] << 8) | (uint32_t)src[3];
}

static uint64_t get_u64_be(const uint8_t *src)
{
    return ((uint64_t)get_u32_be(src) << 32) | get_u32_be(src + 4);
}

static uint32_t crc32_update(uint32_t state, const uint8_t *data, size_t length)
{
    for (size_t i = 0; i < length; ++i) {
        state ^= data[i];
        for (unsigned bit = 0; bit < 8U; ++bit) {
            state = (state >> 1) ^ ((state & 1U) ? UINT32_C(0xEDB88320) : 0U);
        }
    }
    return state;
}

static uint32_t protocol_crc(const uint8_t *packet, uint16_t payload_len)
{
    uint32_t state = UINT32_C(0xFFFFFFFF);
    state = crc32_update(state, packet, WIEVAC_PROTOCOL_CRC_OFFSET);
    state = crc32_update(state, packet + WIEVAC_PROTOCOL_HEADER_LEN, payload_len);
    return state ^ UINT32_C(0xFFFFFFFF);
}

static size_t protocol_encode(uint8_t *packet, size_t capacity, const wievac_header_t *header,
                              const uint8_t *payload)
{
    if (packet == NULL || header == NULL) {
        return 0U;
    }
    const size_t total_len = WIEVAC_PROTOCOL_HEADER_LEN + header->payload_len;
    if (total_len > capacity ||
        (header->payload_len != 0U && payload == NULL)) {
        return 0U;
    }

    memset(packet, 0, WIEVAC_PROTOCOL_HEADER_LEN);
    put_u32_be(packet + 0, WIEVAC_PROTOCOL_MAGIC);
    packet[4] = WIEVAC_PROTOCOL_VERSION;
    packet[5] = header->type;
    put_u16_be(packet + 6, header->flags);
    put_u16_be(packet + 8, WIEVAC_PROTOCOL_HEADER_LEN);
    put_u16_be(packet + 10, header->payload_len);
    put_u16_be(packet + 12, header->feature_schema);
    put_u16_be(packet + 14, 0U);
    put_u32_be(packet + 16, header->link_id);
    put_u32_be(packet + 20, header->tx_id);
    put_u32_be(packet + 24, header->rx_id);
    put_u32_be(packet + 28, header->sequence);
    put_u64_be(packet + 32, header->timestamp_us);
    put_u32_be(packet + 40, header->window_duration_ms);
    put_u32_be(packet + 44, header->sample_count);
    put_u32_be(packet + 48, header->invalid_csi_count);
    put_u32_be(packet + 52, header->wrong_source_count);
    put_u32_be(packet + 56, header->queue_drop_count);
    put_u32_be(packet + 60, header->packet_loss_count);
    if (header->payload_len != 0U) {
        memcpy(packet + WIEVAC_PROTOCOL_HEADER_LEN, payload, header->payload_len);
    }
    put_u32_be(packet + WIEVAC_PROTOCOL_CRC_OFFSET,
               protocol_crc(packet, header->payload_len));
    return total_len;
}

static bool protocol_decode(const uint8_t *packet, size_t length, wievac_header_t *header,
                            const uint8_t **payload)
{
    if (packet == NULL || header == NULL || payload == NULL ||
        length < WIEVAC_PROTOCOL_HEADER_LEN) {
        return false;
    }
    const uint16_t payload_len = get_u16_be(packet + 10);
    if (get_u32_be(packet + 0) != WIEVAC_PROTOCOL_MAGIC ||
        packet[4] != WIEVAC_PROTOCOL_VERSION ||
        get_u16_be(packet + 8) != WIEVAC_PROTOCOL_HEADER_LEN ||
        get_u16_be(packet + 14) != 0U ||
        length != (size_t)WIEVAC_PROTOCOL_HEADER_LEN + payload_len ||
        get_u32_be(packet + WIEVAC_PROTOCOL_CRC_OFFSET) != protocol_crc(packet, payload_len)) {
        return false;
    }

    header->type = packet[5];
    header->flags = get_u16_be(packet + 6);
    header->payload_len = payload_len;
    header->feature_schema = get_u16_be(packet + 12);
    header->link_id = get_u32_be(packet + 16);
    header->tx_id = get_u32_be(packet + 20);
    header->rx_id = get_u32_be(packet + 24);
    header->sequence = get_u32_be(packet + 28);
    header->timestamp_us = get_u64_be(packet + 32);
    header->window_duration_ms = get_u32_be(packet + 40);
    header->sample_count = get_u32_be(packet + 44);
    header->invalid_csi_count = get_u32_be(packet + 48);
    header->wrong_source_count = get_u32_be(packet + 52);
    header->queue_drop_count = get_u32_be(packet + 56);
    header->packet_loss_count = get_u32_be(packet + 60);
    *payload = packet + WIEVAC_PROTOCOL_HEADER_LEN;
    return true;
}

static uint32_t id_from_mac(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    uint32_t value = UINT32_C(2166136261);
    for (size_t i = 0; i < ESP_NOW_ETH_ALEN; ++i) {
        value = (value ^ mac[i]) * UINT32_C(16777619);
    }
    return value == 0U ? 1U : value;
}

static bool is_valid_unicast_peer_mac(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    bool any_nonzero = false;
    for (size_t i = 0; i < ESP_NOW_ETH_ALEN; ++i) {
        any_nonzero = any_nonzero || mac[i] != 0U;
    }
    return any_nonzero && (mac[0] & 0x01U) == 0U &&
           memcmp(mac, s_tx_mac, ESP_NOW_ETH_ALEN) != 0;
}

static esp_err_t nvs_get_optional_u32(nvs_handle_t handle, const char *key,
                                      uint32_t default_value, uint32_t *value)
{
    esp_err_t err = nvs_get_u32(handle, key, value);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        *value = default_value;
        return ESP_OK;
    }
    return err;
}

static esp_err_t load_configuration_and_pairing(void)
{
    esp_err_t err = nvs_open(WIEVAC_NVS_NAMESPACE, NVS_READWRITE, &s_nvs);
    if (err != ESP_OK) {
        return err;
    }
    s_link_id = WIEVAC_DEFAULT_LINK_ID;
    ESP_RETURN_ON_ERROR(nvs_get_optional_u32(s_nvs, "tx_id", id_from_mac(s_tx_mac),
                                              &s_tx_id), TAG, "read tx_id");
    if (s_link_id == 0U || s_tx_id == 0U) {
        ESP_LOGE(TAG, "link_id and tx_id must be non-zero");
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t pair_version = 0U;
    uint32_t pair_link_id = 0U;
    uint32_t pair_tx_id = 0U;
    uint32_t rx_id = 0U;
    uint8_t rx_mac[ESP_NOW_ETH_ALEN] = {0};
    size_t mac_len = sizeof(rx_mac);
    const esp_err_t version_err = nvs_get_u8(s_nvs, "pair_ver", &pair_version);
    const esp_err_t link_err = nvs_get_u32(s_nvs, "pair_link", &pair_link_id);
    const esp_err_t tx_err = nvs_get_u32(s_nvs, "pair_tx", &pair_tx_id);
    const esp_err_t id_err = nvs_get_u32(s_nvs, "rx_id", &rx_id);
    const esp_err_t mac_err = nvs_get_blob(s_nvs, "rx_mac", rx_mac, &mac_len);
    if (version_err == ESP_OK && link_err == ESP_OK && tx_err == ESP_OK &&
        id_err == ESP_OK && mac_err == ESP_OK &&
        pair_version == WIEVAC_NVS_PAIR_VERSION && pair_link_id == s_link_id &&
        pair_tx_id == s_tx_id && rx_id != 0U && mac_len == ESP_NOW_ETH_ALEN &&
        is_valid_unicast_peer_mac(rx_mac)) {
        s_pairing.valid = true;
        s_pairing.rx_id = rx_id;
        memcpy(s_pairing.rx_mac, rx_mac, ESP_NOW_ETH_ALEN);
    } else if ((version_err != ESP_OK && version_err != ESP_ERR_NVS_NOT_FOUND) ||
               (link_err != ESP_OK && link_err != ESP_ERR_NVS_NOT_FOUND) ||
               (tx_err != ESP_OK && tx_err != ESP_ERR_NVS_NOT_FOUND) ||
               (id_err != ESP_OK && id_err != ESP_ERR_NVS_NOT_FOUND) ||
               (mac_err != ESP_OK && mac_err != ESP_ERR_NVS_NOT_FOUND)) {
        ESP_LOGW(TAG, "pairing record is unreadable; entering pairing mode");
    } else if (version_err == ESP_OK || link_err == ESP_OK || tx_err == ESP_OK ||
               id_err == ESP_OK || mac_err == ESP_OK) {
        ESP_LOGW(TAG, "pairing record does not match this TX/link; entering pairing mode");
    }
    return ESP_OK;
}

static esp_err_t save_pairing(const pair_candidate_t *candidate)
{
    ESP_RETURN_ON_ERROR(nvs_set_blob(s_nvs, "rx_mac", candidate->mac,
                                     ESP_NOW_ETH_ALEN), TAG, "store RX MAC");
    ESP_RETURN_ON_ERROR(nvs_set_u32(s_nvs, "rx_id", candidate->rx_id), TAG,
                        "store RX ID");
    ESP_RETURN_ON_ERROR(nvs_set_u32(s_nvs, "pair_link", s_link_id), TAG,
                        "store pairing link ID");
    ESP_RETURN_ON_ERROR(nvs_set_u32(s_nvs, "pair_tx", s_tx_id), TAG,
                        "store pairing TX ID");
    ESP_RETURN_ON_ERROR(nvs_set_u8(s_nvs, "pair_ver", WIEVAC_NVS_PAIR_VERSION), TAG,
                        "store pairing version");
    ESP_RETURN_ON_ERROR(nvs_commit(s_nvs), TAG, "commit pairing");
    memcpy(s_pairing.rx_mac, candidate->mac, ESP_NOW_ETH_ALEN);
    s_pairing.rx_id = candidate->rx_id;
    s_pairing.valid = true;
    return ESP_OK;
}

static esp_err_t clear_pairing(void)
{
    const char *keys[] = {"pair_ver", "pair_link", "pair_tx", "rx_id", "rx_mac"};
    for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
        const esp_err_t err = nvs_erase_key(s_nvs, keys[i]);
        if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
            return err;
        }
    }
    ESP_RETURN_ON_ERROR(nvs_commit(s_nvs), TAG, "commit pairing erase");
    memset(&s_pairing, 0, sizeof(s_pairing));
    return ESP_OK;
}

static void handle_owner_notifications(void)
{
    uint32_t notifications = 0U;
    if (xTaskNotifyWait(0U, UINT32_MAX, &notifications, 0U) == pdTRUE &&
        (notifications & WIEVAC_NOTIFY_CLEAR_PAIRING) != 0U) {
        const esp_err_t err = clear_pairing();
        if (err == ESP_OK) {
            ESP_LOGW(TAG, "pairing cleared by physical button; restarting into pairing mode");
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_restart();
        }
        ESP_LOGE(TAG, "could not clear pairing: %s", esp_err_to_name(err));
    }
}

static void owner_delay_ms(uint32_t delay_ms)
{
    const TickType_t end_tick = xTaskGetTickCount() + pdMS_TO_TICKS(delay_ms);
    while ((int32_t)(end_tick - xTaskGetTickCount()) > 0) {
        TickType_t remaining = end_tick - xTaskGetTickCount();
        const TickType_t poll_ticks = pdMS_TO_TICKS(50U);
        if (remaining > poll_ticks) {
            remaining = poll_ticks;
        }
        vTaskDelay(remaining == 0U ? 1U : remaining);
        handle_owner_notifications();
    }
}

static void get_send_counters(uint32_t *requested, uint32_t *succeeded,
                              uint32_t *failed, uint32_t *timeouts,
                              uint32_t *late_callbacks, uint32_t *queue_drops)
{
    portENTER_CRITICAL(&s_counter_mux);
    *requested = s_send_requested;
    *succeeded = s_send_succeeded;
    *failed = s_send_failed;
    *timeouts = s_send_timeouts;
    *late_callbacks = s_late_callbacks;
    *queue_drops = s_rx_queue_drops;
    portEXIT_CRITICAL(&s_counter_mux);
}

static void espnow_send_cb(const uint8_t *mac_addr, esp_now_send_status_t status)
{
    (void)mac_addr;
    bool notify = false;
    portENTER_CRITICAL(&s_counter_mux);
    if (s_send_in_flight) {
        s_send_status = status;
        if (s_send_timed_out) {
            ++s_late_callbacks;
        } else if (status == ESP_NOW_SEND_SUCCESS) {
            ++s_send_succeeded;
        } else {
            ++s_send_failed;
        }
        s_send_in_flight = false;
        s_send_timed_out = false;
        notify = true;
    }
    portEXIT_CRITICAL(&s_counter_mux);
    if (notify) {
        (void)xSemaphoreGive(s_send_done);
    }
}

static void espnow_recv_cb(const esp_now_recv_info_t *info, const uint8_t *data, int data_len)
{
    if (info == NULL || info->src_addr == NULL || data == NULL || data_len <= 0 ||
        data_len > (int)WIEVAC_PROTOCOL_MAX_PACKET_LEN) {
        return;
    }
    espnow_rx_event_t event = {.length = (uint16_t)data_len};
    memcpy(event.source_mac, info->src_addr, ESP_NOW_ETH_ALEN);
    memcpy(event.bytes, data, (size_t)data_len);
    if (xQueueSend(s_rx_queue, &event, 0) != pdTRUE) {
        portENTER_CRITICAL(&s_counter_mux);
        ++s_rx_queue_drops;
        portEXIT_CRITICAL(&s_counter_mux);
    }
}

static esp_err_t send_and_wait(const uint8_t destination[ESP_NOW_ETH_ALEN],
                               const uint8_t *packet, size_t length)
{
    while (xSemaphoreTake(s_send_done, 0) == pdTRUE) {
        /* Drain a completion left by a callback that arrived after timeout. */
    }

    portENTER_CRITICAL(&s_counter_mux);
    if (s_send_in_flight) {
        portEXIT_CRITICAL(&s_counter_mux);
        return ESP_ERR_INVALID_STATE;
    }
    s_send_in_flight = true;
    s_send_timed_out = false;
    s_send_status = ESP_NOW_SEND_FAIL;
    ++s_send_requested;
    portEXIT_CRITICAL(&s_counter_mux);

    const esp_err_t err = esp_now_send(destination, packet, length);
    if (err != ESP_OK) {
        portENTER_CRITICAL(&s_counter_mux);
        s_send_in_flight = false;
        ++s_send_failed;
        portEXIT_CRITICAL(&s_counter_mux);
        return err;
    }

    if (xSemaphoreTake(s_send_done, pdMS_TO_TICKS(WIEVAC_SEND_CALLBACK_TIMEOUT_MS)) != pdTRUE) {
        bool genuinely_timed_out = false;
        esp_now_send_status_t completed_status = ESP_NOW_SEND_FAIL;
        portENTER_CRITICAL(&s_counter_mux);
        if (s_send_in_flight) {
            s_send_timed_out = true;
            ++s_send_failed;
            ++s_send_timeouts;
            genuinely_timed_out = true;
        } else {
            /* Callback completed at the timeout boundary after the semaphore
             * wait returned. Preserve its real result instead of misreporting. */
            completed_status = s_send_status;
        }
        portEXIT_CRITICAL(&s_counter_mux);
        if (genuinely_timed_out) {
            return ESP_ERR_TIMEOUT;
        }
        return completed_status == ESP_NOW_SEND_SUCCESS ? ESP_OK : ESP_FAIL;
    }
    portENTER_CRITICAL(&s_counter_mux);
    const esp_now_send_status_t completed_status = s_send_status;
    portEXIT_CRITICAL(&s_counter_mux);
    return completed_status == ESP_NOW_SEND_SUCCESS ? ESP_OK : ESP_FAIL;
}

static esp_err_t add_peer(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    if (!esp_now_is_peer_exist(mac)) {
        esp_now_peer_info_t peer = {0};
        memcpy(peer.peer_addr, mac, ESP_NOW_ETH_ALEN);
        peer.channel = WIEVAC_WIFI_CHANNEL;
        peer.ifidx = WIFI_IF_STA;
        peer.encrypt = false;
        ESP_RETURN_ON_ERROR(esp_now_add_peer(&peer), TAG, "add ESP-NOW peer");
    }
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 1, 0)
    esp_now_rate_config_t rate = {
        .phymode = WIEVAC_ESPNOW_PHY_MODE,
        .rate = WIEVAC_ESPNOW_RATE,
        .ersu = false,
        .dcm = false,
    };
    return esp_now_set_peer_rate_config(mac, &rate);
#else
    (void)mac;
    return esp_wifi_config_espnow_rate(WIFI_IF_STA, WIEVAC_ESPNOW_RATE);
#endif
}

static void remove_peer_if_present(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    if (esp_now_is_peer_exist(mac)) {
        const esp_err_t err = esp_now_del_peer(mac);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "could not remove temporary peer " MACSTR ": %s",
                     MAC2STR(mac), esp_err_to_name(err));
        }
    }
}

static wievac_header_t make_header(uint8_t type, uint16_t flags, uint16_t payload_len,
                                   uint32_t rx_id)
{
    uint32_t requested, succeeded, failed, timeouts, late, queue_drops;
    get_send_counters(&requested, &succeeded, &failed, &timeouts, &late, &queue_drops);
    (void)requested;
    (void)succeeded;
    (void)failed;
    (void)timeouts;
    (void)late;
    wievac_header_t header = {
        .type = type,
        .flags = flags,
        .payload_len = payload_len,
        .feature_schema = 0U,
        .link_id = s_link_id,
        .tx_id = s_tx_id,
        .rx_id = rx_id,
        .sequence = s_protocol_sequence++,
        .timestamp_us = (uint64_t)esp_timer_get_time(),
        .queue_drop_count = queue_drops,
    };
    return header;
}

static bool candidate_matches(const pair_candidate_t *candidate,
                              const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    return memcmp(candidate->mac, mac, ESP_NOW_ETH_ALEN) == 0;
}

static size_t collect_pair_candidates(uint32_t hello_nonce, pair_candidate_t *candidates,
                                      size_t capacity, bool *overflow)
{
    const int64_t deadline_us = esp_timer_get_time() +
                                (int64_t)WIEVAC_PAIR_DISCOVERY_MS * 1000;
    size_t count = 0U;
    *overflow = false;
    while (esp_timer_get_time() < deadline_us) {
        handle_owner_notifications();
        const int64_t remaining_us = deadline_us - esp_timer_get_time();
        TickType_t wait_ticks = pdMS_TO_TICKS((uint32_t)(remaining_us / 1000));
        const TickType_t poll_ticks = pdMS_TO_TICKS(100U);
        if (wait_ticks > poll_ticks) {
            wait_ticks = poll_ticks;
        }
        if (wait_ticks == 0U) {
            wait_ticks = 1U;
        }
        espnow_rx_event_t event;
        if (xQueueReceive(s_rx_queue, &event, wait_ticks) != pdTRUE) {
            continue;
        }
        wievac_header_t header;
        const uint8_t *payload;
        if (!protocol_decode(event.bytes, event.length, &header, &payload) ||
            header.type != WIEVAC_MSG_PAIR_OFFER ||
            header.payload_len != WIEVAC_PAIR_OFFER_PAYLOAD_LEN ||
            header.feature_schema != 0U || header.link_id != s_link_id ||
            header.tx_id != s_tx_id || header.rx_id == 0U ||
            !is_valid_unicast_peer_mac(event.source_mac) ||
            get_u32_be(payload) != hello_nonce ||
            get_u32_be(payload + 4) == 0U ||
            (get_u32_be(payload + 8) & WIEVAC_PAIR_REQUIRED_CAPS) !=
                WIEVAC_PAIR_REQUIRED_CAPS) {
            continue;
        }

        size_t index = 0U;
        while (index < count && !candidate_matches(&candidates[index], event.source_mac)) {
            ++index;
        }
        if (index < count) {
            if (candidates[index].rx_id != header.rx_id ||
                candidates[index].offer_nonce != get_u32_be(payload + 4)) {
                *overflow = true;
            }
        } else if (count < capacity) {
            memcpy(candidates[count].mac, event.source_mac, ESP_NOW_ETH_ALEN);
            candidates[count].rx_id = header.rx_id;
            candidates[count].offer_nonce = get_u32_be(payload + 4);
            ++count;
        } else {
            *overflow = true;
        }
    }
    return count;
}

static bool wait_for_pair_ack(const pair_candidate_t *candidate, uint32_t hello_nonce)
{
    const int64_t deadline_us = esp_timer_get_time() +
                                (int64_t)WIEVAC_PAIR_DISCOVERY_MS * 1000;
    while (esp_timer_get_time() < deadline_us) {
        handle_owner_notifications();
        const int64_t remaining_us = deadline_us - esp_timer_get_time();
        TickType_t wait_ticks = pdMS_TO_TICKS((uint32_t)(remaining_us / 1000));
        const TickType_t poll_ticks = pdMS_TO_TICKS(100U);
        if (wait_ticks > poll_ticks) {
            wait_ticks = poll_ticks;
        }
        if (wait_ticks == 0U) {
            wait_ticks = 1U;
        }
        espnow_rx_event_t event;
        if (xQueueReceive(s_rx_queue, &event, wait_ticks) != pdTRUE) {
            continue;
        }
        wievac_header_t header;
        const uint8_t *payload;
        if (memcmp(event.source_mac, candidate->mac, ESP_NOW_ETH_ALEN) == 0 &&
            protocol_decode(event.bytes, event.length, &header, &payload) &&
            header.type == WIEVAC_MSG_PAIR_ACK &&
            header.payload_len == WIEVAC_PAIR_CONFIRM_PAYLOAD_LEN &&
            header.feature_schema == 0U && header.link_id == s_link_id &&
            header.tx_id == s_tx_id && header.rx_id == candidate->rx_id &&
            get_u32_be(payload) == hello_nonce &&
            get_u32_be(payload + 4) == candidate->offer_nonce) {
            return true;
        }
    }
    return false;
}

static esp_err_t run_pairing(void)
{
    uint8_t packet[WIEVAC_PROTOCOL_MAX_PACKET_LEN];
    uint8_t payload[WIEVAC_PAIR_OFFER_PAYLOAD_LEN];

    ESP_LOGW(TAG, "no RX pairing in NVS; PAIR_HELLO mode is active");
    while (!s_pairing.valid) {
        handle_owner_notifications();
        espnow_rx_event_t stale_event;
        while (xQueueReceive(s_rx_queue, &stale_event, 0U) == pdTRUE) {
            /* Discard responses from an earlier nonce before opening a new window. */
        }
        uint32_t requested_before, succeeded_before, failed_before, timeouts_before;
        uint32_t late_before, queue_drops_before;
        get_send_counters(&requested_before, &succeeded_before, &failed_before,
                          &timeouts_before, &late_before, &queue_drops_before);
        uint32_t hello_nonce = esp_random();
        if (hello_nonce == 0U) {
            hello_nonce = 1U;
        }
        put_u32_be(payload, hello_nonce);
        put_u32_be(payload + 4, WIEVAC_PAIR_LOCAL_CAPS);

        for (unsigned attempt = 0; attempt < WIEVAC_PAIR_HELLO_REPETITIONS; ++attempt) {
            wievac_header_t hello = make_header(WIEVAC_MSG_PAIR_HELLO, 0U,
                                                 WIEVAC_PAIR_HELLO_PAYLOAD_LEN, 0U);
            const size_t length = protocol_encode(packet, sizeof(packet), &hello, payload);
            if (length == 0U) {
                return ESP_ERR_INVALID_SIZE;
            }
            const esp_err_t err = send_and_wait(BROADCAST_MAC, packet, length);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "PAIR_HELLO send did not complete: %s", esp_err_to_name(err));
            }
            owner_delay_ms(WIEVAC_PAIR_HELLO_SPACING_MS);
        }

        pair_candidate_t candidates[WIEVAC_MAX_PAIR_CANDIDATES] = {0};
        bool overflow = false;
        const size_t count = collect_pair_candidates(hello_nonce, candidates,
                                                      WIEVAC_MAX_PAIR_CANDIDATES, &overflow);
        uint32_t requested_after, succeeded_after, failed_after, timeouts_after;
        uint32_t late_after, queue_drops_after;
        get_send_counters(&requested_after, &succeeded_after, &failed_after,
                          &timeouts_after, &late_after, &queue_drops_after);
        (void)requested_before;
        (void)succeeded_before;
        (void)failed_before;
        (void)timeouts_before;
        (void)late_before;
        (void)requested_after;
        (void)succeeded_after;
        (void)failed_after;
        (void)timeouts_after;
        (void)late_after;
        if (queue_drops_after != queue_drops_before) {
            ESP_LOGE(TAG, "pairing receive queue dropped packets; no RX selected");
            owner_delay_ms(1000U);
            continue;
        }
        if (overflow || count > 1U) {
            ESP_LOGE(TAG, "pairing ambiguous: %u or more matching RX devices; no RX selected",
                     (unsigned)(overflow ? WIEVAC_MAX_PAIR_CANDIDATES + 1U : count));
            for (size_t i = 0; i < count; ++i) {
                ESP_LOGW(TAG, "candidate rx_id=%" PRIu32 " mac=" MACSTR,
                         candidates[i].rx_id, MAC2STR(candidates[i].mac));
            }
            ESP_LOGW(TAG, "leave only the intended RX in pairing mode, then retry; RSSI is not used");
            owner_delay_ms(2000U);
            continue;
        }
        if (count == 0U) {
            ESP_LOGI(TAG, "no matching RX offer in this pairing window");
            owner_delay_ms(1000U);
            continue;
        }

        pair_candidate_t *candidate = &candidates[0];
        esp_err_t err = add_peer(candidate->mac);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "cannot add RX candidate " MACSTR ": %s",
                     MAC2STR(candidate->mac), esp_err_to_name(err));
            owner_delay_ms(1000U);
            continue;
        }
        put_u32_be(payload, hello_nonce);
        put_u32_be(payload + 4, candidate->offer_nonce);
        wievac_header_t confirm = make_header(WIEVAC_MSG_PAIR_CONFIRM, 0U,
                                               WIEVAC_PAIR_CONFIRM_PAYLOAD_LEN,
                                               candidate->rx_id);
        const size_t length = protocol_encode(packet, sizeof(packet), &confirm, payload);
        err = length == 0U ? ESP_ERR_INVALID_SIZE
                           : send_and_wait(candidate->mac, packet, length);
        if (err == ESP_OK && wait_for_pair_ack(candidate, hello_nonce)) {
            err = save_pairing(candidate);
            if (err == ESP_OK) {
                ESP_LOGI(TAG, "paired link=%" PRIu32 " tx=%" PRIu32
                         " rx=%" PRIu32 " rx_mac=" MACSTR,
                         s_link_id, s_tx_id, candidate->rx_id, MAC2STR(candidate->mac));
                return ESP_OK;
            }
            ESP_LOGE(TAG, "RX acknowledged but NVS save failed: %s", esp_err_to_name(err));
        } else {
            ESP_LOGW(TAG, "pair confirm/ack failed for rx_id=%" PRIu32, candidate->rx_id);
        }
        remove_peer_if_present(candidate->mac);
        owner_delay_ms(1000U);
    }
    return ESP_OK;
}

static void pairing_button_task(void *argument)
{
    (void)argument;
    TickType_t pressed_at = 0U;
    bool handled = false;
    for (;;) {
        if (gpio_get_level(WIEVAC_PAIR_BUTTON_GPIO) == 0) {
            if (pressed_at == 0U) {
                pressed_at = xTaskGetTickCount();
            } else if (!handled &&
                       (xTaskGetTickCount() - pressed_at) >=
                           pdMS_TO_TICKS(WIEVAC_PAIR_BUTTON_HOLD_MS)) {
                handled = true;
                if (s_owner_task != NULL) {
                    (void)xTaskNotify(s_owner_task, WIEVAC_NOTIFY_CLEAR_PAIRING, eSetBits);
                }
            }
        } else {
            pressed_at = 0U;
            handled = false;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

static esp_err_t wifi_init(void)
{
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "esp_netif_init");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "event loop");
    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&wifi_config), TAG, "esp_wifi_init");
    ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_RAM), TAG, "Wi-Fi storage");
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "Wi-Fi mode");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "Wi-Fi start");
    ESP_RETURN_ON_ERROR(esp_wifi_set_ps(WIFI_PS_NONE), TAG, "disable power save");
    ESP_RETURN_ON_ERROR(esp_wifi_set_protocol(WIFI_IF_STA,
                                              WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G |
                                                  WIFI_PROTOCOL_11N),
                        TAG, "Wi-Fi protocol");
    ESP_RETURN_ON_ERROR(esp_wifi_set_bandwidth(WIFI_IF_STA, WIEVAC_WIFI_BANDWIDTH),
                        TAG, "Wi-Fi bandwidth");
    ESP_RETURN_ON_ERROR(esp_wifi_set_channel(WIEVAC_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE),
                        TAG, "Wi-Fi channel");
    ESP_RETURN_ON_ERROR(esp_wifi_set_max_tx_power(WIEVAC_TX_POWER_QDBM), TAG,
                        "TX power");
    ESP_RETURN_ON_ERROR(esp_wifi_get_mac(WIFI_IF_STA, s_tx_mac), TAG, "read real STA MAC");
    return ESP_OK;
}

static esp_err_t espnow_init(void)
{
    ESP_RETURN_ON_ERROR(esp_now_init(), TAG, "esp_now_init");
    ESP_RETURN_ON_ERROR(esp_now_register_send_cb(espnow_send_cb), TAG,
                        "register send callback");
    ESP_RETURN_ON_ERROR(esp_now_register_recv_cb(espnow_recv_cb), TAG,
                        "register receive callback");
    return add_peer(BROADCAST_MAC);
}

static void run_measurement_loop(void)
{
    const TickType_t period_ticks = configTICK_RATE_HZ / WIEVAC_MEASUREMENT_RATE_HZ;
    TickType_t last_wake = xTaskGetTickCount();
    int64_t last_stats_us = esp_timer_get_time();
    uint32_t previous_requested = 0U;
    uint8_t packet[WIEVAC_PROTOCOL_MAX_PACKET_LEN];
    uint8_t payload[WIEVAC_MEASUREMENT_PAYLOAD_LEN];

    for (;;) {
        handle_owner_notifications();
        uint32_t requested, succeeded, failed, timeouts, late, queue_drops;
        get_send_counters(&requested, &succeeded, &failed, &timeouts, &late, &queue_drops);
        put_u32_be(payload + 0, WIEVAC_FIRMWARE_VERSION);
        put_u32_be(payload + 4, WIEVAC_MEASUREMENT_RATE_HZ * 1000U);
        put_u32_be(payload + 8, requested + 1U);
        payload[12] = WIEVAC_WIFI_CHANNEL;
        payload[13] = WIEVAC_WIFI_BANDWIDTH_MHZ;
        payload[14] = WIEVAC_MCS_INDEX;
        payload[15] = (uint8_t)(int8_t)WIEVAC_TX_POWER_QDBM;

        wievac_header_t header = make_header(WIEVAC_MSG_MEASUREMENT,
                                              WIEVAC_FLAG_PAIRED | WIEVAC_FLAG_RUN,
                                              WIEVAC_MEASUREMENT_PAYLOAD_LEN,
                                              s_pairing.rx_id);
        const size_t length = protocol_encode(packet, sizeof(packet), &header, payload);
        const esp_err_t err = length == 0U ? ESP_ERR_INVALID_SIZE
                                           : send_and_wait(s_pairing.rx_mac, packet, length);
        if (err == ESP_ERR_INVALID_STATE) {
            /* No esp_now_send request occurred while the prior callback is pending. */
            --s_protocol_sequence;
        }
        if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
            ESP_LOGW(TAG, "measurement seq=%" PRIu32 " not completed: %s",
                     header.sequence, esp_err_to_name(err));
        }

        const int64_t now_us = esp_timer_get_time();
        if (now_us - last_stats_us >= (int64_t)WIEVAC_STATS_LOG_PERIOD_MS * 1000) {
            get_send_counters(&requested, &succeeded, &failed, &timeouts, &late, &queue_drops);
            const double elapsed_s = (double)(now_us - last_stats_us) / 1000000.0;
            const double request_rate = (double)(requested - previous_requested) / elapsed_s;
            ESP_LOGI(TAG, "send requested=%" PRIu32 " success=%" PRIu32
                     " failed=%" PRIu32 " timeout=%" PRIu32 " late_cb=%" PRIu32
                     " rx_queue_drop=%" PRIu32 " request_rate=%.2f Hz",
                     requested, succeeded, failed, timeouts, late, queue_drops, request_rate);
            previous_requested = requested;
            last_stats_us = now_us;
        }

        if (xTaskDelayUntil(&last_wake, period_ticks) == pdFALSE) {
            /* A deadline was missed; reset phase instead of sending a catch-up burst. */
            last_wake = xTaskGetTickCount();
        }
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    s_rx_queue = xQueueCreate(WIEVAC_RX_EVENT_QUEUE_DEPTH, sizeof(espnow_rx_event_t));
    s_send_done = xSemaphoreCreateBinary();
    if (s_rx_queue == NULL || s_send_done == NULL) {
        ESP_LOGE(TAG, "could not allocate ESP-NOW control queue/semaphore");
        return;
    }

    ESP_ERROR_CHECK(wifi_init());
    ESP_ERROR_CHECK(load_configuration_and_pairing());
    s_owner_task = xTaskGetCurrentTaskHandle();

    gpio_config_t button = {
        .pin_bit_mask = UINT64_C(1) << WIEVAC_PAIR_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&button));

    ESP_ERROR_CHECK(espnow_init());
    if (s_pairing.valid) {
        err = add_peer(s_pairing.rx_mac);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "stored RX peer is unusable (%s); clearing pairing",
                     esp_err_to_name(err));
            ESP_ERROR_CHECK(clear_pairing());
        }
    }

    int8_t actual_power = 0;
    ESP_ERROR_CHECK(esp_wifi_get_max_tx_power(&actual_power));
    ESP_LOGI(TAG, "startup fw=0x%08" PRIx32 " mac=" MACSTR " tx_id=%" PRIu32
             " link_id=%" PRIu32,
             WIEVAC_FIRMWARE_VERSION, MAC2STR(s_tx_mac), s_tx_id, s_link_id);
    ESP_LOGI(TAG, "radio channel=%u bandwidth=%uMHz mcs=%u target_rate=%uHz"
             " tx_power=%d quarter-dBm pairing=%s",
             WIEVAC_WIFI_CHANNEL, WIEVAC_WIFI_BANDWIDTH_MHZ, WIEVAC_MCS_INDEX,
             WIEVAC_MEASUREMENT_RATE_HZ, actual_power,
             s_pairing.valid ? "PAIRED_UNICAST" : "PAIRING");
    if (s_pairing.valid) {
        ESP_LOGI(TAG, "paired rx_id=%" PRIu32 " rx_mac=" MACSTR,
                 s_pairing.rx_id, MAC2STR(s_pairing.rx_mac));
    }
    ESP_LOGI(TAG, "hold GPIO %d low for %u ms to erase pairing and restart",
             WIEVAC_PAIR_BUTTON_GPIO, WIEVAC_PAIR_BUTTON_HOLD_MS);

    if (xTaskCreate(pairing_button_task, "pair_button", 3072, NULL, 4, NULL) != pdPASS) {
        ESP_LOGE(TAG, "could not create pairing-button task");
        return;
    }
    if (!s_pairing.valid) {
        ESP_ERROR_CHECK(run_pairing());
    }
    run_measurement_loop();
}
