#include "dfplayer_mini.h"
#include <string.h>
#include "driver/uart.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "DFPLAYER";

#define DFPLAYER_UART_PORT        UART_NUM_1
#define DFPLAYER_REPEAT_INTERVAL  7000000ULL /* 7 giây nhắc lại câu lệnh */

static bool s_dfplayer_ready = false;
static uint16_t s_target_track = 0;
static uint16_t s_current_track = 0;
static uint64_t s_last_play_us = 0;

static void dfplayer_send_cmd(uint8_t cmd, uint16_t param)
{
    if (!s_dfplayer_ready) {
        return;
    }
    uint8_t buffer[10];
    uint8_t high = (uint8_t)(param >> 8);
    uint8_t low  = (uint8_t)(param & 0xFF);
    int16_t sum  = 0xFF + 0x06 + cmd + 0x00 + high + low;
    uint16_t checksum = (uint16_t)(-sum);

    buffer[0] = 0x7E; /* Start byte */
    buffer[1] = 0xFF; /* Version */
    buffer[2] = 0x06; /* Length */
    buffer[3] = cmd;  /* Command code */
    buffer[4] = 0x00; /* No ACK */
    buffer[5] = high;
    buffer[6] = low;
    buffer[7] = (uint8_t)(checksum >> 8);
    buffer[8] = (uint8_t)(checksum & 0xFF);
    buffer[9] = 0xEF; /* End byte */

    uart_write_bytes(DFPLAYER_UART_PORT, (const char *)buffer, sizeof(buffer));
}

void dfplayer_set_volume(uint8_t volume)
{
    if (volume > 30) volume = 30;
    dfplayer_send_cmd(0x06, (uint16_t)volume);
    ESP_LOGI(TAG, "Da dat am luong DFPlayer: %u/30", (unsigned)volume);
}

void dfplayer_play_mp3(uint16_t track_num)
{
    if (track_num == 0) return;
    /* Lenh 0x12: Phat file trong thu muc /MP3/ (0001.mp3 -> 9999.mp3) */
    dfplayer_send_cmd(0x12, track_num);
    ESP_LOGI(TAG, "Phat am thanh: /MP3/%04u.mp3", (unsigned)track_num);
}

void dfplayer_stop(void)
{
    dfplayer_send_cmd(0x16, 0x0000);
    s_target_track = 0;
    s_current_track = 0;
    ESP_LOGI(TAG, "Dung phat am thanh.");
}

void dfplayer_execute_command(uint8_t command)
{
    uint16_t track = 0;
    switch (command) {
        case 10: /* START_EVAC */
        case 11: /* GO_STRAIGHT */
            track = 3; /* 0003.mp3: Di thang theo huong nay */
            break;
        case 12: /* TURN_LEFT */
            track = 1; /* 0001.mp3: Re trai */
            break;
        case 13: /* TURN_RIGHT */
            track = 2; /* 0002.mp3: Re phai */
            break;
        case 14: /* TURN_BACK / GO_UPSTAIRS */
        case 15: /* GO_DOWNSTAIRS — card has no separate downstairs file */
            track = 4; /* 0004.mp3: Quay lai / cau thang */
            break;
        case 16: /* SAFE_EXIT */
            track = 6; /* 0006.mp3: Cua thoat hiem an toan o day */
            break;
        case 17: /* STOP_DANGER */
            track = 5; /* 0005.mp3: Khu vuc nguy hiem, dung lai va quay dau */
            break;
        case 18: /* WAIT — keep the current announcement; 19 is the stop command */
            return;
        case 19: /* CLEAR / STANDBY */
        default:
            track = 0;
            break;
    }

    if (track == 0) {
        dfplayer_stop();
    } else {
        s_target_track = track;
        if (s_target_track != s_current_track) {
            s_current_track = s_target_track;
            s_last_play_us = (uint64_t)esp_timer_get_time();
            dfplayer_play_mp3(s_current_track);
        }
    }
}

void dfplayer_poll(void)
{
    if (!s_dfplayer_ready || s_current_track == 0) {
        return;
    }
    const uint64_t now_us = (uint64_t)esp_timer_get_time();
    if (now_us - s_last_play_us >= DFPLAYER_REPEAT_INTERVAL) {
        s_last_play_us = now_us;
        dfplayer_play_mp3(s_current_track);
    }
}

static void dfplayer_task(void *arg)
{
    for (;;) {
        dfplayer_poll();
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

esp_err_t dfplayer_init(gpio_num_t tx_pin, gpio_num_t rx_pin)
{
    const uart_config_t uart_config = {
        .baud_rate = 9600,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };

    esp_err_t err = uart_param_config(DFPLAYER_UART_PORT, &uart_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Loi uart_param_config: %s", esp_err_to_name(err));
        return err;
    }

    err = uart_set_pin(DFPLAYER_UART_PORT, tx_pin, rx_pin, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Loi uart_set_pin: %s", esp_err_to_name(err));
        return err;
    }

    err = uart_driver_install(DFPLAYER_UART_PORT, 256, 0, 0, NULL, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Loi uart_driver_install: %s", esp_err_to_name(err));
        return err;
    }

    s_dfplayer_ready = true;
    ESP_LOGI(TAG, "Khoi tao DFPlayer Mini thanh cong tren UART1 (TX=%d, RX=%d, 9600bps)", (int)tx_pin, (int)rx_pin);

    /* Doi 300ms de module DFPlayer Mini on dinh nguon */
    vTaskDelay(pdMS_TO_TICKS(300));
    dfplayer_set_volume(DFPLAYER_DEFAULT_VOLUME);

    /* Tao task tu dong nhac lai am thanh chu ky */
    xTaskCreate(dfplayer_task, "dfplayer_task", 2048, NULL, 2, NULL);

    return ESP_OK;
}
