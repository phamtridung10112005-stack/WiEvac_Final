# WiEvac Feature and Filter Experiment Policy

## 1. Mục đích

WiEvac chưa hoàn thiện. Ba feature và chuỗi lọc V1 là baseline để so sánh, không phải giới hạn sáng tạo. Tài liệu này cho phép thử phương pháp mới nhưng cấm đưa ý tưởng vào production theo cảm tính.

## 2. Baseline phai giu

Baseline V1 gồm:

- amplitude `sqrt(I²+Q²)`;
- median window;
- dual EMA;
- `delta_amp`;
- Welford `std_dev`;
- `sad_value`;
- Formula Flex V1. Trong kien truc dich, Formula duoc port/chay tai RX; Pi V4
  van giu o compatibility/replay.

Baseline phải được tag/commit, có packet/schema và cách replay rõ. Không xóa baseline trước khi phương pháp mới vượt cổng kiểm chứng.

## 3. Phạm vi sáng tạo

Codex được phép:

- thêm, bỏ, thay hoặc kết hợp feature;
- thử filter/normalization/baseline/change detector mới;
- dùng robust statistics, signal processing hoặc ML nhẹ;
- dùng feature thời gian, tần số, subcarrier và radio quality;
- thay đổi phân chia tính toán RX/Pi khi có phân tích tài nguyên;
- ket hop Formula Flex tai RX voi tiny AI de hieu chinh co gioi han;
- tinh mot `local_passability_score`/`EdgeResult` chinh tai RX va de Pi lam
  fusion, forecasting, routing;
- tạo CAPTURE mode để lưu CSI gần thô/dữ liệu trung gian;
- kết luận một feature V1 không hữu ích nếu ablation chứng minh.

Ví dụ được phép nghiên cứu:

- MAD, IQR, trimmed statistics;
- slope, persistence, recovery, autocorrelation;
- spectral energy/entropy/roughness;
- subcarrier correlation/coherence;
- RSSI/noise floor/gain stability;
- packet loss, jitter, invalid/drop ratio;
- phase feature sau khi có phase sanitization phù hợp.

Danh sách trên không phải checklist bắt buộc.

## 4. Hồ sơ ý tưởng

Trước khi code, ghi:

1. `experiment_id`.
2. Nguồn nhiễu hoặc lỗi cần xử lý.
3. Cơ chế/giả thuyết.
4. Công thức và đơn vị.
5. Input và nơi chạy: RX edge, Pi real-time hoặc offline.
6. `feature_schema_version` dự kiến.
7. CPU, RAM, payload, airtime và latency.
8. Nguồn khoa học hoặc “giả thuyết thực nghiệm”.
9. Phép thử có thể bác bỏ.
10. Acceptance/rollback gates.

Không triển khai nhiều feature mới cùng lúc nếu không thể ablation.

## 5. Quality feature và sensing feature

Phải phân biệt:

- **Quality feature**: sai nguồn, loss, jitter, gain, RSSI/noise-floor, invalid/drop.
- **Sensing feature**: thay đổi CSI dự kiến liên quan hiện diện/chuyển động/mật độ.

Quality feature có thể giúp AI nhận ra nhiễu nhưng không tự chứng minh có/không người. Không loại cửa sổ chỉ vì RSSI thấp nếu chưa có policy và kiểm chứng.

## 6. RUN và CAPTURE

### RUN

- RX gui mot EdgeResult gon gom score/state/quality/uncertainty va version.
- Pi khong tinh lai per-link Formula trong duong active.
- Không tăng airtime quá mức.

Formula score va tiny-AI correction co the luu trong debug/CAPTURE de replay,
nhung khong duoc gui thanh hai ket qua production ngang hang.

### CAPTURE

- Dùng trong phiên nghiên cứu có thời hạn.
- Lưu CSI gần thô hoặc snapshot/subsample đủ để tính feature mới.
- Kèm source MAC, LTF/layout, timestamp, radio metadata, sequence, firmware/schema.
- Đo ảnh hưởng của chính việc truyền dữ liệu capture lên kênh CSI.

Không dùng dữ liệu CAPTURE làm production mặc định khi chưa đánh giá tải.

## 7. Cổng kiểm chứng

Một phương pháp mới đi qua:

1. Static/math review.
2. Offline replay trên cùng session với baseline.
3. Ablation.
4. Controlled bench/field scenarios.
5. Cross-session.
6. Hold-out corridor.
7. Shadow mode.
8. Soak/recovery nếu ảnh hưởng production.

Nếu chưa có phần cứng, chỉ được đạt `implemented-pending-field-test`.

## 8. Metric

- false positive khi trống;
- false negative khi có người;
- separation/calibration phù hợp;
- sai số passability nếu có ground truth;
- detection/recovery latency;
- stability qua session/corridor;
- CPU, RAM, payload, airtime và năng lượng;
- gioi han correction, hanh vi khi Formula/AI bat dong, OOD va model thieu;
- độ nhạy với packet rate và radio config.

Một phương pháp không cần thắng mọi metric, nhưng trade-off phải được công khai và không làm xấu invariant an toàn ngoài acceptance gate.

## 9. Schema và tương thích

- Mỗi định nghĩa feature có version.
- Ghi tên, thứ tự, kiểu, đơn vị, missing policy và công thức.
- Không load model khác schema.
- Đổi payload phải đổi protocol/data contract và packet test.
- Không âm thầm đổi nghĩa `sad_value` dưới cùng schema.

## 10. Quyết định

Trạng thái:

- `experimental`;
- `candidate`;
- `active`;
- `rejected`;
- `rolled-back`.

Chỉ `active` khi có bằng chứng theo metric đã chốt, không chỉ tốt ở một link, đáp ứng Flex/tài nguyên và có rollback. Phương pháp rejected chỉ được thử lại với bằng chứng mới được ghi trong ledger.
