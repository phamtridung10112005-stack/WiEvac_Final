# WiEvac Data and AI Lifecycle

Status: target edge-local scoring architecture. A document entry is not
evidence that a model or EdgeResult path is implemented.

## 1. Ownership

### RX edge path

Each RX owns one `link_id` and computes:

- validated CSI and bounded DSP;
- Formula Flex local score;
- optional tiny AI correction or quality/OOD gate;
- one `EdgeResult` per time window.

The RX result is a local normalized score. It is not automatically a calibrated
physical percentage or a probability of a person.

### Pi path

Pi owns:

- packet validation and per-link state;
- recorder and dataset export;
- cross-link fusion;
- trend/forecast models;
- graph route solving and network scheduling;
- model/config registry and OTA.

Pi must not silently recalculate a second per-link result and select whichever
looks better. A V4 replay/shadow calculation is allowed only when explicitly
tagged as `compatibility` or `shadow`.

## 2. Data layout

```text
data/
|-- captures/
|   `-- pi-01/<corridor_id>/<link_id>/<session_id>/
|       |-- edge_results.jsonl or samples.parquet
|       `-- manifest.yaml
`-- labels/
    `-- <session_id>.csv
```

Rules:

- Pi writes `.part` and renames only after a successful close.
- Completed sessions and raw captures are immutable.
- Labels are edited separately from raw data.
- Every row is traceable to session, corridor, link, node, firmware,
  protocol/schema and model versions.
- `RUN` sends compact EdgeResult records.
- `CAPTURE` retains bounded near-raw CSI or snapshots for replay, ablation and
  future model training. Do not stream raw CSI continuously without airtime
  and loss evidence.

## 3. EdgeResult record

Each window should preserve:

- `session_id`, `corridor_id`, `link_id`, `tx_id`, `rx_id`, `boot_id`;
- window start/end, receive timestamp, sequence and `age_ms`;
- `protocol_version`, `feature_schema_version`, `formula_version`;
- `model_version` and `model_hash` when tiny AI is present;
- `local_passability_score` or null;
- `state`, `quality`, `uncertainty`, `disagreement` and `reason`;
- sample, invalid, queue-drop, sequence-gap, loss and jitter counters;
- optional Formula/AI intermediate values in debug/CAPTURE only.

`UNKNOWN` has a null score. It is never silently converted to `0` or
`PASSABLE`.

## 4. Labels

Keep labels on separate axes:

### Presence

- `EMPTY`
- `HUMAN_PRESENT`
- `UNKNOWN`

### Quality/interference

- `NONE`
- `WRONG_SOURCE`
- `WIFI_COCHANNEL`
- `GAIN_SHIFT`
- `PACKET_INSTABILITY`
- `NON_HUMAN_MOTION`
- `ENVIRONMENT_CHANGE`
- `UNKNOWN`

### Passability

- `PASSABLE`
- `DEGRADED`
- `CONGESTED`
- `CRITICAL`
- `UNKNOWN`

Do not infer a physical passability label from one CSI feature or from a
Formula score. Every label needs `label_source`, `label_confidence`, annotator
or sensor, version and validity interval.

## 5. Model roles

### RX tiny AI

The optional RX model may:

- correct Formula Flex within a bounded `max_correction`;
- identify Formula/OOD cases;
- output a quality gate.

It must not bypass invalid CSI, stale, identity or sequence gates. RX model
packages require compatible feature schema, model hash, RAM/flash/latency
measurements and rollback.

### Pi trend model

Pi may predict future local score, link cost, congestion trend or uncertainty
from EdgeResult history. The route is selected by a deterministic graph solver;
the model does not directly emit an unvalidated evacuation route.

### Model lifecycle

```text
experimental -> candidate -> shadow -> active -> retired/rolled-back
```

Missing, incompatible or OOD models return `NOT_READY`, `INCOMPATIBLE` or
`UNKNOWN`. A formula heuristic is not an AI model.

## 6. Dataset and leakage controls

- Overlapping windows from one session stay in one split.
- Split by session/day/corridor, never random adjacent rows.
- At least one hold-out corridor is required before a Flex claim.
- Preprocessing and normalization are fitted on train only.
- Feature and threshold tuning never uses the test set.
- Report class distribution, missingness, loss and quality by corridor/link.
- Keep rejected/noisy windows with rejection reason; do not delete them just to
  make a model look clean.
- Formula output can be a pseudo-label only in a clearly marked auxiliary set;
  it is never ground truth.

## 7. Metrics

For edge score and passability:

- MAE/RMSE or an explicitly justified ordinal metric;
- error by corridor, session, scenario and link;
- stale/unknown behavior and recovery latency;
- false passable rate when the segment is not usable;
- calibration only when a real labeled target exists.

For trend/routing:

- forecast error and horizon;
- route validity and `NO_ROUTE` safety;
- stale-link exclusion;
- end-to-end latency and recovery after link loss.

For tiny AI deployment:

- RAM, flash, CPU time, heap stability and thermal behavior;
- numeric equivalence against the reference implementation;
- behavior on missing, invalid, OOD and disagreement inputs.

Never claim field accuracy, confidence calibration or LightGBM readiness from a
short laboratory session.

## 8. Train registry

Every model stores:

- dataset snapshot/hash;
- code/config version;
- feature schema and formula version;
- hyperparameters and seed;
- split definition and metrics;
- model hash and runtime/dependency version;
- calibration/OOD status;
- target (`rx-tiny-ai` or `pi-trend`).

Never overwrite an active model. Reject a model whose schema or target does not
match the receiving component.
