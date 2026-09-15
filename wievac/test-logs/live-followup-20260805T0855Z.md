# WiEvac Live Follow-up

- Run window (UTC): 2026-08-05T08:48:33Z to 2026-08-05T08:55:30Z.
- Scope: live Pi, dashboard, transport, and a fresh recorder session; no source/build/flash changes were made in this follow-up.

## Live Pi Evidence

- `http://10.42.0.1:8080/api/health` returned HTTP 200 with `ok=true`, `udp_running=true`, dynamic age below 0.05 s, and storage `ready`.
- `/api/status` reported `node_connectivity=online`, `node_online=true`, TX/RX/UDP online, and `transport_receiving_by_link={1:true,2:true}`. `history_revision` increased during polling.
- `/api/csi-live` returned fresh (`stale=false`) valid snapshots for both links, each with `shape_valid=true`, CSI length 128, and 64 subcarriers.
- Pi process and socket evidence: `run_pi_v4.py --require-capture`, `pi_v5_ai_core.py`, UDP `10.42.0.1:8888`, dashboard `0.0.0.0:8080`.
- AP evidence: SSID `WiEvac_Pi5`, channel 11 on `wlan0`.
- Local/remote Pi hashes still match: core `77c3b6c6909e3c3d5faaf82884ba449a780c56f62f64c60159afb5102a6e80cc`; runner `54835501668f7afdde521ffb59ae7bbd16a7a1ee7826253bbdeaa586bff0937e`.

## Fresh Recorder Session

- Session: `20260805T084833Z-5f2ebd3b`.
- Remote JSONL: `/home/junpham/wievac/data/real/v4/sessions/20260805/20260805T084833Z-5f2ebd3b.jsonl`.
- State: `CLOSED`; duration `30.339 s`; `1511` lines; `15130567` bytes; recorder drops were zero.
- Records: `1329` dynamic, `60` feature, `120` snapshots, one `session_start`, and one `session_end`.
- Per-link records: link 1 has `666` dynamic / `30` feature / `60` snapshots; link 2 has `663` dynamic / `30` feature / `60` snapshots.
- Quality-valid dynamic frames: link 1 `349/666`; link 2 `30/663`. Therefore usable dynamic data exists on both links, but link 2 was strongly degraded during this 30 s window.
- Dominant link 2 reasons: `feature_quality_unavailable` (mostly after invalid/context interruptions), `feature_invalid_csi`, `shape_invalid,invalid_csi`, and occasional `rf_level_shift`.

## Interpretation

- Current aggregate `UNKNOWN` / `SIGNAL_INVALID` is a quality-gate result, not an offline transport result. Both links are receiving and both snapshots are fresh.
- Link 2 has materially worse current RF context than link 1 (live RSSI observed around -57 dBm versus around -27 dBm on link 1, with higher AGC), so the fail-closed quality gate is expected to reject many frames. This is evidence for a physical RF/placement/antenna check, not evidence of an unflashed ESP or a dead Pi socket.
- Do not lower the quality gate or relabel invalid frames as valid. Before a stable field run, move/space the nodes and verify antenna seating/orientation, then repeat the same bounded session.
- A browser showing `offline` immediately after navigation can be the dashboard's initial placeholder; hard-refresh `http://10.42.0.1:8080/` and read the separate `Transport receiving` and `Quality valid` indicators.
- Recovery observation at 2026-08-05T09:00:59Z: `/api/health` remained healthy and both links were again `quality_valid=true`, `WARMING_UP`, with `transport_receiving=true`; aggregate `UNKNOWN` then meant warm-up/baseline-not-ready.

## Conclusion

- Transport, pairing, Pi ingest, dashboard HTTP, and recorder durability: `PASS` for this bounded live check.
- Link-quality readiness is variable: link 2 was `DEGRADED` in the bounded session and later recovered to `WARMING_UP`; no detection-accuracy claim.
- Overall project status remains `PASS_WITH_KNOWN_LIMITATIONS` from the final readiness report, with physical RF/placement tuning and isolated RX-loss tests still pending.
