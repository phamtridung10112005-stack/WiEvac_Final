/* WiEvac V4 transmitter: one shared measurement stream sent to two RX links. */

#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "esp_check.h"
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
#include "freertos/task.h"
#include "nvs_flash.h"

#if ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(5, 0, 0) || \
    ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
#error "WiEvac V4 requires ESP-IDF >= 5.0 and < 5.5"
#endif

#define WIEVAC_LINK_ID             UINT32_C(1)
#define WIEVAC_TX_ID               UINT32_C(1)
#define WIEVAC_RX_COUNT            2U
#define WIEVAC_WIFI_CHANNEL        11U
#define WIEVAC_WIFI_BANDWIDTH      WIFI_BW_HT20
#define WIEVAC_ESPNOW_RATE         WIFI_PHY_RATE_MCS0_LGI
#define WIEVAC_MEASUREMENT_HZ      100U
#define WIEVAC_HELLO_INTERVAL_MS   1000U
#define WIEVAC_LOG_INTERVAL_MS     10000U
/* Keep ESP-NOW scheduling bounded when one RX disappears or stops calling back. */
#define WIEVAC_MAX_OUTSTANDING_PER_RX 2U
#define WIEVAC_PEER_TIMEOUT_MS      1500U

#ifndef CONFIG_WIEVAC_RX1_MAC
#define CONFIG_WIEVAC_RX1_MAC      ""
#endif
#ifndef CONFIG_WIEVAC_RX2_MAC
#define CONFIG_WIEVAC_RX2_MAC      ""
#endif

#define V4_MAGIC                   UINT32_C(0x57495634) /* WIV4 */
#define V4_VERSION                 4U
#define V4_HEADER_SIZE             48U
#define V4_CRC_OFFSET              44U
#define V4_SCHEMA                  4U

#define V4_MSG_HELLO               0x01U
#define V4_MSG_PAIR_REPLY          0x02U
#define V4_MSG_MEASUREMENT         0x10U

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

static const char *TAG = "wievac_tx_v4";
static const uint8_t BROADCAST_MAC[ESP_NOW_ETH_ALEN] = {
    0xff, 0xff, 0xff, 0xff, 0xff, 0xff
};

static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static uint8_t s_rx_macs[WIEVAC_RX_COUNT][ESP_NOW_ETH_ALEN];
static uint8_t s_reply_macs[WIEVAC_RX_COUNT][ESP_NOW_ETH_ALEN];
static uint8_t s_reply_mask;
static uint8_t s_paired_mask;
static uint32_t s_boot_id;
static uint32_t s_measurement_sequence;
static uint32_t s_control_sequence;
static uint32_t s_requested;
static uint32_t s_succeeded;
static uint32_t s_failed;
/* Keep transport accounting explicit: a scheduled sample can be skipped
 * before esp_now_send() when the per-peer queue is full. */
static uint32_t s_scheduled;
static uint32_t s_attempted;
static uint32_t s_accepted;
static uint32_t s_callback_success;
static uint32_t s_callback_failure;
static uint32_t s_send_error;
static uint32_t s_skipped_backpressure;
static uint32_t s_scheduled_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_attempted_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_accepted_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_callback_success_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_callback_failure_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_send_error_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_skipped_backpressure_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_requested_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_succeeded_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_failed_per_rx[WIEVAC_RX_COUNT];
static uint32_t s_outstanding_per_rx[WIEVAC_RX_COUNT];
static int64_t s_last_attempt_us[WIEVAC_RX_COUNT];
static int64_t s_last_success_us[WIEVAC_RX_COUNT];
static int64_t s_paired_since_us[WIEVAC_RX_COUNT];
static int64_t s_last_backpressure_log_us[WIEVAC_RX_COUNT];
static bool s_expected_rx_mac_configured[WIEVAC_RX_COUNT];
static bool s_expected_rx_mac_invalid[WIEVAC_RX_COUNT];
static uint8_t s_expected_rx_macs[WIEVAC_RX_COUNT][ESP_NOW_ETH_ALEN];

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

static uint32_t crc32_v4(const uint8_t *bytes, size_t length)
{
    uint32_t crc = UINT32_C(0xffffffff);
    for (size_t i = 0; i < length; ++i) {
        crc ^= bytes[i];
        for (unsigned bit = 0; bit < 8U; ++bit) {
            crc = (crc >> 1) ^ ((crc & 1U) ? UINT32_C(0xedb88320) : 0U);
        }
    }
    return ~crc;
}

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

static bool expected_rx_mac_allows(size_t slot, const uint8_t source_mac[ESP_NOW_ETH_ALEN])
{
    if (slot >= WIEVAC_RX_COUNT || s_expected_rx_mac_invalid[slot]) {
        return false;
    }
    return !s_expected_rx_mac_configured[slot] ||
           memcmp(s_expected_rx_macs[slot], source_mac, ESP_NOW_ETH_ALEN) == 0;
}

static void encode_header(uint8_t packet[V4_HEADER_SIZE], uint8_t type,
                          uint32_t rx_id, uint32_t sequence, uint64_t timestamp_us)
{
    memset(packet, 0, V4_HEADER_SIZE);
    put_u32(packet + 0, V4_MAGIC);
    packet[4] = V4_VERSION;
    packet[5] = type;
    put_u16(packet + 6, V4_HEADER_SIZE);
    put_u16(packet + 8, 0U);
    put_u16(packet + 10, V4_SCHEMA);
    /* Control discovery is corridor-scoped; data/control replies are link-scoped. */
    put_u32(packet + 12, rx_id == 0U ? 0U : rx_id);
    put_u32(packet + 16, WIEVAC_TX_ID);
    put_u32(packet + 20, rx_id);
    put_u32(packet + 24, s_boot_id);
    put_u32(packet + 28, 0U);
    put_u32(packet + 32, sequence);
    put_u64(packet + 36, timestamp_us);
    put_u32(packet + V4_CRC_OFFSET, crc32_v4(packet, V4_CRC_OFFSET));
}

static bool decode_header(const uint8_t *packet, size_t length, v4_header_t *header)
{
    if (packet == NULL || header == NULL || length != V4_HEADER_SIZE ||
        get_u32(packet) != V4_MAGIC || packet[4] != V4_VERSION ||
        get_u16(packet + 6) != V4_HEADER_SIZE || get_u16(packet + 8) != 0U ||
        get_u16(packet + 10) != V4_SCHEMA ||
        get_u32(packet + V4_CRC_OFFSET) != crc32_v4(packet, V4_CRC_OFFSET)) {
        return false;
    }
    header->type = packet[5];
    header->payload_length = 0U;
    header->schema = get_u16(packet + 10);
    header->link_id = get_u32(packet + 12);
    header->tx_id = get_u32(packet + 16);
    header->rx_id = get_u32(packet + 20);
    header->boot_id = get_u32(packet + 24);
    header->gain_epoch = get_u32(packet + 28);
    header->sequence = get_u32(packet + 32);
    header->timestamp_us = get_u64(packet + 36);
    return true;
}

static esp_err_t add_peer(const uint8_t mac[ESP_NOW_ETH_ALEN])
{
    if (esp_now_is_peer_exist(mac)) {
        return ESP_OK;
    }
    esp_now_peer_info_t peer = {0};
    memcpy(peer.peer_addr, mac, ESP_NOW_ETH_ALEN);
    peer.channel = WIEVAC_WIFI_CHANNEL;
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;
    ESP_RETURN_ON_ERROR(esp_now_add_peer(&peer), TAG, "add peer");
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

static void send_callback(const uint8_t *mac, esp_now_send_status_t status)
{
    const int64_t callback_us = esp_timer_get_time();
    portENTER_CRITICAL(&s_mux);
    if (status == ESP_NOW_SEND_SUCCESS) {
        ++s_succeeded;
        ++s_callback_success;
    } else {
        ++s_failed;
        ++s_callback_failure;
    }
    if (mac != NULL) {
        for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
            if (memcmp(mac, s_rx_macs[slot], ESP_NOW_ETH_ALEN) == 0) {
                if (s_outstanding_per_rx[slot] > 0U) {
                    --s_outstanding_per_rx[slot];
                }
                if (status == ESP_NOW_SEND_SUCCESS) {
                    ++s_succeeded_per_rx[slot];
                    ++s_callback_success_per_rx[slot];
                    /* A late callback from a peer that has already timed
                     * out must not revive that peer's liveness state. */
                    if ((s_paired_mask & (1U << slot)) != 0U) {
                        s_last_success_us[slot] = callback_us;
                    }
                } else {
                    ++s_failed_per_rx[slot];
                    ++s_callback_failure_per_rx[slot];
                }
                break;
            }
        }
    }
    portEXIT_CRITICAL(&s_mux);
}

static bool rx_id_slot(uint32_t rx_id, size_t *slot)
{
    if (rx_id < 1U || rx_id > WIEVAC_RX_COUNT) {
        return false;
    }
    *slot = (size_t)(rx_id - 1U);
    return true;
}

static void receive_callback(const esp_now_recv_info_t *info, const uint8_t *data, int length)
{
    if (info == NULL || info->src_addr == NULL || data == NULL || length != V4_HEADER_SIZE) {
        return;
    }
    v4_header_t header;
    if (!decode_header(data, (size_t)length, &header) ||
        header.type != V4_MSG_PAIR_REPLY || header.link_id != header.rx_id ||
        header.tx_id != WIEVAC_TX_ID || header.boot_id == 0U ||
        header.sequence == 0U || header.timestamp_us == 0U) {
        return;
    }
    ESP_LOGI(TAG, "pair_reply_rx rx_id=%" PRIu32 " source=" MACSTR,
             header.rx_id, MAC2STR(info->src_addr));
    size_t slot;
    if (!rx_id_slot(header.rx_id, &slot)) {
        return;
    }
    if (!expected_rx_mac_allows(slot, info->src_addr)) {
        return;
    }
    portENTER_CRITICAL(&s_mux);
    memcpy(s_reply_macs[slot], info->src_addr, ESP_NOW_ETH_ALEN);
    s_reply_mask |= (uint8_t)(1U << slot);
    portEXIT_CRITICAL(&s_mux);
}

static void send_packet(uint8_t type, const uint8_t destination[ESP_NOW_ETH_ALEN],
                        uint32_t rx_id, uint32_t sequence, uint64_t timestamp_us)
{
    uint8_t packet[V4_HEADER_SIZE];
    encode_header(packet, type, rx_id, sequence, timestamp_us);
    size_t slot;
    const bool has_rx_slot = rx_id_slot(rx_id, &slot);
    const bool is_measurement = has_rx_slot && type == V4_MSG_MEASUREMENT;
    const int64_t attempt_us = esp_timer_get_time();
    bool report_backpressure = false;
    portENTER_CRITICAL(&s_mux);
    if (is_measurement) {
        ++s_scheduled;
        ++s_scheduled_per_rx[slot];
    }
    if (has_rx_slot && s_outstanding_per_rx[slot] >= WIEVAC_MAX_OUTSTANDING_PER_RX) {
        ++s_skipped_backpressure;
        if (is_measurement) {
            ++s_skipped_backpressure_per_rx[slot];
        }
        if (attempt_us - s_last_backpressure_log_us[slot] >= 1000000) {
            s_last_backpressure_log_us[slot] = attempt_us;
            report_backpressure = true;
        }
        portEXIT_CRITICAL(&s_mux);
        if (report_backpressure) {
            ESP_LOGW(TAG, "send type=%u rx=%" PRIu32 " skipped=backpressure", type, rx_id);
        }
        return;
    }
    ++s_attempted;
    ++s_requested;
    if (has_rx_slot) {
        if (is_measurement) {
            ++s_attempted_per_rx[slot];
        }
        ++s_requested_per_rx[slot];
        ++s_outstanding_per_rx[slot];
        s_last_attempt_us[slot] = attempt_us;
    }
    portEXIT_CRITICAL(&s_mux);
    const esp_err_t err = esp_now_send(destination, packet, sizeof(packet));
    if (err != ESP_OK) {
        portENTER_CRITICAL(&s_mux);
        ++s_send_error;
        ++s_failed;
        if (has_rx_slot) {
            if (s_outstanding_per_rx[slot] > 0U) {
                --s_outstanding_per_rx[slot];
            }
            if (is_measurement) {
                ++s_send_error_per_rx[slot];
            }
            ++s_failed_per_rx[slot];
        }
        portEXIT_CRITICAL(&s_mux);
        ESP_LOGW(TAG, "send type=%u rx=%" PRIu32 " failed: %s", type, rx_id,
                 esp_err_to_name(err));
    } else {
        portENTER_CRITICAL(&s_mux);
        ++s_accepted;
        if (is_measurement) {
            ++s_accepted_per_rx[slot];
        }
        portEXIT_CRITICAL(&s_mux);
    }
}

static void expire_unresponsive_peers(int64_t now_us)
{
    const int64_t timeout_us = (int64_t)WIEVAC_PEER_TIMEOUT_MS * 1000;
    uint8_t expired_mask = 0U;
    uint32_t attempts[WIEVAC_RX_COUNT] = {0};
    uint32_t successes[WIEVAC_RX_COUNT] = {0};
    uint32_t failures[WIEVAC_RX_COUNT] = {0};
    uint32_t skipped[WIEVAC_RX_COUNT] = {0};
    int64_t last_success[WIEVAC_RX_COUNT] = {0};
    portENTER_CRITICAL(&s_mux);
    for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
        if ((s_paired_mask & (1U << slot)) == 0U) {
            continue;
        }
        const int64_t last_delivery = s_last_success_us[slot];
        const int64_t liveness_start = last_delivery > 0 ? last_delivery : s_paired_since_us[slot];
        if (liveness_start > 0 && now_us - liveness_start > timeout_us) {
            s_paired_mask &= (uint8_t)~(1U << slot);
            s_outstanding_per_rx[slot] = 0U;
            expired_mask |= (uint8_t)(1U << slot);
            attempts[slot] = s_attempted_per_rx[slot];
            successes[slot] = s_callback_success_per_rx[slot];
            failures[slot] = s_callback_failure_per_rx[slot] + s_send_error_per_rx[slot];
            skipped[slot] = s_skipped_backpressure_per_rx[slot];
            last_success[slot] = last_delivery;
        }
    }
    portEXIT_CRITICAL(&s_mux);
    for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
        if ((expired_mask & (1U << slot)) != 0U) {
            const int64_t since_success = last_success[slot] > 0
                                              ? now_us - last_success[slot]
                                              : -1;
            ESP_LOGW(TAG, "RX-%u liveness timeout; clearing paired state "
                          "since_success_us=%" PRId64 " attempts=%" PRIu32
                          " success=%" PRIu32 " failure=%" PRIu32
                          " skipped_backpressure=%" PRIu32,
                     (unsigned)(slot + 1U), since_success, attempts[slot],
                     successes[slot], failures[slot], skipped[slot]);
        }
    }
}

static void accept_pair_replies(void)
{
    uint8_t pending_mask;
    uint8_t candidates[WIEVAC_RX_COUNT][ESP_NOW_ETH_ALEN];
    portENTER_CRITICAL(&s_mux);
    pending_mask = s_reply_mask;
    s_reply_mask = 0U;
    memcpy(candidates, s_reply_macs, sizeof(candidates));
    portEXIT_CRITICAL(&s_mux);
    for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
        if ((pending_mask & (1U << slot)) == 0U) {
            continue;
        }
        if (!expected_rx_mac_allows(slot, candidates[slot])) {
            ESP_LOGW(TAG, "RX-%u pair reply rejected by MAC allow-list", (unsigned)(slot + 1U));
            continue;
        }
        if (add_peer(candidates[slot]) != ESP_OK) {
            ESP_LOGW(TAG, "RX-%u pair reply peer rejected", (unsigned)(slot + 1U));
            continue;
        }
        const int64_t paired_us = esp_timer_get_time();
        portENTER_CRITICAL(&s_mux);
        const bool was_paired = (s_paired_mask & (1U << slot)) != 0U;
        const bool mac_changed = was_paired &&
                                 memcmp(s_rx_macs[slot], candidates[slot], ESP_NOW_ETH_ALEN) != 0;
        memcpy(s_rx_macs[slot], candidates[slot], ESP_NOW_ETH_ALEN);
        s_paired_mask |= (uint8_t)(1U << slot);
        /* Pair replies establish identity only. They are not measurement
         * delivery, so they must never refresh last-success liveness. */
        if (!was_paired || mac_changed) {
            s_outstanding_per_rx[slot] = 0U;
            s_paired_since_us[slot] = paired_us;
            s_last_success_us[slot] = 0;
        } else if (s_paired_since_us[slot] <= 0) {
            s_paired_since_us[slot] = paired_us;
        }
        portEXIT_CRITICAL(&s_mux);
        ESP_LOGI(TAG, "paired to RX-%u " MACSTR, (unsigned)(slot + 1U),
                 MAC2STR(candidates[slot]));
    }
}

static void wifi_init(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_create_default_wifi_sta() == NULL ? ESP_FAIL : ESP_OK);
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_protocol(WIFI_IF_STA,
                                          WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G |
                                              WIFI_PROTOCOL_11N));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, WIEVAC_WIFI_BANDWIDTH));
    ESP_ERROR_CHECK(esp_wifi_set_channel(WIEVAC_WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE));
}

static void log_status(void)
{
    uint32_t requested;
    uint32_t succeeded;
    uint32_t failed;
    uint32_t scheduled;
    uint32_t attempted;
    uint32_t accepted;
    uint32_t callback_success;
    uint32_t callback_failure;
    uint32_t send_error;
    uint32_t skipped_backpressure;
    uint8_t paired_mask;
    portENTER_CRITICAL(&s_mux);
    requested = s_requested;
    succeeded = s_succeeded;
    failed = s_failed;
    scheduled = s_scheduled;
    attempted = s_attempted;
    accepted = s_accepted;
    callback_success = s_callback_success;
    callback_failure = s_callback_failure;
    send_error = s_send_error;
    skipped_backpressure = s_skipped_backpressure;
    paired_mask = s_paired_mask;
    portEXIT_CRITICAL(&s_mux);
    ESP_LOGI(TAG, "paired_mask=0x%02x scheduled=%" PRIu32
             " attempted=%" PRIu32 " accepted=%" PRIu32
             " callback_success=%" PRIu32 " callback_failure=%" PRIu32
             " send_error=%" PRIu32 " skipped_backpressure=%" PRIu32
             " requested=%" PRIu32 " ok=%" PRIu32 " fail=%" PRIu32
             " tx_sequence=%" PRIu32,
             paired_mask, scheduled, attempted, accepted, callback_success,
             callback_failure, send_error, skipped_backpressure, requested,
             succeeded, failed, s_measurement_sequence);
    for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
        uint32_t scheduled_rx;
        uint32_t attempted_rx;
        uint32_t accepted_rx;
        uint32_t callback_success_rx;
        uint32_t callback_failure_rx;
        uint32_t send_error_rx;
        uint32_t skipped_rx;
        portENTER_CRITICAL(&s_mux);
        scheduled_rx = s_scheduled_per_rx[slot];
        attempted_rx = s_attempted_per_rx[slot];
        accepted_rx = s_accepted_per_rx[slot];
        callback_success_rx = s_callback_success_per_rx[slot];
        callback_failure_rx = s_callback_failure_per_rx[slot];
        send_error_rx = s_send_error_per_rx[slot];
        skipped_rx = s_skipped_backpressure_per_rx[slot];
        portEXIT_CRITICAL(&s_mux);
        /* Include skipped samples in the denominator; otherwise a saturated
         * dead peer can report a misleading 99.x% delivery rate. */
        const float delivery_rate = scheduled_rx == 0U
                                        ? 0.0f
                                        : (100.0f * (float)callback_success_rx / (float)scheduled_rx);
        ESP_LOGI(TAG, "rx-%u scheduled=%" PRIu32 " attempted=%" PRIu32
                 " accepted=%" PRIu32 " callback_success=%" PRIu32
                 " callback_failure=%" PRIu32 " send_error=%" PRIu32
                 " skipped_backpressure=%" PRIu32 " delivery_rate=%.1f%%",
                 (unsigned)(slot + 1U), scheduled_rx, attempted_rx, accepted_rx,
                 callback_success_rx, callback_failure_rx, send_error_rx,
                 skipped_rx, (double)delivery_rate);
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
    const char *expected_rx_text[WIEVAC_RX_COUNT] = {
        CONFIG_WIEVAC_RX1_MAC, CONFIG_WIEVAC_RX2_MAC,
    };
    for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
        if (expected_rx_text[slot][0] == '\0') {
            ESP_LOGW(TAG, "pairing_auth=trusted_closed_lab rx-%u_mac=unset", (unsigned)(slot + 1U));
        } else if (parse_mac_text(expected_rx_text[slot], s_expected_rx_macs[slot])) {
            s_expected_rx_mac_configured[slot] = true;
        } else {
            s_expected_rx_mac_invalid[slot] = true;
            ESP_LOGE(TAG, "invalid RX-%u MAC allow-list value; pairing disabled for slot",
                     (unsigned)(slot + 1U));
        }
    }
    s_boot_id = esp_random();
    if (s_boot_id == 0U) {
        s_boot_id = 1U;
    }
    wifi_init();
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_send_cb(send_callback));
    ESP_ERROR_CHECK(esp_now_register_recv_cb(receive_callback));
    ESP_ERROR_CHECK(add_peer(BROADCAST_MAC));

    ESP_LOGI(TAG, "V4 TX boot=%" PRIu32 " link=%" PRIu32
             " channel=%u bandwidth=HT20 mcs=0 phy_rate=%u measurement_hz=%u",
             s_boot_id, WIEVAC_LINK_ID, WIEVAC_WIFI_CHANNEL,
             (unsigned)WIEVAC_ESPNOW_RATE, WIEVAC_MEASUREMENT_HZ);
    TickType_t wake = xTaskGetTickCount();
    int64_t last_hello_us = 0;
    int64_t last_log_us = 0;
    const TickType_t period = (configTICK_RATE_HZ / WIEVAC_MEASUREMENT_HZ) > 0U
                                  ? (configTICK_RATE_HZ / WIEVAC_MEASUREMENT_HZ) : 1U;
    for (;;) {
        const int64_t now_us = esp_timer_get_time();
        accept_pair_replies();
        expire_unresponsive_peers(now_us);
        if (now_us - last_hello_us >= (int64_t)WIEVAC_HELLO_INTERVAL_MS * 1000) {
            send_packet(V4_MSG_HELLO, BROADCAST_MAC, 0U, ++s_control_sequence,
                        (uint64_t)now_us);
            last_hello_us = now_us;
        }

        /* Increment sequence and capture timestamp once; both RX links get this exact pair. */
        uint8_t paired_mask;
        uint8_t destinations[WIEVAC_RX_COUNT][ESP_NOW_ETH_ALEN];
        portENTER_CRITICAL(&s_mux);
        paired_mask = s_paired_mask;
        memcpy(destinations, s_rx_macs, sizeof(destinations));
        bool backpressure = false;
        for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
            if ((paired_mask & (1U << slot)) != 0U &&
                s_outstanding_per_rx[slot] >= WIEVAC_MAX_OUTSTANDING_PER_RX) {
                backpressure = true;
            }
        }
        if (backpressure) {
            for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
                if ((paired_mask & (1U << slot)) != 0U) {
                    /* Count the skipped per-RX measurement exactly as
                     * send_packet() would have counted a selective skip. */
                    ++s_scheduled;
                    ++s_scheduled_per_rx[slot];
                    ++s_skipped_backpressure;
                    ++s_skipped_backpressure_per_rx[slot];
                }
            }
        }
        portEXIT_CRITICAL(&s_mux);
        if (paired_mask != 0U) {
            if (backpressure) {
                /* Keep the shared sequence contiguous when one RX cannot
                 * accept the measurement; do not selectively skip a link. */
                if (now_us - last_log_us >= (int64_t)WIEVAC_LOG_INTERVAL_MS * 1000) {
                    ESP_LOGW(TAG, "measurement skipped=backpressure paired_mask=0x%02x", paired_mask);
                }
            } else {
                const uint32_t sequence = ++s_measurement_sequence;
                const uint64_t timestamp_us = (uint64_t)esp_timer_get_time();
                for (size_t slot = 0; slot < WIEVAC_RX_COUNT; ++slot) {
                    if ((paired_mask & (1U << slot)) != 0U) {
                        send_packet(V4_MSG_MEASUREMENT, destinations[slot], (uint32_t)(slot + 1U),
                                    sequence, timestamp_us);
                    }
                }
            }
        }
        if (now_us - last_log_us >= (int64_t)WIEVAC_LOG_INTERVAL_MS * 1000) {
            log_status();
            last_log_us = now_us;
        }
        if (xTaskDelayUntil(&wake, period) == pdFALSE) {
            wake = xTaskGetTickCount();
        }
    }
}
