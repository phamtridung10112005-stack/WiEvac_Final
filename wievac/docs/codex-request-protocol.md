# Codex Short-Request Protocol

## 0. GOAL EXECUTION LOCK (CURRENT IMPLEMENTATION)

`AGENTS.md` Section 0 and `PROJECT_STATE.md` are the authority for the
current checkout. Older design prose is historical unless the task explicitly
promotes it. Before writing anything, Codex must establish:

`OBJECTIVE`, `ALLOWED_FILES`, `READ_ONLY_FILES`, `FORBIDDEN_ACTIONS`,
`ACCEPTANCE_GATES`, and `STOP_CONDITIONS`.

The active RX source allowlist is, unless a task narrows it:

- `firmware/receiver/main/app_main.c`
- `firmware/receiver/main/edge_result_v5_pipeline.c`
- `firmware/receiver/main/edge_result_v5_pipeline.h`
- `firmware/receiver/main/noise_filter.c`
- `firmware/receiver/main/noise_filter.h`
- `firmware/receiver/main/formula_flex.c`
- `firmware/receiver/main/formula_flex.h`
- `firmware/receiver/main/CMakeLists.txt`

`edge_result_v5_pipeline.c/.h` are existing active dependencies. They may not
be deleted, renamed, duplicated or replaced merely to force a smaller file
count. RX ownership remains explicit: `app_main` orchestrates hardware and
transport; `noise_filter` filters and validates; `formula_flex` owns baseline,
rebase, normalization and score semantics; the existing pipeline owns window,
queue and codec compatibility.

For a software-only RX goal, Pi active files are read-only inspection targets
unless explicitly listed, TX/config/data/tests/archive are read-only, and no
new source, script, runner, dashboard, report, manifest, backup, log,
fixture, artifact or directory may be created. Internet research may inform
the design, but no downloaded code, binary, dataset or dependency may enter
the repository.

OTA is removed from the current WiEvac implementation. Do not restore OTA
downloaders, controllers, servers, manifests, rollback tasks, OTA flags or
OTA partitions. Do not SSH, flash, open serial or touch COM ports unless the
same task explicitly authorizes it. If a necessary change falls outside the
allowlist, stop with `BLOCKED_FILE_SCOPE`.

The only accepted direct receiver build output is
`firmware/receiver/build`. Never use isolated `.edge-result-*` build output for
this workflow and never edit generated `build/config/sdkconfig.h`.

## Mục đích

Người dùng được phép giao việc bằng tiếng Việt tự nhiên, ngắn và không cần thuật ngữ kỹ thuật. Codex tự chuyển yêu cầu thành task brief nội bộ rồi làm đúng phạm vi.

Ví dụ hợp lệ:

- `xem lại code ESP nhận`
- `debug Pi bị nhảy 13% lúc trống`
- `nghĩ cách giảm nhiễu`
- `thêm AI`
- `kiểm tra có khóa cứng không`
- `làm tiếp phần hai node`

Không trả lại một prompt dài để bắt người dùng gửi lại.

## 1. Task brief nội bộ

Codex tự xác định:

1. Intent: review, chẩn đoán, sửa, tối ưu, đề xuất hay triển khai.
2. Target: TX, RX, Pi, protocol, data/labels, Formula, AI hay dashboard.
3. Goal và hành vi mong đợi.
4. File được phép chạm và phần phải giữ nguyên.
5. Ràng buộc Flex, khoa học, thời gian thực và tương thích.
6. Bằng chứng hiện có: code, ảnh, log, session, model hoặc phần cứng.
7. Giả thuyết và bước nhỏ nhất đủ làm.
8. Test/metric cần dùng.
9. Deliverable: chẩn đoán, bản vá, tài liệu hay kế hoạch.

Task brief mặc định không in ra toàn bộ để tiết kiệm token.

## 2. Ý nghĩa mặc định

| Người dùng nói | Codex thực hiện |
|---|---|
| `xem`, `review`, `kiểm tra` | Đọc và đánh giá; không sửa |
| `đề xuất`, `nghĩ cách`, `ý tưởng` | Đưa phương án, trade-off và phép thử; không sửa |
| `debug` | Định vị nguyên nhân; chỉ sửa nếu yêu cầu bao hàm sửa |
| `sửa`, `fix` | Chẩn đoán, sửa tối thiểu và kiểm chứng |
| `viết lại` | Thiết kế lại đúng phạm vi, sửa tại chỗ trong `ALLOWED_FILES` và kiểm chứng |
| `tối ưu` | Đo baseline trước, tối ưu bottleneck có bằng chứng |
| `làm luôn`, `triển khai` | Thực hiện phương án đã chốt và chạy kiểm tra |
| `làm tiếp` | Đọc `PROJECT_STATE.md`, Git và ledger rồi tiếp tục an toàn |
| ảnh + triệu chứng | Áp dụng `incident-debug-protocol.md` |

## 3. Ánh xạ tên thông dụng sang repo

- `ESP phát`, `con phát`: `firmware/transmitter/`.
- `ESP nhận`, `lọc CSI`, `con thu`: `firmware/receiver/`.
- `file Pi`, `lõi Pi`: chỉ file active được nêu đích danh; mặc định là `scripts/run_pi_v5_compact.py` và các adapter `edge_result_v5_*` hiện có.
- `dữ liệu`: `data/captures/`.
- `nhãn`: `data/labels/`.
- `cấu hình node`: `config/`.
- `ba chỉ số`: baseline V1 gồm `delta_amp`, `std_dev`, `sad_value`; không giới hạn feature mới.
- `AI`: model đã train/inference thật; heuristic không được tính là AI.
- `EdgeResult`: một kết quả local duy nhất của RX, gồm score/state/quality/
  uncertainty/version; không phải chỉ một số rời rạc.
- `Formula Flex`: đường deterministic chạy trên RX trong kiến trúc đích.
- `tiny AI`: model nhẹ trên RX chỉ hiệu chỉnh Formula trong biên giới hạn;
  `Pi trend AI` là model khác dùng để dự đoán xu hướng toàn mạng.
- `độ thông qua`: nếu người dùng muốn score local, dùng
  `local_passability_score`; giữ `UNKNOWN` khác `0` và không tự gọi là phần
  trăm vật lý khi chưa có ground truth/calibration.
- `node`: nếu có thể gây nhầm, Codex phải chuyển sang `tx_id`, `rx_id` hoặc `link_id`.

Nếu có nhiều file phù hợp, Codex tìm trong repo và trạng thái dự án trước khi hỏi.

## 4. Khi được tự giả định

Chỉ tự giả định khi lựa chọn:

- an toàn và dễ hoàn tác;
- không xóa dữ liệu;
- không đổi protocol/feature schema nếu chưa được yêu cầu và chưa cập nhật
  `edge-result-v5-contract.md`/packet tests;
- không coi EdgeResult V5 là đã triển khai nếu chưa có firmware build, replay,
  fault test và field evidence;
- không thay kiến trúc;
- không thêm dependency lớn;
- có thể kiểm chứng mà không cần phần cứng.

Nêu ngắn gọn giả định quan trọng.

### Khóa phạm vi file

- Mỗi task phải xác định `ALLOWED_FILES` trước khi sửa. Chỉ được sửa file
  trong danh sách; không tạo file thay thế để né phạm vi.
- Task receiver mặc định chỉ được sửa `firmware/receiver/main/app_main.c`,
  `noise_filter.c/.h`, `formula_flex.c/.h` và `main/CMakeLists.txt` chỉ để
  đăng ký đúng các file trên. `edge_result_v5_pipeline.c/.h` chỉ được chạm
  khi task ghi rõ queue/codec compatibility. Cấm tạo C/H/PY module thay thế.
- Task Pi mặc định chỉ được sửa file Pi được nêu đích danh; không tạo runner,
  service, dashboard hoặc core mới.
- `firmware/transmitter/`, `data/`, `config/`, `tests/` và
  `docs/legacy_code_v1/` là read-only nếu task không ghi rõ ngoại lệ.
- Không tự tạo backup, report, manifest, log, build output, artifact, module
  hoặc thư mục mới. Chỉ tạo backup timestamped khi người dùng cho phép rõ
  ràng một thay đổi xóa/ghi đè; output tạm phải xóa sau kiểm tra.
- Nếu ownership hoặc phạm vi không rõ, dừng với `BLOCKED_FILE_SCOPE` thay vì
  tự chọn kiến trúc.

### OTA

- OTA đã bị loại khỏi firmware và Pi runtime hiện tại. Không thêm lại OTA
  downloader, manifest transport, rollback task, OTA partition, Pi OTA server,
  OTA controller hoặc tham số build OTA.
- Firmware hiện tại chỉ được nạp thủ công bằng USB/ESP-IDF khi người dùng yêu
  cầu; không tự SSH, flash hoặc OTA.

## 5. Khi bắt buộc hỏi

Hỏi tối thiểu khi:

- hai cách hiểu dẫn tới kiến trúc khác nhau;
- phải xóa/ghi đè dữ liệu hoặc đổi protocol không tương thích;
- cần định nghĩa “% thông qua”, ground truth hoặc mức rủi ro chưa có;
- không xác định được link/corridor/session;
- thao tác ảnh hưởng hệ thống đang chạy thật;
- cần quyền truy cập, secret hoặc phần cứng chưa có.

Nếu phần cứng chưa có, chỉ chạy review/build/test rẻ và liên quan trực tiếp; không giả lập phần cứng hoặc chạy full suite chỉ để tăng số test. Phải ghi rõ field validation còn chờ và test nào đã bỏ qua.

## 6. Quy tắc theo nhiệm vụ

### Review

- Không sửa file.
- Phân loại: lỗi chắc chắn, rủi ro, đề xuất.
- Ghi tác động và cách kiểm chứng.

### Debug/sửa lỗi

- Tái hiện hoặc xác định bằng chứng tối thiểu.
- Lần theo TX → RX → protocol → Pi → Formula/AI → dashboard/data.
- Không chỉnh ngưỡng ngay khi chưa loại trừ sai nguồn, packet loss, gain, baseline hoặc schema.
- Sửa phạm vi nhỏ nhất và thêm regression check.

### Tối ưu

- Chốt metric trước: FP, FN, MAE, latency, CPU, RAM, airtime hoặc payload.
- So sánh trước/sau trên cùng dữ liệu/điều kiện.
- Không tuyên bố tốt hơn nếu chưa đo.

### Ý tưởng/thuật toán

- Nêu vấn đề thực tế.
- Phân biệt nguồn khoa học và giả thuyết.
- Không giới hạn vào ba feature cũ.
- Khuyến nghị thí nghiệm nhỏ nhất có thể bác bỏ.
- Không viết code nếu người dùng chỉ yêu cầu ý tưởng.

### Viết lại/triển khai

- Giữ bản baseline có thể chạy.
- Chốt data contract trước khi sửa đồng thời ESP và Pi.
- Không tạo placeholder giả hoặc gọi phần chưa có là hoàn thành.
- Build/test và tự review trước khi bàn giao.

## 7. Cách báo kết quả

Mặc định trả lời:

1. Kết quả chính.
2. File/tầng đã thay đổi.
3. Test và bằng chứng.
4. Rủi ro hoặc phần chờ field test.

Không bắt người dùng đọc toàn bộ quá trình suy luận.

