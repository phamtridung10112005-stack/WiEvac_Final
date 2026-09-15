# EdgeResult V6 Reference-to-C Port Map

This map binds the active receiver path to the existing tested reference. It is
an implementation audit, not a claim of field parity.

| Reference step | Reference/C source | Active C implementation | Port status |
|---|---|---|---|
| CSI input and bounded queue | `edge_result_v5_reference.py:CsiFrame`, `FormulaFlex.submit` | `edge_result_pipeline_submit_csi`, `v6_submit_and_emit` from `app_main.c` | ADAPTED_FOR_EMBEDDED |
| CSI validation | `CsiFrame.__post_init__`, pipeline validation | `compute_sample_features`, source/MAC/timestamp/sequence checks | ADAPTED_FOR_EMBEDDED |
| Magnitude/features | `FormulaFlex._features` | `compute_sample_features`, `update_link` | ADAPTED_FOR_EMBEDDED |
| Robust median/MAD | `FormulaFlex._candidate_stats`, `_baseline` | `median_values`, `maybe_update_baseline`, `baseline_deviation` | ADAPTED_FOR_EMBEDDED |
| Adaptive baseline | `FormulaFlex._set_baseline`, `confirm_empty` | bounded short/long baseline buffers and `maybe_update_baseline` | ADAPTED_FOR_EMBEDDED |
| Drift/transition | `FormulaFlex._window_profile`, transition logic | `make_result` shift candidate/rebase/occupied states | ADAPTED_FOR_EMBEDDED |
| Formula Flex | `FormulaFlex.score` | `default_formula` or configured `edge_result_formula_fn` | ADAPTED_FOR_EMBEDDED |
| Filtered evidence/rate limiting | reference filtered score and bounded correction | C result filtered/raw evidence and bounded tiny-AI correction | ADAPTED_FOR_EMBEDDED |
| Occupancy/blocking/passability | reference evidence/state gates | `make_result` occupancy/blocking evidence and fail-closed states | ADAPTED_FOR_EMBEDDED |
| Uncertainty/quality/loss | reference quality and uncertainty guards | `make_result` quality, uncertainty, jitter, packet loss | ADAPTED_FOR_EMBEDDED |
| Sequence/counters/stale | runtime sequence and freshness validation | `update_link`, queue drops, sequence gaps, timestamp rejection | ADAPTED_FOR_EMBEDDED |
| Tiny-AI/model metadata | `TinyAiRunner.correct`, metadata guards | C callback hook plus `model_state`, correction bound, schema guard | ADAPTED_FOR_EMBEDDED |
| V6 wire encoding | `pi/app/edge_result_v5.py` | `edge_result_v5_encode` (WIV5/protocol 5/schema 6/CRC32) | PORTED |
| Active CSI -> UDP entrypoint | reference pipeline runner | `app_main.c:process_csi` -> `v6_submit_and_emit` -> `enqueue_udp` | PORTED |

The V4 encoders remain compiled only as control/replay compatibility code. With
`CONFIG_WIEVAC_EDGE_RESULT_V6_ACTIVE=1`, dynamic/snapshot/feature V4 UDP sends
are suppressed and the active datagram is compact V6 only.
