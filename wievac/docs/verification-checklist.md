# WiEvac Verification Checklist

Chỉ đánh dấu mục đã chạy thật. Mục không thể chạy phải ghi `N/A` hoặc `pending` cùng lý do.

## A. Repo và phạm vi

- [ ] File nằm đúng `firmware/transmitter`, `firmware/receiver`, `pi/app`, `data`, `config`, `docs` hoặc `scripts`.
- [ ] Không sửa file sinh tự động/`managed_components` thủ công.
- [ ] Không commit build/cache/credential/raw capture lớn/file `.part`.
- [ ] Working tree trước khi sửa đã được kiểm tra; thay đổi người dùng được giữ nguyên.
- [ ] Không tạo module/placeholder ngoài nhu cầu hiện tại.

## B. Data Contract

- [ ] Có magic/message type và `protocol_version`.
- [ ] Có `link_id`, `tx_id`, `rx_id`.
- [ ] Có sequence/window sequence, duration và sample count.
- [ ] Có `feature_schema_version`.
- [ ] Kiểu, đơn vị, thứ tự, endianness và flags được mô tả.
- [ ] Reject đúng wrong version/length/link/schema/NaN/Inf.
- [ ] Có packet test vector C↔Python.
- [ ] Đo duplicate/loss/reorder/stale.

## C. ESP32-S3 phát

- [ ] Mỗi TX có MAC/ID duy nhất và đúng link.
- [ ] Peer ghép cặp đúng; broadcast nếu dùng có lý do.
- [ ] Nhịp gửi thực tế và send success/failure được đo.
- [ ] Không phát mù khi callback/buffer đang lỗi.
- [ ] Channel/bandwidth/MCS/power/rate nằm trong cấu hình và log.
- [ ] Không mặc định TX power cực đại là tối ưu.
- [ ] Hai TX được test packet loss/nhiễu chéo trước khi thêm TDMA.
- [ ] Build đúng ESP-IDF, không có warning mới chưa giải thích.

## D. ESP32-S3 nhận

- [ ] Lọc đúng source MAC/TX/link trước DSP.
- [ ] Xử lý `first_word_invalid`.
- [ ] Parse đúng LLTF/HT-LTF/STBC-LTF và I/Q.
- [ ] Không cắt 64 subcarrier thiếu căn cứ.
- [ ] Tác động `channel_filter_en` lên roughness/correlation đã được kiểm tra.
- [ ] Callback ngắn; DSP/network/log nặng ở task khác.
- [ ] Window theo thời gian thật và gửi sample count.
- [ ] Có packet rate/loss/jitter/invalid/source-mismatch/queue-drop.
- [ ] Có RSSI/noise floor/gain metadata khi API hỗ trợ.
- [ ] Queue full/reconnect/Pi offline được test.
- [ ] Không dùng ngưỡng CSI thô như sự thật chung; local score phải ghi rõ
  semantics.
- [ ] Log reset đúng phạm vi phần mềm/phần cứng.
- [ ] RUN/CAPTURE mode không gây tải ngoài dự kiến.

### D2. EdgeResult local scoring

- [ ] RX tao mot EdgeResult chinh moi cua so, co score/state/quality/
  uncertainty va version.
- [ ] `UNKNOWN` co score null va khac `0`.
- [ ] Formula Flex chay tai RX voi warm-up, baseline va correction bound.
- [ ] Tiny AI (neu co) dung dung model hash/schema, khong vuot validity gate.
- [ ] Bat dong Formula/AI thanh `DEGRADED`/`UNKNOWN`, khong chon ket qua dep.
- [ ] Da do RAM/flash/heap/latency/airtime va numeric parity voi reference.
- [ ] V4 Formula/Pi chi duoc gan nhan compatibility/shadow.

## E. Pi ingest và recorder

- [ ] Mỗi `link_id` có state riêng; không khóa bằng IP.
- [ ] Ingest không bị train/file/UI chặn.
- [ ] Shared state thread-safe hoặc có ownership rõ.
- [ ] Không `except Exception: pass`.
- [ ] Stale/degraded/unknown handling đúng.
- [ ] File `.part` được finalize an toàn.
- [ ] Session/manifest/schema/versions được lưu.
- [ ] Raw completed session không bị ghi đè.
- [ ] Disk full/write error được log và không làm giả output.
- [ ] Pi khong tinh lai per-link CSI/Formula trong EdgeResult RUN.
- [ ] Trend/routing loai stale/unknown/invalid link va tra `NO_ROUTE` khi can.
- [ ] Scheduler co time-slot/rate/retry bounded va test voi 20-30 node.

## F. Formula

- [ ] Không dùng một mẫu đầu làm baseline.
- [ ] Có warm-up, spread, confidence và contamination policy.
- [ ] Recalibration không mặc định hành lang trống.
- [ ] Mọi hằng số được phân loại theo Flex.
- [ ] Sweep/sensitivity cho tham số quan trọng.
- [ ] `is_human=false` không vẫn làm score tăng trái policy.
- [ ] Z-score không gọi là SNR.
- [ ] Score không gọi là phần trăm thật nếu chưa hiệu chuẩn.
- [ ] Detection/recovery latency được đo.
- [ ] Local score co `score_semantics` va khong bi nham voi confidence.
- [ ] Packet loss/RSSI/jitter chu yeu lam giam quality/tang uncertainty.

## G. AI và dashboard

- [ ] Model inference thật mới được gọi là AI.
- [ ] RX tiny AI va Pi trend AI co target, schema, input va metric rieng.
- [ ] Sai/thiếu model báo `NOT_READY`/`INCOMPATIBLE`.
- [ ] OOD/quality xấu có `UNKNOWN`/`DEGRADED`.
- [ ] Formula/AI intermediate chỉ hiển thị/lưu riêng ở debug/CAPTURE; RUN có
  một EdgeResult chính và provenance.
- [ ] Disagreement được log, không âm thầm chọn kết quả đẹp.
- [ ] Probability được kiểm tra calibration trước khi gọi confidence.
- [ ] Model/schema/version/hash và rollback tồn tại.
- [ ] Candidate chạy shadow trước active.
- [ ] Model RX co RAM/flash/latency/rollback; model Pi co target/version/hash
  rieng.

## H. Dataset và nhãn

- [ ] Label có source/confidence/version và khoảng thời gian.
- [ ] Pseudo-label không gọi là ground truth.
- [ ] Không dùng Formula output làm sự thật để chứng minh AI.
- [ ] Split theo session/ngày/corridor; không leakage cửa sổ chồng.
- [ ] Có hold-out corridor.
- [ ] Synthetic/augmentation không dùng thay validation thực.
- [ ] Không suy 50 người từ 4–5 người như ground truth.
- [ ] Dataset/model ghi đúng feature schema.

## I. Field scenarios

- [ ] Trống yên.
- [ ] Trống nhưng Wi‑Fi nền cao.
- [ ] Quạt/cửa/xe đẩy/máy móc, không người.
- [ ] Một người đi qua nhiều lần/đứng yên/sát tường.
- [ ] Nhiều người với hướng/tốc độ khác nhau.
- [ ] Khởi động lúc trống và lúc có người.
- [ ] Đổi anten/vị trí/chiều dài/rộng hành lang.
- [ ] TX ngoài cặp đo.
- [ ] Hai link hoạt động đồng thời.
- [ ] Mất gói/reconnect/gain shift/reboot.
- [ ] Soak test và recovery.

## J. Metric và Flex

- [ ] FP và FN báo riêng.
- [ ] Passability/count error chỉ báo khi có ground truth.
- [ ] Latency p50/p95 và recovery time.
- [ ] CPU/RAM/flash/payload/airtime.
- [ ] Burst/loss/duplicate/out-of-order/reboot/mat rieng tung link.
- [ ] Soak 20-30 node va p95 ingest/command latency.
- [ ] Kết quả từng link/session/corridor, không chỉ metric gộp.
- [ ] Cross-session và hold-out corridor.
- [ ] Không tuyên bố “mọi hành lang” từ một địa điểm.

## K. Chống lặp/regression

- [ ] Có incident/experiment ID.
- [ ] Baseline và acceptance gates chốt trước test.
- [ ] Đã tìm rejected approach liên quan.
- [ ] Một experiment chính tại một thời điểm.
- [ ] Regression liên kết với thay đổi gây ra nó.
- [ ] Failed change đã rollback/cô lập.
- [ ] Accepted change có regression protection.
- [ ] Sau hai thử nghiệm suy đoán thất bại đã dừng lấy dữ liệu mới.

## L. Báo cáo hoàn thành

Codex phải báo:

1. Component/file đã thay đổi.
2. Protocol/schema/config/dependency bị ảnh hưởng.
3. Hằng số và công thức thêm/sửa/xóa cùng cơ sở.
4. Test thực sự đã chạy và metric có thật.
5. Tầng xác nhận cao nhất.
6. Rủi ro/giả định/field test còn thiếu.

## V4 compatibility evidence (2026-08-05)

The following is historical V4 evidence. It does not satisfy EdgeResult V5
active or field-verification gates.

- Software status: `implemented-pending-field-test`.
- Passed: 70 production-path Python tests, Python syntax compilation, V4 JSONL replay, dashboard JavaScript syntax check, and a fresh isolated ESP-IDF 5.4.4 V4 build for TX, RX-1, and RX-2; the manifest is explicitly `lab_only_unflashed`.
- Artifact evidence: isolated `.v4-build/manifest.json` records protocol/schema V4, RX IDs 1/2, topology provenance, SHA-256 values for all three images, and explicit no-flash/liveness/allowlist blockers; legacy `firmware/*/build` binaries remain excluded from deployment.
- The build gate now rejects `TEST_ONLY`, `CHANGE_ME`, and TEST-NET IPv4 placeholders. The checked-in ignored local configs therefore block a deployment build until real local values are supplied; existing artifacts built with those placeholders are test-only and must not be flashed.
- Feature `window_duration_ms` and `duration_s` are now derived from the TX CSI window (`window_start_tx_us` to `window_end_tx_us`), not the RX scheduler wake time, so the Pi contract remains valid under callback/scheduler delay.
- A quality-invalid dynamic frame and duplicate/out-of-order feature frame both break or reject temporal context; rejected features are recorded with `accepted=false` and cannot replace the latest accepted feature.
- RX UDP source sockets now bind the configured Pi UDP port, matching the Pi endpoint allow-list semantics; this is still pending hardware packet-capture confirmation.
- Corridor topology/radio/UDP keys are required at runtime; missing `tx_id`, link `tx_id`, UDP port, stale timeout, or required radio metadata now fails fast instead of using hidden defaults.
- Still pending: flash, boot logs, `paired_mask=0x03`, `network_ready=1`, Pi capture from both links, and all hardware/field acceptance tests.
- Not claimed: empty false-positive rate, person recall/F1, detection/recovery latency, broadcast CSI behavior, or cross-corridor generalization.
- Next evidence: packet logs from both RX devices, all requested labeled corridor sessions, Pi soak/disk-full/reconnect tests, RF/gain/interference tests, and directional-delay calibration.
