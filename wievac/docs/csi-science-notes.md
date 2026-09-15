# CSI Science Notes

Kien truc dich: CSI va Formula Flex duoc xu ly tai RX de tao EdgeResult local;
Pi dung EdgeResult cho fusion, du doan xu huong va routing. Protocol V4 va cac
feature V4 duoc giu cho compatibility/replay, khong phai bang chung EdgeResult
V5 da active.

Iteration-4 implementation boundary: Formula Flex, adaptive baseline and
passability evidence belong to the RX reference pipeline. The compact Pi path
does not ingest raw CSI or recompute these quantities; it only validates and
records EdgeResult metadata. Trend, routing and scheduling remain reference
modules and are not mounted by the default V5 service. Tiny AI is `NOT_READY`
without a real artifact, and all score/uncertainty values remain uncalibrated
software indices rather than field accuracy claims.

## 1. Quy tắc ghi cơ sở

Mỗi công thức/feature/bộ lọc phải mang một nhãn:

- **Có cơ sở chung**: tồn tại trong toán học, tài liệu chuẩn hoặc nghiên cứu.
- **Có cơ sở chung nhưng cách dùng/hệ số WiEvac chưa được chứng minh**.
- **Giả thuyết thực nghiệm WiEvac**.
- **Không có cơ sở**: không được dùng trong đường quyết định nếu thiếu kiểm chứng phù hợp.

Nguồn chỉ chứng minh đúng phạm vi của nguồn. Bài báo về CSI sensing không tự động chứng minh hệ số, feature hoặc độ chính xác của WiEvac.

## 2. Dữ liệu CSI và metadata

### Biên độ

\[
|H_k| = \sqrt{I_k^2 + Q_k^2}
\]

- **Có cơ sở toán học**: mô-đun của số phức.
- Điều kiện: đọc đúng cặp I/Q và đúng vị trí LTF/subcarrier.
- Biên độ thay đổi có thể do người, vật, multipath, gain, công suất, packet type hoặc nhiễu; không phải nhãn người.

### MAC nguồn

`wifi_csi_info_t.mac` dùng để nhận biết nguồn packet CSI. RX phải lọc TX đã ghép cặp trước khi đưa mẫu vào DSP.

### `first_word_invalid`

Khi cờ này đúng, bốn byte đầu CSI không hợp lệ theo hạn chế phần cứng được Espressif mô tả. Code phải bỏ/đánh dấu đúng thay vì dùng như mẫu hợp lệ.

### LTF và độ dài buffer

LLTF, HT-LTF và STBC-HT-LTF có bố cục/thứ tự do driver quy định. Không được bật nhiều LTF rồi mặc định 64 số phức đầu tiên luôn là cùng một tập subcarrier.

### Channel filter

`channel_filter_en` làm mượt các sóng mang kề nhau. Điều này có thể có lợi cho nhiễu nhưng có thể làm biến dạng feature đo roughness/tương quan kề nhau. Trạng thái: **tác động lên feature WiEvac phải được ablation**.

### Radio metadata

RSSI, noise floor, timestamp và các trường `rx_ctrl` là tín hiệu chẩn đoán chất lượng, không tự động là bằng chứng có người. Gain/AGC thay đổi có thể tạo biến thiên biên độ giả.

Nguồn Espressif:

- https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/wifi-driver/wifi-vendor-features.html
- https://docs.espressif.com/projects/esp-idf/en/v5.5.4/esp32s3/api-guides/wifi.html
- https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/network/esp_now.html
- https://github.com/espressif/esp-csi

## 3. Baseline feature V1

### `delta_amp`

\[
\Delta =
\frac{\sum_k |EMA_{fast,k}-EMA_{slow,k}|}
{\sum_k EMA_{slow,k}+\epsilon}
\]

- Mục đích: mức thay đổi biên độ tương đối giữa thành phần nhanh và nền chậm.
- **Có cơ sở chung** cho fast/slow tracking và amplitude variation.
- Alpha nhanh/chậm, công thức alpha động và \(\epsilon\) của WiEvac là tham số/thiết kế thực nghiệm.
- Không trực tiếp là mật độ hoặc phần trăm nghẽn.

### `std_dev`

Độ lệch chuẩn theo thời gian của \(\Delta\), tính bằng Welford:

\[
M_{2,n}=M_{2,n-1}+(x_n-\bar{x}_{n-1})(x_n-\bar{x}_n)
\]

\[
\sigma=\sqrt{\frac{M_2}{N}}
\]

- Welford có cơ sở toán học cho variance online ổn định.
- Phải ghi rõ dùng population hay sample variance.
- Giả thuyết “người làm std cao” cần kiểm chứng; hành lang trống vẫn có thể dao động cao.
- Cửa sổ phải có thời lượng thật, không suy từ số callback.

### `sad_value` / `spectral_roughness`

\[
R=\frac{1}{K-1}\sum_k |d_k-d_{k-1}|
\]

- Mô tả đúng hơn là total variation/roughness dọc trục subcarrier của feature đã chuẩn hóa.
- **Có cơ sở chung** cho sai khác tuyệt đối cục bộ.
- Tên, chuẩn hóa và giả thuyết phân biệt người/vật của WiEvac chưa được chứng minh.
- V2 nên version hóa khi đổi tên hoặc định nghĩa.

## 4. Phép xử lý baseline

| Phép xử lý | Cơ sở | Phần chưa chứng minh |
|---|---|---|
| Median window | Robust với impulse/outlier cục bộ | Kích thước 5 và độ trễ |
| EMA | Làm mượt/theo dõi online | Alpha phụ thuộc sample rate |
| Dynamic alpha | Giả thuyết WiEvac | Dạng hàm và hệ số |
| Welford | Toán học online variance | Cửa sổ/loại variance |
| Z-score | Chuẩn hóa thống kê | Z=2 không phải quy luật người |
| Median/MAD/IQR | Robust statistics | Cách ánh xạ sang quyết định |
| Peak normalization | Heuristic chuẩn hóa | Không chứng minh phần trăm |
| Persistence/debounce | Chống spike thời gian | Trade-off FN/latency |

Nguồn nền:

- NIST z-score: https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm
- NIST EWMA: https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc324.htm

## 5. Feature nghiên cứu (V2/V5 candidate)

Danh sách sau là hướng thí nghiệm, không phải yêu cầu bắt buộc:

- median/MAD/IQR của delta;
- slope, persistence, recovery time và change-point score;
- RSSI/noise-floor/gain mean, spread và change;
- packet rate, loss, reorder, jitter, invalid/drop ratio;
- subcarrier correlation/coherence/entropy/energy;
- phase feature sau khi xử lý CFO/SFO/PDD và có bằng chứng.

Feature chất lượng radio phải được phân biệt với feature hiện diện người. AI có thể dùng cả hai nhưng phải ghi đúng vai trò.

`coherent_motion` trong Protocol V4 hien la median signed-correlation heuristic giua cac
delta shape bins. Nó không phải magnitude-squared coherence chuẩn, không phải xác suất
người và chưa được hiệu chuẩn. Các ngưỡng motion/coherence/interference nằm trong
`corridor-01.json` dưới `analysis`, có `threshold_profile_version` và chỉ được xem là
heuristic-unvalidated cho tới khi có sweep FP/FN theo session/corridor.

## 6. Những điều không được diễn giải quá mức

- CSI phản ứng với kênh truyền và multipath, không riêng cơ thể người.
- Hành lang trống không đồng nghĩa tín hiệu phẳng hoặc std nhỏ tuyệt đối.
- Reset state toán học không đồng nghĩa reset AGC/noise floor phần cứng.
- Dữ liệu một link không chứng minh tổng quát hóa.
- LightGBM phân loại nhiễu chỉ tốt trong miền dữ liệu đã học; OOD/UNKNOWN là đầu ra cần thiết.
- `predict_proba` không tự động là confidence đã hiệu chuẩn.
- Mot `local_passability_score` chuan hoa khong tu dong la phan tram thong qua
  that. Neu chua co ground truth/calibration, ghi no la score/index ky thuat.
- Packet loss, RSSI thap, jitter va invalid CSI la quality evidence; khong tu
  dong bien thanh obstruction evidence.
- Tiny AI tai RX chi duoc hieu chinh Formula trong gioi han da version hoa.
  Bat dong lon phai thanh `DEGRADED`/`UNKNOWN`, khong chon ket qua dep hon.

## 7. Mẫu ghi feature/công thức mới

Mỗi mục mới phải có:

1. Tên và `feature_schema_version`.
2. Công thức/thuật toán, đơn vị và miền giá trị.
3. Mục đích và loại nhiễu/tín hiệu dự kiến.
4. Giả định.
5. Nguồn hoặc nhãn giả thuyết.
6. Vi tri chay: RX edge, Pi real-time (fusion/trend/routing) hay offline.
7. CPU/RAM/payload/latency.
8. Replay, ablation và cross-corridor result.
9. Trạng thái `experimental`, `candidate`, `active` hoặc `rejected`.

Thiếu nguồn trực tiếp không cấm thí nghiệm; nó đòi hỏi ghi đúng trạng thái và bằng chứng thực nghiệm mạnh hơn.
