# WiEvac CSI Algorithm Review - V4 compatibility va Edge V5 target

Status: design/review document. No section below proves that EdgeResult V5 is
implemented or field-verified.

## 1. Baseline can keep

### TX

- ESP-NOW tao luu luong do.
- HT20/radio rate on dinh trong mot session.
- Tat power save khi can kiem soat do tre.
- `tx_id`, MAC, sequence va timestamp la danh tinh cua stream.

### RX

- Median filter chong impulse/outlier cuc bo.
- Dual EMA theo doi thay doi nhanh/cham.
- Welford cho variance online.
- Queue tach callback CSI khoi DSP/UDP/ghi log.
- Reconnect va last-safe behavior phai co test.

### Pi

- Per-link state, recorder, stale handling va V4 replay van duoc giu cho
  compatibility.
- V4 Formula tren Pi khong phai duong Formula active cua EdgeResult V5.

Giu baseline khong co nghia giu nguyen he so, ten goi hay vi tri thuc thi.

## 2. Rủi ro can giu trong moi kien truc

### TX

- MAC/TX ID trung hoac broadcast khong ro cap co the lam sai link.
- Nhiet do, cong suat, MCS va airtime anh huong CSI; phai ghi manifest.
- Nhieu TX/RX can co schedule va counter, khong dung delay vong lap lam packet
  rate danh nghia.

### RX

- Phai loc dung MAC, `first_word_invalid`, LTF/layout, do dai va shape truoc
  khi tinh score.
- Cua so phai theo thoi gian that, khong suy ra tu so callback co dinh.
- Gain shift, packet loss, RSSI thap va nhiễu khong duoc tu dong bi dien giai
  la vat can.
- Khong duoc xoa bo quality/rejected evidence khoi CAPTURE.
- ID khong duoc tao chi tu byte cuoi MAC.

### Pi

- Phai chong trung, frame tre, out-of-order, reboot va link isolation.
- Khong duoc dung `UNKNOWN`/stale/degraded lam score tot.
- Khong goi heuristic la AI, dictionary la D* Lite, hay z-score la SNR.

## 3. EdgeResult V5 target

### RX edge pipeline

```text
CSI callback
  -> validity/MAC/LTF/radio gate
  -> bounded filter + time window
  -> Formula Flex local score
  -> tiny AI correction/OOD gate (optional)
  -> one EdgeResult for this link/window
```

Formula la ket qua nen deterministic. Tiny AI chi duoc hieu chinh trong
`max_correction`, kiem tra OOD/quality, hoac dua ket qua ve `DEGRADED`/
`UNKNOWN`.

`local_passability_score` 0..100 la score chuan hoa cuc bo. Chi goi la phan
tram vat ly khi co ground truth va calibration tren nhieu session/corridor.

EdgeResult phai co score nullable, state, quality, uncertainty, age, boot ID,
window sequence, counters, formula/model/schema version va reason. `UNKNOWN`
khac `0`.

### Pi responsibilities

Pi chi lam trong duong EdgeResult active:

- validate va record EdgeResult;
- hop nhat cac link;
- du doan xu huong score/link cost;
- tinh route bang Dijkstra/A*;
- dieu phoi time-slot, packet rate, retry va config.

Pi khong tinh lai per-link CSI/Formula roi am tham thay the EdgeResult. V4
replay/shadow phai duoc tag ro.

## 4. Build va deployment order

1. Dong bang V4 compatibility, test vector va rollback.
2. Chot `edge-result-v5-contract.md` va schema.
3. Port DSP/Formula Flex sang RX ma khong bo quality/rejection counters.
4. Them tiny AI o shadow mode; do RAM/flash/latency/heap va numeric parity.
5. Pi ingest EdgeResult va mo phong 20-30 node.
6. Test burst/loss/duplicate/reorder/reset/mat rieng tung link.
7. Chay 1 TX + 2 RX that, sau do moi mo rong.
8. Active tiny AI va staged OTA chi sau khi co evidence.

## 5. Xac nhan

Co the xac nhan static review, build, unit/replay va packet compatibility khi
chua co phan cung. Khong duoc tuyen bo:

- do thong qua/chinh xac phat hien nguoi;
- calibration confidence;
- Flex qua moi corridor;
- model OOD/LightGBM readiness;
- EdgeResult field active.

Trang thai phu hop khi chua co field test la
`IMPLEMENTED_PENDING_FIELD_TEST` hoac `DESIGN_ONLY`.
