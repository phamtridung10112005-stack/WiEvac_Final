# WiEvac - Tai lieu ban giao cho AI

Muc dich: day la diem vao duy nhat cho AI moi khi tiep quan du an. AI phai doc file nay truoc, sau do doc wievac/AGENTS.md va wievac/PROJECT_STATE.md neu sua code. File nay tong hop muc tieu, kien truc, quyen so huu file, giao thuc, cong thuc, luat an toan va ranh gioi bang chung. No khong thay the viec kiem tra source khi thuc hien thay doi.

## 1. Muc tieu va pham vi

- Ten: WiEvac / CSI Lite.
- Thu muc goc: C:\D\DU_AN_CSI_LITE - Copy.
- Ma nguon: C:\D\DU_AN_CSI_LITE - Copy\wievac.
- Muc tieu: dung Wi-Fi CSI tren ESP32-S3 de uoc luong do thong cua tung doan hanh lang.
- TX phat luong do; moi RX thu CSI, loc, tinh Formula Flex va phat mot EdgeResult; Pi chi nhan, kiem tra, ghi va hien thi.
- Khong co cam ket ve field accuracy, calibration, occupancy percentage that, production readiness hay Tiny AI trong pham vi hien tai.
- OTA, remote rollback va Pi-to-node control da bi loai khoi pham vi.

## 2. Thu tu uu tien

1. Trung thuc khoa hoc va fail-closed.
2. Moi link doc lap, dung identity va truy vet duoc.
3. Formula Flex thich nghi theo baseline/noise rieng cua link, khong dung nguong CSI tho co dinh.
4. Giu UNKNOWN, NOT_READY, STALE, UNVERIFIED va score null khi thieu bang chung.
5. Giu WIV5, protocol 5, schema 7 neu goal khong yeu cau dong bo giao thuc.
6. Khong doi du lieu thieu thanh 0%, PASSABLE hay ket luan chac chan.

## 3. Authority bat buoc

- wievac/AGENTS.md: luat thao tac, allowlist, quy trinh /goal, luat build/flash va metric semantics.
- wievac/PROJECT_STATE.md: kien truc active va gioi han bang chung.
- Neu mau thuan, hai file tren thang cac tai lieu khac.
- Tai lieu wievac/docs la quy uoc, contract, khoa hoc va lich su; khong tu dong la active implementation.

## 4. File active va quyen so huu

### Receiver

Cac file dang compile trong firmware/receiver/main/CMakeLists.txt:

- app_main.c: entrypoint app_main; Wi-Fi/ESP-NOW; CSI callback; pairing; measurement context; queue; UDP sender; serial telemetry; goi pipeline.
- edge_result_v5_pipeline.c/.h: cua so va queue; link state; sequence-gap; window boundary; EdgeResult; codec WIV5; schema-6 metadata; API compatibility. Day la dependency bat buoc, khong xoa chi vi ten co v5.
- noise_filter.c/.h: validate CSI; tach I/Q; magnitude; median; robust spread/MAD.
- formula_flex.c/.h: baseline median/MAD; temporal-noise scale; bootstrap; environment rebase; deviation; score; state/evidence.
- main/CMakeLists.txt: chi dang ky cac file tren.
- main/Kconfig.projbuild: RX ID, Wi-Fi, Pi UDP va expected TX MAC tuy chon.

Kien truc thuc te la app_main + pipeline compatibility + noise_filter + formula_flex. Khong duoc tao pipeline/receiver module thu hai de thay.

### Pi

- Entrypoint active local: scripts/run_pi_v5_compact.py.
- Modules active: pi/app/edge_result_v5.py (codec/validate); edge_result_v5_runtime.py (ingest, identity, freshness, boot/sequence, recorder); edge_result_v5_service.py (API/service/topology); edge_result_v5_dashboard.py (mot dashboard va mot poller).
- Pi khong tinh lai CSI hay Formula Flex; khong dung legacy Pi Formula/fusion/trend/routing trong compact active path.
- Khong tao run_pi_v4.py, pi_v5_ai_core.py, runner/service/dashboard/core moi de thay active entrypoint. Legacy chi doc/replay khi goal cho phep.

### Transmitter

- Active source: firmware/transmitter/main/app_main.c.
- TX chi phat luong do, hello/beacon va ESP-NOW measurement.
- TX khong tu suy luan occupancy. COM20 mac dinh bi cam neu goal khong ghi ro.

### Config/data/output

- Topology: config/corridors/corridor-01-edge-result-v5.json.
- RX profile: config/local/rx-01.sdkconfig, config/local/rx-02.sdkconfig.
- TX profile: config/local/tx.sdkconfig.
- data/ la bang chung append-only; khong xoa trong task thong thuong.
- tests/ la read-only neu goal khong mo scope.
- firmware/*/build/ la output sinh; khong sua truc tiep bin/elf/sdkconfig.h/compile_commands.

## 5. Identity va topology

| Node | Cong lich su | MAC da xac nhan lich su | Link | RX ID | TX |
|---|---|---|---|---|---|
| RX-1 | COM16 | 94:A9:90:EA:EA:10 | link-1 | 1 | tx-1 |
| RX-2 | COM22 | 28:84:85:48:ED:30 | link-2 | 2 | tx-1 |
| TX | COM20 | xem hardware evidence moi | phat cho ca hai | - | tx-1 |

- Hai RX thuoc device-1 theo corridor topology.
- RX-1 = node rx-1, link-1, rx_id 1.
- RX-2 = node rx-2, link-2, rx_id 2.
- Khong de RX-2 phat device-2.
- Moi link co sequence/state doc lap tren Pi.

## 6. Wire contract

- Magic ASCII WIV5.
- Protocol 5, active feature schema 7, message EDGE_RESULT.
- Prefix V5 giu byte-for-byte; schema 6 them baseline/rebase/evidence metadata; schema 7 them Doppler shadow.
- Packet co identity, boot_id/window_seq, window timestamps, nullable score, state, quality, uncertainty, formula/model versions, sample/invalid/queue/gap counters va CRC.
- UNKNOWN bat buoc score null/score_valid=false; khong ma hoa UNKNOWN thanh score 0.
- Decoder co the doc schema 5/6 compatibility, nhung packet active moi la schema 7.
- Sai identity/schema/CRC phai reject, khong fallback im lang.

## 7. Luong end-to-end

TX ESP-NOW measurement
-> RX pairing va context validation
-> CSI callback ngan -> queue
-> app_main task
-> noise_filter validate/magnitude/median/spread
-> pipeline gom cua so khoang 1 giay va gate gap/drop/quality
-> formula_flex baseline/deviation/score/state
-> pipeline encode WIV5 schema 7
-> RX UDP sender -> Pi UDP 8888
-> Pi decode/identity/freshness/sequence
-> recorder + /api/v5/overview + dashboard port 8080

Mot cua so chi co mot owner la pipeline. V6 active phat qua edge_result_pipeline_process_one khi cua so dong; legacy scheduler khong duoc force-flush partial V6 window.

## 8. Noise filter

noise_filter_process:
1. Kiem tra con tro, do dai, even length, gioi han va first_word_invalid.
2. Tach tung cap real/imaginary.
3. Tinh magnitude = sqrt(real^2 + imaginary^2).
4. common_amplitude = median cac magnitude.
5. robust_spread = median(abs(magnitude - median)).
Median/MAD giam outlier nhung khong bien du lieu xau thanh du lieu sach. CSI zero, malformed, gap, queue drop va invalid context khong duoc hoc baseline.

## 9. Window va baseline

Default pipeline: window_duration_us=1000000 (xap xi 1 giay), baseline_min_samples=8, stale_after_ms=2000. Topology Pi hien co stale_after_ms=3000; phai phan biet pipeline default va Pi runtime config.

Moi link co baseline RAM rieng:
- baseline_center: median amplitude cua cua so sach.
- baseline_mad: MAD co floor an toan.
- temporal_noise_scale/confidence: envelope nhiễu theo thoi gian va confidence heuristic.
- baseline_version: tang khi tao/rebase.
- baseline_update_reason: ly do cap nhat.

Mat nguon lam mat baseline RAM. Boot moi bat dau baseline_ready=false, baseline_version=0 va hoc tai vi tri hien tai.

Bootstrap chi nhan cua so khong invalid, khong gap, khong queue drop, quality duong va motion trong gate. Can nhieu cua so sach; lich su code yeu cau toi thieu 8 baseline samples va khoang 16 clean windows de qua gate. Khi chua ready: UNKNOWN, score null.

## 10. Environment rebase va kha nang flex

Neu moi truong thay doi sau khi baseline da co:
STABLE -> SHIFT_CANDIDATE/ENVIRONMENT_SHIFT -> REBASE_PENDING -> candidate sach on dinh -> UPDATED (version tang) -> STABLE.

- Cua so chuyen tiep khong co score hop le.
- Candidate dung median/MAD rieng; khong ghi de baseline cu ngay.
- Gap/drop/invalid/motion manh/occupancy evidence co the pause hoac huy candidate.
- Promotion phai ghi automatic_environment_rebase.
- Flex source-level da implement; flex live chi duoc xac nhan khi telemetry cho thay baseline_version tang va state UPDATED/STABLE.
- Khi node tat lau roi boot o vi tri moi, day la cold start; baseline cu khong duoc coi la persistent. Khu vuc nen trong va on dinh trong luc bootstrap.

## 11. Formula Flex va score

local_passability_score la local index 0..100, khong phai phan tram nguoi/do thong va khong phai confidence da calibration. Dau % tren dashboard chi la cach hien thi.

scale = max(1.4826 * baseline_mad, temporal_noise_scale, 0.0001)
deviation = abs(common_amplitude - baseline_center) / scale

excess_motion = max(temporal_motion - 1, 0)
combined_stress = deviation + 0.5*excess_motion
ungated_score = 100 / (1 + combined_stress)
published_score = ungated_score

r khong phai P(nguoi) va khong phai dien tich hanh lang. Moi link hoc doppler_ratio_scale tu cua so trong. doppler_motion = r / scale. Occupancy khoa OCCUPIED chi khi temporal_motion >= occupancy_z VA doppler_motion >= occupancy_z. Pho giong trong cua link (khong doppler_walking) thi rebase/drift duoc phep — hanh lang nha va truong deu hoc nen rieng, khong dung nguong %/Hz cua mot noi.

Khi FFT invalid, khong khoa occupancy (de doi moi truong van rebase duoc).

occupancy_evidence = 100*excess/(1+excess) voi excess = max(doppler_motion - 1, 0). BLOCKED can persistence va independent_blocking; level shift yen khong du.

State:
- UNKNOWN: thieu data, warmup, stale, gap/drop/invalid, transition hoac ambiguity; score null.
- PASSABLE: evidence sach hien tai khong vuot blocking gate; khong phai chung minh tuyet doi trong.
- DEGRADED: quality/uncertainty/motion/model disagreement.
- BLOCKED: displacement dai han + evidence blocking doc lap.

## 12. Metric va loss

Tach rieng:
- TX scheduling/send/callback failure.
- TX->RX measurement loss.
- CSI sequence/callback gap.
- RX invalid/context reject.
- RX queue drop.
- RX->Pi UDP send failure.
- Pi decode, duplicate, out_of_order, unknown-link reject.

packet_loss_ratio trong wire V5 la CSI gap ratio:
csi_gap_ratio = sequence_gap / (sample_count + sequence_gap)
No khong phai UDP loss RX-to-Pi. Pi phai hien thi csi_gap_ratio, queue_drop_ratio, invalid_csi_ratio, udp_loss_ratio null neu chua co RX emit counter, va Pi ingest counters rieng.

## 13. Pi runtime/dashboard

- Pi lich su: 10.42.0.1, user junpham. Khong ghi password vao file.
- Active remote command: /home/junpham/wievac/scripts/run_pi_v5_compact.py.
- UDP 0.0.0.0:8888; dashboard/API 0.0.0.0:8080.
- Health: /api/health -> status=ok, udp_running=true.
- Dashboard lich su: http://10.42.0.1:8080/.

Dashboard mapping:
- accepted=0 va khong co arrival timestamp -> CHUA NHAN PACKET.
- packet moi UNKNOWN/candidate -> DANG HOC DU LIEU.
- PASSABLE -> TRONG/PASSABLE.
- DEGRADED -> DEGRADED/CROWDED.
- BLOCKED -> KHO DI QUA.
- Da tung nhan nhung stale -> MAT KET NOI.
- API loi thi giu card/history cu va hien loi rieng.
- Chi mot fetch poller /api/v5/overview; khong them liveRefresh/liveUpdate.

## 14. OTA va cam

- OTA da bi loai khoi WiEvac hien tai.
- Khong them esp_ota_ops, esp_http_client, manifest downloader, OTA server/controller, remote config, command channel hay auto rollback.
- Tai lieu ESP-IDF/managed_components co the noi OTA nhung khong phai OTA active cua WiEvac.
- Firmware doi bang USB/ESP-IDF khi user uy quyen ro rang.
- Khong hard-code credential, MAC, IP deployment vao logic.

## 15. Quy trinh /goal

Moi /goal phai co:

ROLE: senior ESP32-S3/RF/Wi-Fi CSI va signal-processing engineer.
OBJECTIVE: ket qua cu the, khong mo rong kien truc.
ALLOWED_FILES: danh sach file duoc sua.
READ_ONLY_FILES: file chi doc.
FORBIDDEN_ACTIONS: tao module/runner moi; OTA; TX/COM20; xoa data/log; doi protocol/schema; tao artifact/report/backup neu chua cho phep.
ACCEPTANCE_GATES: gate source/test/build/flash/boot/live tuong ung.
STOP_CONDITIONS: vuot allowlist, thieu credential/hardware, hoac thieu evidence thi dung va ghi BLOCKED.
HARDWARE_AUTHORIZATION: ghi ro co duoc build/flash/serial/SSH, dung COM nao va mapping nao.

AI phai doc file nay + AGENTS.md + PROJECT_STATE.md truoc khi sua. "Doc folder" khong la quyen sua toan bo. Khong tao file tuong tu de ne allowlist. Mot goal mot gia thuyet chinh.

Neu goal khong cap quyen deploy: khong flash, OTA, SSH hay serial monitor. Neu goal cap quyen flash: RX-1 dung profile rx-01 va COM16; RX-2 dung rx-02 va COM22; phai xac nhan generated CONFIG_WIEVAC_RX_ID, artifact/hash, flash, boot va Pi live. COM20 chi duoc cham khi goal ghi ro.

## 16. Muc do bang chung

Tach rieng:
1. Static/source inspection.
2. Focused unit/C contract tests.
3. Executable C behavior/parity.
4. ESP-IDF build/generated config.
5. Flash verification.
6. Boot/serial identity, pairing, CSI, UDP.
7. Pi health/fresh packet acceptance.
8. Baseline/bootstrap/rebase live telemetry.
9. Field accuracy/calibration/generalization.
10. Production approval.

Build/HTTP 200 khong phai flash/live proof. Source Formula Flex khong phai field accuracy. Hardware PASS cu khong tu dong ap dung cho goal moi.

Lich su da co cac lan RX-1 COM16 va RX-2 COM22 build/flash/boot-verify, Pi compact deploy va hai link accepted packet; do la bang chung cu, phai refresh neu ket luan hien tai. Neu khong co evidence moi, dung NOT_RUN/UNVERIFIED/NOT_READY.

## 17. Checklist ket thuc

- Doc authority va ghi allowlist truoc khi sua.
- Khong tao module/runner/dashboard/pipeline moi.
- Giu WIV5/protocol 5/schema 7, identity dung va UNKNOWN/null score.
- Tach loss/quality/uncertainty.
- Giu data/log/replay/legacy evidence.
- Phan biet source, test, build, flash, boot, live, field.
- Vuot scope hoac thieu evidence: BLOCKED, khong workaround bang file moi.

## 18. Ban do tai lieu va cong cu

Tai lieu quan trong trong wievac/docs:

- codex-request-protocol.md: cach viet request co scope va stop condition.
- engineering-change-protocol.md: quy trinh thay doi, experiment va rollback.
- incident-debug-protocol.md: chan doan theo bang chung, khong sua lan man.
- edge-result-v5-contract.md: wire/data contract EdgeResult.
- flex-threshold-policy.md: nguyen tac threshold theo link, baseline va flex.
- csi-science-notes.md va csi-algorithm-review.md: co so CSI, rui ro va gioi han khoa hoc.
- feature-experiment-policy.md: quy tac them/bo feature va falsification.
- data-ai-lifecycle.md: capture/label/split/model lifecycle; khong dung Formula output lam ground truth.
- verification-checklist.md: cac gate source/test/build/hardware/field.
- experiment-ledger.md: lich su experiment; khong coi status cu la active truth.
- edge-result-v6-reference-port-map.md: mapping reference/port lich su; khong phai quyen tao architecture moi.

Scripts hien co:

- build_edge_result_v6.ps1: wrapper build profile RX; ten v6 la lich su cua schema-6 path. Khong tao wrapper build thu hai.
- run_pi_v5_compact.py: active Pi entrypoint.
- startup_pi_v5_compact.sh/.ps1: startup helper hien co.
- replay_edge_result_v5.py: replay EdgeResult.
- export_dataset.py: export view co label, khong sua raw data.
- report_metrics.py: bao cao metric mo ta, khong phai calibration proof.

Tests tap trung vao codec, runtime, dashboard, C contract, wire parity, dataset va project-state contract. Khong sua test chi de lam test xanh neu behavior source sai. Chay bo nho nhat co the bac bo gia thuyet; deployment goal phai tiep tuc den build/flash/boot/live theo uy quyen.

## 19. Build va nap thu cong

- ESP-IDF da dung thanh cong trong lich su: 5.4.4; Python environment lich su: idf5.4_py3.11_env. Phai kiem tra lai tren may hien tai.
- Canonical receiver project: wievac/firmware/receiver.
- Canonical output: wievac/firmware/receiver/build/wievac_receiver.bin.
- Khong tin sdkconfig hoac build cache cu. Truoc moi build node, dung profile dung va kiem tra generated build/config/sdkconfig.h.
- RX-1 bat buoc generated CONFIG_WIEVAC_RX_ID=1; RX-2 bat buoc =2.
- Build hai node tuan tu trong cung canonical build directory co the lam binary cu bi ghi de; phai ghi hash/artifact identity truoc khi chuyen profile, hoac build va flash tung node ngay sau khi xac nhan identity.
- Flash phai dung project ESP-IDF, bootloader, partition table va application theo flash_args duoc sinh; khong chi ghi file app tuy tien.
- Khong sua source khi doi RX-1/RX-2; chi doi build profile/config va build lai.
- TX la project firmware/transmitter va chi build/flash neu user cho phep COM20.
- Sau flash, can serial boot marker rx_id/link, pairing, CSI counter va UDP sender; sau do Pi accepted counter cua dung link phai tang.

## 20. Gioi han va viec con mo

- Tiny AI: NOT_READY; khong co model artifact/hash active.
- C/Python behavioral parity: UNVERIFIED neu chua chay executable parity moi. Python reference/test khong duoc coi la firmware C active.
- Field accuracy/calibration/generalization: UNVERIFIED. Can ground truth va nhieu session/corridor, bao gom hold-out corridor.
- Score 0..100 van la heuristic local index. Can dinh nghia nghiep vu va calibration truoc khi goi la phan tram do thong.
- Automatic cold-start baseline va rebase co logic source, nhung phai test tai vi tri moi voi baseline telemetry, false-positive/false-negative va recovery time.
- UDP loss end-to-end chua the suy ra neu khong co RX emit/send sequence counter doi chieu tai Pi.
- Endpoint allow-listing co the khong bat trong lab; khi do chi la trusted closed-lab, khong phai source authentication production.
- Routing/fusion/trend fields trong topology/docs la design/reference, khong active tren compact Pi.
- Cac thu muc build, __pycache__, test-log va artifact cu co the ton tai; khong coi chung la source va khong xoa neu user khong giao cleanup voi target chinh xac.
