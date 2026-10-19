"""Offline empty-corridor novelty ladder.

Default output is a candidate directory. Firmware weights change only with
--install. --synthetic-benchmark never writes firmware.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pi" / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate_loco import (  # noqa: E402
    _median, _quantile, auroc, choose_deploy_rung, DEPLOY_RULE, fit_rung, novelty, pack_blob,
    score_rung1_f32, score_rung2_f32, score_rung3_f32, tpr_at_fpr,
)

HEADER_PATH = ROOT / "firmware" / "receiver" / "main" / "tiny_ai_model_weights.h"
META_PATH = ROOT / "firmware" / "receiver" / "main" / "tiny_ai_model_weights.json"
CANDIDATE_ROOT = ROOT / "models" / "candidates"
BALANCE_SEED = 1
_SCORERS = {1: score_rung1_f32, 2: score_rung2_f32, 3: score_rung3_f32}


def synthetic_corpus(seed: int = 1) -> dict:
    """Two train corridors of empty rows and one test corridor with both classes."""
    rng = random.Random(seed)

    def rows(count: int, shift: list[float], occupied: bool) -> list[list[float]]:
        made = []
        for _ in range(count):
            if occupied:
                made.append([shift[index] + rng.gauss(0.0, 1.0) for index in range(42)])
            else:
                factor = rng.gauss(0.0, 1.0)
                made.append([shift[index] + factor + rng.gauss(0.0, 0.05) for index in range(42)])
        return made

    corpus = {}
    for name in ("corridor-A", "corridor-B"):
        shift = [rng.uniform(-0.15, 0.15) for _ in range(42)]
        corpus[name] = {
            "role": "train", "empty": rows(40, shift, False), "occupied": [],
            "excluded_count": 0, "clean_minutes": 40 / 60,
        }
    shift = [rng.uniform(-0.15, 0.15) for _ in range(42)]
    corpus["corridor-C"] = {
        "role": "test", "empty": rows(40, shift, False), "occupied": rows(20, shift, True),
        "excluded_count": 0, "clean_minutes": 1.0,
    }
    return corpus


def _label_of(record: dict, session_label: str | None) -> str:
    label = record.get("label")
    if label in {"EMPTY", "OCCUPIED", "EXCLUDED"}:
        return label
    if session_label == "OCCUPIED_VAL":
        return "OCCUPIED"
    if session_label == "EMPTY":
        return "EMPTY"
    return "EXCLUDED"


def _read_session(path: Path) -> dict | None:
    corridor = None
    session_label = None
    role = "train"
    vectors = []
    excluded = 0
    quality: dict = {}
    ordinal = 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            kind = record.get("type")
            if kind == "session":
                corridor = str(record["corridor_id"])
                session_label = record.get("session_label")
                role = record.get("role", "train")
            elif kind == "feature" and record.get("complete"):
                label = _label_of(record, session_label)
                if label == "EXCLUDED":
                    excluded += 1
                elif label in {"EMPTY", "OCCUPIED"}:
                    vectors.append({
                        "features": [float(value) for value in record["features"]],
                        "label": label,
                        "ordinal": ordinal,
                        "t_since_start_s": record.get("t_since_start_s"),
                        "short_coverage": record.get("short_coverage"),
                        "loss_30s": record.get("loss_30s"),
                        "finite": record.get("finite"),
                        "disturbance_warning": bool(record.get("disturbance_warning")),
                    })
                ordinal += 1
            elif kind == "quality":
                quality = record
    if corridor is None:
        return None
    return {
        "path": path, "corridor": corridor, "role": role, "session_label": session_label,
        "vectors": vectors, "excluded": excluded, "quality": quality,
    }


def load_datasets(root: Path) -> dict:
    """Sessions grouped by corridor. Filtering happens in preprocess_corpus."""
    corridors: dict = {}
    for path in sorted(root.glob("corridor_*/*.jsonl.gz")):
        session = _read_session(path)
        if session is None:
            continue
        bucket = corridors.setdefault(session["corridor"], {"role": None, "sessions": [], "excluded_count": 0})
        if bucket["role"] is None:
            bucket["role"] = session["role"]
        elif bucket["role"] != session["role"]:
            raise ValueError(f"corridor {session['corridor']} mixes roles in {path}")
        if session["role"] == "train":
            for vector in session["vectors"]:
                if vector["label"] == "OCCUPIED":
                    raise ValueError(f"train corridor has OCCUPIED in {path}")
        bucket["excluded_count"] += session["excluded"]
        bucket["sessions"].append(session)
    return corridors


# Same scale as the collector: noise floor near 23, empty-corridor drift
# up to ~181 (2026-10-17 A/B), walking near 10^4.
DISTURBANCE_LOWBAND_LIMIT = 300.0


def lowband_mean(features: list[float]) -> float:
    lowband = features[32:40]
    return sum(lowband) / len(lowband) if lowband else 0.0


# Loss arrives in bursts (channel interference), so a session-average loss
# throws away clean minutes. Per-vector coverage/loss_30s already removes the
# burst windows; a whole session is dropped only when most of it is broken.
MIN_CLEAN_FRACTION = 0.5


def filter_session(session: dict, role: str, *, warmup_s: float, min_coverage: float,
                   max_loss: float,
                   disturbance_threshold: float = DISTURBANCE_LOWBAND_LIMIT,
                   min_clean_fraction: float = MIN_CLEAN_FRACTION) -> tuple[list[dict], dict, str | None]:
    drops = {"nonfinite": 0, "warmup": 0, "coverage": 0, "poor_session": 0, "disturbance": 0}
    kept = []
    eligible = 0
    for vector in session["vectors"]:
        features = vector["features"]
        finite_flag = vector.get("finite")
        values_ok = all(math.isfinite(value) for value in features)
        if finite_flag is False or not values_ok:
            drops["nonfinite"] += 1
            continue
        since = vector.get("t_since_start_s")
        if since is None:
            if vector["ordinal"] < 90:
                drops["warmup"] += 1
                continue
        elif since < warmup_s:
            drops["warmup"] += 1
            continue
        eligible += 1
        coverage = vector.get("short_coverage")
        loss = vector.get("loss_30s")
        if (coverage is not None and coverage < min_coverage) or (loss is not None and loss > max_loss):
            drops["coverage"] += 1
            continue
        kept.append(vector)
    quality = session.get("quality") or {}
    fps = quality.get("frames_per_s", quality.get("frames_per_sec"))
    clean_fraction = len(kept) / eligible if eligible else 1.0
    if clean_fraction < min_clean_fraction or (fps is not None and fps < 50):
        drops["poor_session"] += len(kept)
        print(f"poor session dropped: {session['path']}")
        return [], drops, "poor"
    if role == "train" and kept:
        # Recomputed from features: the stored flag may come from an older threshold.
        warned = [index for index, vector in enumerate(kept)
                  if lowband_mean(vector["features"]) > disturbance_threshold]
        if len(warned) / len(kept) > 0.10:
            drops["disturbance"] += len(kept)
            print(f"disturbance session dropped: {session['path']}")
            return [], drops, "disturbance"
        drop_at = set()
        for index in warned:
            drop_at.update(range(index, min(index + 31, len(kept))))
        if drop_at:
            drops["disturbance"] += len(drop_at)
            kept = [vector for index, vector in enumerate(kept) if index not in drop_at]
    return kept, drops, None


def _balance(corpus: dict, drops: dict, enabled: bool) -> None:
    if not enabled:
        return
    names = [name for name, bucket in corpus.items() if bucket["role"] == "train" and bucket["empty"]]
    if len(names) < 2:
        return
    target = min(len(corpus[name]["empty"]) for name in names)
    rng = random.Random(BALANCE_SEED)
    for name in names:
        rows = corpus[name]["empty"]
        extra = len(rows) - target
        if extra > 0:
            drops["balance"] += extra
            corpus[name]["empty"] = rng.sample(rows, target)


def preprocess_corpus(raw: dict, *, warmup_s: float = 120.0, min_coverage: float = 0.9,
                      max_loss: float = 0.05, balance: bool = True,
                      disturbance_threshold: float = DISTURBANCE_LOWBAND_LIMIT,
                      min_clean_fraction: float = MIN_CLEAN_FRACTION) -> tuple[dict, dict]:
    drops = {"nonfinite": 0, "warmup": 0, "coverage": 0, "poor_session": 0, "disturbance": 0, "balance": 0}
    poor_files: list[str] = []
    disturbance_files: list[str] = []
    corpus = {}
    for name, bucket in raw.items():
        empty: list[list[float]] = []
        occupied: list[list[float]] = []
        for session in bucket["sessions"]:
            kept, step, reason = filter_session(
                session, bucket["role"], warmup_s=warmup_s,
                min_coverage=min_coverage, max_loss=max_loss,
                disturbance_threshold=disturbance_threshold,
                min_clean_fraction=min_clean_fraction,
            )
            for key, value in step.items():
                drops[key] += value
            if reason == "poor":
                poor_files.append(str(session["path"]))
                continue
            if reason == "disturbance":
                disturbance_files.append(str(session["path"]))
                continue
            for vector in kept:
                (occupied if vector["label"] == "OCCUPIED" else empty).append(vector["features"])
        corpus[name] = {
            "role": bucket["role"], "empty": empty, "occupied": occupied,
            "excluded_count": bucket["excluded_count"],
            "clean_minutes": (len(empty) + len(occupied)) / 60.0,
            "sessions": len(bucket["sessions"]),
        }
    _balance(corpus, drops, balance)
    warnings = [
        f"clean minutes {name}={bucket['clean_minutes']:.2f} < 20 "
        "(vectors overlap; this is not independent minutes)"
        for name, bucket in sorted(corpus.items()) if bucket["clean_minutes"] < 20.0
    ]
    report = {
        "drops": drops, "poor_sessions": poor_files,
        "disturbance_sessions": disturbance_files,
        "summary": [
            {
                "corridor": name, "role": bucket["role"],
                "empty": len(bucket["empty"]), "occupied": len(bucket["occupied"]),
                "excluded": bucket["excluded_count"], "sessions": bucket["sessions"],
                "clean_minutes": bucket["clean_minutes"],
            }
            for name, bucket in sorted(corpus.items())
        ],
        "warnings": warnings,
    }
    return corpus, report


def _print_summary(report: dict) -> None:
    print("corridor role sessions EMPTY OCCUPIED EXCLUDED clean_min")
    warnings = []
    for row in report["summary"]:
        print(
            f"{row['corridor']} {row['role']} {row['sessions']} {row['empty']} "
            f"{row['occupied']} {row['excluded']} {row['clean_minutes']:.2f}"
        )
        if row["clean_minutes"] < 20.0:
            warnings.append(
                f"clean minutes {row['corridor']}={row['clean_minutes']:.2f} < 20 "
                "(consecutive vectors overlap; this is not 20 independent minutes)"
            )
    drops = report["drops"]
    print(
        "dropped nonfinite={nonfinite} warmup={warmup} coverage={coverage} "
        "poor_session={poor_session} disturbance={disturbance} balance={balance}".format(**drops)
    )
    for line in warnings:
        print(line)
    report["warnings"] = warnings


def _problems(corpus: dict) -> list[str]:
    problems = []
    train = [name for name, bucket in corpus.items() if bucket["role"] == "train"]
    test = [name for name, bucket in corpus.items() if bucket["role"] == "test"]
    if len(train) < 2:
        problems.append(f"need >= 2 train corridors, have {len(train)}")
    for name in train:
        count = len(corpus[name]["empty"])
        if count < 600:
            problems.append(f"{name} has {count} EMPTY after filtering, need >= 600")
    if not test:
        problems.append("missing test corridor")
    for name in test:
        empty = len(corpus[name]["empty"])
        occupied = len(corpus[name]["occupied"])
        if empty < 300 or occupied < 120:
            problems.append(f"{name} has EMPTY={empty} OCCUPIED={occupied}, need >= 300 and >= 120")
    return problems


def calibrate_rungs(train: dict[str, list[list[float]]]) -> list[dict | None]:
    names = sorted(train)
    fitted = []
    for rung in range(4):
        ratios = []
        deltas = []
        oof_scores = []
        for held in names:
            fit_rows = [row for name in names if name != held for row in train[name]]
            model = fit_rung(rung, fit_rows)
            if model is None or not train[held]:
                continue
            q_in = _quantile([novelty(model, row) for row in fit_rows], 0.99)
            scores = [novelty(model, row) for row in train[held]]
            q_oof = _quantile(scores, 0.99)
            if rung == 2:
                deltas.append(q_oof - q_in)
            elif q_in > 1e-9:
                ratios.append(q_oof / q_in)
            else:
                ratios.append(1.0 if q_oof <= 1e-9 else q_oof / 1e-9)
            oof_scores.append((held, scores))
        all_rows = [row for name in names for row in train[name]]
        final = fit_rung(rung, all_rows)
        if final is None:
            fitted.append(None)
            continue
        q_final = final["threshold"]
        if rung == 2:
            adjust = _median(deltas) if deltas else 0.0
            final["threshold"] = q_final + adjust
        else:
            adjust = _median(ratios) if ratios else 1.0
            final["threshold"] = q_final * adjust
        final["q_in_final"] = q_final
        final["adjust"] = adjust
        final["oof"] = []
        for held, scores in oof_scores:
            final["oof"].append({
                "corridor": held,
                "fpr": sum(score > final["threshold"] for score in scores) / len(scores),
            })
        fitted.append(final)
    return fitted


def _split_metrics(model: dict, empty: list[list[float]], occupied: list[list[float]]) -> dict:
    labels = [0] * len(empty) + [1] * len(occupied)
    scores = [novelty(model, row) for row in empty + occupied]
    threshold = model["threshold"]
    fpr = sum(score > threshold for score in scores[:len(empty)]) / len(empty) if empty else 0.0
    tpr = sum(score > threshold for score in scores[len(empty):]) / len(occupied) if occupied else 0.0
    return {
        "auroc": auroc(labels, scores) if labels else None,
        "tpr01": tpr_at_fpr(labels, scores, 0.01) or 0.0,
        "tpr05": tpr_at_fpr(labels, scores, 0.05) or 0.0,
        "fpr_at": fpr, "tpr_at": tpr,
    }


def evaluate_corpus(corpus: dict) -> dict:
    train = {name: bucket["empty"] for name, bucket in corpus.items() if bucket["role"] == "train"}
    test_names = sorted(name for name, bucket in corpus.items() if bucket["role"] == "test")
    models = calibrate_rungs(train)
    per_rung = []
    tpr01 = []
    fpr_at = []
    for rung, model in enumerate(models):
        folds = []
        if model is None:
            folds = [{"auroc": None, "tpr01": 0.0, "tpr05": 0.0, "fpr_at": 1.0, "tpr_at": 0.0} for _ in test_names]
        else:
            for name in test_names:
                bucket = corpus[name]
                folds.append(_split_metrics(model, bucket["empty"], bucket["occupied"]))
        per_rung.append({"rung": rung, "adjust": None if model is None else model.get("adjust"),
                         "threshold": None if model is None else model["threshold"],
                         "oof": [] if model is None else model.get("oof", []), "test": folds})
        tpr01.append([fold["tpr01"] for fold in folds])
        fpr_at.append([fold["fpr_at"] for fold in folds])
    selected = choose_deploy_rung(tpr01, fpr_at) if test_names else 0
    return {"models": models, "rungs": per_rung, "selected": selected, "tpr01": tpr01, "fpr_at": fpr_at}


def _print_eval(result: dict) -> None:
    for rung in result["rungs"]:
        folds = rung["test"]
        if not folds:
            print(f"rung {rung['rung']} no test corridor")
            continue
        tpr01 = sum(fold["tpr01"] for fold in folds) / len(folds)
        tpr05 = sum(fold["tpr05"] for fold in folds) / len(folds)
        aucs = [fold["auroc"] for fold in folds if fold["auroc"] is not None]
        auc = sum(aucs) / len(aucs) if aucs else float("nan")
        fpr = max(fold["fpr_at"] for fold in folds)
        tpr = sum(fold["tpr_at"] for fold in folds) / len(folds)
        print(
            f"rung {rung['rung']} tpr@1%={tpr01:.3f} tpr@5%={tpr05:.3f} auroc={auc:.3f} "
            f"fpr@deploy={fpr:.3f} tpr@deploy={tpr:.3f}"
        )
        for item in rung["oof"]:
            print(f"  oof {item['corridor']} fpr={item['fpr']:.3f}")
    print(f"selected {result['selected']}")
    if len([row for row in result["rungs"][0]["test"]]) == 1:
        print("warning: one test corridor both selects the rung and reports the score; add a second test corridor")


def _python_empty(model: dict, row: list[float]) -> bool:
    return not (novelty(model, row) > model["threshold"])


def parity_ratio(model: dict, rows: list[list[float]]) -> float:
    if model["rung"] not in _SCORERS or not rows:
        return 1.0
    blob = pack_blob(model)
    scorer = _SCORERS[model["rung"]]
    same = 0
    for row in rows:
        if scorer(blob, row)[1] == _python_empty(model, row):
            same += 1
    return same / len(rows)


def evidence_span(model: dict) -> float | None:
    """Novelty distance from a typical empty window to the threshold.

    The RX scales the AI deduction by (novelty - threshold) / span, so the
    deduction is full once a window is as far beyond the threshold as the
    threshold is beyond a typical empty window. Train data only."""
    threshold = model.get("threshold")
    median = model.get("median_novelty")
    if threshold is None or median is None:
        return None
    span = float(threshold) - float(median)
    return span if span > 0.0 else None


def write_artifact(model: dict, header_path: Path, meta_path: Path, ladder: dict) -> str:
    blob = pack_blob(model)
    digest = hashlib.sha256(blob).hexdigest()
    version = f"tiny-ai-rung{model['rung']}-v1"
    span = evidence_span(model)
    body = []
    for start in range(0, len(blob), 12):
        chunk = ", ".join(f"0x{byte:02x}" for byte in blob[start:start + 12])
        body.append(f"    {chunk},")
    header = "\n".join([
        "#ifndef WIEVAC_TINY_AI_MODEL_WEIGHTS_H",
        "#define WIEVAC_TINY_AI_MODEL_WEIGHTS_H",
        "/* Generated by pi/training/train_tiny_ai.py. Not field-verified. */",
        "#define TINY_AI_MODEL_PRESENT 1",
        f"#define TINY_AI_MODEL_RUNG {model['rung']}",
        f'#define TINY_AI_MODEL_VERSION "{version}"',
        f'#define TINY_AI_MODEL_HASH "{digest}"',
        *( [f"#define TINY_AI_EVIDENCE_SPAN {span:.6g}f"] if span else [] ),
        f"static const unsigned tiny_ai_weight_blob_len = {len(blob)}U;",
        "static const unsigned char tiny_ai_weight_blob[] = {",
        *body,
        "};",
        "#endif /* WIEVAC_TINY_AI_MODEL_WEIGHTS_H */",
        "",
    ])
    header_path.write_text(header, encoding="utf-8")
    meta = {
        "present": True,
        "rung": model["rung"],
        "version": version,
        "feature_schema": "dyn-42-v1",
        "sha256": digest,
        "threshold": model["threshold"],
        "threshold_adjust": model.get("adjust"),
        "evidence_span": span,
        "loco_selected": ladder.get("selected"),
        "deploy_rule": DEPLOY_RULE,
        "corridors": ladder.get("corridors", []),
        "train_corridors": ladder.get("train_corridors", []),
        "test_corridors": ladder.get("test_corridors", []),
        "note": "Empty-class novelty only. IMPLEMENTED_PENDING_FIELD_TEST. AI may only lower a score or block rebase.",
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return digest


def _blob_from_header(text: str) -> bytes:
    match = re.search(r"tiny_ai_weight_blob\[\] = \{([^}]*)\}", text, re.S)
    if match is None:
        raise ValueError("header has no weight blob")
    return bytes(int(item, 16) for item in re.findall(r"0x([0-9a-fA-F]{2})", match.group(1)))


def install_candidate(candidate: Path) -> int:
    header = candidate / "tiny_ai_model_weights.h"
    meta = candidate / "tiny_ai_model_weights.json"
    if not header.is_file() or not meta.is_file():
        print(f"install refused: {candidate} is missing weights", file=sys.stderr)
        return 2
    text = header.read_text(encoding="utf-8")
    found = re.search(r'#define TINY_AI_MODEL_HASH "([0-9a-fA-F]+)"', text)
    digest = hashlib.sha256(_blob_from_header(text)).hexdigest()
    if found is None or found.group(1).lower() != digest:
        print("install refused: blob sha256 does not match TINY_AI_MODEL_HASH", file=sys.stderr)
        return 2
    HEADER_PATH.write_text(text, encoding="utf-8")
    META_PATH.write_text(meta.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"installed {header} -> {HEADER_PATH}")
    return 0


def _export(model: dict, corpus: dict, result: dict, report: dict) -> int:
    rows = []
    for name, bucket in corpus.items():
        if bucket["role"] == "test":
            rows.extend(bucket["empty"])
            rows.extend(bucket["occupied"])
    ratio = parity_ratio(model, rows)
    print(f"firmware parity empty_like={ratio:.4f}")
    if ratio < 0.995:
        print("export skipped (firmware parity below 99.5%)")
        return 2
    version = f"tiny-ai-rung{model['rung']}-v1"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = CANDIDATE_ROOT / f"{version}-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    ladder = {
        "selected": result["selected"],
        "corridors": sorted(corpus),
        "train_corridors": sorted(name for name, bucket in corpus.items() if bucket["role"] == "train"),
        "test_corridors": sorted(name for name, bucket in corpus.items() if bucket["role"] == "test"),
    }
    digest = write_artifact(model, folder / "tiny_ai_model_weights.h", folder / "tiny_ai_model_weights.json", ladder)
    report["selected"] = result["selected"]
    report["deploy_rule"] = DEPLOY_RULE
    report["rungs"] = result["rungs"]
    report["parity"] = ratio
    report["sha256"] = digest
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {folder} sha256={digest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the empty-corridor novelty ladder.")
    parser.add_argument("--dataset-root", type=Path, default=ROOT / "data" / "datasets")
    parser.add_argument("--synthetic-benchmark", action="store_true")
    parser.add_argument("--warmup-s", type=float, default=120.0)
    parser.add_argument("--min-coverage", type=float, default=0.9)
    parser.add_argument("--max-loss", type=float, default=0.05)
    parser.add_argument("--min-clean-fraction", type=float, default=MIN_CLEAN_FRACTION)
    parser.add_argument("--no-balance", action="store_true")
    parser.add_argument("--disturbance-threshold", type=float, default=DISTURBANCE_LOWBAND_LIMIT)
    parser.add_argument("--install", type=Path)
    args = parser.parse_args(argv)
    if args.install is not None:
        return install_candidate(args.install)
    report: dict = {"drops": {}, "summary": [], "warnings": []}
    if args.synthetic_benchmark:
        corpus = synthetic_corpus()
        report["summary"] = [
            {
                "corridor": name, "role": bucket["role"], "sessions": 1,
                "empty": len(bucket["empty"]), "occupied": len(bucket["occupied"]),
                "excluded": bucket["excluded_count"], "clean_minutes": bucket["clean_minutes"],
            }
            for name, bucket in sorted(corpus.items())
        ]
        report["drops"] = {"nonfinite": 0, "warmup": 0, "coverage": 0, "poor_session": 0, "disturbance": 0, "balance": 0}
    else:
        try:
            raw = load_datasets(args.dataset_root)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        corpus, report = preprocess_corpus(
            raw, warmup_s=args.warmup_s, min_coverage=args.min_coverage,
            max_loss=args.max_loss, balance=not args.no_balance,
            min_clean_fraction=args.min_clean_fraction,
            disturbance_threshold=args.disturbance_threshold,
        )
        problems = _problems(corpus)
        _print_summary(report)
        if problems:
            for line in problems:
                print(line, file=sys.stderr)
            print("firmware stays NOT_READY", file=sys.stderr)
            return 2
    if args.synthetic_benchmark:
        _print_summary(report)
    result = evaluate_corpus(corpus)
    _print_eval(result)
    if len([name for name, bucket in corpus.items() if bucket["role"] == "test"]) < 2:
        pass
    if result["selected"] <= 0:
        print("export skipped (no rung 1-3 met the deploy rule)")
        print(DEPLOY_RULE)
        return 0
    if args.synthetic_benchmark:
        print("export skipped (synthetic does not replace firmware NOT_READY weights)")
        return 0
    model = result["models"][result["selected"]]
    if model is None:
        print("export skipped (refit failed)")
        return 2
    return _export(model, corpus, result, report)


if __name__ == "__main__":
    raise SystemExit(main())
