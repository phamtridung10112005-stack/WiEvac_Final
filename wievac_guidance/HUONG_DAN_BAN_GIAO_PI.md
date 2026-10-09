# TÀI LIỆU BÀN GIAO & TÓM TẮT HỆ THỐNG WIEVAC TRÊN RASPBERRY PI 5

> **Thời gian tạo:** 07/10/2026  
> **Người nhận bàn giao:** Nhóm nghiên cứu / Kỹ sư phụ trách phần cứng & mạng WiEvac  
> **Mục đích:** Tài liệu hướng dẫn chi tiết về cấu trúc hệ thống, các thay đổi trên Pi, cơ chế đóng gói dữ liệu và nhảy node (Multi-hop) để thử nghiệm thực tế.

---

## 1. XÁC NHẬN PHẠM VI THAY ĐỔI TRÊN RASPBERRY PI

Toàn bộ các can thiệp và bổ sung **CHỈ DIỄN RA TRONG THƯ MỤC `/home/junpham/wievac_guidance/`**.  
Các thư mục khác trên Pi (như `/home/junpham/wievac/`, `/home/junpham/wievac-ota/`, `/home/junpham/wievac_project/` và các cấu hình hệ điều hành gốc) **HOÀN TOÀN KHÔNG BỊ CHỈNH SỬA, NGUYÊN VẸN 100%**.

### Chi tiết các file được cập nhật/thêm mới trong `wievac_guidance`:
1. **`pi_downlink_bridge.py`** *(File mới tạo)*:
   - **Nhiệm vụ:** Lắng nghe các lệnh điều hướng từ thuật toán D* Lite qua MQTT (`building/guidance/speaker/#`, `building/incident`), tự động đóng gói thành gói tin nhị phân chuẩn 29 bytes và bắn qua UDP port 8889 xuống ESP32 Gateway để điều khiển Loa ngoài thực tế.
2. **`startup_guidance.sh`** *(Cập nhật)*:
   - Thêm dòng khởi động tự động cho `pi_downlink_bridge.py` chạy nền cùng `edge_core.py` và `pi_csi_bridge.py`. Mỗi khi Pi khởi động, toàn bộ chuỗi dịch vụ sẽ tự động chạy thông suốt.
3. **`config.json`** *(Đồng bộ danh sách thiết bị)*:
   - Bổ sung cấu hình 3 thiết bị Loa tương ứng với 3 Node thực tế:
     - `Node 1` (`node_spkr_01`): Loa Cửa thoát hiểm (Gateway - Đầu hành lang)
     - `Node 2` (`node_spkr_02`): Loa Giữa hành lang (Transmitter - Giữa hành lang)
     - `Node 3` (`node_spkr_03`): Loa Cuối hành lang (Relay - Cuối hành lang)

---

## 2. CƠ CHẾ ĐÓNG GÓI DỮ LIỆU (PACKET PACKAGING)

Dữ liệu truyền giữa Raspberry Pi và các Node ESP32 **ĐÃ ĐƯỢC ĐÓNG GÓI CHUẨN Ở CẢ 2 CHIỀU**, không dùng chuỗi String/JSON cồng kềnh qua sóng radio:

### A. Chiều Downlink (Pi -> ESP32: Điều khiển Loa)
- Gói tin nhị phân chuẩn định dạng `wievac_mesh_packet_t` có độ dài cố định **đúng 29 bytes**:
  - **Header (16 bytes)**:
    - `magic = 0x57455643` (`'WEVC'` - Mã độc quyền nhận diện hệ thống WiEvac)
    - `version = 1`
    - `message_type = 5` (WIEVAC_MSG_CONTROL)
    - `sequence = command_id`
    - `ttl = 12` (Thời hạn sống chống lặp vòng), `hop_count = 0`
  - **Payload (13 bytes)**:
    - `target_node_id`: `1` (Loa 1), `2` (Loa 2), `3` (Loa 3), hoặc `0` (Broadcast cả 3 Loa)
    - `command`: Mã số chỉ dẫn âm thanh:
      - `10`: Bắt đầu sơ tán khẩn cấp (`START_EVAC`)
      - `11`: Đi thẳng (`GO_STRAIGHT` - Track 003)
      - `12`: Rẽ trái (`TURN_LEFT` - Track 001)
      - `13`: Rẽ phải (`TURN_RIGHT` - Track 002)
      - `14`: Quay đầu lại / Lên lầu (`TURN_BACK` - Track 004)
      - `16`: Lối thoát an toàn (`SAFE_EXIT` - Track 006)
      - `17`: Dừng lại / Nguy hiểm (`STOP_DANGER` - Track 005)
      - `19`: Dừng phát âm thanh (`CLEAR`)

### B. Chiều Uplink (ESP32 -> Pi: Gửi CSI & Trạng thái Node)
- **Node gần Pi**: Gửi thẳng gói datagram `WIV5`/`WIV6` qua Wi-Fi UDP (Port 8888) lên `run_pi_v5_compact.py`.
- **Node xa Pi (Relay)**: Đóng gói đặc trưng CSI (`mean_amp`, `std_dev`) thành gói tin mesh nhị phân 29 bytes `WEVC` (`message_type = 1`), sau đó Gateway chuyển tiếp nguyên vẹn lên Pi.

---

## 3. CƠ CHẾ NHẢY NODE (MULTI-HOP MESH ROUTING)

Hệ thống hỗ trợ cơ chế nhảy node ở **CẢ 2 CHIỀU**:

```
[ Raspberry Pi 5 ]
       │ Wi-Fi AP (10.42.0.1)
       ▼ UDP Port 8888/8889 (Trực tiếp)
 ┌───────────────┐
 │ Node 1 (RX)   │ ◄─── GATEWAY (Đầu hành lang): Bắt Wi-Fi Pi, nối mạng trực tiếp
 │ 🔊 Loa 1      │
 └───────┬───────┘
         │ ESP-NOW Broadcast (Kênh 11) - Nhảy Hop 1
         ▼
 ┌───────────────┐
 │ Node 2 (TX)   │ ◄─── TRANSMITTER (Giữa hành lang): Phát CSI 100Hz, thu ESP-NOW phát Loa 2
 │ 🔊 Loa 2      │
 └───────┬───────┘
         │ ESP-NOW Broadcast (Kênh 11) - Nhảy Hop 2
         ▼
 ┌───────────────┐
 │ Node 3 (RX)   │ ◄─── RELAY (Cuối hành lang): Nằm ngoài vùng Wi-Fi của Pi, nhận lệnh qua mesh!
 │ 🔊 Loa 3      │
 └───────────────┘
```

### 1. Chiều từ Pi xuống ESP32 (Phát lệnh Loa):
1. Pi (`pi_downlink_bridge.py`) gửi gói UDP 29 bytes tới Node 1 (Gateway) qua port 8889.
2. Node 1 nhận gói tin:
   - Nếu lệnh cho Node 1 (hoặc broadcast `0`): Loa Node 1 phát ngay.
   - Node 1 lập tức phát Broadcast qua **ESP-NOW (Kênh 11)** ra không gian.
3. Node 2 (Transmitter) và Node 3 (Relay ở xa) thu sóng ESP-NOW:
   - Nếu đúng `target_node_id` của mình: Kích hoạt module DFPlayer Mini phát file mp3 tương ứng.
   - Gửi lại gói `CONTROL_ACK` qua ESP-NOW về Node 1.
4. Node 1 nhận `ACK` từ Node 2 / Node 3 $\rightarrow$ Chuyển tiếp ngược lên Pi qua UDP $\rightarrow$ Pi xác nhận Loa ngoài thực địa đã thực thi.

### 2. Chiều từ ESP32 lên Pi (Đo sóng CSI mật độ người):
1. Node 3 (ở cuối hành lang, xa Pi): Thu sóng CSI từ Node 2, tính toán biên độ. Vì không bắt được Wi-Fi của Pi, Node 3 tự động chuyển vai trò thành **RELAY**.
2. Node 3 đóng gói dữ liệu thành gói mesh `WEVC` và bắn qua **ESP-NOW**.
3. Node 1 (Gateway ở gần Pi) bắt được gói ESP-NOW từ Node 3 $\rightarrow$ Tự động chuyển tiếp (Forward) gói tin này lên Pi qua UDP port 8888.
4. Pi (`run_pi_v5_compact.py`) nhận dữ liệu, cập nhật điểm số tắc nghẽn của Node 3 vào API `/api/v5/overview`.

---

## 4. HƯỚNG DẪN VẬN HÀNH CHO NGƯỜI TIẾP NHẬN

### A. Kiểm tra dịch vụ trên Pi
Khi bật nguồn Raspberry Pi 5, các dịch vụ sẽ tự động chạy nền. Để kiểm tra:
```bash
# Xem các tiến trình đang chạy:
ps aux | grep -E 'edge_core|pi_csi_bridge|pi_downlink_bridge|run_pi_v5_compact' | grep -v grep

# Xem log hoạt động của cầu nối loa:
tail -f /home/junpham/wievac_guidance/pi_downlink_bridge.log
```

### B. Lệnh phát loa kiểm tra thủ công từ Pi (Nghiệm thu phần cứng)
Nếu muốn test âm thanh từng loa ngoài hành lang:
```bash
# Phát Loa 1 (Đi thẳng):
python3 /home/junpham/wievac/scripts/send_guidance.py --target 1 --cmd 11

# Phát Loa 2 (Rẽ trái):
python3 /home/junpham/wievac/scripts/send_guidance.py --target 2 --cmd 12

# Phát Loa 3 (Lối thoát an toàn):
python3 /home/junpham/wievac/scripts/send_guidance.py --target 3 --cmd 16

# Phát TOÀN BỘ 3 LOA (Bắt đầu sơ tán khẩn cấp):
python3 /home/junpham/wievac/scripts/send_guidance.py --target 0 --cmd 10

# Tắt âm thanh toàn bộ 3 loa:
python3 /home/junpham/wievac/scripts/send_guidance.py --target 0 --cmd 19
```

### C. Lưu ý phần cứng quan trọng
1. **Wi-Fi Kênh 11:** Hotspot trên Pi bắt buộc phải là **Channel 11** (khớp với Kênh ESP-NOW của 3 node).
2. **Nguồn cấp Loa DFPlayer Mini:** Phải cắm chân VCC của DFPlayer vào chân **VIN / 5V** của ESP32 (không cắm chân 3.3V vì dễ bị sụt áp reset ESP).
3. **Thẻ nhớ:** Định dạng `FAT32`, thư mục `mp3/`, chứa đủ 6 file `0001.mp3` $\rightarrow$ `0006.mp3`.
4. **Đổi ID động:** Cắm từng ESP32 vào máy tính, mở Serial Monitor (115200) gõ:
   - Node đầu: `SET_ID=1`
   - Node giữa (TX): `SET_ID=2`
   - Node cuối: `SET_ID=3`
