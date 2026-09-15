# WiEvac Screenshot and Field-Incident Debug Protocol

## 1. Kích hoạt

Áp dụng khi người dùng gửi ảnh terminal/dashboard/serial/CSV/biểu đồ kèm mô tả hiện tượng, ví dụ:

> Không có người nhưng lên 13%; có người đi qua lại không tăng.

Đây là báo cáo lỗi hợp lệ. Không bắt người dùng viết lại prompt kỹ thuật.

Nếu người dùng yêu cầu sửa, đồng thời áp dụng:

- `engineering-change-protocol.md`;
- `experiment-ledger.md`;
- `verification-checklist.md`.

## 2. Trích xuất bằng chứng

Chỉ ghi điều nhìn thấy:

- thời gian, `link_id`/node/IP nếu có;
- feature, quality metadata, baseline;
- Formula/AI output và confidence/state/reason;
- packet rate/loss/jitter/stale/reconnect;
- model/protocol/schema version;
- error/warning/trace.

Không bịa phần ảnh bị khuất, số người, timeline hoặc điều kiện vật lý.

## 3. Chuyển mô tả thành hồ sơ

- **Observed**: hành vi thực tế.
- **Expected**: hành vi mong muốn.
- **Impact**: sai cảnh báo, sai điều hướng, mất dữ liệu hay chỉ lỗi UI.
- **Known context**: link/session/firmware/model.
- **Unknown**: timeline người, nhiễu, radio config, dữ liệu trước/sau.

Người dùng không cần tự dùng từ false positive/false negative.

## 4. Lần theo đường dữ liệu

### TX — `firmware/transmitter/`

- ID/MAC/link có đúng và duy nhất?
- Channel/bandwidth/MCS/power/rate có đúng session?
- Sequence có tăng, send callback có lỗi?
- Có TX khác gây airtime/collision?

### RX — `firmware/receiver/`

- Có lọc đúng source MAC/link?
- Có xử lý `first_word_invalid` và LTF/layout?
- Packet rate/sample count có đúng cửa sổ?
- RSSI/noise floor/gain/invalid/drop có đổi?
- Feature phản ứng hay lỗi nằm trước feature?

### Protocol

- Version/length/endianness/schema đúng?
- Có loss/reorder/duplicate/stale?
- Có nhầm `node_id` với `link_id`?

### Pi — `pi/app/`

- Ingest và recorder có exception/concurrency/disk error?
- EdgeResult co dung identity/boot/window/timestamp/CRC khong?
- State cua moi `link_id` co doc lap va freshness dung khong?
- Pi co am tham tinh lai per-link score trong EdgeResult RUN khong?
- Trend model co dung model/schema/OOD va du lieu stale khong?
- Routing co loai link `UNKNOWN`/stale va bao `NO_ROUTE` khi can khong?
- Dashboard có hiển thị sai output?

### RX local score — `firmware/receiver/`

- Formula Flex co warm-up/baseline/quality gate dung khong?
- Tiny AI co dung model/hash/schema va correction bound khong?
- Bat dong Formula/AI co thanh `DEGRADED`/`UNKNOWN` khong?
- `UNKNOWN` co bi ma hoa nham thanh `0%` khong?

Không mặc định lỗi nằm ở threshold Pi.

## 5. Không chữa triệu chứng mù quáng

Khi vừa FP vừa FN, không:

- chỉ tăng/hạ một ngưỡng;
- thêm smoothing mà không đo latency;
- ép một người ứng với một phần trăm;
- reset nền liên tục;
- train model từ pseudo-label của công thức đang lỗi;
- gọi nhiễu không biết là “đã lọc”.

Ưu tiên kiểm tra nguồn CSI, packet quality, gain, baseline, feature separability và schema.

## 6. Bằng chứng đủ/chưa đủ

### Đủ

1. Tạo incident/experiment.
2. Chốt acceptance gates.
3. Sửa nguyên nhân nhỏ nhất.
4. Giữ rollback.
5. Replay/bench/field theo khả năng.
6. Đánh giá cả FP, FN, latency và regression.

### Chưa đủ

1. Xếp hạng giả thuyết.
2. Loại giả thuyết mâu thuẫn với bằng chứng.
3. Bổ sung logging/test tối thiểu nếu phạm vi cho phép.
4. Yêu cầu đúng một session hoặc phép đo phân biệt được nguyên nhân.

Ví dụ yêu cầu dữ liệu:

> Thu cùng `link_id`: 30 giây trống, 10 giây một người đi qua, 30 giây trống; ghi timeline, feature, RSSI/noise floor/gain, packet loss và Formula/AI output.

Không hỏi chung “gửi thêm thông tin”.

## 7. Khi chưa có Pi/phần cứng

Có thể:

- review code;
- tạo packet/log test;
- kiểm tra công thức và concurrency;
- replay dữ liệu đã có;
- triển khai diagnostic logging.

Không thể xác nhận:

- giảm nhiễu RF thật;
- detection accuracy thực địa;
- hai-node coexistence;
- cross-corridor Flex.

Kết luận phải là `implemented-pending-field-test` hoặc `inconclusive`.

## 8. Cách trả lời

1. Ảnh/log chứng minh gì.
2. Nguyên nhân đã chứng minh hoặc giả thuyết ưu tiên.
3. Đã sửa/kiểm tra gì.
4. Kết quả trước–sau có thật.
5. Phép thử cụ thể tiếp theo nếu còn thiếu.
