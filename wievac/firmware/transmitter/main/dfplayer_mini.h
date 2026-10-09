#ifndef DFPLAYER_MINI_H
#define DFPLAYER_MINI_H

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"
#include "driver/gpio.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Cấu hình chân UART mặc định cho DFPlayer Mini trên ESP32-S3 */
#define DFPLAYER_DEFAULT_TX_PIN   GPIO_NUM_1  /* ESP32 TX -> Nối qua trở 1k tới RX DFPlayer */
#define DFPLAYER_DEFAULT_RX_PIN   GPIO_NUM_2  /* ESP32 RX <- Nối tới TX DFPlayer */
#define DFPLAYER_DEFAULT_VOLUME   25          /* Mức âm lượng: 0 - 30 */

/* Khởi tạo giao tiếp UART với module DFPlayer Mini */
esp_err_t dfplayer_init(gpio_num_t tx_pin, gpio_num_t rx_pin);

/* Cài đặt mức âm lượng (0 - 30) */
void dfplayer_set_volume(uint8_t volume);

/* Phát file âm thanh trong thư mục /MP3/ (0001.mp3 -> 9999.mp3) */
void dfplayer_play_mp3(uint16_t track_num);

/* Dừng phát âm thanh */
void dfplayer_stop(void);

/* Thực thi mã lệnh điều hướng WiEvac và tự động chọn bài MP3 tương ứng:
 *   10: START_EVAC    -> Track 3 (Đi thẳng)
 *   11: GO_STRAIGHT   -> Track 3 (Đi thẳng)
 *   12: TURN_LEFT     -> Track 1 (Rẽ trái)
 *   13: TURN_RIGHT    -> Track 2 (Rẽ phải)
 *   14: TURN_BACK / GO_UPSTAIRS -> Track 4 (Quay lại / lên cầu thang)
 *   15: GO_DOWNSTAIRS -> Track 4 (cùng clip cầu thang; thẻ chỉ có 0001–0006)
 *   16: SAFE_EXIT     -> Track 6 (Cửa thoát hiểm)
 *   17: STOP_DANGER   -> Track 5 (Dừng lại / Nguy hiểm)
 *   18: WAIT         -> Giữ bài đang phát
 *   19: CLEAR/STANDBY -> Dừng phát âm thanh
 */
void dfplayer_execute_command(uint8_t command);

/* Kiểm tra chu kỳ nhắc lại âm thanh (gọi trong vòng lặp hoặc timer task) */
void dfplayer_poll(void);

#ifdef __cplusplus
}
#endif

#endif /* DFPLAYER_MINI_H */
