# WiEvac - Codex Project Rules

## 0. ACTIVE AUTHORITY AND GOAL EXECUTION LOCK

For the current implementation, this section, Section 4 below and
`PROJECT_STATE.md` override any future/target architecture prose in other
documents. The active Pi is compact ingest, per-link state, recorder and
dashboard/status only. Fusion, trend, routing, model registry and all OTA text
elsewhere are DESIGN_ONLY/HISTORICAL and must not be implemented in this goal.

Before every goal changes a file, Codex must determine and report internally:
`OBJECTIVE`, `ALLOWED_FILES`, `READ_ONLY_FILES`, `FORBIDDEN_ACTIONS`,
`ACCEPTANCE_GATES` and `STOP_CONDITIONS`. No write is allowed before this
preflight.

- Only files in `ALLOWED_FILES` may be edited. Never create a replacement
  module, runner, service, dashboard, core, pipeline, script or directory to
  evade the allowlist.
- Never create backups, reports, manifests, logs, fixtures, artifacts or
  isolated build trees unless the user names the exact path and authorizes it.
- If a required change falls outside the allowlist, stop with
  `BLOCKED_FILE_SCOPE`; do not work around it by copying or rewriting code.
- Internet research is allowed for papers, formulas and technical references,
  but do not download code, binaries, datasets or dependencies into the repo.
  Cite sources in chat and distinguish reference claims from verified code.
- Current active compact wire authority is `WIV5`, protocol `5`, feature
  schema `7`. Do not create another V5/V6 codec or runner because of
  historical filenames.

## 0.1 GOAL PROMPT CONTRACT

Every `/goal` must state, before any write:

- `ROLE` (normally senior ESP32-S3/RF/Wi-Fi CSI and signal-processing engineer);
- `OBJECTIVE`;
- `ALLOWED_FILES`;
- `READ_ONLY_FILES`;
- `FORBIDDEN_ACTIONS`;
- `ACCEPTANCE_GATES`;
- `STOP_CONDITIONS`;
- explicit hardware/SSH authorization, if build, flash, serial or Pi deployment
  is required.

Codex reads this file, `PROJECT_STATE.md`, and the named active files. "Read
the project/folder" is not permission to edit unrelated files and is not a
request to scan or rewrite historical data. Do not create a file, module,
runner, service, build tree, report, manifest or backup unless its exact path
is allowed.

### Canonical active files

- RX: `firmware/receiver/main/app_main.c`,
  `edge_result_v5_pipeline.c/.h`, `noise_filter.c/.h`, `formula_flex.c/.h`,
  and `main/CMakeLists.txt`.
- RX ownership: `app_main` handles CSI/hardware/callback/queue/orchestration/
  transport; `noise_filter` handles validation/filter/robust statistics;
  `formula_flex` handles baseline/rebase/normalization/score/state/uncertainty;
  the existing pipeline owns window/queue/codec compatibility.
- Pi: `scripts/run_pi_v5_compact.py` plus the existing
  `pi/app/edge_result_v5.py`, `edge_result_v5_runtime.py`,
  `edge_result_v5_service.py` and `edge_result_v5_dashboard.py` only.
- TX, data, config topology, tests, docs/archive and `managed_components` are
  read-only unless the goal explicitly names an exception.

### Manual handoff and generated output

The default goal is software-only: no SSH, OTA, flash, serial monitor or COM
access. An explicit `/goal` that names build/flash/serial/SSH/Pi deployment is
sufficient authorization for that goal. The exact COM, host, identity and
acceptance checks must still be stated. If the user supplies a password, use it
only in an ephemeral authentication session; never echo, log, persist or write
it to source/config/report/history. TX/COM20 remains forbidden unless the goal
explicitly names it. The handoff must state project path, active RX ID, binary
path and RX-1=`COM16`/RX-2=`COM22` mapping.

`firmware/receiver/build/`, `build/config/sdkconfig.h`, `.elf`, `.bin` and
`compile_commands.json` are generated output, never source and never edited
directly. The canonical build output is only `firmware/receiver/build/`;
do not use isolated `.edge-result-*` output for this workflow.

Completion may say `BUILD_PASS` or `IMPLEMENTED_PENDING_FIELD_TEST`. It may not
say production-ready, accurate, calibrated, field-verified, Tiny-AI-active,
C/Python-parity-complete, OTA-ready or flash-success without independent proof.

## 1. Muc tieu va thu tu uu tien

WiEvac dung Wi-Fi CSI tu cac cap ESP32-S3 phat-thu de uoc luong do thong qua
cua tung doan hanh lang. Trong pham vi hien tai, moi RX tinh ket qua cho link
cua minh; Pi chi ingest, record va display EdgeResult compact.

Thu tu uu tien bat buoc:

1. An toan va trung thuc khoa hoc.
2. Ket qua local cua tung link phai doc lap, truy vet duoc va fail-closed.
3. Flex: chuyen sang hanh lang hoac bo tri khac ma khong phu thuoc nguong CSI
   tho cua noi cu.
4. Gan thoi gian thuc: biet ro tuoi du lieu, do tre va thoi gian phuc hoi.
5. Kha nang mo rong, tai lap va rollback bang cach nap lai thu cong qua
   USB/ESP-IDF; khong co rollback tu dong va OTA khong thuoc pham vi firmware
   hien tai.

Khong lam dep dashboard bang cach che `UNKNOWN`, `NOT_READY`, stale,
uncertainty cao hoac mat du lieu.

## 2. Trang thai kien truc

- Protocol V4 va duong V4 hien tai la compatibility/replay path. Neu can
  rollback firmware, nguoi dung tu nap lai image cu qua USB/ESP-IDF; khong tao
  co che rollback tu dong.
- Active compact EdgeResult uses the C receiver path plus the existing Python
  Pi decoder/runtime. A build or deployment claim requires fresh generated
  config, boot and live evidence; source text alone is not deployment proof.
- V4/replay/legacy paths are preserved but inactive/read-only by default. Do not
  delete, reactivate or substitute them for the compact runtime unless the goal
  explicitly authorizes it.
- OTA da bi loai khoi WiEvac. Khong duoc them lai `esp_ota_ops`,
  `esp_http_client`, manifest downloader, rollback task, OTA partition,
  OTA controller, OTA server hoac tham so build OTA.
- Moi bao cao phai tach ro `DESIGN_ONLY`, `IMPLEMENTED`, `SHADOW`, `ACTIVE`
  va `FIELD_VERIFIED`.

## 3. Cau truc repo

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

Anh xa bat buoc:

- ESP phat: `firmware/transmitter/`.
- ESP nhan CSI va tinh EdgeResult: `firmware/receiver/`.
- Chuong trinh tren Pi: `pi/app/`.
- Du lieu thu: `data/captures/`.
- Nhan va su kien doi chung: `data/labels/`.
- Cau hinh node/link: `config/`.
- Quy trinh va co so khoa hoc: `docs/`.
- Cong cu build/deploy/replay: `scripts/`.

Khong tao module placeholder hoac tang thu muc chi de danh dau y tuong tuong
lai.

## 4. Phan quyen tinh toan

### ESP32-S3 phat (TX)

- Chi tao luu luong do va beacon/schedule neu giao thuc yeu cau.
- Moi TX co `tx_id` va MAC duy nhat; sequence va timestamp phai don dieu.
- Khong tu suy luan do thong qua cua hanh lang.
- Theo doi nhip phat va ket qua gui; khong lay so vong lap lam so goi thanh
  cong.
- Kenh, MCS, bandwidth, cong suat va packet rate la cau hinh co ghi log.

### ESP32-S3 nhan (RX)

RX chiu trach nhiem cho mot `link_id` va doan ma link do dai dien:

1. Loc MAC TX da ghep cap va kiem tra LTF, `first_word_invalid`, do dai CSI,
   shape va metadata radio.
2. Loc/normalization/DSP cuc bo theo cua so thoi gian that.
3. Tinh feature va Formula Flex local.
4. Neu co model tuong thich, chay tiny AI de hieu chinh hoac kiem tra ket qua
   Formula; AI khong duoc tu y vuot quality gate.
5. Xuat **mot EdgeResult chinh** cho moi cua so, kem quality, uncertainty,
   state, timestamp, sequence va version.

Callback CSI phai ngan; DSP, model inference, UDP va ghi log nang chay o task
khac. Khi Pi mat hoac du lieu khong dat quality, RX tiep tuc xu ly co bo nho
gioi han nhung EdgeResult phai la `UNKNOWN`/`STALE`, khong phan doan thanh
`0%` hay `PASSABLE`.

### Raspberry Pi 5

Pi active hien tai chi lam ingest EdgeResult compact, ghi recorder va hien thi
dashboard/status API. Entrypoint active la file hien co
`scripts/run_pi_v5_compact.py`; no khong duoc bi thay bang runner moi.

Pi active phai:

- xac thuc, giai ma, chong trung, chong frame tre/lech thu tu;
- quan ly state doc lap theo `link_id`;
- luu EdgeResult va rejection metadata;
- phuc vu dashboard/status API hien co.

Fusion, trend, routing va model registry khong thuoc active Pi path. Pi khong
co OTA trong phien ban nay.

## 5. Dinh danh va trang thai EdgeResult

- `device_id`: thiet bi vat ly.
- `tx_id`: ESP phat.
- `rx_id`: ESP nhan.
- `link_id`: cap TX->RX va doan hanh lang tuong ung.
- `corridor_id`: vi tri/trien khai.
- `session_id`: phien thu.

EdgeResult toi thieu phai co:

```text
node_id, link_id, boot_id, window_seq
window_start, window_end, age_ms
local_passability_score, state, quality, uncertainty
formula_version, model_version, schema_version
sample_count, invalid_count, loss_ratio, jitter_ms
```

`local_passability_score` la diem chuan hoa cuc bo 0..100. Chi duoc goi la
phan tram vat ly/nghiep vu khi da co dinh nghia, ground truth va calibration.
Neu khong du dieu kien, dung `score`, `index` hoac `UNKNOWN`.

`state` toi thieu gom `PASSABLE`, `DEGRADED`, `BLOCKED` va `UNKNOWN`. `UNKNOWN`
khac `0%`: no co nghia la khong du du lieu de ket luan.

## 6. Data contract va version

Chi thay doi wire schema, payload layout, identity, CRC, protocol or schema
version moi bat buoc cap nhat dong bo TX, RX, Pi, replay vector va Data
Contract. Thay doi local trong noise filter, Formula Flex, baseline/windowing
hoac dashboard mapping ma giu nguyen wire contract khong duoc keo theo TX hay
component khong lien quan.

Goi EdgeResult phai truy duoc:

- message type, protocol/schema version;
- `link_id`, `tx_id`, `rx_id`, `boot_id`;
- window sequence va timestamp monotonic;
- score/state/quality/uncertainty;
- formula/model version va model hash neu co;
- sample/loss/invalid/drop counters;
- CRC/authentication va payload length.

Pi-to-node commands, remote configuration, OTA and rollback are out of scope.
Khong tao command channel, ACK protocol, controller or daemon cho pham vi nay
neu goal khong mo scope ro rang. Sai schema/model tren duong compact phai bi tu
choi, khong fallback im lang.

### Metric and loss semantics

Khong dung mot gia tri `packet_loss` chung cho nhieu tang. Theo doi va ghi
nhan rieng:

- TX scheduling/send/callback failures;
- TX-to-RX measurement loss;
- CSI sequence/callback gaps;
- RX invalid/context rejects;
- RX queue drops;
- RX-to-Pi UDP send failures;
- Pi decode, duplicate, `out_of_order` and unknown-link rejections.

CSI sequence gap khong phai UDP loss neu chua co bang chung cung tang do. Loss,
gap, queue drop, invalid CSI va quality thap co the lam giam quality/tang
uncertainty hoac tra `UNKNOWN`/`DEGRADED`, nhung khong tu minh tao occupancy hay
blocking evidence.

## 7. Formula Flex va tiny AI

- Median/MAD/IQR, EMA co gioi han, persistence va robust z la cong cu co the
  dung neu co don vi, gia thuyet va test.
- Formula Flex la ket qua nen, chay tai RX trong kien truc dich.
- Khi Doppler hop le, r so voi envelope r cua CHINH link do (khong khoa % nha/truong).
  Occupancy/BLOCKED chi khoa khi ca temporal z VA doppler z deu cao; pho giong
  trong cua link thi duoc rebase (tranh ket RX-1). Diem cong bo van la Formula Flex,
  khong nhan r: mot nguoi khong lam % truot mai.
- Tiny AI chi duoc hieu chinh/kiem tra Formula trong gioi han da dinh truoc;
  khong duoc bien mot heuristic thanh AI.
- Neu Formula va AI bat dong lon, EdgeResult phai tang uncertainty hoac
  `DEGRADED/UNKNOWN`; khong chon ket qua dep hon.
- Packet loss, jitter, RSSI thap hoac invalid CSI chu yeu lam giam quality va
  tang uncertainty; khong duoc tu dong coi la co vat can.
- Khong dung nguong CSI tho co dinh cho moi hanh lang.

## 8. Du lieu va AI

- `RUN`: chi gui EdgeResult gon, uu tien do tre va do on dinh.
- `CAPTURE`: luu CSI gan tho/snapshot co gioi han de replay, ablation va train.
- Raw session da dong la bat bien; file `.part` chi doi ten khi dong thanh cong.
- Duy tri quality/rejection metadata de AI hoc ca nhieu nen, khong cat bo mot
  cach vo thuc cac cua so xau.
- Khong dung Formula output lam ground truth cho model.
- Train/validation/test phai tach theo session/ngay/corridor; phai co hold-out
  corridor.
- Model RX va model Pi co version/hash/schema rieng; lifecycle:
  `candidate -> shadow -> active -> retired/rollback`.
- Model thieu, sai schema hoac OOD tra `NOT_READY`/`INCOMPATIBLE`/`UNKNOWN`.

## 9. Quy trinh thay doi

Truoc khi sua:

1. Xac dinh component va file duoc phep cham.
2. Ghi baseline, gia thuyet, phep thu bac bo va acceptance gates.
3. Liet ke protocol/schema/model/formula/resource thay doi.
4. Ghi ro phan chi co the kiem tra khi chua co phan cung.

### Gioi han file bat buoc

- Moi nhiem vu phai ghi ro `ALLOWED_FILES` truoc khi sua. Chi duoc sua cac
  file trong danh sach do.
- Neu nhiem vu receiver chia thanh ba phan, danh sach mac dinh duy nhat la:
  `firmware/receiver/main/app_main.c`,
  `firmware/receiver/main/noise_filter.c`,
  `firmware/receiver/main/noise_filter.h`,
   `firmware/receiver/main/formula_flex.c`,
   `firmware/receiver/main/formula_flex.h`,
   `firmware/receiver/main/CMakeLists.txt` (chi de dang ky dung cac file tren).
   `edge_result_v5_pipeline.c/.h` chi duoc sua khi nhiem vu ghi ro codec/
   queue compatibility; khong duoc tao pipeline/reference/runner thay the.
   Cam tao bat ky `.c`, `.h`, `.py` hoac entrypoint moi trong receiver.
- Nhiem vu Pi chi duoc sua file active duoc ghi ro trong task:
  `scripts/run_pi_v5_compact.py`, `pi/app/edge_result_v5.py`,
  `pi/app/edge_result_v5_runtime.py`, `pi/app/edge_result_v5_service.py`,
  `pi/app/edge_result_v5_dashboard.py`; khong tao runner/service/dashboard/core
  thay the.
- `firmware/transmitter/`, `data/`, `config/`, `docs/legacy_code_v1/` va
  `tests/` la read-only neu nhiem vu khong ghi ro ngoai le.
- Khong tu tao backup, report, manifest, log, artifact, module moi hoac thu muc
  moi. Chi tao backup timestamped khi nguoi dung cho phep ro rang mot thay doi
  xoa/ghi de; backup phai nam trong `ALLOWED_FILES`. Build output tam phai xoa
  sau khi kiem tra neu task khong cho phep giu lai.
- Neu can xoa/doi ten file ngoai `ALLOWED_FILES`, dung lai va bao
  `BLOCKED_FILE_SCOPE`, khong tu suy dien quyen.

Trong khi sua:

- Mot gia thuyet chinh moi experiment.
- Khong xoa V4/legacy, data, config, tests hoac docs archive neu task khong
  ghi ro file do.
- Khong phat hanh EdgeResult active truoc replay va test fault.
- Khong dung dashboard xanh lam bang cach an UNKNOWN/stale.

Sau khi sua:

- Chay bo xac minh nho nhat co the bac bo gia thuyet cua task. Khong chay full
  suite khong lien quan, nhung khong dung o source/static test neu goal da cap
  quyen deploy hardware/Pi: khi do bat buoc co generated RX identity, build,
  full flash, boot/serial va live Pi acceptance phu hop pham vi.
- Khi task lien quan, uu tien burst, loss, duplicate, out-of-order, reboot,
  Pi mat, model sai va mat rieng tung link.
- Neu chua co phan cung, ghi `IMPLEMENTED_PENDING_FIELD_TEST`, khong ghi
  `FIELD_VERIFIED`.

## 10. Trung thuc khoa hoc va dieu cam

- Khong bịa nguon, metric, du lieu hay do chinh xac.
- `predict_proba` khong tu dong la confidence da calibration.
- Score 0..100 khong tu dong la phan tram nguoi hay phan tram trong hanh lang.
- Khong goi heuristic la AI, hoac goi solver tu dien la D* Lite.
- Khong tuyen bo dung moi hanh lang tu mot link/mot phong lab.
- Khong hard-code credential, MAC ghep cap, IP trien khai trong logic.
- Khong commit build/cache/log runtime/raw capture lon/credential/model tam.
- Khong sua truc tiep `managed_components/`.
- `managed_components/` co the chua tai lieu/target OTA cua ESP-IDF; do la
  dependency ben thu ba, khong phai OTA cua WiEvac va khong duoc sua, copy hoac
  khoi phuc vao active source.
