#ifndef WIEVAC_NOISE_FILTER_H
#define WIEVAC_NOISE_FILTER_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const uint8_t *csi;
    uint16_t csi_length;
    bool first_word_invalid;
} noise_filter_input_t;

typedef struct {
    float amplitude;
    float robust_spread;
    uint16_t sample_pairs;
} noise_filter_output_t;

/* Shadow Doppler from a 1 s amplitude series. Not occupancy probability.
 * Forward DFT only. Pipeline may scale published score and occupancy by r
 * when this result is valid; FFT-invalid must not pretend the hall is empty.
 * F_LO is below the first positive FFT bin at fs=100 Hz / N=64 (1.56 Hz) so
 * gait cadence (~1-2 Hz steps) counts as motion, not DC. The old 2 Hz floor
 * put that bin in DC and missed a person crossing TX-RX. */
#define NOISE_FILTER_DOPPLER_FFT_N 64U
#define NOISE_FILTER_DOPPLER_MIN_SAMPLES 32U
#define NOISE_FILTER_DOPPLER_F_LO_HZ 0.5f
#define NOISE_FILTER_DOPPLER_F_HI_HZ 12.0f
#define NOISE_FILTER_DOPPLER_MAX_RELATIVE_JITTER 0.25f

typedef struct {
    bool valid;
    float ratio;
    float fs_hz;
    uint16_t sample_count;
} noise_filter_doppler_t;

bool noise_filter_validate(const noise_filter_input_t *input);
bool noise_filter_process(const noise_filter_input_t *input,
                          noise_filter_output_t *output);
bool noise_filter_doppler_ratio(const float *amplitudes,
                                const uint64_t *timestamps_us,
                                uint16_t count,
                                noise_filter_doppler_t *output);

#ifdef __cplusplus
}
#endif

#endif /* WIEVAC_NOISE_FILTER_H */
