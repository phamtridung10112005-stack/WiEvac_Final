#include "tiny_ai_features.h"

#include <string.h>

/* GCC rejects the STDC pragma; CMakeLists passes -ffp-contract=off instead. */
#if defined(__clang__)
#pragma STDC FP_CONTRACT OFF
#endif

_Static_assert(TINY_AI_FEATURES == 42, "dynamic feature count");
_Static_assert(TINY_AI_TONES == 52, "HT20 tone count");

static const uint8_t k_tone[TINY_AI_TONES] = {
    6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26,
    27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47,
    48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58
};
static const uint8_t k_band_size[TINY_AI_BANDS] = {7, 7, 6, 6, 6, 6, 7, 7};
/* cos(2*pi*k/240), k=3..60. Same bits as GOERTZEL_COS in tiny_ai_features.py. */
static const uint32_t k_goertzel_cos[58] = {
    0x3f7f35f9u, 0x3f7e98fdu, 0x3f7dcf55u, 0x3f7cd925u, 0x3f7bb698u, 0x3f7a67e2u,
    0x3f78ed3cu, 0x3f7746eau, 0x3f757536u, 0x3f737871u, 0x3f7150f4u, 0x3f6eff20u,
    0x3f6c835eu, 0x3f69de1du, 0x3f670fd4u, 0x3f641901u, 0x3f60fa29u, 0x3f5db3d7u,
    0x3f5a46a0u, 0x3f56b31du, 0x3f52f9efu, 0x3f4f1bbdu, 0x3f4b1934u, 0x3f46f30au,
    0x3f42a9f7u, 0x3f3e3ebdu, 0x3f39b223u, 0x3f3504f3u, 0x3f303801u, 0x3f2b4c25u,
    0x3f26423au, 0x3f211b24u, 0x3f1bd7cau, 0x3f167918u, 0x3f110000u, 0x3f0b6d77u,
    0x3f05c277u, 0x3f000000u, 0x3ef44e27u, 0x3ee87171u, 0x3edc6bf3u, 0x3ed03fc9u,
    0x3ec3ef15u, 0x3eb77c01u, 0x3eaae8bcu, 0x3e9e377au, 0x3e916a76u, 0x3e8483eeu,
    0x3e6f0c4du, 0x3e54e6cdu, 0x3e3a9bf3u, 0x3e20305bu, 0x3e05a8a8u, 0x3dd61305u,
    0x3da0af2au, 0x3d565e3au, 0x3cd6710au, 0x25a34c4cu
};

static float fbits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static float addf(float left, float right) { return left + right; }
static float subf(float left, float right) { return left - right; }
static float mulf(float left, float right) { return left * right; }
static float divf(float left, float right) { return left / right; }

static float absf32(float value)
{
    return value < 0.0f ? -value : value;
}

static float sqrt_f32(float value)
{
    union { float f; uint32_t u; } guess;
    int step;
    if (!(value > 0.0f)) {
        return 0.0f;
    }
    guess.f = value;
    guess.u = (guess.u >> 1) + 0x1fc00000u;
    for (step = 0; step < 4; ++step) {
        float quot = divf(value, guess.f);
        float sum = addf(guess.f, quot);
        guess.f = mulf(0.5f, sum);
    }
    return guess.f;
}

static bool finite_f(float value)
{
    return value == value && value < 1.0e30f && value > -1.0e30f;
}

static void mean_var(const float *values, uint16_t count, float *mean_out, float *var_out)
{
    float total = 0.0f;
    float mean;
    float energy = 0.0f;
    uint16_t index;
    for (index = 0; index < count; ++index) {
        total = addf(total, values[index]);
    }
    mean = divf(total, (float)count);
    for (index = 0; index < count; ++index) {
        float delta = subf(values[index], mean);
        energy = addf(energy, mulf(delta, delta));
    }
    *mean_out = mean;
    *var_out = divf(energy, (float)count);
}

static float autocorr(const float *values, uint16_t count, uint16_t lag)
{
    float mean;
    float ignored;
    float deltas[64];
    float energy = 0.0f;
    float lagged = 0.0f;
    uint16_t index;
    if (count <= lag || count > 64U) {
        return 0.0f;
    }
    mean_var(values, count, &mean, &ignored);
    for (index = 0; index < count; ++index) {
        deltas[index] = subf(values[index], mean);
        energy = addf(energy, mulf(deltas[index], deltas[index]));
    }
    if (!(energy > fbits(0x2b8cbcccu))) {
        return 0.0f;
    }
    for (index = 0; index + lag < count; ++index) {
        lagged = addf(lagged, mulf(deltas[index], deltas[index + lag]));
    }
    return divf(lagged, energy);
}

static float roughness(const float *values, uint16_t count, float noise)
{
    float total = 0.0f;
    uint16_t index;
    if (count < 2U) {
        return 0.0f;
    }
    for (index = 0; index + 1U < count; ++index) {
        total = addf(total, absf32(subf(values[index + 1U], values[index])));
    }
    return divf(divf(total, (float)(count - 1U)), sqrt_f32(noise));
}

static float xcorr(const float *left, const float *right, uint16_t count)
{
    float mean_left;
    float mean_right;
    float ignored;
    float numerator = 0.0f;
    float energy_left = 0.0f;
    float energy_right = 0.0f;
    float denom;
    uint16_t index;
    if (count == 0U || count > 64U) {
        return 0.0f;
    }
    mean_var(left, count, &mean_left, &ignored);
    mean_var(right, count, &mean_right, &ignored);
    for (index = 0; index < count; ++index) {
        float a = subf(left[index], mean_left);
        float b = subf(right[index], mean_right);
        numerator = addf(numerator, mulf(a, b));
        energy_left = addf(energy_left, mulf(a, a));
        energy_right = addf(energy_right, mulf(b, b));
    }
    denom = mulf(energy_left, energy_right);
    if (!(denom > fbits(0x2b8cbcccu))) {
        return 0.0f;
    }
    return divf(numerator, sqrt_f32(denom));
}

static float goertzel_power(const float *values)
{
    /* ponytail: scratch is global; one RX worker calls this */
    static float centered[TINY_AI_LONG_N];
    float mean;
    float ignored;
    float total = 0.0f;
    uint16_t index;
    uint16_t bin;
    mean_var(values, (uint16_t)TINY_AI_LONG_N, &mean, &ignored);
    for (index = 0; index < TINY_AI_LONG_N; ++index) {
        centered[index] = subf(values[index], mean);
    }
    for (bin = 0; bin < 58U; ++bin) {
        float coeff = mulf(2.0f, fbits(k_goertzel_cos[bin]));
        float s1 = 0.0f;
        float s2 = 0.0f;
        float power;
        for (index = 0; index < TINY_AI_LONG_N; ++index) {
            float prod = mulf(coeff, s1);
            float s0 = subf(addf(centered[index], prod), s2);
            s2 = s1;
            s1 = s0;
        }
        power = subf(addf(mulf(s1, s1), mulf(s2, s2)), mulf(coeff, mulf(s1, s2)));
        if (power < 0.0f) {
            power = 0.0f;
        }
        total = addf(total, power);
    }
    return total;
}

static void band_means(const float amplitudes[TINY_AI_TONES], float out[TINY_AI_BANDS])
{
    uint16_t offset = 0U;
    uint16_t band;
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        float total = 0.0f;
        uint8_t index;
        for (index = 0; index < k_band_size[band]; ++index) {
            total = addf(total, amplitudes[offset + index]);
        }
        out[band] = divf(total, (float)k_band_size[band]);
        offset = (uint16_t)(offset + k_band_size[band]);
    }
}

static void commit_short(tiny_ai_extractor_t *extractor)
{
    tiny_ai_bucket_t bucket;
    uint16_t band;
    if (!extractor->short_acc.open || extractor->short_acc.count == 0U) {
        return;
    }
    bucket.key = extractor->short_acc.key;
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        bucket.mean[band] = divf(extractor->short_acc.sum[band], (float)extractor->short_acc.count);
    }
    if (extractor->short_n == TINY_AI_SHORT_CAP) {
        memmove(&extractor->short_q[0], &extractor->short_q[1],
                (size_t)(TINY_AI_SHORT_CAP - 1U) * sizeof(extractor->short_q[0]));
        extractor->short_n = (uint16_t)(TINY_AI_SHORT_CAP - 1U);
    }
    extractor->short_q[extractor->short_n++] = bucket;
}

static void commit_long(tiny_ai_extractor_t *extractor)
{
    uint16_t band;
    if (!extractor->long_acc.open || extractor->long_acc.count == 0U) {
        return;
    }
    if (extractor->long_n == TINY_AI_LONG_N) {
        memmove(extractor->long_q[0], extractor->long_q[1],
                (size_t)(TINY_AI_LONG_N - 1U) * TINY_AI_BANDS * sizeof(float));
        extractor->long_n = (uint16_t)(TINY_AI_LONG_N - 1U);
    }
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        extractor->long_q[extractor->long_n][band] =
            divf(extractor->long_acc.sum[band], (float)extractor->long_acc.count);
    }
    ++extractor->long_n;
}

static void open_acc(tiny_ai_acc_t *acc, uint64_t key, const float bands[TINY_AI_BANDS])
{
    uint16_t band;
    acc->open = true;
    acc->key = key;
    acc->count = 1U;
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        acc->sum[band] = bands[band];
    }
}

static void add_acc(tiny_ai_acc_t *acc, const float bands[TINY_AI_BANDS])
{
    uint16_t band;
    /* ponytail: one bucket cannot see more than 20 ms of a 1 kHz burst */
    if (acc->count == UINT16_MAX) {
        return;
    }
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        acc->sum[band] = addf(acc->sum[band], bands[band]);
    }
    ++acc->count;
}

static float median_of_init(const tiny_ai_extractor_t *extractor, uint16_t band)
{
    float sorted[TINY_AI_NOISE_INIT_N];
    uint16_t i;
    uint16_t j;
    for (i = 0; i < TINY_AI_NOISE_INIT_N; ++i) {
        const float value = extractor->noise_init[i][band];
        j = i;
        while (j > 0U && sorted[j - 1U] > value) {
            sorted[j] = sorted[j - 1U];
            --j;
        }
        sorted[j] = value;
    }
    return sorted[TINY_AI_NOISE_INIT_N / 2U];
}

static bool emit_features(tiny_ai_extractor_t *extractor, uint64_t timestamp_us, float out[TINY_AI_FEATURES])
{
    static float long_rows[TINY_AI_LONG_N][TINY_AI_BANDS];
    static float short_rows[64][TINY_AI_BANDS];
    static uint64_t short_keys[64];
    static float column[TINY_AI_LONG_N];
    static float short_column[64];
    float variances[TINY_AI_BANDS];
    uint16_t total;
    uint16_t skip;
    uint16_t count = 0U;
    uint16_t short_n = 0U;
    uint16_t band;
    uint16_t row;
    uint64_t now_key;
    uint64_t min_key;
    const float eps = fbits(0x358637bdu);
    if (extractor->emit_started && timestamp_us < extractor->last_emit_ts + TINY_AI_EMIT_STEP_US) {
        return false;
    }
    total = (uint16_t)(extractor->long_n + (extractor->long_acc.open ? 1U : 0U));
    if (total < TINY_AI_LONG_N) {
        return false;
    }
    skip = (uint16_t)(total - TINY_AI_LONG_N);
    for (row = skip; row < extractor->long_n; ++row) {
        memcpy(long_rows[count], extractor->long_q[row], sizeof(long_rows[0]));
        ++count;
    }
    if (extractor->long_acc.open) {
        for (band = 0; band < TINY_AI_BANDS; ++band) {
            long_rows[count][band] = divf(extractor->long_acc.sum[band], (float)extractor->long_acc.count);
        }
        ++count;
    }
    if (count != TINY_AI_LONG_N) {
        return false;
    }
    now_key = timestamp_us / TINY_AI_SHORT_BUCKET_US;
    min_key = now_key - 39ULL;
    for (row = 0; row < extractor->short_n; ++row) {
        if (extractor->short_q[row].key < min_key || extractor->short_q[row].key > now_key) {
            continue;
        }
        if (short_n >= 64U) {
            break;
        }
        memcpy(short_rows[short_n], extractor->short_q[row].mean, sizeof(short_rows[0]));
        short_keys[short_n] = extractor->short_q[row].key;
        ++short_n;
    }
    if (extractor->short_acc.open && extractor->short_acc.key >= min_key &&
        extractor->short_acc.key <= now_key && short_n < 64U) {
        for (band = 0; band < TINY_AI_BANDS; ++band) {
            short_rows[short_n][band] =
                divf(extractor->short_acc.sum[band], (float)extractor->short_acc.count);
        }
        short_keys[short_n] = extractor->short_acc.key;
        ++short_n;
    }
    if (short_n < 8U || short_keys[short_n - 1U] - short_keys[0] < 38ULL) {
        return false;
    }
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        float mean;
        for (row = 0; row < short_n; ++row) {
            short_column[row] = short_rows[row][band];
        }
        mean_var(short_column, short_n, &mean, &variances[band]);
    }
    if (extractor->noise_init_n < TINY_AI_NOISE_INIT_N) {
        memcpy(extractor->noise_init[extractor->noise_init_n], variances, sizeof(variances));
        ++extractor->noise_init_n;
        extractor->last_emit_ts = timestamp_us;
        extractor->emit_started = true;
        if (extractor->noise_init_n < TINY_AI_NOISE_INIT_N) {
            return false;
        }
        for (band = 0; band < TINY_AI_BANDS; ++band) {
            const float median = median_of_init(extractor, band);
            extractor->noise[band] = median > eps ? median : eps;
            extractor->noise_ready[band] = true;
        }
    } else {
        for (band = 0; band < TINY_AI_BANDS; ++band) {
            if (!(variances[band] > mulf(2.0f, extractor->noise[band]))) {
                extractor->noise[band] = addf(mulf(fbits(0x3f733333u), extractor->noise[band]),
                                              mulf(fbits(0x3d4ccccdu), variances[band]));
            }
        }
    }
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        out[band] = divf(variances[band], extractor->noise[band]);
    }
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        for (row = 0; row < short_n; ++row) {
            short_column[row] = short_rows[row][band];
        }
        out[8U + band] = roughness(short_column, short_n, extractor->noise[band]);
    }
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        for (row = 0; row < short_n; ++row) {
            short_column[row] = short_rows[row][band];
        }
        out[16U + band] = autocorr(short_column, short_n, 1U);
    }
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        for (row = 0; row < short_n; ++row) {
            short_column[row] = short_rows[row][band];
        }
        out[24U + band] = autocorr(short_column, short_n, 5U);
    }
    for (band = 0; band < TINY_AI_BANDS; ++band) {
        for (row = 0; row < TINY_AI_LONG_N; ++row) {
            column[row] = long_rows[row][band];
        }
        out[32U + band] = divf(goertzel_power(column), mulf((float)TINY_AI_LONG_N, extractor->noise[band]));
    }
    for (row = 0; row < short_n; ++row) {
        short_column[row] = short_rows[row][0];
        column[row] = short_rows[row][7];
    }
    out[40] = xcorr(short_column, column, short_n);
    for (row = 0; row < short_n; ++row) {
        short_column[row] = short_rows[row][3];
        column[row] = short_rows[row][4];
    }
    out[41] = xcorr(short_column, column, short_n);
    memcpy(extractor->features, out, sizeof(extractor->features));
    extractor->feature_ts = timestamp_us;
    extractor->last_emit_ts = timestamp_us;
    extractor->have_features = true;
    extractor->emit_started = true;
    return true;
}

void tiny_ai_extractor_init(tiny_ai_extractor_t *extractor)
{
    if (extractor == NULL) {
        return;
    }
    memset(extractor, 0, sizeof(*extractor));
}

bool tiny_ai_amplitudes_from_csi(const uint8_t *csi, uint16_t csi_length, float out[TINY_AI_TONES])
{
    uint16_t index;
    if (csi == NULL || out == NULL || csi_length < 118U || (csi_length & 1U) != 0U) {
        return false;
    }
    for (index = 0; index < TINY_AI_TONES; ++index) {
        const uint16_t tone = k_tone[index];
        const float real = (float)(int8_t)csi[tone * 2U + 1U];
        const float imag = (float)(int8_t)csi[tone * 2U];
        float power = addf(mulf(real, real), mulf(imag, imag));
        out[index] = sqrt_f32(power);
    }
    return true;
}

bool tiny_ai_extractor_push(tiny_ai_extractor_t *extractor, uint64_t timestamp_us,
                            const float amplitudes[TINY_AI_TONES], float out[TINY_AI_FEATURES])
{
    float bands[TINY_AI_BANDS];
    float local[TINY_AI_FEATURES];
    float clean[TINY_AI_TONES];
    uint16_t index;
    uint64_t short_key;
    uint64_t long_key;
    if (extractor == NULL || amplitudes == NULL) {
        return false;
    }
    for (index = 0; index < TINY_AI_TONES; ++index) {
        if (!finite_f(amplitudes[index])) {
            return false;
        }
        clean[index] = amplitudes[index];
    }
    if (extractor->have_ts && timestamp_us <= extractor->last_ts) {
        return false;
    }
    extractor->last_ts = timestamp_us;
    extractor->have_ts = true;
    band_means(clean, bands);
    short_key = timestamp_us / TINY_AI_SHORT_BUCKET_US;
    long_key = timestamp_us / TINY_AI_LONG_BUCKET_US;
    if (!extractor->short_acc.open) {
        open_acc(&extractor->short_acc, short_key, bands);
    } else if (extractor->short_acc.key == short_key) {
        add_acc(&extractor->short_acc, bands);
    } else {
        commit_short(extractor);
        open_acc(&extractor->short_acc, short_key, bands);
    }
    if (!extractor->long_acc.open) {
        open_acc(&extractor->long_acc, long_key, bands);
    } else if (extractor->long_acc.key == long_key) {
        add_acc(&extractor->long_acc, bands);
    } else if (long_key != extractor->long_acc.key + 1ULL) {
        extractor->long_n = 0U;
        open_acc(&extractor->long_acc, long_key, bands);
    } else {
        commit_long(extractor);
        open_acc(&extractor->long_acc, long_key, bands);
    }
    if (!emit_features(extractor, timestamp_us, local)) {
        return false;
    }
    if (out != NULL) {
        memcpy(out, local, sizeof(local));
    }
    return true;
}

bool tiny_ai_extractor_latest(const tiny_ai_extractor_t *extractor, float out[TINY_AI_FEATURES],
                              uint64_t *timestamp_us)
{
    if (extractor == NULL || out == NULL || !extractor->have_features) {
        return false;
    }
    memcpy(out, extractor->features, sizeof(extractor->features));
    if (timestamp_us != NULL) {
        *timestamp_us = extractor->feature_ts;
    }
    return true;
}

#if defined(TINY_AI_HOST_MAIN)
#include <stdio.h>
#include <stdlib.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

int main(void)
{
    static tiny_ai_extractor_t extractor;
    uint32_t count = 0U;
    uint32_t emitted = 0U;
    uint32_t index;
    float *stored;
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    if (fread(&count, sizeof(count), 1, stdin) != 1 || count > 100000U) {
        return 2;
    }
    stored = (float *)calloc((size_t)count, TINY_AI_FEATURES * sizeof(float));
    if (stored == NULL && count != 0U) {
        return 2;
    }
    tiny_ai_extractor_init(&extractor);
    for (index = 0; index < count; ++index) {
        uint64_t timestamp = 0U;
        float amplitudes[TINY_AI_TONES];
        float features[TINY_AI_FEATURES];
        if (fread(&timestamp, sizeof(timestamp), 1, stdin) != 1 ||
            fread(amplitudes, sizeof(float), TINY_AI_TONES, stdin) != TINY_AI_TONES) {
            free(stored);
            return 2;
        }
        if (tiny_ai_extractor_push(&extractor, timestamp, amplitudes, features)) {
            memcpy(stored + (size_t)emitted * TINY_AI_FEATURES, features, sizeof(features));
            ++emitted;
        }
    }
    fwrite(&emitted, sizeof(emitted), 1, stdout);
    if (emitted > 0U) {
        fwrite(stored, sizeof(float), (size_t)emitted * TINY_AI_FEATURES, stdout);
    }
    free(stored);
    return 0;
}
#endif
