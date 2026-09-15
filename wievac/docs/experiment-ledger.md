# WiEvac Experiment and Incident Ledger

## 1. Cách dùng

Đây là bộ nhớ kỹ thuật chống lặp. Đọc trước mọi sửa lỗi/tối ưu và cập nhật trước–sau mỗi thử nghiệm.

- Không xóa thất bại.
- Không sửa kết quả cũ để phù hợp kết luận mới.
- Khi dài, archive incident đã đóng nhưng giữ index/kết luận.
- Mọi record tham chiếu đúng commit, session, link và version nếu có.

## 2. Luật chống luẩn quẩn

1. Mỗi incident/experiment có ID duy nhất.
2. Chỉ một experiment `in-progress` cho cùng incident.
3. Tìm theo symptom, component, feature, threshold và protocol trước khi thử.
4. Không lặp `rejected`/`rolled-back` nếu thiếu bằng chứng mới và điều kiện thay đổi.
5. Không chồng bản sửa lên thử nghiệm chưa accepted.
6. Regression phải trỏ về experiment gây ra nó.
7. Accepted change phải thêm invariant/test/field scenario bảo vệ.
8. Hai thử nghiệm suy đoán thất bại không có dữ liệu mới → `blocked-needs-evidence`.

## 3. Protected invariants

| invariant_id | Mô tả | Test/metric | Nguồn | Trạng thái |
|---|---|---|---|---|
| INV-001 | C/Python decode cùng Protocol V2 | Packet test vector | Chưa xác nhận | proposed |
| INV-002 | RX chỉ xử lý TX đã ghép cặp | Source-filter test | Chưa xác nhận | proposed |
| INV-003 | Mỗi sample truy được link/session/schema | Recorder/schema test | Chưa xác nhận | proposed |
| INV-004 | Stale/degraded/unknown không thành output tốt | State/routing test | Chưa xác nhận | proposed |
| INV-005 | AI thiếu/sai model báo NOT_READY/INCOMPATIBLE | Model-load test | Chưa xác nhận | proposed |
| INV-006 | Raw capture đã đóng không bị ghi đè | Data immutability test | Chưa xác nhận | proposed |

`proposed` không có nghĩa đã được kiểm chứng.

Current migration note: `INV-001` in the historical table mentions Protocol V2.
For new work, the invariant is version-agnostic: every active wire path must
have a versioned C/Python contract and packet vectors; V4 is compatibility and
EdgeResult V5 is the target contract.

## 4. Active incidents

Chưa có incident được chuyển vào ledger mới.

Mẫu:

```markdown
### INC-YYYYMMDD-NN — Tên ngắn

- Status: open | investigating | blocked-needs-evidence | fixed-pending-field-test | closed
- Reported symptom:
- Expected behavior:
- First observed:
- Affected component/path:
- Link/corridor/session:
- Hardware available: yes | no | partial
- Evidence/files/logs:
- Baseline commit/firmware/protocol/schema/model:
- Metrics to improve:
- Protected metrics/invariants:
- Ranked hypotheses:
- Minimum next measurement:
- Related experiments:
```

## 5. Experiment records

Mẫu:

```markdown
### EXP-YYYYMMDD-NN — Tên phương án

- Incident:
- Status: planned | in-progress | accepted | rejected | inconclusive | implemented-pending-field-test | rolled-back | blocked-needs-evidence
- Component/path:
- Hypothesis:
- New evidence since similar attempts:
- Falsification test:
- Baseline commit/version:
- Data/session/split:
- Protocol/feature/model version:
- Files changed:
- Constants/formulas/schema/dependencies changed:
- Expected benefit:
- CPU/RAM/flash/payload/airtime/latency risk:
- Downstream risks:
- Acceptance gates fixed before test:
- Tests actually run:
- Highest verification level:
- Before metrics:
- After metrics:
- Side effects/regressions:
- Verdict and reason:
- Commit/diff/model/data references:
- Rollback status:
- Regression protection added:
- Field validation still required:
- Conditions required before revisiting:
```

Mỗi experiment được append, không ghi đè record cũ.

## 6. Rejected approach index

| approach/signature | Experiment | Lý do loại | Regression | Chỉ thử lại khi |
|---|---|---|---|---|
| Chưa có | — | — | — | — |

## 7. Accepted decisions

| decision | Experiment | Bằng chứng | Invariant/test | Phạm vi chưa chứng minh |
|---|---|---|---|---|
| Chưa có | — | — | — | — |

## 8. Regression chain

```text
EXP-A changed X
  -> regression Y observed by test/log/session
  -> EXP-A rolled back or isolated
  -> EXP-B must pass original X gate and Y regression gate
```

Không tiếp tục sửa Y/Z trên cùng trạng thái bẩn khi chưa xác định thay đổi gây regression đầu tiên.

### EXP-20260804-01 - Protocol V4 one-TX/two-RX CSI pipeline

- Incident: topology migration from one-link V3 to one transmitter observed by RX-1/RX-2.
- Status: implemented-pending-field-test
- Component/path: `firmware/transmitter/main/app_main.c`, `firmware/receiver/main/app_main.c`, `pi/app/pi_v5_ai_core.py`, `config/`, `docs/`, `tests/`, `scripts/replay_v4.py`
- Hypothesis: a shared TX sequence/timestamp plus independent per-link quality/baseline state can make link disagreement explicit without promoting one bad link to `PERSON_PASS`.
- New evidence since similar attempts: V4 packet vector, dual-link decoder tests, direct Xtensa compile of TX/RX and RX-ID=2.
- Falsification test: replay duplicate/out-of-order/gap/reboot/invalid frames; field sessions with simultaneous gain/noise shift and directional person passes.
- Baseline commit/version: repository copy before this change; V3 one-link implementation.
- Data/session/split: no qualifying V4 field dataset available; existing V2 `.part` data is preserved and excluded.
- Protocol/feature/model version: Protocol V4, feature schema 4, no AI model activation.
- Files changed: V4 firmware, Pi dual-link core, topology config, contract/vector/tests/replay.
- Constants/formulas/schema/dependencies changed: `>12f15I2Q`, `>20f20I4Q`, quality-flag MCS/PHY-rate bytes, median/MAD baseline, timestamp-bounded resampling, 250 ms alignment budget; no new Python dependency.
- Expected benefit: shared TX alignment, independent link diagnostics, robust baseline and explicit `UNKNOWN`/`STALE`/`INTERFERENCE` states.
- CPU/RAM/flash/airtime/latency risk: dynamic payload 124 bytes, feature payload 192 bytes; per-link bounded buffers; unicast round-robin doubles TX measurement airtime.
- Downstream risks: receiver deployment credentials/endpoints must be supplied through local Kconfig; channel/rate field validation and geometry delay still require hardware evidence.
- Acceptance gates fixed before test: empty FP <1/10 min, recall/F1 >=0.90 only with labeled hold-out data, latency <2 s, recovery <3 s, no person event on common-mode radio shift.
- Tests actually run: 14 Python unit/vector/config tests, Python syntax compile, replay of V4 vector, direct Xtensa GCC static compile for TX/RX and RX-ID=2; simulation smoke scripts blocked by missing JuPedSim/Sionna/Mitsuba packages.
- Highest verification level: packet test + static compile + offline replay; no bench/field level.
- Before metrics: one-link V3 state/stream/baseline; no comparable two-link metric.
- After metrics: protocol/vector and isolation invariants pass; no FP/FN/latency claim from synthetic/vector data.
- Side effects/regressions: Pi now rejects and logs protocol errors instead of silently dropping them; dashboard API exposes per-link/fused states.
- Verdict and reason: implemented-pending-field-test; software contract is internally aligned, real-radio behavior is unverified.
- Rollback status: V3 source remains in `docs/legacy_code_v1/`; no destructive data changes.
- Regression protection added: C/Python packet vector, link isolation, invalid-input and disagreement tests, bounded replay utility.
- Field validation still required: all 14 requested sessions, broadcast-vs-unicast packet logs, gain/noise/door/fan/cart cases, Pi soak and disk-full test.
- Conditions required before revisiting: capture labeled V4 sessions for both links and calibrate directional delay from corridor geometry.

#### Implementation follow-up (2026-08-04)

- Corrected the link identity path: `HELLO` uses `link_id=0`; TX measurement and RX reply/data
  frames use `link_id=rx_id`, so RX-ID=2 is no longer mislabeled as link 1.
- Moved receiver SSID, password, Pi address, and UDP port to local Kconfig/sdkconfig values;
  no deployment credential or endpoint remains in source.
- Removed the unused alternate dynamic payload parser; Pi now accepts only the documented
  `>12f15I2Q` V4 dynamic layout.
- Revalidated direct Xtensa compilation for TX, RX-ID=1, and RX-ID=2: all exit 0 with no warnings.

#### Production-path follow-up (2026-08-04)

- Status remains `implemented-pending-field-test`; no decision-mode or person-recognition claim is enabled.
- Pi now loads topology/radio expectations from the corridor config, validates source endpoint when an allow-list is supplied,
  rejects non-finite/zero/malformed V4 context, and tracks RX-header, RX-payload, TX, feature, and boot identities separately.
- Dual-link fusion requires fresh quality-gated evidence aligned on TX boot, TX sequence, and TX timestamp; stale,
  rebooted, mismatched, or degraded links remain `UNKNOWN`/`DEGRADED` rather than becoming `PERSON_PASS` or `EMPTY`.
- Cold-start baseline learning is disabled until an operator starts an explicit empty session; reboot, gain transition,
  timestamp discontinuity, queue loss, and sequence gaps clear or pause temporal state. Fixed confidence values are no longer emitted.
- CSI association in the receiver now uses the measurement packet carried with the CSI callback, source/destination MAC checks,
  TX context monotonicity, boot isolation, and per-window queue-drop deltas. Network readiness no longer assumes the Pi is the gateway.
- Recorder writes use a bounded worker queue and CAPTURE mode can persist `datagram_hex`; replay accepts recorder JSONL captures.
- Software evidence: 23 Python unit tests pass, Python syntax compilation passes, replay of a JSONL V4 capture passes, and
  Xtensa syntax compilation passes for the patched TX/RX sources. Full ESP-IDF build is pending because the local IDF 5.4.4
  Python environment is missing; checked-in `firmware/*/build` binaries remain V3 and are not deployable.

#### Final software verification follow-up (2026-08-05)

- Corrected the Windows PowerShell build wrapper: script-relative defaults are resolved after parameter binding and RX sdkconfig
  validation accepts CRLF files. `-SkipBuild` now inspects all three isolated artifacts and writes a complete manifest.
- The dual production path now requires a fresh, identity-matched, timestamp-valid, quality-gated feature window before a dynamic
  frame can affect state or baseline. Bad feature quality is explicitly `SIGNAL_INVALID` and cannot become `PERSON_PASS`.
- The Pi decoder now validates the configured `(link_id, tx_id, rx_id)` mapping and rejects inconsistent feature sample/invalid/outlier
  counters. PHY-rate code validation remains opt-in until the corridor radio config records the ESP-IDF enum value.
- Tests actually run after the final changes: `27` Python unit/config/vector/invariant tests pass; Python syntax compilation passes.
- Full ESP-IDF `5.4.4` build completed for `tx-v4`, `rx-1-v4` (`CONFIG_WIEVAC_RX_ID=1`), and `rx-2-v4`
  (`CONFIG_WIEVAC_RX_ID=2`) into `.v4-build`; no flash was performed. Manifest hashes are recorded in `.v4-build/manifest.json`.
- Hardware evidence is still absent. Endpoint allow-list values are not present in the repository and ESP-NOW encryption/MAC allow-list
  is not enabled, so pairing remains trusted closed-lab behavior pending deployment configuration and field logs.
- Added optional MAC allow-list gates on both sides of ESP-NOW pairing (`CONFIG_WIEVAC_EXPECTED_TX_MAC`,
  `CONFIG_WIEVAC_RX1_MAC`, `CONFIG_WIEVAC_RX2_MAC`). Current local build values leave these empty, so the built images
  intentionally log trusted closed-lab mode until real device MACs are supplied.
- After the pairing changes, full ESP-IDF `5.4.4` build was rerun successfully for all three images. Updated SHA-256 values:
  TX `325b4ad400d089891afedf8abf453c58afd838815e7564f4509a15e5100d9a9a`, RX-1
  `fecfe265f229e5d4a6df87006203faf0fbb66a7ba3043eebb6c6f6744c13f65b`, RX-2
  `5b73dd56795bbe34423e26a8a7b95eba1a25b4cdb86524447ef1bb274055410d`.
- Warm-up baseline collection now buffers a complete window and rejects prebaseline motion/interference before committing any
  vectors, preventing a moving occupant at boot from becoming the empty reference. The safety block threshold is configurable
  (`WIEVAC_PREBASELINE_MOTION_BLOCK_SCORE`) and is not a calibrated human detector.

#### Adversarial production-path follow-up (2026-08-04)

- Status remains `implemented-pending-field-test`; the safe verdict is `NO-GO` for evacuation/navigation decisions.
- Hardened decoder and recorder boundaries: malformed/partial `WIEVAC_RX_ENDPOINTS` now fail fast; recorder records preserve
  source endpoints and replay consumes recorder JSONL; recorder writer timeout becomes explicit `WRITE_ERROR`; snapshot radio
  values and dynamic/feature RX timestamps are finite and header-consistent; invalid CSI shape still reports its actual
  subcarrier count for diagnostics.
- Topology configuration is now required and must match Protocol V4/feature schema 4; fusion skew, dynamic rate, and stale
  timeout come from corridor config (with explicit environment overrides) and are range-validated.
- Dashboard now renders RX-1 and RX-2 independently with state, age, quality gate/score, loss, queue drops, sequence gaps,
  dynamic rate, baseline readiness/reason, fused alignment, disagreement, and reason. Confidence remains explicitly uncalibrated.
- Tests actually run after the adversarial pass: `29` Python unit/config/vector/invariant tests pass; Python syntax compilation
  passes; recorder JSONL V4 replay accepts the capture vector and preserves per-link diagnostics.
- Full ESP-IDF `5.4.4` build rerun from current source into isolated `.v4-build`; no flash performed and no checked-in V3
  artifact was reused. Current SHA-256 values: TX `325b4ad400d089891afedf8abf453c58afd838815e7564f4509a15e5100d9a9a`,
  RX-1 `85f1071099e6acc93cf36bb4eba03087b4bf350f18f29b2478a4cf4c8908a1d0`, RX-2
  `b0011c65d21ef5d7ccd945675c4f27581200bdd55eb42388693bef0fa6c65025`.
- Remaining blockers are hardware/field evidence, real RX endpoint allow-list values, real ESP-NOW MAC allow-lists or encryption,
  boot logs proving both links/network readiness/paired mask, and calibrated FP/FN/recovery data under RF and non-human motion.
- V4 recorder default namespace is now `data/real/v4`; the preserved V3 namespace is never selected implicitly.
- Final source build after network/RF/threshold hardening (ESP-IDF `5.4.4`, no flash): TX
  `a6e40841582b032e1d81443566ed88e71a127fb5dd5cd160eee9157ec6371c82`, RX-1
  `ddab8318c32228e9245e7af5b7b90f707a7831beea2617c11bd249a2ee1c6f1a`, RX-2
  `d27df54212f228ac436a8e480f1abe3fba298246faf62fa6c526752dae1f3dfa`; all contain V4 markers and no V3 markers.
- Final software regression count is `33` tests (`unittest`), with `py_compile` and recorder-vector replay passing.
- Pi status/dashboard now expose `transport.endpoint_policy`; an unset `WIEVAC_RX_ENDPOINTS` is visibly `trusted_closed_lab`
  and produces a startup warning rather than being mistaken for production source binding.

#### Adversarial review loop (2026-08-05)

- Re-ran the required production-path suite after dashboard fail-closed hardening: `42` Python tests pass.
- Rechecked Python compilation, JSONL replay, dashboard JavaScript syntax, and the isolated V4 manifest/artifact hashes.
- Added dashboard/API guards so negative timestamp skew, baseline-not-ready, disagreement, or partial TX context cannot render
  a decision-like aggregate state; one fresh RX reports `partial`, never complete TX `online`.
- Corrected the verification checklist evidence block so it records the completed ESP-IDF 5.4.4 build while keeping flash,
  boot logs, endpoint/MAC deployment binding, and hardware/field evidence explicitly pending.
- Verdict remains `NO-GO` and software status remains `implemented-pending-field-test`.

#### Transport endpoint binding follow-up (2026-08-05)

- Adversarial review found that Pi endpoint allow-list examples validate source IP and port, while the RX UDP sender previously
  left its source port ephemeral. RX now binds its UDP source socket to `CONFIG_WIEVAC_PI_UDP_PORT` before sending.
- Added a topology regression check for the firmware `bind()` path and a decoder endpoint-rejection test, then rebuilt all
  three ESP-IDF 5.4.4 images into isolated `.v4-build` artifacts. Final suite: `45` tests pass; final artifact hashes are
  recorded in the manifest.
- Endpoint configuration now also rejects duplicate `(IP, port)` bindings across links, preventing one physical source from
  being ambiguously trusted as both RX-1 and RX-2.
- Corridor runtime configuration now fails fast when `tx_id`, per-link `tx_id`, `pi.udp_port`, `pi.stale_after_ms`, or required
  radio keys are missing; no silent fallback to TX ID 1, UDP 8888, or default radio metadata remains.
- Hardware packet capture is still required to confirm the observed source endpoint matches the configured allow-list.

#### CSI/feature context correction (2026-08-05)

- Adversarial review found a firmware edge case where the measurement tracker could advance beyond the last CSI callback in a
  window. The RX feature packet could then carry a `tx_timestamp_us` newer than `window_end_tx_us`, or emit a feature with no
  real CSI context. `emit_feature()` now skips empty-CSI windows and always uses the last CSI record's TX boot/sequence/timestamp
  for feature timing, while retaining measurement loss counters separately.
- Rebuilt all three isolated V4 images with ESP-IDF `5.4.4`; current SHA-256 values are TX
  `a6e40841582b032e1d81443566ed88e71a127fb5dd5cd160eee9157ec6371c82`, RX-1
  `23a813ffa9ce17406c43e24f688dace51dfb1e65cdb474cbe167fc22a3f5673e`, RX-2
  `8d897698308e4f9f3b9a53f9a1fc0b385c4f3be0171445cca2ea2914d7288876`; manifest hashes match.
- The required production-path suite now has `53` passing tests, with Python compilation, recorder-vector replay, and dashboard
  JavaScript syntax checks passing. No flash or hardware validation was performed.

#### Quality and feature-context hard boundaries (2026-08-05)

- Any quality-invalid dynamic frame now clears the dynamic/feature/arrival/RF buffers, motion hysteresis, and event context. Baseline learning pauses until operator reconfirmation, so later valid frames cannot reuse evidence from across the invalid boundary.
- Duplicate or out-of-order feature frames are rejected by the production feature tracker, marked `accepted=false` in recorder records, and cannot replace `latest_feature_by_link`.
- Feature duration fields now use the TX CSI window span rather than the RX scheduler span. This keeps `window_duration_ms` consistent with `window_start_tx_us`/`window_end_tx_us` when callback delivery is delayed.
- Loss, duplicate, reorder, and accepted-header denominators are reset with the active `(link_id, rx_boot_id, tx_boot_id)` identity. They are never mixed across an RX/TX reboot.
- The build script rejects `TEST_ONLY`, `CHANGE_ME`, and TEST-NET addresses. Existing artifacts made with placeholder local configs are retained only as test evidence and are not deployable.
- A fresh ESP-IDF `5.4.4` rebuild completed with `NINJAFLAGS=-j1` after the receiver timing change; the manifest is explicitly marked `lab_only_unflashed` and `blocked_until_real_local_config`. Artifact markers/hashes were rechecked, but no flash or boot evidence exists.

#### Exact TX alignment gate (2026-08-05)

- Fusion no longer treats a sub-250 ms difference as sufficient identity. `tx_boot_id`, `tx_sequence`, and `tx_timestamp_us`
  must match exactly; receive-time skew remains diagnostic only. A new regression test rejects a 1 ms TX timestamp mismatch.
- The protocol contract now documents exact TX-tuple alignment, and the software suite is expected to remain fail-closed for any
  mismatched timestamp, even when the configured sanity limit is not exceeded.

#### Recorder close error propagation (2026-08-05)

- Recorder `session_end` queue-full/failure now returns `WRITE_ERROR` with the `.jsonl.part` path instead of exposing a stale
  `IDLE`/previous close result. A regression test exercises the production `Recorder.close()` path under enqueue failure.

#### Temporal discontinuity reset (2026-08-05)

- A `dynamics_features()` timestamp/resampling failure now clears the dynamic and feature buffers, motion hysteresis, arrival/RF
history, and baseline-learning eligibility before returning `SIGNAL_INVALID`. This prevents a pre-gap `PERSON_PASS` streak or
stale feature context from crossing a 150-250 ms continuity break. The production test suite includes this regression.

#### Reproducible manifest trust labeling (2026-08-05)

- `scripts/build_v4.ps1` now accepts an ignored local transmitter sdkconfig, rejects the example MAC
  `AA:BB:CC:DD:EE:FF`, and records `trusted_closed_lab` plus explicit deployment blockers when MAC allow-lists are empty.
- `-SkipBuild` manifests are labeled `artifact_inspection_only` and `blocked_until_rebuild_and_field_verification`; an inspected
  binary is not represented as rebuilt from the config supplied to that inspection run.
- PowerShell parsing and a non-secret temporary-config inspection smoke check passed. The real local configs still intentionally
  block deployment because they contain test placeholders; no flash or hardware evidence exists.
- The manifest now scopes `trusted_closed_lab` to ESP-NOW pairing, always records that Pi UDP endpoint allow-listing is a separate
  runtime requirement, validates non-empty MAC syntax, and keeps stable ignored RX sdkconfig copies plus source provenance.
- A feature-frame RX/TX boot-context change now invalidates old dynamic/baseline/fusion state immediately, including when the first
  feature frame arrives before the first post-reboot dynamic frame. Missing artifacts make `-SkipBuild` fail rather than producing a
  partial manifest.
- Regression coverage for feature-first reboot ordering and delayed post-reboot features is now included; the full production-path
  suite is `56` tests and remains green.

#### Trusted-lab runner, recorder gate, and TX liveness follow-up (2026-08-05)

- Recorder start is fail-closed: `/api/record/start` now returns an error unless the durable `session_start` record is written;
  queue-full, write-error, and writer-timeout regressions leave baseline learning disabled and preserve the `.jsonl.part` audit file.
- `scripts/run_pi_v4.py` loads the corridor topology, validates the `10.42.0.1:8888` bind and both link identities, waits for
  dashboard health, reports receiving links, and exposes `trusted_closed_lab` versus endpoint allow-list policy without printing secrets.
- Replay and the runner support `--require-capture` so forensic runs fail clearly unless `WIEVAC_CAPTURE_MODE=1` and raw datagram records exist.
- TX scheduling now bounds two outstanding ESP-NOW sends per RX, tracks callback delivery separately, and clears an unresponsive RX
  after 1.5 seconds without increasing pressure on the surviving link. Field delivery-rate/liveness evidence remains pending.
- RX images embed `V4 RX rx_id=1` or `V4 RX rx_id=2`; the build gate checks the identity marker and rejects any V3 marker.
- Pi RF-context gating now includes FFT gain jumps alongside RSSI, noise-floor, and AGC shifts; such windows are `INTERFERENCE`
  and cannot promote motion to `PERSON_PASS`.
- Fresh ESP-IDF 5.4.4 build completed into `.v4-build` with `lab_only_unflashed`/`built-not-flashed` status. Current suite: `70` tests,
  Python syntax compilation, replay, and marker checks pass; no flash, boot log, Pi packet capture, or field validation was performed.
- Latest manifest image hashes: TX `981ab500ffde43b3ef862108558d06acdc994ed47c32a9f9cc9f893c91ad67d4`, RX-1
  `9b891c9da9134d5b2070a2b040f0ec735e0bb85d89da6ea0a4aa1081d4cd10d2`, RX-2
  `7994a7db29866496692baf914cf68a46791b9830063ff7b03cfdfacb00d611e3`.

### EXP-20260806-OTA-DATA-PREP - OTA-safe preparation and labeled export

- Incident: Prepare WiEvac for staged remote firmware updates and training-data collection.
- Status: implemented-pending-field-test
- Component/path: `scripts/export_dataset.py`, `tests/test_dataset_export.py`, `docs/ota-rollout-plan.md`
- Hypothesis: Explicit operator labels plus preserved technical quality evidence can create a safe training-data view without mutating raw captures; OTA readiness must be staged before any remote update.
- Falsification test: invalid sidecar labels are rejected; raw JSONL records remain source inputs; OTA acceptance requires target/hash/boot/health/rollback evidence.
- Baseline/version: current V4 production path and existing recorder JSONL sessions.
- Protocol/feature/model version: Protocol V4, feature schema 4, dataset schema 1; no model activation.
- Acceptance gates: no raw/session deletion; no occupancy label inferred from quality state; no OTA success claim from build or HTTP status.
- Tests actually run: 91 Python unittest cases; exporter preservation/label tests pass; Python core remains syntactically valid.
- Verdict: implemented-pending-field-test; data export is software-verified, OTA remains blocked because firmware/partition/rollback/target endpoint and field evidence are absent.
- Field validation still required: per-node USB OTA migration, RX OTA health-confirm/rollback, TX transport decision, Pi endpoint hash/target binding, live recorder continuity, and labeled corridor sessions.

### EXP-20260807-DYNAMIC-RATE-METRICS - Per-link transport telemetry and canonical analysis records

- Incident: dynamic rate display briefly falls from the nominal rate to `0` and back while frames still arrive; algorithm metrics are not preserved as one per-link record.
- Status: in-progress
- Component/path: `pi/app/pi_v5_ai_core.py`, `scripts/report_metrics.py`, `tests/`
- Hypothesis: separate causal receive/valid/accepted estimators per link, with explicit warming/stale states and no quality-reject reset, will remove false zero telemetry; one canonical `analysis_metric` record per dynamic frame will preserve accepted and rejected evidence.
- New evidence since similar attempts: existing `LinkState.arrival_history` is cleared during temporal recovery after quality rejection, and the snapshot estimator returns `0` whenever fewer than two samples remain.
- Falsification test: deterministic 20/25 Hz streams, short gaps below stale timeout, quality rejects with continuing transport, link isolation, boot/stream reset, duplicate/reorder/gap, and JSONL schema/API tests.
- Baseline commit/version: repository snapshot before this change; Protocol V4, feature schema 4.
- Data/session/split: synthetic unit fixtures plus existing recorder JSONL; no raw capture is modified.
- Protocol/feature/model version: unchanged; no firmware change planned.
- Files changed: Pi rate/metric path, dashboard API/rendering, report helper, tests, completion report.
- Constants/formulas/schema/dependencies changed: causal median inter-arrival estimator; `analysis_metric` schema version 1; no new dependency.
- Expected benefit: truthful per-link transport/analysis telemetry and replayable algorithm evidence, including rejected windows.
- CPU/RAM/flash/payload/airtime/latency risk: bounded deques and recorder queue only; no ESP payload or OTA impact.
- Downstream risks: live Pi/hardware validation remains required; report statistics are descriptive and not calibrated confidence.
- Acceptance gates fixed before test: fail-closed decisions unchanged; rejected/noisy data never enters clean baseline; RX-1/RX-2 remain independent; firmware/OTA untouched.

### EXP-20260807-EDGE-LOCAL-SCORE-DESIGN - Formula Flex plus tiny AI at RX

- Incident: redesign the per-link result so 20-30 nodes do not send raw CSI or
  require Pi to recalculate every local score.
- Status: planned
- Component/path: `firmware/receiver/`, `pi/app/`, `docs/edge-result-v5-contract.md`,
  protocol/schema/tests and OTA model packaging.
- Hypothesis: an RX can validate CSI, run bounded Formula Flex and an optional
  tiny-AI correction, then send one versioned EdgeResult per window. Pi can
  reserve compute for per-link freshness, cross-link fusion, trend forecasting,
  graph routing and network scheduling.
- Falsification test: replay parity against V4 where applicable; burst/loss/
  duplicate/reorder/reset and isolated-link tests; 20-30 node load/airtime
  simulation; RAM/flash/latency/heap and model numeric-equivalence checks.
- Baseline/version: Protocol V4, feature schema 4, current Pi Formula path;
  target EdgeResult V5 and schema 5 (design only).
- Data/session/split: preserve existing raw/JSONL sessions; collect labeled
  sessions later for physical passability calibration.
- Constants/formulas/schema/dependencies changed: local score semantics,
  bounded AI correction, EdgeResult fields, model/schema/hash binding; no
  production code changed by this documentation experiment.
- Expected benefit: lower airtime/ingest work and clear ownership of each link
  while retaining quality, uncertainty and replay evidence.
- Downstream risks: score calibration, model OOD, OTA rollback, scheduler
  collisions and disagreement handling remain unverified.
- Acceptance gates fixed before test: `UNKNOWN` != `0`; invalid/stale data is
  fail-closed; packet loss raises uncertainty rather than proving obstruction;
  Pi does not silently replace EdgeResult in RUN; V4 rollback remains available.
- Tests actually run: none; this is a design record only.
- Highest verification level: static design review.
- Verdict and reason: design-only pending implementation, replay and hardware.
- Rollback status: keep V4 firmware, vectors, legacy archive and Pi replay path.
- Field validation still required: labeled multi-corridor sessions, isolated
  link loss, 20-30 node soak, OTA health/rollback and route safety.
