"""Dynamic 42-vector shared with firmware/receiver/main/tiny_ai_features.c.

Static corridor gain is removed by using only short-term-normalized
temporal structure. Float32 operation order is the parity contract.
"""

from __future__ import annotations

import math
import struct

BANDS = 8
TONES = 52
FEATURES = 42
FEATURE_SCHEMA = "dyn-42-v1"
SHORT_US = 50_000
LONG_US = 125_000
LONG_N = 240
SHORT_CAP = 48
EMIT_US = 1_000_000
SHORT_SPAN = 38
NOISE_INIT_N = 31  # median of the first 31 short-window variances (C: TINY_AI_NOISE_INIT_N)
FRESH_US = 5_000_000
# HT20 FFT index, DC at 32. Same geometry as noise_filter.
TONE_INDEX = tuple(list(range(6, 32)) + list(range(33, 59)))
BAND_SIZE = (7, 7, 6, 6, 6, 6, 7, 7)
# cos(2*pi*k/240) for k=3..60 as float32 bits. Shared with the C table.
GOERTZEL_COS = (
    0x3F7F35F9, 0x3F7E98FD, 0x3F7DCF55, 0x3F7CD925, 0x3F7BB698, 0x3F7A67E2,
    0x3F78ED3C, 0x3F7746EA, 0x3F757536, 0x3F737871, 0x3F7150F4, 0x3F6EFF20,
    0x3F6C835E, 0x3F69DE1D, 0x3F670FD4, 0x3F641901, 0x3F60FA29, 0x3F5DB3D7,
    0x3F5A46A0, 0x3F56B31D, 0x3F52F9EF, 0x3F4F1BBD, 0x3F4B1934, 0x3F46F30A,
    0x3F42A9F7, 0x3F3E3EBD, 0x3F39B223, 0x3F3504F3, 0x3F303801, 0x3F2B4C25,
    0x3F26423A, 0x3F211B24, 0x3F1BD7CA, 0x3F167918, 0x3F110000, 0x3F0B6D77,
    0x3F05C277, 0x3F000000, 0x3EF44E27, 0x3EE87171, 0x3EDC6BF3, 0x3ED03FC9,
    0x3EC3EF15, 0x3EB77C01, 0x3EAAE8BC, 0x3E9E377A, 0x3E916A76, 0x3E8483EE,
    0x3E6F0C4D, 0x3E54E6CD, 0x3E3A9BF3, 0x3E20305B, 0x3E05A8A8, 0x3DD61305,
    0x3DA0AF2A, 0x3D565E3A, 0x3CD6710A, 0x25A34C4C,
)


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def add(left: float, right: float) -> float:
    return f32(f32(left) + f32(right))


def sub(left: float, right: float) -> float:
    return f32(f32(left) - f32(right))


def mul(left: float, right: float) -> float:
    return f32(f32(left) * f32(right))


def div(left: float, right: float) -> float:
    return f32(f32(left) / f32(right))


def abs_f32(value: float) -> float:
    value = f32(value)
    return f32(-value) if value < 0.0 else value


def sqrt_f32(value: float) -> float:
    """Four Newton steps from a shared exponent guess. Not libm."""
    value = f32(value)
    if not value > 0.0:
        return f32(0)
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    bits = ((bits >> 1) + 0x1FC00000) & 0xFFFFFFFF
    guess = struct.unpack("<f", struct.pack("<I", bits))[0]
    for _ in range(4):
        guess = mul(f32(0.5), add(guess, div(value, guess)))
    return guess


def _cos(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def band_means(amplitudes: list[float]) -> list[float]:
    means = []
    offset = 0
    for size in BAND_SIZE:
        total = f32(0)
        for index in range(size):
            total = add(total, amplitudes[offset + index])
        means.append(div(total, f32(size)))
        offset += size
    return means


def amplitudes_from_csi(csi: bytes) -> list[float] | None:
    if len(csi) < 118 or (len(csi) % 2) != 0:
        return None
    amps = []
    for index in TONE_INDEX:
        imag = struct.unpack_from("<b", csi, index * 2)[0]
        real = struct.unpack_from("<b", csi, index * 2 + 1)[0]
        power = add(mul(f32(real), f32(real)), mul(f32(imag), f32(imag)))
        amps.append(sqrt_f32(power))
    return amps


def _mean_var(values: list[float]) -> tuple[float, float]:
    count = len(values)
    total = f32(0)
    for value in values:
        total = add(total, value)
    mean = div(total, f32(count))
    energy = f32(0)
    for value in values:
        delta = sub(value, mean)
        energy = add(energy, mul(delta, delta))
    return mean, div(energy, f32(count))


def _autocorr(values: list[float], lag: int) -> float:
    count = len(values)
    if count <= lag:
        return f32(0)
    mean, _variance = _mean_var(values)
    deltas = [sub(value, mean) for value in values]
    energy = f32(0)
    for delta in deltas:
        energy = add(energy, mul(delta, delta))
    if not energy > f32(1e-12):
        return f32(0)
    lagged = f32(0)
    for index in range(count - lag):
        lagged = add(lagged, mul(deltas[index], deltas[index + lag]))
    return div(lagged, energy)


def _roughness(values: list[float], noise: float) -> float:
    count = len(values)
    if count < 2:
        return f32(0)
    total = f32(0)
    for index in range(count - 1):
        total = add(total, abs_f32(sub(values[index + 1], values[index])))
    return div(div(total, f32(count - 1)), sqrt_f32(noise))


def _xcorr(left: list[float], right: list[float]) -> float:
    count = len(left)
    if count == 0 or count != len(right):
        return f32(0)
    mean_left, _ = _mean_var(left)
    mean_right, _ = _mean_var(right)
    numerator = f32(0)
    energy_left = f32(0)
    energy_right = f32(0)
    for index in range(count):
        a = sub(left[index], mean_left)
        b = sub(right[index], mean_right)
        numerator = add(numerator, mul(a, b))
        energy_left = add(energy_left, mul(a, a))
        energy_right = add(energy_right, mul(b, b))
    denom = mul(energy_left, energy_right)
    if not denom > f32(1e-12):
        return f32(0)
    return div(numerator, sqrt_f32(denom))


def _goertzel_power(values: list[float]) -> float:
    mean, _variance = _mean_var(values)
    centered = [sub(value, mean) for value in values]
    total = f32(0)
    two = f32(2)
    for bits in GOERTZEL_COS:
        coeff = mul(two, _cos(bits))
        s1 = f32(0)
        s2 = f32(0)
        for sample in centered:
            prod = mul(coeff, s1)
            s0 = sub(add(sample, prod), s2)
            s2 = s1
            s1 = s0
        power = sub(add(mul(s1, s1), mul(s2, s2)), mul(coeff, mul(s1, s2)))
        if power < 0.0:
            power = f32(0)
        total = add(total, power)
    return total


def _column(rows: list[list[float]], band: int) -> list[float]:
    return [row[band] for row in rows]


def compute_features(short_rows: list[list[float]], long_rows: list[list[float]],
                     noise: list[float], noise_ready: list[bool],
                     noise_init: list[list[float]]) -> list[float] | None:
    """None while the noise reference is still being initialised: it is the
    median of the first NOISE_INIT_N short-window variances (C: same rule)."""
    variances = []
    for band in range(BANDS):
        _mean, variance = _mean_var(_column(short_rows, band))
        variances.append(variance)
    if len(noise_init) < NOISE_INIT_N:
        noise_init.append(list(variances))
        if len(noise_init) < NOISE_INIT_N:
            return None
        for band in range(BANDS):
            median = sorted(row[band] for row in noise_init)[NOISE_INIT_N // 2]
            noise[band] = median if median > f32(1e-6) else f32(1e-6)
            noise_ready[band] = True
    else:
        for band in range(BANDS):
            if not variances[band] > mul(f32(2), noise[band]):
                noise[band] = add(mul(f32(0.95), noise[band]), mul(f32(0.05), variances[band]))
    out = []
    for band in range(BANDS):
        out.append(div(variances[band], noise[band]))
    for band in range(BANDS):
        out.append(_roughness(_column(short_rows, band), noise[band]))
    for band in range(BANDS):
        out.append(_autocorr(_column(short_rows, band), 1))
    for band in range(BANDS):
        out.append(_autocorr(_column(short_rows, band), 5))
    scale = f32(LONG_N)
    for band in range(BANDS):
        power = _goertzel_power(_column(long_rows, band))
        out.append(div(power, mul(scale, noise[band])))
    out.append(_xcorr(_column(short_rows, 0), _column(short_rows, 7)))
    out.append(_xcorr(_column(short_rows, 3), _column(short_rows, 4)))
    return out


class Extractor:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.short: list[tuple[int, list[float]]] = []
        self.short_open: list | None = None
        self.long: list[list[float]] = []
        self.long_open: list | None = None
        self.noise = [f32(0)] * BANDS
        self.noise_ready = [False] * BANDS
        self.noise_init: list[list[float]] = []
        self.features: list[float] | None = None
        self.feature_ts = 0
        self.last_emit_ts: int | None = None
        self.last_ts: int | None = None

    def push(self, timestamp_us: int, amplitudes: list[float]) -> list[float] | None:
        if len(amplitudes) != TONES:
            return None
        amplitudes = [f32(value) for value in amplitudes]
        if any(not math.isfinite(value) for value in amplitudes):
            return None
        if self.last_ts is not None and timestamp_us <= self.last_ts:
            return None
        self.last_ts = timestamp_us
        bands = band_means(amplitudes)
        self._accumulate_short(timestamp_us // SHORT_US, bands)
        self._accumulate_long(timestamp_us // LONG_US, bands)
        return self._emit(timestamp_us)

    def _accumulate_short(self, key: int, bands: list[float]) -> None:
        acc = self.short_open
        if acc is None:
            self.short_open = [key, list(bands), 1]
            return
        if acc[0] == key:
            for band in range(BANDS):
                acc[1][band] = add(acc[1][band], bands[band])
            acc[2] += 1
            return
        self._commit_short()
        self.short_open = [key, list(bands), 1]

    def _commit_short(self) -> None:
        acc = self.short_open
        if acc is None:
            return
        count = f32(acc[2])
        self.short.append((acc[0], [div(acc[1][band], count) for band in range(BANDS)]))
        if len(self.short) > SHORT_CAP:
            del self.short[0]

    def _accumulate_long(self, key: int, bands: list[float]) -> None:
        acc = self.long_open
        if acc is None:
            self.long_open = [key, list(bands), 1]
            return
        if acc[0] == key:
            for band in range(BANDS):
                acc[1][band] = add(acc[1][band], bands[band])
            acc[2] += 1
            return
        if key != acc[0] + 1:
            self.long = []
            self.long_open = [key, list(bands), 1]
            return
        self._commit_long()
        self.long_open = [key, list(bands), 1]

    def _commit_long(self) -> None:
        acc = self.long_open
        if acc is None:
            return
        count = f32(acc[2])
        self.long.append([div(acc[1][band], count) for band in range(BANDS)])
        if len(self.long) > LONG_N:
            del self.long[0]

    def _open_means(self, acc: list) -> list[float]:
        count = f32(acc[2])
        return [div(acc[1][band], count) for band in range(BANDS)]

    def _emit(self, timestamp_us: int) -> list[float] | None:
        if self.last_emit_ts is not None and timestamp_us < self.last_emit_ts + EMIT_US:
            return None
        rows = list(self.long)
        if self.long_open is not None:
            rows.append(self._open_means(self.long_open))
        if len(rows) < LONG_N:
            return None
        long_rows = rows[-LONG_N:]
        now_key = timestamp_us // SHORT_US
        min_key = now_key - 39
        short_rows = []
        keys = []
        for key, means in self.short:
            if min_key <= key <= now_key:
                short_rows.append(means)
                keys.append(key)
        if self.short_open is not None and min_key <= self.short_open[0] <= now_key:
            short_rows.append(self._open_means(self.short_open))
            keys.append(self.short_open[0])
        if len(short_rows) < 8 or keys[-1] - keys[0] < SHORT_SPAN:
            return None
        features = compute_features(short_rows, long_rows, self.noise, self.noise_ready, self.noise_init)
        self.last_emit_ts = timestamp_us
        if features is None:
            return None
        self.features = features
        self.feature_ts = timestamp_us
        self.last_emit_ts = timestamp_us
        return features


def _self_check() -> None:
    assert len(TONE_INDEX) == TONES
    assert sum(BAND_SIZE) == TONES
    assert len(GOERTZEL_COS) == 58
    extractor = Extractor()
    timestamp = 1_000_000
    emitted = None
    for index in range(560):
        amplitudes = [1.0 + 0.01 * (index % 5)] * TONES
        emitted = extractor.push(timestamp, amplitudes) or emitted
        timestamp += 125_000
    assert emitted is not None and len(emitted) == FEATURES
    assert all(math.isfinite(value) for value in emitted)


if __name__ == "__main__":
    _self_check()
    print("tiny_ai_features ok", FEATURES)
