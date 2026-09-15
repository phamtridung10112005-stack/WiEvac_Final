import socket
import struct
import time
import math
from datetime import datetime
import csv
import threading
import os
import sys

# =====================================================================
# CẤU HÌNH MẠNG & THÔNG SỐ PI 5
# =====================================================================
UDP_IP = "0.0.0.0"
UDP_PORT = 8888
ESP_PORT = 8889           # Cổng lắng nghe của ESP32 để nhận lệnh Calibrate
CSV_FILENAME = "wievac_training_data.csv"
BROADCAST_IP = "10.42.0.255"

# (Không còn dùng bất kỳ Hằng số Ngưỡng nào, AI tự tính bằng Z-Score)

# ĐỊNH DẠNG MÀU SẮC TERMINAL
RESET, BOLD = "\033[0m", "\033[1m"
GREEN, RED, YELLOW, MAGENTA, CYAN, BLUE = "\033[92m", "\033[91m", "\033[93m", "\033[95m", "\033[96m", "\033[94m"

# Lock chống nghẽn ghi file
csv_lock = threading.Lock()

# =====================================================================
# KHỐI 1: LÕI TOÁN HỌC TRƯỢT (FULLY ADAPTIVE NODE TRACKER)
# =====================================================================
class NodeState:
    def __init__(self, node_id, ip_addr, send_sock):
        self.node_id = node_id
        self.ip_addr = ip_addr
        self.send_sock = send_sock
        
        # BỘ BÁM NỀN NHIỄU BẤT ĐỐI XỨNG (Asymmetric Noise Tracker)
        # Bắt đầu bằng None để áp dụng Khởi động êm (Soft-Start)
        self.base_std = None
        self.var_std = None
        self.base_delta = None
        self.base_sad = None
        self.var_sad = None
        self.peak_delta = None
        self.last_calibrate_time = 0.0  # Cooldown chống spam Calibrate

        
        # Bộ đếm tự Vực dậy (Auto-Resurrection)
        self.static_block_time = 0.0
        self.elevated_time_std = 0.0  # Catch-Up Timer cho baseline bị kẹt
        self.elevated_time_sad = 0.0
        
        self.last_update = time.time()
        self.congestion_pct = 0.0
        self.state_msg = "KHỞI TẠO"
        self.color = RESET
        self.history = []
        self.bio_confirm_count = 0  # Confirmation Counter — chống spike đơn lẻ
        self.is_biological = False  # Trạng thái Schmitt Trigger
        
    def update_congestion(self, delta_ratio, std_dev, sad_value):
        current_time = time.time()
        dt = current_time - self.last_update
        self.last_update = current_time
        
        
        # -------------------------------------------------------------
        # 0. KHỞI ĐỘNG ÊM (SOFT-START) - CHỐNG NỔ PHƯƠNG SAI
        # -------------------------------------------------------------
        if self.base_sad is None:
            self.base_sad = sad_value
            self.base_std = std_dev
            self.base_delta = delta_ratio
            self.peak_delta = max(4.0, delta_ratio + 0.5)
            self.is_biological = False
            
            # Khởi tạo phương sai ban đầu
            self.var_std = 0.05 ** 2
            self.var_sad = 0.05 ** 2
            self.bio_confirm_count = 0
            self.elevated_time_std = 0.0
            self.elevated_time_sad = 0.0
            self.last_calibrate_time = current_time
            return 0.0, 0.0, 0.0, self.base_std

        # -------------------------------------------------------------
        # 1. QUIET-ONLY BASELINE + CATCH-UP TIMER (Fully Adaptive)
        # -------------------------------------------------------------
        # Bước 1: Tính z-score tạm dùng baseline hiện tại
        diff_std = std_dev - self.base_std
        diff_sad = sad_value - self.base_sad
        
        # CHỐNG CHẠM ĐÁY PHƯƠNG SAI (Ngăn chặn False Positive siêu nhạy)
        var_floor_std = max(0.0005, (0.15 * self.base_std) ** 2)
        var_floor_sad = max(0.00005, (0.15 * self.base_sad) ** 2)
        self.var_std = max(var_floor_std, self.var_std)
        self.var_sad = max(var_floor_sad, self.var_sad)
        
        std_of_std = math.sqrt(self.var_std)
        std_of_sad = math.sqrt(self.var_sad)
        z_score_std = diff_std / std_of_std
        z_score_sad = diff_sad / std_of_sad

        # Bước 2: Yên tĩnh hoặc Tĩnh lặng bất thường (Z < 2.0) → cập nhật baseline
        if z_score_std < 2.0:
            # Nếu Z < -2.0, tức là môi trường THỰC TẾ tĩnh lặng hơn rất nhiều so với Nền đang nhớ
            # => Nền bị sai (do khởi động lúc ESP32 chưa ổn định hoặc có người). Kéo nền rớt thật nhanh!
            rate_std = 0.1 if z_score_std < -2.0 else 0.01
            self.base_std += rate_std * diff_std
            self.var_std += rate_std * (diff_std ** 2 - self.var_std)
            self.elevated_time_std = 0.0
        else:
            # Bước 3: Catch-up — elevated > 60s → baseline sai, ép bám
            self.elevated_time_std += dt
            if self.elevated_time_std > 60.0:
                self.base_std += 0.002 * diff_std
                self.var_std += 0.002 * (diff_std ** 2 - self.var_std)

        if z_score_sad < 2.0:
            rate_sad = 0.1 if z_score_sad < -2.0 else 0.01
            self.base_sad += rate_sad * diff_sad
            self.var_sad += rate_sad * (diff_sad ** 2 - self.var_sad)
            self.elevated_time_sad = 0.0
        else:
            self.elevated_time_sad += dt
            if self.elevated_time_sad > 60.0:
                self.base_sad += 0.002 * diff_sad
                self.var_sad += 0.002 * (diff_sad ** 2 - self.var_sad)

        # Cập nhật base_delta CÙNG LÚC với std và sad khi hành lang yên tĩnh (Z < 2.0)
        # Bao gồm cả Z âm sâu (càng âm càng tĩnh lặng)
        if z_score_std < 2.0 and z_score_sad < 2.0:
            diff_d = delta_ratio - self.base_delta
            # Rút nền delta cực nhanh nếu delta thực tế thấp hơn nền
            rate_delta = 0.1 if diff_d < 0 else 0.01
            self.base_delta += rate_delta * diff_d
            self.base_delta = max(0.01, self.base_delta)

        # -------------------------------------------------------------
        # 2. PHÁT HIỆN SỰ SỐNG (CHARGE PUMP CHO 1Hz)
        # -------------------------------------------------------------
        # Hạ ngưỡng xuống 2.0 vì sàn phương sai đã bảo vệ chống rác
        z_exceeds = (z_score_sad > 2.0) or (z_score_std > 2.0)
        if z_exceeds:
            self.bio_confirm_count = min(self.bio_confirm_count + 3, 8)  # Sạc cực nhanh (+3s hold)
        else:
            self.bio_confirm_count = max(self.bio_confirm_count - 1, 0)
            
        self.is_biological = (self.bio_confirm_count > 0)

        # Tính % Tắc nghẽn
        diff_delta = max(0, delta_ratio - self.base_delta)

        # Bám đỉnh động (Asymmetric Peak Tracker)
        if diff_delta > self.peak_delta:
            self.peak_delta = 0.7 * self.peak_delta + 0.3 * diff_delta
        else:
            self.peak_delta = 0.999 * self.peak_delta + 0.001 * diff_delta
            
        # Áp mức trần cứng (Tương đương 1 đám đông) để 1 người đi qua không bao giờ đạt 100%
        self.peak_delta = max(4.0, self.peak_delta)

        if self.peak_delta > 0.05:
            raw_pct = (diff_delta / self.peak_delta) * 100.0
        else:
            raw_pct = 0.0
            
        # LỌC MỀM
        if self.is_biological:
            if raw_pct > self.congestion_pct:
                self.congestion_pct = 0.5 * raw_pct + 0.5 * self.congestion_pct
            else:
                self.congestion_pct = 0.05 * raw_pct + 0.95 * self.congestion_pct
        else:
            self.congestion_pct = 0.6 * raw_pct + 0.4 * self.congestion_pct
            
        self.congestion_pct = min(100.0, max(0.0, self.congestion_pct))

        # -------------------------------------------------------------
        # 3. AUTO-RESURRECTION (TỰ VỰC DẬY KHI LỖI NỀN)
        # -------------------------------------------------------------
        stuck_too_long = (self.elevated_time_std > 60.0) or (self.elevated_time_sad > 60.0)
        if stuck_too_long:
            self.static_block_time += dt
            if self.static_block_time > 5.0 and (current_time - self.last_calibrate_time) > 30.0:
                print(f"\n{BOLD}{MAGENTA}[!] PHÁT HIỆN LỖI SAI NỀN VẬT LÝ Ở NODE {self.node_id}. AUTO-CALIBRATING...{RESET}\n")
                try:
                    self.send_sock.sendto(b'\xFF', (self.ip_addr, ESP_PORT))
                except Exception as e:
                    pass
                self.last_calibrate_time = current_time
                self.static_block_time = 0.0
                # Cho phép Soft-Start kích hoạt lại ở gói tin tiếp theo
                self.base_sad = None
        else:
            self.static_block_time = 0.0

        # Tính Độ Nghẽn Hành Lang (Đã được thực hiện ở Lọc Mềm bên trên)

        # Phân loại trạng thái dựa trên pct ĐÃ LÀM MỊN
        if self.congestion_pct > 60.0:
            self.state_msg = "KẸT CỨNG (Đám đông/Nút thắt)"
            self.color = RED
        elif self.congestion_pct > 25.0:
            self.state_msg = "ĐÔNG NGƯỜI (Di chuyển chậm)"
            self.color = YELLOW
        elif self.congestion_pct > 5.0:
            self.state_msg = "CÓ NGƯỜI (Thông thoáng)"
            self.color = MAGENTA
        else:
            self.state_msg = "HÀNH LANG TRỐNG"
            self.color = GREEN
        
        # Buffer cho CSV (Lưu max z_score để tương thích format cũ)
        self.history.append([self.node_id, delta_ratio, std_dev, sad_value, self.base_std, max(z_score_sad, z_score_std), self.congestion_pct])
        return self.congestion_pct, z_score_sad, z_score_std, self.base_std

# =====================================================================
# LÕI AI TRÊN PI 5 (THE BRAIN)
# =====================================================================
class WiEvacPi5Core:
    def __init__(self):
        self.nodes = {} 
        self.d_star_weights = {} 
        
        # Socket chuyên để bắn lệnh (Có thể gửi Broadcast hoặc Direct IP)
        self.cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.cmd_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        # Khởi tạo CSV
        if not os.path.exists(CSV_FILENAME):
            with open(CSV_FILENAME, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['node_id', 'delta_ratio', 'std_dev', 'sad_value', 'base_std_noise', 'snr_std', 'congestion_pct'])
                
        print(f"{BOLD}{CYAN}🚀 [WIEVAC V5.2 - FULLY ADAPTIVE AI CORE] SẴN SÀNG!{RESET}\n")
        
        threading.Thread(target=self.csv_writer_thread, daemon=True).start()
        threading.Thread(target=self.keyboard_commander_thread, daemon=True).start()

    def csv_writer_thread(self):
        while True:
            time.sleep(5) 
            with csv_lock:
                with open(CSV_FILENAME, 'a', newline='') as f:
                    writer = csv.writer(f)
                    for node_id, node in self.nodes.items():
                        if node.history:
                            writer.writerows(node.history)
                            node.history = []

    def keyboard_commander_thread(self):
        print(f"{BOLD}{YELLOW}>>> HƯỚNG DẪN: Nhấn 'C' + Enter để ép TẤT CẢ ESP32 Calibrate lại Nền! <<<{RESET}")
        while True:
            cmd = sys.stdin.readline().strip().upper()
            if cmd == 'C':
                print(f"{BOLD}{MAGENTA}[!] ĐANG PHÁT LỆNH 0xFF TỚI TOÀN BỘ MẠNG...{RESET}")
                try:
                    self.cmd_sock.sendto(b'\xFF', (BROADCAST_IP, ESP_PORT))
                except Exception as e:
                    print(f"Lỗi: {e}")

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_IP, UDP_PORT))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        sock.settimeout(3.0) 

        while True:
            try:
                data, addr = sock.recvfrom(1024)
                timestamp_str = datetime.now().strftime('%H:%M:%S')
                
                if len(data) == 17:
                    node_id, delta_ratio, std_dev, sad_value, pkt_ts = struct.unpack('<BfffI', data)
                    
                    if node_id not in self.nodes:
                        self.nodes[node_id] = NodeState(node_id, addr[0], self.cmd_sock)
                        print(f"{BOLD}{BLUE}[+] PHÁT HIỆN NODE MỚI: {node_id} TỪ IP {addr[0]}{RESET}")
                    else:
                        self.nodes[node_id].ip_addr = addr[0] # Cập nhật nhỡ router cấp lại IP
                        
                    node = self.nodes[node_id]
                    pct, z_sad, z_std, base_std = node.update_congestion(delta_ratio, std_dev, sad_value)
                    self.d_star_weights[node_id] = pct
                    
                    bar_len = 20
                    filled = int(bar_len * pct / 100)
                    bar = '█' * filled + '-' * (bar_len - filled)
                    
                    # Rút gọn Terminal cho dễ nhìn: Tỉ lệ | Nhiễu Nền | Tỉ số SNR
                    print(f"[{timestamp_str}] N:{node_id:02d} | "
                          f"Δ:{delta_ratio:4.2f} Std:{std_dev:4.3f} SAD:{sad_value:4.3f} (Z_SAD:{z_sad:4.1f} Z_STD:{z_std:4.1f}) | "
                          f"{node.color}THÔNG QUA: {100-pct:5.1f}% (Nghẽn {pct:4.1f}%) [{bar}] -> {node.state_msg}{RESET}")

            except socket.timeout:
                current_time = time.time()
                for n_id, n in list(self.nodes.items()):
                    if current_time - n.last_update > 5.0:
                        print(f"{BOLD}{RED}[{datetime.now().strftime('%H:%M:%S')}] ❌ NODE {n_id} MẤT KẾT NỐI! Tháo khỏi D* Lite.{RESET}")
                        del self.d_star_weights[n_id]
                        del self.nodes[n_id] 
                        
            except Exception as e:
                pass

if __name__ == "__main__":
    core = WiEvacPi5Core()
    core.run()