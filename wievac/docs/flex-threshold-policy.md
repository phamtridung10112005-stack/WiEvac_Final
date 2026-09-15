# WiEvac Flex Threshold and Local Score Policy

 Kien truc dich: Formula Flex chay tai ESP32-S3 RX cho tung `link_id`. Pi nhan
EdgeResult, hop nhat va dieu huong; Pi khong duoc am tham thay the local score
bang mot nguong per-link moi trong duong RUN.

## 1. Mục đích

“Không khóa cứng ngưỡng” không có nghĩa cấm mọi con số. Flex nghĩa là quyết định không phụ thuộc mù quáng vào mức CSI thô riêng của một hành lang.

## 2. Phân loại mọi hằng số

| Loại | Ví dụ | Quy tắc |
|---|---|---|
| Protocol/tài nguyên | port, packet size, queue depth | Được phép; version/log/test overflow/drop |
| Radio/hardware | channel, MCS, bandwidth, TX power | Cấu hình; ghi manifest và kiểm thử nhiễu |
| DSP/thời gian | median window, EMA tau, window duration | Được phép có điều kiện; gắn sample rate/thời gian và sweep |
| Bảo vệ số học | epsilon, variance floor | Được phép; nêu đơn vị, miền và độ nhạy |
| Tương đối/thống kê | robust z, percentile, MAD score | Được phép có điều kiện; baseline từng link và kiểm FP/FN |
| Model decision | probability threshold, OOD gate | Phải tune/calibrate trên validation, khóa trước test và version |
| Vật lý/nghiệp vụ | “delta>3 có người”, “>60% kẹt” | Cấm dùng chung nếu chưa hiệu chuẩn và kiểm chứng |

Mọi pull request/thử nghiệm thay hằng số phải ghi loại, mục đích, nguồn và test.

## 3. Baseline

- Dùng nhiều mẫu trong warm-up; không dùng duy nhất mẫu đầu.
- Có center, spread, sample count, age và confidence.
- Baseline riêng cho từng `link_id`.
- Không update từ mọi mẫu mù quáng.
- Có policy chống contamination khi người/nhiễu kéo dài.
- Recalibration không mặc định hành lang đang trống.
- Shock/change point chuyển sang `DEGRADED`/`UNKNOWN` trước khi học nền mới.
- Lưu lý do và thời điểm baseline reset.
- Runtime phải duy trì short và long windows riêng cho từng link. Mỗi cửa sổ
  so sánh robust center, MAD/IQR-style spread, quality/valid ratio, normalized
  temporal motion, slope và short-vs-long center/spread shift. Flatline ổn định
  và nhiễu lặp lại được phép trở thành baseline; gap, drop, CSI invalid và
  change-point không được học nền. Mọi rebase phải có persistence và bằng
  chứng không có occupancy/blocking kéo dài.

## 4. Thời gian

- Window định nghĩa bằng thời gian thật.
- Nếu dùng sample count, phải kèm packet rate đo được.
- EMA nên biểu diễn/đánh giá theo time constant khi sample rate có thể đổi.
- Output có `age_ms`, window duration và latency.
- Tối ưu smoothing phải đo cả giảm nhiễu và tăng detection/recovery delay.

## 5. Công suất và radio

- Không mặc định TX power cực đại luôn tốt nhất.
- Radio config giữ ổn định trong một session; thay đổi phải mở session/version mới hoặc đánh dấu.
- Ngưỡng/normalization không được che gain shift, packet loss hoặc đổi nguồn.
- Hai link phải đo nhiễu chéo trước khi thêm TDMA/channel plan phức tạp.

## 6. Local score va phan tram

- Ratio so voi baseline/peak chi la normalized score.
- `local_passability_score` cua RX co the dung thang 0..100 de dieu khien va
  dieu huong, nhung mac dinh la diem ky thuat cuc bo, khong phai phan tram vat
  ly/nghiep vu hay xac suat co nguoi.
- Muon goi la phan tram thong qua that phai dinh nghia dai luong (dien tich
  trong, capacity, density hay flow), co ground truth va calibration curve tren
  nhieu corridor/session.
- Khi chua du dieu kien, dung ten `score`/`index`, ghi ro `score_semantics` va
  khong tuyet doi hoa so 0..100.
- `UNKNOWN` phai co score `null`; khong ma hoa du lieu mat/stale thanh `0`.
- Packet loss, jitter, RSSI thap va invalid CSI chu yeu lam giam quality/tang
  uncertainty. Chi dua vao `BLOCKED` khi sensing evidence va quality gate cung
  hop le.

## 7. Tiny AI correction tai RX

- Formula Flex la ket qua nen deterministic.
- Tiny AI chi duoc hieu chinh trong gioi han `max_correction`, kiem tra OOD/
  quality hoac yeu cau `DEGRADED`/`UNKNOWN`.
- Model phai co schema/version/hash, benchmark RAM/flash/latency va rollback.
- Bat dong lon giua Formula va AI phai duoc ghi nhan; khong chon ket qua dep hon.
- Trong RUN, RX gui mot EdgeResult chinh. Formula score, AI score va correction
  luu trong CAPTURE/debug de replay/ablation.

## 8. AI threshold

AI vẫn có threshold nên không được tuyên bố “zero threshold”.

- Threshold chọn từ validation theo cost FP/FN đã định.
- Không tune trên test set.
- Probability cần kiểm tra calibration; class weighting có thể làm probability lệch.
- Threshold, calibration và model/schema version phải đi cùng nhau.
- OOD/UNKNOWN là đầu ra hợp lệ.

## 9. Một ngưỡng được chấp nhận khi

1. Có mục đích và loại rõ.
2. Không phụ thuộc CSI thô của một hành lang hoặc đã hiệu chuẩn đúng phạm vi.
3. Có nguồn/tài liệu hoặc experiment.
4. Acceptance gate được chốt trước test.
5. Có sweep/sensitivity analysis.
6. Có cross-session và khi phù hợp có hold-out corridor.
7. Không che bằng tên `fully_adaptive`, `AI` hoặc `plug-and-play`.

## 10. Legacy values phải audit khi viết lại

- TX power cố định ở cực đại.
- RX median/EMA/dynamic-alpha và “100 packet = 1 second”.
- RX offline `delta_amp > 3.0`, `std_dev > 1.5`.
- Pi z-score `±2.0` (V4 compatibility only).
- Pi `peak_delta >= 4.0` (V4 compatibility only).
- Pi state `5/25/60%` (V4 compatibility only).
- Pi timeout/catch-up `5/30/60 s` (V4 compatibility only).
- Variance floors và smoothing rates.

Không phải tất cả đều phải xóa; phải phân loại, cấu hình hóa, thử độ nhạy hoặc loại nếu dùng sai vai trò.
