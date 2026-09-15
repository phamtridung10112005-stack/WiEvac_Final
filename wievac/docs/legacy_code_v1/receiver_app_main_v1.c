/*
 * SPDX-FileCopyrightText: 2025-2026 Espressif Systems (Shanghai) CO LTD
 * SPDX-License-Identifier: Apache-2.0
 * * EDGE DSP NODE - WIEVAC PROJECT (BẢN CHUYÊN NGHIỆP EDGE-AI V3)
 * Xử lý tín hiệu CSI: Lọc Trung Vị, Nền Thích Nghi Tự Động (Toán học thuần túy), TD-CSI Delta.
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>         
#include "esp_timer.h"    
#include "lwip/sockets.h" 
#include "esp_event.h"    
#include "nvs_flash.h"
#include "esp_mac.h"
#include "rom/ets_sys.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_csi_gain_ctrl.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

// ==============================================================================
// [KHỐI 1] CẤU HÌNH PHẦN CỨNG & MẠNG
// ==============================================================================
#define FAIL_SAFE_PIN 4             

uint8_t g_auto_node_id = 0;         
bool g_is_standalone_mode = true;   
static int g_udp_socket = -1;
static struct sockaddr_in g_pi_addr;
static const char *TAG = "WIEVAC_EDGE";

#define EDGE_UDP_PORT       8889           // Cổng nhận lệnh từ Pi 5
#define CMD_FORCE_CALIBRATE 0xFF           // Mã lệnh Reset nền

volatile bool g_force_calibrate = true;    // Luôn ép chụp nền lúc vừa khởi động

// Hộp thư Queue để chống nghẽn Task Wi-Fi
static QueueHandle_t udp_queue;

// -- CẤU HÌNH WI-FI & PI 5 --
#define PI_GATEWAY_IP   "10.42.0.1"    
#define PI_UDP_PORT     8888           
#define PI_WIFI_SSID    "WiEvac_Pi5"   
#define PI_WIFI_PASS    "12345678"     
#define CONFIG_LESS_INTERFERENCE_CHANNEL 11

#define CONFIG_WIFI_BANDWIDTH    WIFI_BW_HT20      
#define CONFIG_ESP_NOW_PHYMODE   WIFI_PHY_MODE_HT20   
#define CONFIG_ESP_NOW_RATE      WIFI_PHY_RATE_MCS0_LGI
#define CONFIG_FORCE_GAIN        0  
#define CONFIG_GAIN_CONTROL      1

// ==============================================================================
// [KHỐI 2] CẤU TRÚC DỮ LIỆU ĐÓNG GÓI (BỘ 3 KIM CƯƠNG - 17 BYTES)
// ==============================================================================
typedef struct __attribute__((packed)) {
    uint8_t  node_id;       
    float    delta_amp;     // Biên độ xáo trộn (Đã triệt tiêu nền tường)
    float    std_dev;       // Gia tốc rung lắc của Delta
    float    sad_value;     // Độ lởm chởm của Delta (Phân biệt người/vật)
    uint32_t timestamp;     
} csi_feature_packet_t;


// ==============================================================================
// [KHỐI 3] TASK CHUYÊN TRÁCH GỬI VÀ NHẬN UDP (GIAO TIẾP 2 CHIỀU)
// ==============================================================================
static void udp_sender_task(void *pvParameters) {
    csi_feature_packet_t packet;
    while (1) {
        // Đứng chờ lấy data từ Hộp thư (Block vô thời hạn nếu không có data)
        if (xQueueReceive(udp_queue, &packet, portMAX_DELAY) == pdTRUE) {
            if (!g_is_standalone_mode && g_udp_socket >= 0) {
                sendto(g_udp_socket, &packet, sizeof(packet), 0, (struct sockaddr *)&g_pi_addr, sizeof(g_pi_addr));
            }
        }
    }
}

static void udp_receiver_task(void *pvParameters) {
    char rx_buffer[16];
    int sock = -1;

    while (1) {
        if (g_is_standalone_mode) {
            if (sock != -1) { close(sock); sock = -1; }
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        if (sock == -1) {
            sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
            if (sock >= 0) {
                struct sockaddr_in dest_addr;
                dest_addr.sin_addr.s_addr = htonl(INADDR_ANY);
                dest_addr.sin_family = AF_INET;
                dest_addr.sin_port = htons(EDGE_UDP_PORT);
                
                if (bind(sock, (struct sockaddr *)&dest_addr, sizeof(dest_addr)) < 0) {
                    ESP_LOGE(TAG, "Lỗi bind UDP port %d", EDGE_UDP_PORT);
                    close(sock);
                    sock = -1;
                    vTaskDelay(pdMS_TO_TICKS(1000));
                    continue;
                }
                
                // Cài Timeout để recvfrom không bị block vĩnh viễn (giúp thoát vòng lặp khi rớt mạng)
                struct timeval tv;
                tv.tv_sec = 2;
                tv.tv_usec = 0;
                setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
                
                ESP_LOGI(TAG, "UDP Listener đang lắng nghe lệnh Pi 5 ở cổng %d", EDGE_UDP_PORT);
            }
        }

        if (sock >= 0) {
            struct sockaddr_in source_addr;
            socklen_t socklen = sizeof(source_addr);
            int len = recvfrom(sock, rx_buffer, sizeof(rx_buffer) - 1, 0, (struct sockaddr *)&source_addr, &socklen);
            
            if (len > 0) {
                if ((uint8_t)rx_buffer[0] == CMD_FORCE_CALIBRATE) {
                    ESP_LOGW(TAG, "=======================================");
                    ESP_LOGW(TAG, "NHẬN LỆNH FORCE CALIBRATE TỪ PI 5!");
                    ESP_LOGW(TAG, "Đang Reset Nền Toán Học...");
                    ESP_LOGW(TAG, "=======================================");
                    g_force_calibrate = true;
                }
            }
        }
    }
}

static void udp_client_init(void) {
    g_udp_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (g_udp_socket >= 0) {
        memset(&g_pi_addr, 0, sizeof(g_pi_addr));
        g_pi_addr.sin_addr.s_addr = inet_addr(PI_GATEWAY_IP);
        g_pi_addr.sin_family = AF_INET;
        g_pi_addr.sin_port = htons(PI_UDP_PORT);
    }
}

// ==============================================================================
// [KHỐI 4] QUẢN LÝ KẾT NỐI (AUTO RECOVERY)
// ==============================================================================
static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    }
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_CONNECTED) {
        ESP_LOGI(TAG, "Wi-Fi Connected! Chờ cấp IP...");
    } 
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "Đã nhận IP: " IPSTR, IP2STR(&event->ip_info.ip));
        
        g_is_standalone_mode = false;
        gpio_set_level(FAIL_SAFE_PIN, 0);
        esp_wifi_set_promiscuous(true); 
        esp_wifi_set_csi(true);
        ESP_LOGI(TAG, "CSI Engine Khởi động! Đang truyền Bộ 3 Kim Cương lên Pi 5.");
    }
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        esp_wifi_set_promiscuous(false);
        esp_wifi_set_csi(false);
        g_is_standalone_mode = true;
        ESP_LOGW(TAG, "Mất kết nối Pi 5. Bật Sinh tồn & Đang kết nối lại...");
        esp_wifi_connect();
    }
}

// ==============================================================================
// HÀM BỔ TRỢ: BỘ LỌC TRUNG VỊ (SORTING NETWORK O(1))
// Lấp đầy lỗ hổng hiệu năng, chạy hằng số thời gian không dùng vòng lặp sorting
// ==============================================================================
#define MEDIAN_WINDOW 5
#define SWAP(a,b) { if ((a) > (b)) { float t = (a); (a) = (b); (b) = t; } }
static float get_median(float arr[], int n) {
    float temp[MEDIAN_WINDOW];
    for (int i = 0; i < n; i++) temp[i] = arr[i];
    SWAP(temp[0], temp[1]);
    SWAP(temp[3], temp[4]);
    SWAP(temp[0], temp[3]);
    SWAP(temp[1], temp[4]);
    SWAP(temp[1], temp[2]);
    SWAP(temp[2], temp[3]);
    SWAP(temp[1], temp[2]);
    return temp[2];
}

// ==============================================================================
// [KHỐI 5] LÕI DSP (MEDIAN -> DUAL EMA ĐỘNG -> TD-CSI)
// ==============================================================================
static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info) {
    if (!info || !info->buf) return;

    // Bộ nhớ đệm cho DSP Pipeline
    static float amp_history[64][MEDIAN_WINDOW] = {0};
    static uint8_t hist_idx = 0;
    static float ema_fast[64] = {0};
    static float ema_slow[64] = {0};
    static bool is_init = true;

    // --- CÁC BIẾN CỦA THUẬT TOÁN WELFORD (BẢO VỆ ĐỘ CHÍNH XÁC PHƯƠNG SAI) ---
    static int welford_count = 0;
    static float welford_mean = 0.0f;
    static float welford_M2 = 0.0f;
    static float acc_sad = 0.0f;
    static int s_count = 0;

    // XỬ LÝ LỆNH FORCE CALIBRATE (ĐỒNG BỘ CẢ TOÁN HỌC LẪN PHẦN CỨNG)
    if (g_force_calibrate) {
        is_init = true;
        welford_count = 0;
        welford_mean = 0.0f;
        welford_M2 = 0.0f;
        acc_sad = 0.0f;
        s_count = 0;
        g_force_calibrate = false;
        ESP_LOGI(TAG, "CSI Engine: Đã ép khởi tạo lại toàn bộ Nền Toán Học, AGC và Noise Floor!");
    }

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;
    float compensate_gain = 1.0f;
    static uint8_t agc_gain = 0;
    static int8_t fft_gain = 0;

#if CONFIG_GAIN_CONTROL
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    if (s_count < 100) esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain, fft_gain);
#endif

    int num_subcarriers = info->len / 2; 
    if (num_subcarriers > 64) num_subcarriers = 64;

    float sum_absolute_delta = 0.0f;
    float sum_ema_slow = 0.0f;
    int valid_sc = 0;

    // PASS 1: Cập nhật nền, Tính tổng biên độ và tổng độ biến thiên
    for (int i = 0; i < num_subcarriers; i++) {
        // Gạch bỏ các sóng mang nhiễu viền và tia DC
        if (i < 6 || i > (num_subcarriers - 6) || (i > (num_subcarriers / 2 - 3) && i < (num_subcarriers / 2 + 3))) continue;
        
        int8_t i_real = info->buf[2 * i];
        int8_t q_imag = info->buf[2 * i + 1];
        float raw_amp = sqrtf((float)(i_real * i_real + q_imag * q_imag)) * compensate_gain;
        
        // 1. CHÉM BÓNG MA (LỌC TRUNG VỊ O(1)) & VÁ LỖI ZERO-PADDING
        float med_amp;
        if (is_init) {
            for (int k = 0; k < MEDIAN_WINDOW; k++) amp_history[i][k] = raw_amp;
            med_amp = raw_amp;
            ema_fast[i] = raw_amp;
            ema_slow[i] = raw_amp;
        } else {
            amp_history[i][hist_idx] = raw_amp;
            med_amp = get_median(amp_history[i], MEDIAN_WINDOW);
        }

        // =================================================================
        // 2. SỐNG CHUNG VỚI LŨ (TRƯỢT NỀN TOÁN HỌC)
        // =================================================================
        float alpha_fast = 0.8f;
        ema_fast[i] = alpha_fast * med_amp + (1.0f - alpha_fast) * ema_fast[i];
        
        // Công thức Alpha động cho ema_slow (giữ nguyên độ lì của nền)
        float fluctuation = fabsf(med_amp - ema_fast[i]) / (ema_fast[i] + 1.0f);
        float dynamic_alpha = 0.005f / (1.0f + fluctuation * fluctuation * 20.0f);
        ema_slow[i] = dynamic_alpha * med_amp + (1.0f - dynamic_alpha) * ema_slow[i];
        // =================================================================

        sum_absolute_delta += fabsf(ema_fast[i] - ema_slow[i]);
        sum_ema_slow += ema_slow[i];
        valid_sc++;
    }

    if (is_init) is_init = false;
    hist_idx = (hist_idx + 1) % MEDIAN_WINDOW;

    // GHI NHẬN BIẾN THIÊN THỜI GIAN VÀO THUẬT TOÁN WELFORD
    if (valid_sc > 0) {
        // Tỉ lệ nhiễu toàn cục: Tự động triệt tiêu sóng yếu, tôn vinh sóng khỏe
        float current_mean_delta = sum_absolute_delta / (sum_ema_slow + 1.0f);
        
        // PASS 2: Tính SAD (độ lởm chởm) chuẩn hóa theo biên độ trung bình của gói tin
        float mean_amp = sum_ema_slow / valid_sc;
        float current_packet_sad = 0.0f;
        float prev_delta = -1.0f;
        
        for (int i = 0; i < num_subcarriers; i++) {
            if (i < 6 || i > (num_subcarriers - 6) || (i > (num_subcarriers / 2 - 3) && i < (num_subcarriers / 2 + 3))) continue;
            
            float absolute_delta = fabsf(ema_fast[i] - ema_slow[i]);
            // Chuẩn hóa theo Năng lượng trung bình của cả gói tin
            float sc_delta = absolute_delta / (mean_amp + 1.0f);
            
            if (prev_delta >= 0) {
                current_packet_sad += fabsf(sc_delta - prev_delta);
            }
            prev_delta = sc_delta;
        }

        acc_sad += (current_packet_sad / valid_sc);
        
        welford_count++;
        float delta_w = current_mean_delta - welford_mean;
        welford_mean += delta_w / welford_count;
        float delta_w2 = current_mean_delta - welford_mean;
        welford_M2 += delta_w * delta_w2;
    }
    
    s_count++;

    // --- ĐÓNG GÓI 1 GIÂY (100 PACKETS) GỬI LÊN PI 5 ---
    if (welford_count >= 100) {
        // Tính Mean của Delta (từ Welford)
        float final_mean = welford_mean;
        
        // Tính Std của Delta bằng thuật toán Welford (Chống tràn bit và âm số)
        float final_var = welford_M2 / welford_count;
        float final_std = sqrtf(final_var);
        
        // Tính SAD trung bình
        float final_sad = acc_sad / (float)welford_count;

        csi_feature_packet_t packet;
        packet.node_id = g_auto_node_id;  
        packet.delta_amp = final_mean;
        packet.std_dev = final_std;
        packet.sad_value = final_sad;
        packet.timestamp = (uint32_t)(esp_timer_get_time() / 1000); 

        if (g_is_standalone_mode) {
            // [CHẾ ĐỘ SINH TỒN OFFLINE]
            if (packet.delta_amp > 3.0f && packet.std_dev > 1.5f) {
                gpio_set_level(FAIL_SAFE_PIN, 1);
            } else {
                gpio_set_level(FAIL_SAFE_PIN, 0);
            }
        } else {
            // Ném gói tin vào Queue siêu tốc độ
            xQueueSend(udp_queue, &packet, 0);
        }

        // RESET BỘ ĐỆM WELFORD CHO 1 GIÂY TIẾP THEO
        welford_count = 0;
        welford_mean = 0.0f;
        welford_M2 = 0.0f;
        acc_sad = 0.0f;
    }
}

// ==============================================================================
// [KHỐI 6] KHỞI TẠO CÁC HỆ THỐNG
// ==============================================================================
static void wifi_init() {
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_netif_init());
    esp_netif_create_default_wifi_sta();
    
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));
    
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = PI_WIFI_SSID,
            .password = PI_WIFI_PASS,
            .channel = CONFIG_LESS_INTERFERENCE_CHANNEL,
            .pmf_cfg = { .capable = true, .required = false },
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(WIFI_IF_STA, CONFIG_WIFI_BANDWIDTH));
}

static void wifi_esp_now_init(esp_now_peer_info_t peer) {
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_set_pmk((uint8_t *)"pmk1234567890123"));
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
}

static void wifi_csi_init() {
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = true,
        .stbc_htltf2_en    = true,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = false,
        .shift             = false,
    };
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, NULL));
}

void app_main() {
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);
    
    gpio_reset_pin(FAIL_SAFE_PIN);
    gpio_set_direction(FAIL_SAFE_PIN, GPIO_MODE_OUTPUT);
    gpio_set_level(FAIL_SAFE_PIN, 0);

    uint8_t mac_addr[6];
    esp_read_mac(mac_addr, ESP_MAC_WIFI_STA);
    g_auto_node_id = mac_addr[5]; 
    
    ESP_LOGI(TAG, "=======================================");
    ESP_LOGI(TAG, "🚀 EDGE DSP NODE (ID: %d) READY!", g_auto_node_id);
    ESP_LOGI(TAG, "=======================================");

    udp_queue = xQueueCreate(10, sizeof(csi_feature_packet_t));
    if (udp_queue == NULL) {
        ESP_LOGE(TAG, "Lỗi tạo Queue!");
        return;
    }
    xTaskCreate(udp_sender_task, "udp_sender", 4096, NULL, 5, NULL);
    xTaskCreate(udp_receiver_task, "udp_receiver", 4096, NULL, 5, NULL);
    wifi_init();
    esp_now_peer_info_t peer = {
        .channel   = CONFIG_LESS_INTERFERENCE_CHANNEL,
        .ifidx     = WIFI_IF_STA,
        .encrypt   = false,
        .peer_addr = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff},
    };
    wifi_esp_now_init(peer);
    wifi_csi_init();
    udp_client_init(); 
}