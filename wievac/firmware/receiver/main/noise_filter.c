#include "noise_filter.h"

#include <math.h>
#include <string.h>

#define NOISE_FILTER_MAX_CSI_BYTES 128U

static void sort_values(float *values, uint16_t count)
{
    for (uint16_t i = 1U; i < count; ++i) {
        const float value = values[i];
        uint16_t j = i;
        while (j > 0U && values[j - 1U] > value) {
            values[j] = values[j - 1U];
            --j;
        }
        values[j] = value;
    }
}

static float median_values(const float *values, uint16_t count)
{
    if (values == NULL || count == 0U || count > NOISE_FILTER_MAX_CSI_BYTES / 2U) {
        return 0.0f;
    }
    float copy[NOISE_FILTER_MAX_CSI_BYTES / 2U];
    memcpy(copy, values, (size_t)count * sizeof(copy[0]));
    sort_values(copy, count);
    if ((count & 1U) != 0U) {
        return copy[count / 2U];
    }
    return 0.5f * (copy[count / 2U - 1U] + copy[count / 2U]);
}

/* Packet-rate Hampel on the scalar amplitude. One RX owns one link. */
static float s_hampel_buffer[NOISE_FILTER_HAMPEL_WINDOW];
static uint8_t s_hampel_count;
static uint8_t s_hampel_index;

static float noise_filter_hampel_update(float value)
{
    float window[NOISE_FILTER_HAMPEL_WINDOW];
    float absdev[NOISE_FILTER_HAMPEL_WINDOW];
    uint16_t n;

    if (!isfinite((double)value)) {
        return value;
    }
    /* Store the raw sample. Replacing the buffer with the filtered value
     * freezes occupancy/rebase steps: a new level never enters the window. */
    s_hampel_buffer[s_hampel_index] = value;
    s_hampel_index = (uint8_t)((s_hampel_index + 1U) % NOISE_FILTER_HAMPEL_WINDOW);
    if (s_hampel_count < NOISE_FILTER_HAMPEL_WINDOW) {
        ++s_hampel_count;
    }
    n = s_hampel_count;
    if (n < 3U) {
        return value;
    }
    memcpy(window, s_hampel_buffer, (size_t)n * sizeof(window[0]));
    const float median = median_values(window, n);
    for (uint16_t i = 0U; i < n; ++i) {
        absdev[i] = fabsf(s_hampel_buffer[i] - median);
    }
    const float mad = median_values(absdev, n);
    const float scaled = NOISE_FILTER_HAMPEL_MAD_SCALE * mad;
    /* Near-zero MAD is a quiet hall, not a license to reject every change. */
    if (scaled <= NOISE_FILTER_HAMPEL_MAD_FLOOR) {
        return value;
    }
    if (fabsf(value - median) > NOISE_FILTER_HAMPEL_THRESHOLD * scaled) {
        return median;
    }
    return value;
}

void noise_filter_reset(void)
{
    memset(s_hampel_buffer, 0, sizeof(s_hampel_buffer));
    s_hampel_count = 0U;
    s_hampel_index = 0U;
}

bool noise_filter_validate(const noise_filter_input_t *input)
{
    return input != NULL && input->csi != NULL &&
           input->csi_length >= 2U &&
           input->csi_length <= NOISE_FILTER_MAX_CSI_BYTES &&
           (input->csi_length & 1U) == 0U && !input->first_word_invalid;
}

bool noise_filter_process(const noise_filter_input_t *input,
                          noise_filter_output_t *output)
{
    if (output == NULL) {
        return false;
    }
    memset(output, 0, sizeof(*output));
    if (!noise_filter_validate(input)) {
        return false;
    }

    const uint16_t pairs = (uint16_t)(input->csi_length / 2U);
    float magnitudes[NOISE_FILTER_MAX_CSI_BYTES / 2U];
    float total = 0.0f;
    for (uint16_t index = 0U; index < pairs; ++index) {
        const int8_t imaginary = (int8_t)input->csi[index * 2U];
        const int8_t real = (int8_t)input->csi[index * 2U + 1U];
        const float magnitude = sqrtf((float)real * (float)real +
                                      (float)imaginary * (float)imaginary);
        if (!isfinite((double)magnitude)) {
            return false;
        }
        magnitudes[index] = magnitude;
        total += magnitude;
    }
    if (pairs == 0U || !isfinite((double)total) || total <= 0.0f) {
        return false;
    }

    /* Fixed spaced HT20 bins: avoid DC (0/32), pilots (21/43), guards. */
    static const uint8_t k_selected[NOISE_FILTER_SELECTED_COUNT] = {
        12U, 14U, 16U, 18U, 20U, 24U, 28U, 36U, 40U, 44U, 48U, 50U
    };
    float selected[NOISE_FILTER_SELECTED_COUNT];
    uint16_t selected_count = 0U;
    for (uint16_t i = 0U; i < NOISE_FILTER_SELECTED_COUNT; ++i) {
        const uint16_t bin = k_selected[i];
        if (bin >= pairs) {
            continue;
        }
        selected[selected_count++] = magnitudes[bin];
    }
    if (selected_count < NOISE_FILTER_SELECTED_MIN) {
        selected_count = 0U;
        for (uint16_t index = 0U; index < pairs; ++index) {
            if (index < 11U || index > 52U || index == 32U ||
                index == 21U || index == 43U) {
                continue;
            }
            if (selected_count >= NOISE_FILTER_SELECTED_COUNT) {
                break;
            }
            selected[selected_count++] = magnitudes[index];
        }
    }
    const float *amp_src = selected_count >= NOISE_FILTER_SELECTED_MIN ? selected : magnitudes;
    const uint16_t amp_count = selected_count >= NOISE_FILTER_SELECTED_MIN ? selected_count : pairs;
    const float center = median_values(amp_src, amp_count);
    /* Use the robust center as the filtered amplitude so one impulse cannot
     * poison the feature that feeds baseline learning and scoring. */
    float deviations[NOISE_FILTER_MAX_CSI_BYTES / 2U];
    for (uint16_t index = 0U; index < amp_count; ++index) {
        deviations[index] = fabsf(amp_src[index] - center);
    }
    output->robust_spread = median_values(deviations, amp_count);
    output->amplitude = noise_filter_hampel_update(center);
    output->sample_pairs = amp_count;
    return isfinite((double)output->amplitude) &&
           isfinite((double)output->robust_spread);
}

#define NOISE_FILTER_PI 3.14159265358979323846f
#define NOISE_FILTER_DOPPLER_EPS 1.0e-12f

static void fft64(float *re, float *im)
{
    uint16_t j = 0U;
    for (uint16_t i = 1U; i < NOISE_FILTER_DOPPLER_FFT_N; ++i) {
        uint16_t bit = NOISE_FILTER_DOPPLER_FFT_N >> 1U;
        while (j >= bit) {
            j = (uint16_t)(j - bit);
            bit = (uint16_t)(bit >> 1U);
        }
        j = (uint16_t)(j + bit);
        if (i < j) {
            const float tr = re[i];
            const float ti = im[i];
            re[i] = re[j];
            im[i] = im[j];
            re[j] = tr;
            im[j] = ti;
        }
    }
    for (uint16_t len = 2U; len <= NOISE_FILTER_DOPPLER_FFT_N; len = (uint16_t)(len << 1U)) {
        const float ang = -2.0f * NOISE_FILTER_PI / (float)len;
        const float wlen_re = cosf(ang);
        const float wlen_im = sinf(ang);
        for (uint16_t i = 0U; i < NOISE_FILTER_DOPPLER_FFT_N; i = (uint16_t)(i + len)) {
            float wr = 1.0f;
            float wi = 0.0f;
            const uint16_t half = (uint16_t)(len >> 1U);
            for (uint16_t k = 0U; k < half; ++k) {
                const uint16_t u = (uint16_t)(i + k);
                const uint16_t v = (uint16_t)(u + half);
                const float tre = wr * re[v] - wi * im[v];
                const float tim = wr * im[v] + wi * re[v];
                re[v] = re[u] - tre;
                im[v] = im[u] - tim;
                re[u] += tre;
                im[u] += tim;
                const float nwr = wr * wlen_re - wi * wlen_im;
                wi = wr * wlen_im + wi * wlen_re;
                wr = nwr;
            }
        }
    }
}

bool noise_filter_doppler_ratio(const float *amplitudes,
                                const uint64_t *timestamps_us,
                                uint16_t count,
                                noise_filter_doppler_t *output)
{
    if (output == NULL) {
        return false;
    }
    memset(output, 0, sizeof(*output));
    if (amplitudes == NULL || timestamps_us == NULL ||
        count < NOISE_FILTER_DOPPLER_MIN_SAMPLES ||
        count > NOISE_FILTER_DOPPLER_FFT_N) {
        return false;
    }

    float intervals[NOISE_FILTER_DOPPLER_FFT_N];
    const uint16_t interval_count = (uint16_t)(count - 1U);
    for (uint16_t i = 0U; i < interval_count; ++i) {
        if (timestamps_us[i + 1U] <= timestamps_us[i]) {
            return false;
        }
        const float dt = (float)(timestamps_us[i + 1U] - timestamps_us[i]);
        if (!isfinite((double)dt) || dt <= 0.0f) {
            return false;
        }
        intervals[i] = dt;
    }
    const float median_us = median_values(intervals, interval_count);
    if (!isfinite((double)median_us) || median_us < 1.0f) {
        return false;
    }
    float mean_us = 0.0f;
    for (uint16_t i = 0U; i < interval_count; ++i) {
        mean_us += intervals[i];
    }
    mean_us /= (float)interval_count;
    float var_us = 0.0f;
    for (uint16_t i = 0U; i < interval_count; ++i) {
        const float d = intervals[i] - mean_us;
        var_us += d * d;
    }
    var_us /= (float)interval_count;
    const float std_us = sqrtf(fmaxf(var_us, 0.0f));
    if (!isfinite((double)std_us) ||
        std_us / median_us > NOISE_FILTER_DOPPLER_MAX_RELATIVE_JITTER) {
        return false;
    }

    const float fs_hz = 1000000.0f / median_us;
    /* Nyquist must cover the walking band at 2.4 GHz; do not invent Hz. */
    if (!isfinite((double)fs_hz) ||
        fs_hz < (2.0f * NOISE_FILTER_DOPPLER_F_HI_HZ)) {
        return false;
    }

    float mean_amp = 0.0f;
    for (uint16_t i = 0U; i < count; ++i) {
        if (!isfinite((double)amplitudes[i])) {
            return false;
        }
        mean_amp += amplitudes[i];
    }
    mean_amp /= (float)count;

    float re[NOISE_FILTER_DOPPLER_FFT_N];
    float im[NOISE_FILTER_DOPPLER_FFT_N];
    memset(re, 0, sizeof(re));
    memset(im, 0, sizeof(im));
    const float denom = count > 1U ? (float)(count - 1U) : 1.0f;
    for (uint16_t i = 0U; i < count; ++i) {
        const float hann = 0.5f * (1.0f - cosf(2.0f * NOISE_FILTER_PI * (float)i / denom));
        re[i] = (amplitudes[i] - mean_amp) * hann;
    }
    fft64(re, im);

    float energy_dc = 0.0f;
    float energy_motion = 0.0f;
    float energy_high = 0.0f;
    const uint16_t nyquist = NOISE_FILTER_DOPPLER_FFT_N / 2U;
    for (uint16_t k = 0U; k <= nyquist; ++k) {
        const float freq = (float)k * fs_hz / (float)NOISE_FILTER_DOPPLER_FFT_N;
        const float power = re[k] * re[k] + im[k] * im[k];
        if (!isfinite((double)power)) {
            return false;
        }
        /* k=0 is residual DC after demeaning. Bins from F_LO..F_HI include
         * gait (~1-2 Hz). Do not park the first positive bin in DC. */
        if (k == 0U || freq < NOISE_FILTER_DOPPLER_F_LO_HZ) {
            energy_dc += power;
        } else if (freq <= NOISE_FILTER_DOPPLER_F_HI_HZ) {
            energy_motion += power;
        } else {
            energy_high += power;
        }
    }
    const float denom_e = energy_dc + energy_motion + energy_high + NOISE_FILTER_DOPPLER_EPS;
    const float ratio = energy_motion / denom_e;
    if (!isfinite((double)ratio) || ratio < 0.0f) {
        return false;
    }
    output->valid = true;
    output->ratio = ratio > 1.0f ? 1.0f : ratio;
    output->fs_hz = fs_hz;
    output->sample_count = count;
    return true;
}
