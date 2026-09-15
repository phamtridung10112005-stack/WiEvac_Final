# WiEvac Project Conventions

## 0. Current authority and scope lock

The current implementation is intentionally small and explicit:

`TX -> ESP32-S3 RX -> noise_filter -> formula_flex -> existing EdgeResult
pipeline -> Pi compact ingest/record/dashboard`.

The Pi active path is `scripts/run_pi_v5_compact.py` and its existing
`edge_result_v5_*` adapters. It does not perform a second Formula, fusion,
trend, routing or model-inference pass. Any document describing those roles is
design-only or historical unless the task explicitly changes the scope.

The active receiver files are `main/app_main.c`,
`main/edge_result_v5_pipeline.c/.h`, `main/noise_filter.c/.h`,
`main/formula_flex.c/.h` and `main/CMakeLists.txt`. Do not create a second
pipeline, reference implementation, runner, service, dashboard or similarly
named module. Do not delete or rename the existing pipeline without a
separate migration task that updates every consumer.

OTA is not part of the current source/configuration. Historical OTA wording,
third-party dependency names and old reports do not authorize adding OTA
code, flags, partitions, transport or servers. Manual USB/ESP-IDF flashing is
the only deployment path, and only when explicitly requested.

Every change task must use an explicit file allowlist. Files outside it are
read-only. Do not create backups, manifests, reports, logs, fixtures,
artifacts, isolated build trees or temporary source files unless the user
authorizes the exact destination. The only canonical receiver build output is
`firmware/receiver/build`; generated files there are inspected, never edited.

For a no-hardware task, do not SSH, flash, use serial, emulate hardware or run
a full test suite without a direct question it answers. Use focused source,
static, syntax and direct build checks only, and report hardware/field status
as unverified.

## 1. Pham vi va trang thai

Kien truc dich cua WiEvac la edge-local scoring:

```text
TX -> ESP32-S3 RX(link) -> Formula Flex + tiny AI -> EdgeResult
                                      |
                                      v
                         Pi ingest/record/display
```

V4 chi con la compatibility/control path ben trong firmware ESP-NOW. EdgeResult
V5 la thiet ke dich, chi tiet trong `edge-result-v5-contract.md`, va chi duoc
goi la active sau khi co build, replay, fault test, field test va rollback
evidence.

Khong sua code trong khi chi dang cap nhat tai lieu quy uoc.

## 2. Cau truc chuan

```text
wievac/
|-- AGENTS.md
|-- PROJECT_STATE.md
|-- README.md
|-- config/
|-- data/
|   |-- captures/
|   `-- labels/
|-- docs/
|-- firmware/
|   |-- receiver/
|   `-- transmitter/
|-- pi/
|   `-- app/
`-- scripts/
```

- Moi project ESP la mot project ESP-IDF doc lap.
- `firmware/transmitter/` chi tao luu luong do.
- `firmware/receiver/` thu CSI, DSP, Formula Flex va tiny AI neu duoc active.
- `scripts/run_pi_v5_compact.py` va cac adapter `edge_result_v5_*` la Pi
  active: ingest, record va display. Pi khong co runtime phan tich thu hai.
  OTA khong con la thanh phan hien tai.
- `docs/` chi chua quy uoc, contract, khoa hoc va evidence.
- `scripts/` chi chua cong cu dang dung.
- `docs/legacy_code_v1/` la archive rollback, khong duoc dua vao build active.

## 3. File sinh tu dong va secret

- Khong commit `build/`, `.cache/`, `__pycache__/`, `.part`, log runtime va raw
  capture lon.
- Khong sua truc tiep `managed_components/`.
- Dung file local/ignored cho credential, SSID, secret, MAC thuc te va IP.
- Raw session da dong la bat bien; file `.part` chi doi ten sau khi dong thanh
  cong.

## 4. Dinh danh

- `device_id`: thiet bi vat ly.
- `tx_id`: ESP phat.
- `rx_id`: ESP nhan.
- `link_id`: cap TX->RX va doan hanh lang tuong ung.
- `corridor_id`: hanh lang/vi tri.
- `session_id`: phien thu.
- `boot_id`: mot lan khoi dong cua node.
- `window_seq`: cua so ket qua cua node.

ID phai on dinh qua reboot va khong phu thuoc duy nhat vao DHCP/IP hoac byte
cuoi MAC. Moi link phai truy duoc:

```text
link_id -> tx_id + tx_mac + rx_id + rx_mac + corridor_id
```

## 5. Phan quyen tinh toan

### RX

RX phai tao mot `EdgeResult` chinh cho moi cua so thoi gian:

- kiem tra CSI/MAC/LTF/shape/radio metadata;
- loc va normalization co gioi han;
- tinh feature, Formula Flex va quality;
- chay tiny AI de hieu chinh Formula neu model hop le;
- tra score/state/quality/uncertainty va version.

Khong gui mot so score thieu context. Khong coi packet loss, RSSI thap hay
CSI invalid tu dong la doan hanh lang bi chan.

### Pi

Pi khong tinh lai Formula cho tung link trong duong EdgeResult active. Pi:

- xac thuc/chong trung/chong frame tre;
- theo doi tung `link_id` doc lap;
- luu EdgeResult va rejection metadata;
- cung cap recorder, dashboard va status API hien co. Trend/routing/fusion neu
  ton tai chi la offline/legacy.

### Gioi han file va module

- Moi task phai co `ALLOWED_FILES`; Codex khong duoc sua ngoai danh sach hoac
  tao module/entrypoint thay the.
- Receiver mac dinh chi gom `main/app_main.c`, `main/noise_filter.c/.h`,
  `main/formula_flex.c/.h` va `main/CMakeLists.txt` chi de dang ky cac file do;
  pipeline cu chi duoc sua khi task ghi ro codec/queue compatibility. Cam tao
  module C/H/PY, pipeline, reference hoac entrypoint thay the.
- Pi chi sua file active duoc dich danh. Khong tao runner, service, dashboard
  hoac core moi.
- `transmitter/`, `data/`, `config/`, `tests/` va `docs/legacy_code_v1/` la
  read-only neu khong co ngoai le.
- Khong tu tao backup/report/manifest/log/build/artifact moi trong task
  code-only; chi tao backup neu nguoi dung cho phep thay doi pha hoai. Output
  tam bat buoc phai duoc xoa sau khi kiem tra.
- OTA/rollback transport, OTA partition, OTA server va OTA build flags da bi
  loai bo; khong duoc them lai. Chu OTA trong dependency ben thu ba khong phai
  active WiEvac va khong duoc sua `managed_components/`.

V4 Formula/Pi duoc phep ton tai o duong replay/shadow de doi chung, khong duoc
am tham tron vao EdgeResult active.

## 6. EdgeResult semantics

`local_passability_score` la diem chuan hoa cuc bo 0..100. Neu chua co ground
truth/calibration, khong goi no la phan tram vat ly hay xac suat co nguoi.

State toi thieu:

- `PASSABLE`: du lieu hop le, score co the dung.
- `DEGRADED`: du lieu dung nhung quality/uncertainty giam.
- `BLOCKED`: diem cho thay doan co nguy co khong thong qua theo policy.
- `UNKNOWN`: khong du du lieu de ket luan.

`UNKNOWN` khong duoc ma hoa thanh `0`. Moi EdgeResult phai co:

```text
node_id, link_id, boot_id, window_seq
window_start, window_end, age_ms
local_passability_score, state, quality, uncertainty
formula_version, model_version, schema_version
sample_count, invalid_count, loss_ratio, jitter_ms
```

## 7. Data contract

Moi thay doi payload phai cap nhat TX, RX, Pi, packet vectors, replay va tai
lieu contract cung mot thay doi. V4 giu nguyen de tuong thich; EdgeResult V5
phai co magic/protocol/schema rieng.

Goi va lenh toi thieu phai co:

- version, type, length, CRC/authentication;
- link/node identity, boot/session, sequence va timestamp monotonic;
- score/state/quality/uncertainty va counters;
- formula/model hash/version;
- lenh co `command_id`, `config_version`, TTL, ACK va last-safe fallback.

Sai schema/model phai bi tu choi va ghi reason. Khong patch im lang giua TX/RX/Pi.

## 8. Du lieu

```text
data/captures/pi-01/<corridor_id>/<link_id>/<session_id>/
data/labels/<session_id>.csv
```

Session can co data, manifest dieu kien, labels/su kien neu co, firmware,
protocol, feature/model version va radio metadata. Che do:

- `RUN`: EdgeResult gon, uu tien airtime va do tre.
- `CAPTURE`: CSI gan tho/snapshot co gioi han cho replay, ablation va train.

Khong truyen CSI tho lien tuc trong production neu chua do queue, airtime,
packet loss va tac dong nguoc len phep do.

## 9. Formula va AI

- Formula Flex va tiny AI cua RX phai co version, don vi, gioi han correction,
  acceptance gate va rollback.
- AI thieu model, sai schema hoac OOD tra `NOT_READY`, `INCOMPATIBLE` hoac
  `UNKNOWN`.
- Formula va AI co the duoc ghi rieng trong debug/CAPTURE, nhung EdgeResult
  production chi co mot ket qua chinh.
- Neu Formula va AI bat dong lon, tang uncertainty/DEGRADED; khong chon ket qua
  dep hon mot cach im lang.
- Pi chi dung EdgeResult de fusion, forecasting, routing va dieu phoi.

## 10. Branch va thay doi

- Tinh nang: `feature/<task>`.
- Sua loi: `fix/<task>`.
- Thi nghiem: `experiment/<task>`.

Khong thay doi dong thoi firmware DSP, packet schema, Formula, model va route
policy neu khong co experiment/co so de kiem tra rieng. Moi thay doi tuan thu
`engineering-change-protocol.md`, `experiment-ledger.md` va
`verification-checklist.md`.
