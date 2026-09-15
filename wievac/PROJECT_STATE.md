# WiEvac Project State

## Scope and status

- This file describes durable architecture and evidence limits, not a frozen
  deployment report. Every goal must refresh hardware/Pi/build status from
  current evidence; do not inherit `FLASH_STATUS`, `LIVE_PI_DEPLOYMENT` or
  verdict values from an older run.
- The active compact path is a C receiver plus the existing Python Pi decoder,
  recorder and dashboard using `WIV5`, protocol `5`, schema `7`.
- An explicit goal may build, flash RX-1/RX-2, read serial and deploy the
  compact Pi runtime. Those claims require fresh generated-config, boot and
  live-acceptance evidence in that same goal.
- OTA, remote rollback and Pi-to-node control remain out of scope.
- RX-local scoring is the target architecture; it is not a claim of field
  accuracy, calibration or generalization.
- Legacy V4/replay sources are preserved but inactive/read-only by default.
  V4 remains only where firmware pairing/control compatibility requires it.

## RX responsibilities

Each receiver owns its link-local signal decision and evidence:

- validates CSI locally, including source identity, LTF/shape metadata, malformed I/Q, and quality gates;
- applies the adaptive Formula Flex locally using link-local normalized features;
- learns a link-local Doppler `r` envelope; occupancy freeze requires both amplitude z and Doppler z vs that envelope so a new hall can rebase instead of locking like RX-1; published score stays Formula Flex (not `r` times stress);
- learns a local baseline only from eligible, quality-approved windows;
- emits local passability score/index, state, uncertainty, and blocking evidence;
- fails closed to `UNKNOWN`/`DEGRADED` when data is stale, invalid, ambiguous, or outside the safe evidence envelope.

Quiet persistent shifts and stationary occupants do not silently become a new
baseline. A clean, quality-gated shift may enter an explicit candidate/rebase
state; during transition the score is UNKNOWN/invalid, and promotion must
record a new `baseline_version` and `baseline_update_reason`. Invalid CSI,
sequence gaps, queue drops and change-point windows never train the baseline.

## Pi responsibilities

The active compact Pi runner receives EdgeResult packets, not raw CSI and not a second copy of the RX algorithm. Pi only ingests compact EdgeResult, only records accepted and rejected compact results, and only displays the compact dashboard:

- decodes and validates compact EdgeResult schema, CRC, identity, freshness, sequence, and boot;
- records accepted compact results and bounded rejected packet envelopes;
- serves the compact dashboard and status APIs;
- keeps link state independent and preserves rejection evidence.

The active entrypoint is `scripts/run_pi_v5_compact.py`; it does not import
the removed legacy Pi analysis core, `LinkState`, `DualLinkFusion`, Pi Formula,
Pi baseline, Pi trend or Pi routing. Reference analysis modules are not mounted
by default.

## Protocol and configuration

- Active compact topology uses feature schema `7` and the current Formula Flex contract documented in `docs/flex-threshold-policy.md`.
- Active compact wire contract is `WIV5`, protocol `5`, schema `7`. Legacy V4
  is not an alternate active Pi runtime; it is retained only for compatibility
  or replay where explicitly requested.
- Active node/link identity is configured in `config/corridors/`; schema and Formula versions must agree with codec defaults.
- Endpoint allow-listing is optional in lab mode; configured link identity is still validated. Source-IP authentication is not claimed unless `WIEVAC_RX_ENDPOINTS` is configured and tested.

## Models and interpretation limits

- Tiny AI is `NOT_READY` without a real, compatible model artifact and hash.
- Scores are uncalibrated local indices, not percentages of people or corridor occupancy.
- Field accuracy, calibration, and generalization are unverified.
- No LightGBM readiness claim exists.

## Evidence and rollback

- Raw captures, accepted/rejected records, replay vectors and logs are
  append-only evidence and must not be deleted.
- Existing data, logs and legacy evidence are preserved. No automatic backup or
  report is required; a timestamped backup is created only when the user
  explicitly authorizes a destructive/risky edit and names the destination.
- Active C firmware may be built and deployed when the goal authorizes it, but
  C/Python behavioral parity remains `UNVERIFIED` unless an executable parity
  check proves it.
- Tiny AI remains `NOT_READY`; field accuracy, calibration, generalization and
  production readiness remain `UNVERIFIED` unless separately proven.

## Current verification boundary

- `COMPACT_PI_SOFTWARE_STATUS=PASS` means only local software checks passed.
- `LIVE_PI_DEPLOYMENT_STATUS`, `HARDWARE_STATUS` and `FLASH_STATUS` are
  task-local values and must be refreshed from current evidence.
- `FIELD_STATUS=UNVERIFIED` remains the default until a separately documented
  field run proves it. OTA is not supported by the current source.
