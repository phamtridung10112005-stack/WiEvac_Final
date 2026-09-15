# WiEvac Rigorous Engineering Change Protocol

## 1. Phạm vi

Áp dụng khi sửa lỗi, viết lại, tối ưu hoặc thay:

- `firmware/transmitter/`;
- `firmware/receiver/`;
- `pi/app/`;
- protocol/feature schema;
- Formula/baseline;
- recorder/dataset/labels;
- LightGBM/dashboard/điều hướng.

Kien truc dich phan quyen ro: RX tao EdgeResult local (Formula Flex + tiny AI
tuy chon); Pi ingest/fusion/trend/routing/recorder. V4 Pi Formula chi la
compatibility/shadow khi EdgeResult V5 dang duoc trien khai.

Mục tiêu: tránh sửa theo cảm tính, tránh regression dây chuyền và giữ khả năng rollback.

## 2. Thang bằng chứng

- Nguồn khoa học: chứng minh phương pháp có cơ sở chung.
- Static review: chứng minh code/công thức có vẻ nhất quán.
- Build/unit test: chứng minh phần mềm biên dịch/chạy ca kiểm thử.
- Packet test: chứng minh C/Python tương thích giao thức.
- Replay: chứng minh hành vi trên dữ liệu đã có.
- Bench test: chứng minh trên cặp ESP trong kịch bản kiểm soát.
- Field test: chứng minh trong hành lang thực.
- Cross-corridor: bằng chứng về tổng quát hóa.

Tầng thấp không thay thế tầng cao. Nếu thiếu phần cứng, kết luận là `implemented-pending-field-test`.

## 3. Giai đoạn 0 — Đóng khung

Trước khi sửa, tạo incident/experiment trong `experiment-ledger.md`:

1. Observed và expected.
2. Component/path chịu tác động.
3. Baseline commit/firmware/protocol/schema/model.
4. Dataset/session/link/corridor dùng kiểm tra.
5. Metric mục tiêu và protected invariants.
6. Acceptance/rejection gates định trước.
7. Hardware/data hiện có và phần còn thiếu.

Không đổi tiêu chí sau khi nhìn kết quả chỉ để chấp nhận phương án.

## 4. Giai đoạn 1 — Kiểm tra đường dữ liệu

Theo thứ tự:

1. TX identity/config/sequence/send success.
2. Kênh và nhiễu chéo giữa các TX.
3. RX source filter/CSI layout/invalid/gain/queue.
4. Protocol version/length/endianness/sequence/staleness.
5. Pi ingest/link state/recorder.
6. RX Formula Flex/quality gate/EdgeResult semantics.
7. RX tiny-AI schema/model/OOD/calibration va resource gate.
8. Pi fusion/trend/routing/dashboard.

Không mặc định mọi lỗi phần trăm đều do threshold Pi.

## 5. Giai đoạn 2 — Giả thuyết bác bỏ được

Mỗi giả thuyết ghi:

- cơ chế;
- bằng chứng ủng hộ;
- bằng chứng phản đối;
- phép đo bác bỏ;
- component cần sửa;
- rủi ro downstream.

Xếp hạng theo bằng chứng, mức nguy hiểm và chi phí kiểm tra. Không sửa đồng thời nhiều nguyên nhân độc lập.

## 6. Giai đoạn 3 — Thiết kế thay đổi

Bản sửa phải:

- nhỏ nhất đủ kiểm tra giả thuyết;
- dễ rollback;
- giữ baseline hoặc compatibility path;
- công khai thay đổi protocol/schema/config;
- liệt kê hằng số, công thức và dependency mới;
- dự báo CPU/RAM/flash/payload/airtime/latency;
- chỉ rõ file sẽ chạm và file không chạm;
- có regression test/invariant phù hợp.

### Thay protocol

Chốt Data Contract trước, rồi cập nhật:

1. TX payload nếu liên quan.
2. RX decode/feature packet.
3. Pi decoder.
4. Packet test vector.
5. Dataset/manifest/schema docs.

Không dựa chỉ vào `sizeof(struct)` mà thiếu version/endianness/validation.

### Thay DSP/feature

- Giữ feature V1 khi cần đối chứng.
- Đổi `feature_schema_version`.
- Có replay và ablation.
- Đo sample-rate sensitivity và latency.
- Nếu chuyen Formula sang RX, phai kiem tra numeric parity voi duong V4 shadow,
  correction bound, heap/RAM/flash va airtime.
- EdgeResult chi co mot ket qua production; intermediate Formula/AI luu o
  CAPTURE/debug.

### Thay AI

- Khóa dataset split và metric trước train.
- Không thay cả label policy và model rồi gán lợi ích cho riêng model.
- Candidate không được ghi đè active.
- Phan biet model `rx-tiny-ai` va `pi-trend`; schema, hash, target va rollback
  cua hai model la doc lap.
- Tiny AI tai RX khong duoc vuot validity gate; bat dong lon voi Formula phai
  tang uncertainty/DEGRADED/UNKNOWN.

## 7. Giai đoạn 4 — Kiểm chứng

Chạy theo khả năng:

1. Static/math/concurrency review.
2. ESP build và Python syntax/unit tests.
3. Cross-language packet tests.
4. Replay cũ/mới trên cùng session.
5. Bench một link.
6. Bench hai link/nhiễu chéo.
7. Field scenarios.
8. Cross-session/corridor.
9. Soak/reconnect/recovery.
10. Mo phong 20-30 node va test scheduler/airtime.

Báo tầng cao nhất thực sự vượt qua và phần chưa chạy.

## 8. Metric nhiều chiều

Không chấp nhận chỉ bằng một biểu đồ đẹp. Xem đồng thời:

- FP lúc trống;
- FN khi có người;
- passability/count error khi có ground truth;
- detection/recovery latency;
- stability và stale behavior;
- packet loss/jitter/drop;
- CPU/RAM/flash/payload/airtime;
- cross-corridor performance;
- AI calibration/OOD nếu có.

Không chấp nhận cải thiện metric chính nếu làm hỏng protected invariant hoặc metric an toàn ngoài giới hạn.

## 9. Chống regression dây chuyền

- Một incident chỉ có một experiment `in-progress`.
- Không chồng B lên A khi A chưa accepted; rollback hoặc cô lập A.
- Regression Y phải liên kết tới experiment tạo ra nó.
- Bản sửa Y phải chạy lại test của X và test regression Y.
- Không sửa tiếp X→Y→Z trên working tree không xác định được nguyên nhân.
- Sau hai thử nghiệm suy đoán thất bại không có dữ liệu mới: `blocked-needs-evidence`.

## 10. Trạng thái hợp lệ

- `planned`
- `in-progress`
- `accepted`
- `rejected`
- `inconclusive`
- `implemented-pending-field-test`
- `rolled-back`
- `blocked-needs-evidence`

Chỉ dùng “đã sửa”, “ổn định”, “chính xác” hoặc “Flex” khi bằng chứng đạt đúng phạm vi tuyên bố.
