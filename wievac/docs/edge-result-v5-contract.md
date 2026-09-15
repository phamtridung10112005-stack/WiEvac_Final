# WiEvac EdgeResult V5 Contract

Status: DESIGN_ONLY. This document describes the target architecture; it does
not prove that firmware or Pi code already implements it.

Iteration-4 scope note (2026-08-08): the software V5 service is reference and
replay-capable only. Its active compact surface is limited to EdgeResult
decode/validation, per-link state, recorder and dashboard overview. Trend,
routing and transport-scheduling modules are explicitly unmounted by default
and may only be enabled by a reference/test configuration. The preserved V4
runner remains the compatibility path; no V5 field or deployment claim is
made.

Wire status: schema V6 is the default encoder and carries adaptive baseline,
transition, model and nullable evidence metadata. The decoder retains V5
compatibility with conservative metadata defaults. The portable C pipeline
emits/decodes V6 and remains reference-only until an executable C/Python byte
parity harness is run; its current source-level formula is not an active
production claim.

## 1. Muc dich

Moi ESP32-S3 RX phu trach mot `link_id` va tinh mot ket qua local cho doan
hanh lang ma link do dai dien. RX ket hop Formula Flex voi tiny AI tuy chon,
sau do gui mot `EdgeResult` gon ve Pi.

Pi khong tinh lai CSI/Formula cho tung link trong duong active. Pi xac thuc,
hop nhat, du doan xu huong, tinh route va dieu phoi cac node.

Protocol V4 van duoc giu lam compatibility/replay path cho den khi V5 duoc
field-verified.

## 2. Data flow

```text
CSI callback
  -> validity/MAC/LTF/radio gate
  -> bounded DSP + window features
  -> Formula Flex local score
  -> optional tiny-AI correction/gate
  -> one EdgeResult per window
  -> Pi ingest/fusion/trend/routing
```

Callback khong duoc chay I/O, model inference dai hoac ghi file. Moi queue
giua callback va worker phai co gioi han va dem drop.

## 3. Local score semantics

`local_passability_score` la diem chuan hoa cuc bo trong [0, 100]. Truoc khi
co ground truth va calibration, day la score/index ky thuat, khong phai phan
tram vat ly, xac suat co nguoi hoac confidence da hieu chuan.

State:

- `PASSABLE`: quality dat gate va score co the su dung.
- `DEGRADED`: score co the su dung co dieu kien; uncertainty tang.
- `BLOCKED`: policy cua doan cho thay nguy co khong thong qua.
- `UNKNOWN`: khong du du lieu, stale, OOD hoac khong the xac nhan.

`UNKNOWN` phai co score `null`. Khong ma hoa mat du lieu thanh `0`.

## 4. Formula Flex va tiny AI

Formula Flex la duong nen deterministic cua RX. Co the dung amplitude,
median/MAD, EMA co gioi han, delta, spread, roughness, persistence va cac
quality feature da duoc version hoa.

Tiny AI chi duoc:

- hieu chinh Formula trong gioi han `max_correction`; hoac
- phat hien truong hop Formula khong dang tin; hoac
- tra `UNKNOWN`/`DEGRADED` khi OOD/quality xau.

Model khong duoc tu y bo qua validity gate. Neu Formula va AI bat dong lon,
ghi `disagreement=true`, tang uncertainty va ap dung fallback bao thu.

Trong RUN, chi gui ket qua chinh. Trong CAPTURE/debug, co the ghi Formula
score, AI score, correction, feature vector va snapshot de replay.

## 5. EdgeResult fields

Goi V5 phai co it nhat:

```text
magic
protocol_version
message_type=EDGE_RESULT
payload_length
crc/authentication

node_id, device_id, tx_id, rx_id, link_id, corridor_id
session_id, boot_id, window_seq
window_start_us, window_end_us, rx_timestamp_us

local_passability_score (nullable)
state
quality_score
uncertainty_score
disagreement
formula_version
model_version
model_hash
feature_schema_version

sample_count
invalid_count
queue_drop_count
sequence_gap
packet_loss_ratio
jitter_ms
age_ms
reason_code
```

All timestamps are monotonic in their declared domain. `boot_id` and
`window_seq` are required to reject old data after reboot. Score/quality/
uncertainty must be finite when non-null and bounded to their declared range.

## 6. Pi contract

Pi must independently validate identity, version, CRC, sequence, timestamp,
freshness and quality for every link. A bad link cannot clear or overwrite the
state of another link.

Pi may calculate:

- fused corridor state;
- trend/forecast of each link and of the graph;
- route cost and route using Dijkstra/A*;
- telemetry time-slots, packet rate, retry and node configuration.

AI may predict link cost or future score, but a deterministic graph solver
selects the route. Stale/unknown/invalid links are excluded. If no valid route
exists, output `NO_ROUTE`.

## 7. Model and configuration commands

Every model/config command from Pi must contain:

```text
command_id
target_node_id
config_version
model_version/model_hash
valid_from_epoch
expires_at_or_ttl
```

The node ACKs acceptance and reports active version. A schema/model mismatch,
expired command or failed self-test is rejected. The node keeps the last-safe
configuration and continues measuring if Pi disappears.

## 8. Required verification before ACTIVE

- Static/math review of Formula and correction bounds.
- Replay equivalence against the V4/reference pipeline where applicable.
- Tiny AI RAM/flash/latency/heap and numeric-equivalence test.
- Burst, jitter, loss, duplicate, out-of-order and reboot tests.
- Isolated loss of each link without corrupting the other link.
- Pi stale/unknown/NO_ROUTE and route safety tests.
- 20-30 node load simulation and soak test.
- Staged OTA with hash, version, health confirmation and rollback.

Only after these gates can the status move from `DESIGN_ONLY`/`SHADOW` to
`ACTIVE`; accuracy and calibration still require labeled field sessions.
