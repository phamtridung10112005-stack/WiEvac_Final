"""Leave-one-corridor-out ladder for empty-corridor novelty models.

Rung 0 is univariate MAD. Later rungs are selected only when their TPR at
FPR 1% is strictly higher on every held-out corridor. No dataset ships
with this tree; callers pass rows in.
"""

from __future__ import annotations

import math
import struct
from typing import Iterable

FEATURES = 42


def _mean(rows: list[list[float]]) -> list[float]:
    count = len(rows)
    return [sum(row[index] for row in rows) / count for index in range(FEATURES)]


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    weight = pos - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _median(values: list[float]) -> float:
    return _quantile(values, 0.5)


def auroc(labels: list[int], scores: list[float]) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    order = sorted(range(len(scores)), key=lambda index: scores[index])
    ranks = [0.0] * len(scores)
    cursor = 0
    while cursor < len(scores):
        end = cursor
        while end + 1 < len(scores) and scores[order[end + 1]] == scores[order[cursor]]:
            end += 1
        rank = (cursor + end) / 2.0 + 1.0
        for index in range(cursor, end + 1):
            ranks[order[index]] = rank
        cursor = end + 1
    positive_ranks = sum(ranks[index] for index, label in enumerate(labels) if label)
    return (positive_ranks - positives * (positives + 1) / 2.0) / (positives * negatives)


def tpr_at_fpr(labels: list[int], scores: list[float], fpr_limit: float) -> float | None:
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    order = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
    true_pos = 0
    false_pos = 0
    best = 0.0
    for index in order:
        if labels[index]:
            true_pos += 1
        else:
            false_pos += 1
        if false_pos / negatives <= fpr_limit:
            best = max(best, true_pos / positives)
    return best


def select_rung(tpr01: list[list[float]]) -> int:
    """tpr01[rung][fold]. A rung wins only by beating the previous one on every fold."""
    selected = 0
    folds = len(tpr01[0])
    for rung in range(1, len(tpr01)):
        if all(tpr01[rung][fold] > tpr01[rung - 1][fold] for fold in range(folds)):
            selected = rung
        else:
            break
    return selected


def _mad_fit(rows: list[list[float]]) -> dict:
    center = []
    scale = []
    for index in range(FEATURES):
        column = [row[index] for row in rows]
        mid = _median(column)
        mad = _median([abs(value - mid) for value in column])
        center.append(mid)
        scale.append(max(mad, 1e-6) / 0.67448975)
    return {"rung": 0, "center": center, "scale": scale}


def _mad_novelty(model: dict, row: list[float]) -> float:
    total = 0.0
    for index in range(FEATURES):
        z = (row[index] - model["center"][index]) / model["scale"][index]
        total += z * z
    return total / FEATURES


def _invert(matrix: list[list[float]]) -> list[list[float]] | None:
    size = len(matrix)
    work = [row[:] + [1.0 if col == index else 0.0 for col in range(size)] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(work[row][col]))
        if abs(work[pivot][col]) < 1e-12:
            return None
        work[col], work[pivot] = work[pivot], work[col]
        scale = work[col][col]
        work[col] = [value / scale for value in work[col]]
        for row in range(size):
            if row == col or work[row][col] == 0.0:
                continue
            factor = work[row][col]
            work[row] = [work[row][index] - factor * work[col][index] for index in range(size * 2)]
    return [row[size:] for row in work]


def _ledoit_wolf(rows: list[list[float]]) -> tuple[list[float], list[list[float]]] | None:
    """Shrink the sample covariance toward a scaled identity. O(n p^2)."""
    count = len(rows)
    mu = _mean(rows)
    centered = [[row[index] - mu[index] for index in range(FEATURES)] for row in rows]
    cov = [[0.0] * FEATURES for _ in range(FEATURES)]
    for row in centered:
        for left in range(FEATURES):
            for right in range(FEATURES):
                cov[left][right] += row[left] * row[right]
    for left in range(FEATURES):
        for right in range(FEATURES):
            cov[left][right] /= count
    trace = sum(cov[index][index] for index in range(FEATURES))
    target = trace / FEATURES
    fro_cov = 0.0
    delta2 = 0.0
    for left in range(FEATURES):
        for right in range(FEATURES):
            fro_cov += cov[left][right] * cov[left][right]
            gap = cov[left][right] - (target if left == right else 0.0)
            delta2 += gap * gap
    beta_acc = 0.0
    for row in centered:
        quad = sum(row[index] * row[index] for index in range(FEATURES))
        sq = 0.0
        for left in range(FEATURES):
            mixed = sum(cov[left][right] * row[right] for right in range(FEATURES))
            sq += row[left] * mixed
        beta_acc += quad * quad - 2.0 * sq + fro_cov
    shrink = 0.0 if delta2 <= 0.0 else min(1.0, max(0.0, (beta_acc / (count * count)) / delta2))
    shrunk = []
    for left in range(FEATURES):
        shrunk.append([
            shrink * (target if left == right else 0.0) + (1.0 - shrink) * cov[left][right]
            for right in range(FEATURES)
        ])
    precision = _invert(shrunk)
    if precision is None:
        precision = _invert([[(shrunk[i][i] if i == j else 0.0) for j in range(FEATURES)] for i in range(FEATURES)])
    if precision is None:
        return None
    return mu, precision


def _mahalanobis(mu: list[float], precision: list[list[float]], row: list[float]) -> float:
    delta = [row[index] - mu[index] for index in range(FEATURES)]
    total = 0.0
    for left in range(FEATURES):
        mixed = sum(precision[left][right] * delta[right] for right in range(FEATURES))
        total += delta[left] * mixed
    return total


def _gmm_fit(rows: list[list[float]], components: int) -> dict:
    count = len(rows)
    order = sorted(range(count), key=lambda index: rows[index][0])
    means = []
    variances = []
    for component in range(components):
        start = component * count // components
        stop = max(start + 1, (component + 1) * count // components)
        chunk = [rows[index] for index in order[start:stop]]
        means.append(_mean(chunk))
        variances.append([1.0] * FEATURES)
    weights = [1.0 / components] * components
    resp = [[0.0] * components for _ in rows]
    for _epoch in range(8):
        for row_index, row in enumerate(rows):
            terms = []
            for component in range(components):
                dist = 0.0
                log_det = 0.0
                for index in range(FEATURES):
                    var = max(variances[component][index], 1e-3)
                    delta = row[index] - means[component][index]
                    dist += delta * delta / var
                    log_det += math.log(2.0 * math.pi * var)
                terms.append(math.log(max(weights[component], 1e-8)) - 0.5 * log_det - 0.5 * dist)
            top = max(terms)
            raw = [math.exp(term - top) for term in terms]
            normalizer = sum(raw) or 1.0
            resp[row_index] = [value / normalizer for value in raw]
        for component in range(components):
            mass = sum(resp[row_index][component] for row_index in range(count)) or 1e-8
            weights[component] = mass / count
            means[component] = [
                sum(resp[row_index][component] * rows[row_index][index] for row_index in range(count)) / mass
                for index in range(FEATURES)
            ]
            variances[component] = [
                max(sum(resp[row_index][component] * (rows[row_index][index] - means[component][index]) ** 2
                        for row_index in range(count)) / mass, 1e-3)
                for index in range(FEATURES)
            ]
    return {"rung": 2, "weights": weights, "means": means, "variances": variances}


def _gmm_bic(model: dict, rows: list[list[float]]) -> float:
    total = sum(_gmm_loglik(model, row) for row in rows)
    components = len(model["weights"])
    params = components * (1 + FEATURES + FEATURES) - 1
    return -2.0 * total + params * math.log(len(rows))


def _gmm_loglik(model: dict, row: list[float]) -> float:
    terms = []
    for component, weight in enumerate(model["weights"]):
        dist = 0.0
        log_det = 0.0
        for index in range(FEATURES):
            var = max(model["variances"][component][index], 1e-3)
            delta = row[index] - model["means"][component][index]
            dist += delta * delta / var
            log_det += math.log(2.0 * math.pi * var)
        terms.append(math.log(max(weight, 1e-8)) - 0.5 * log_det - 0.5 * dist)
    top = max(terms)
    return top + math.log(sum(math.exp(term - top) for term in terms))


def _ae_fit(rows: list[list[float]], bottleneck: int = 8) -> dict:
    mu = _mean(rows)
    var = [0.0] * FEATURES
    for row in rows:
        for index in range(FEATURES):
            delta = row[index] - mu[index]
            var[index] += delta * delta
    inv_std = [1.0 / math.sqrt(max(value / len(rows), 1e-6)) for value in var]
    data = [[(row[index] - mu[index]) * inv_std[index] for index in range(FEATURES)] for row in rows]
    rng = _lcg(1)
    w1 = [[rng() * 0.1 for _ in range(FEATURES)] for _ in range(bottleneck)]
    b1 = [0.0] * bottleneck
    w2 = [[rng() * 0.1 for _ in range(bottleneck)] for _ in range(FEATURES)]
    b2 = [0.0] * FEATURES
    lr = 0.05
    for _epoch in range(6):
        for sample in data:
            hidden = [b1[unit] + sum(w1[unit][index] * sample[index] for index in range(FEATURES))
                      for unit in range(bottleneck)]
            recon = [b2[index] + sum(w2[index][unit] * hidden[unit] for unit in range(bottleneck))
                     for index in range(FEATURES)]
            grad = [2.0 * (recon[index] - sample[index]) / FEATURES for index in range(FEATURES)]
            hidden_grad = [sum(w2[index][unit] * grad[index] for index in range(FEATURES))
                           for unit in range(bottleneck)]
            for index in range(FEATURES):
                for unit in range(bottleneck):
                    w2[index][unit] -= lr * grad[index] * hidden[unit]
                b2[index] -= lr * grad[index]
            for unit in range(bottleneck):
                for index in range(FEATURES):
                    w1[unit][index] -= lr * hidden_grad[unit] * sample[index]
                b1[unit] -= lr * hidden_grad[unit]
    return {"rung": 3, "mu": mu, "inv_std": inv_std, "w1": w1, "b1": b1, "w2": w2, "b2": b2,
            "bottleneck": bottleneck}


def _lcg(seed: int):
    state = seed & 0xFFFFFFFF

    def draw() -> float:
        nonlocal state
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        return (state / 4294967296.0) * 2.0 - 1.0

    return draw


def _ae_mse(model: dict, row: list[float]) -> float:
    sample = [(row[index] - model["mu"][index]) * model["inv_std"][index] for index in range(FEATURES)]
    hidden = [model["b1"][unit] + sum(model["w1"][unit][index] * sample[index]
                                       for index in range(FEATURES))
              for unit in range(model["bottleneck"])]
    recon = [model["b2"][index] + sum(model["w2"][index][unit] * hidden[unit]
                                       for unit in range(model["bottleneck"]))
             for index in range(FEATURES)]
    return sum((recon[index] - sample[index]) ** 2 for index in range(FEATURES)) / FEATURES


def fit_rung(rung: int, rows: list[list[float]]) -> dict | None:
    if len(rows) < 4:
        return None
    if rung == 0:
        model = _mad_fit(rows)
    elif rung == 1:
        shrunk = _ledoit_wolf(rows)
        if shrunk is None:
            return None
        mu, precision = shrunk
        model = {"rung": 1, "mu": mu, "precision": precision}
    elif rung == 2:
        candidates = [_gmm_fit(rows, components) for components in (2, 3, 4)]
        model = min(candidates, key=lambda item: _gmm_bic(item, rows))
    elif rung == 3:
        model = _ae_fit(rows)
    else:
        return None
    scores = [novelty(model, row) for row in rows]
    model["threshold"] = _quantile(scores, 0.99)
    return model


def novelty(model: dict, row: list[float]) -> float:
    rung = model["rung"]
    if rung == 0:
        return _mad_novelty(model, row)
    if rung == 1:
        return _mahalanobis(model["mu"], model["precision"], row)
    if rung == 2:
        return -_gmm_loglik(model, row)
    return _ae_mse(model, row)


def evaluate_fold(train_rows: list[list[float]], empty_rows: list[list[float]],
                  occupied_rows: list[list[float]]) -> list[dict]:
    labels = [0] * len(empty_rows) + [1] * len(occupied_rows)
    held = empty_rows + occupied_rows
    metrics = []
    for rung in range(4):
        model = fit_rung(rung, train_rows)
        if model is None:
            metrics.append({"auroc": None, "tpr01": 0.0, "tpr05": 0.0})
            continue
        scores = [novelty(model, row) for row in held]
        metrics.append({
            "auroc": auroc(labels, scores),
            "tpr01": tpr_at_fpr(labels, scores, 0.01) or 0.0,
            "tpr05": tpr_at_fpr(labels, scores, 0.05) or 0.0,
        })
    return metrics


def run_ladder(corpus: dict[str, dict[str, list[list[float]]]]) -> dict:
    names = [name for name, bucket in corpus.items() if bucket["empty"] and bucket["occupied"]]
    names.sort()
    if len(names) < 2:
        raise ValueError("LOCO needs at least two corridors with EMPTY and OCCUPIED_VAL rows")
    folds = []
    for held in names:
        train = []
        for name in names:
            if name != held:
                train.extend(corpus[name]["empty"])
        folds.append(evaluate_fold(train, corpus[held]["empty"], corpus[held]["occupied"]))
    tpr01 = [[folds[fold][rung]["tpr01"] for fold in range(len(folds))] for rung in range(4)]
    selected = select_rung(tpr01)
    return {"corridors": names, "folds": folds, "selected": selected, "tpr01": tpr01}


def _f32(value: float) -> bytes:
    return struct.pack("<f", float(value))


def _i8(value: float, scale: float) -> int:
    if scale <= 0.0:
        return 0
    quant = int(round(value / scale))
    return max(-127, min(127, quant))


def pack_blob(model: dict) -> bytes:
    rung = model["rung"]
    if rung not in (1, 2, 3):
        raise ValueError("rung 0 stays on the firmware MAD path and is not exported")
    blob = b"TAI1" + struct.pack("<II", 1, rung) + _f32(model["threshold"])
    if rung == 2:
        mu = [
            sum(model["means"][comp][index] * model["weights"][comp] for comp in range(len(model["weights"])))
            for index in range(FEATURES)
        ]
    else:
        mu = model["mu"]
    blob += b"".join(_f32(mu[index]) for index in range(FEATURES))
    if rung == 1:
        for row in model["precision"]:
            blob += b"".join(_f32(value) for value in row)
        return blob
    if rung == 2:
        components = len(model["weights"])
        blob += struct.pack("<I", components)
        for component, weight in enumerate(model["weights"]):
            log_det = 0.0
            inv_var = []
            for index in range(FEATURES):
                var = max(model["variances"][component][index], 1e-3)
                inv_var.append(1.0 / var)
                log_det += math.log(2.0 * math.pi * var)
            blob += _f32(math.log(max(weight, 1e-8)))
            blob += b"".join(_f32(model["means"][component][index]) for index in range(FEATURES))
            blob += b"".join(_f32(value) for value in inv_var)
            blob += _f32(-0.5 * log_det)
        return blob
    scales = []
    arrays = (model["w1"], model["b1"], model["w2"], model["b2"])
    flat = []
    for array in arrays:
        values = array if isinstance(array[0], float) else [value for row in array for value in row]
        flat.append(values)
        peak = max(abs(value) for value in values) or 1.0
        scales.append(peak / 127.0)
    blob += struct.pack("<I", model["bottleneck"])
    blob += b"".join(_f32(model["inv_std"][index]) for index in range(FEATURES))
    blob += b"".join(_f32(scale) for scale in scales)
    w1_scale, b1_scale, w2_scale, b2_scale = scales
    bottleneck = model["bottleneck"]
    for unit in range(bottleneck):
        blob += struct.pack("<b", _i8(model["b1"][unit], b1_scale))
        blob += b"".join(struct.pack("<b", _i8(model["w1"][unit][index], w1_scale)) for index in range(FEATURES))
    for index in range(FEATURES):
        blob += struct.pack("<b", _i8(model["b2"][index], b2_scale))
        blob += b"".join(struct.pack("<b", _i8(model["w2"][index][unit], w2_scale)) for unit in range(bottleneck))
    return blob


def score_rung1_f32(blob: bytes, row: Iterable[float]) -> tuple[float, bool]:
    """Device Mahalanobis. Statement order matches tiny_ai_gatekeeper.c."""
    from tiny_ai_features import add, div, f32, mul, sub

    values = [f32(value) for value in row]
    if blob[:4] != b"TAI1":
        raise ValueError("bad blob")
    threshold = struct.unpack_from("<f", blob, 12)[0]
    mu = [struct.unpack_from("<f", blob, 16 + index * 4)[0] for index in range(FEATURES)]
    base = 16 + FEATURES * 4
    delta = [sub(values[index], mu[index]) for index in range(FEATURES)]
    acc = f32(0)
    for left in range(FEATURES):
        for right in range(FEATURES):
            weight = struct.unpack_from("<f", blob, base + (left * FEATURES + right) * 4)[0]
            prod = mul(delta[left], weight)
            prod = mul(prod, delta[right])
            acc = add(acc, prod)
    if acc < 0.0:
        acc = f32(0)
    empty_like = not (acc > threshold)
    if not threshold > 0.0:
        empty_score = f32(0.0 if acc > 0.0 else 1.0)
    else:
        empty_score = div(threshold, add(threshold, acc))
    return empty_score, empty_like


def refit_selected(corpus: dict[str, dict[str, list[list[float]]]], rung: int) -> dict | None:
    if rung <= 0:
        return None
    rows = []
    for bucket in corpus.values():
        rows.extend(bucket["empty"])
    return fit_rung(rung, rows)
