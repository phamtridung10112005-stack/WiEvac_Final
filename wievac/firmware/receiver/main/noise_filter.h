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

/* HT20 data tones used for sensing: skip DC, pilots and OFDM guards.
 * Same 12-index idea as ESPectre ML (even spacing, not all 64 bins). */
#define NOISE_FILTER_SELECTED_COUNT 12U

typedef struct {
    float amplitude;
    float robust_spread;
    uint16_t sample_pairs;
    /* Fixed HT20 mask (DC/pilot/guard removed). NAN if that bin is absent. */
    float bins[NOISE_FILTER_SELECTED_COUNT];
    uint16_t bin_count;
} noise_filter_output_t;
#define NOISE_FILTER_SELECTED_MIN 4U
#define NOISE_FILTER_HAMPEL_WINDOW 7U
#define NOISE_FILTER_HAMPEL_THRESHOLD 5.0f
#define NOISE_FILTER_HAMPEL_MAD_SCALE 1.4826f
#define NOISE_FILTER_HAMPEL_MAD_FLOOR 1.0e-3f

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

/* Linear drop vs a per-bin empty-corridor reference. Knob: raise if an
 * empty hall's own ripple trips nulls. */
#define NOISE_FILTER_NULL_DROP_DB 6.0f

typedef struct {
    bool valid;
    float ratio;
    float fs_hz;
    uint16_t sample_count;
    /* High-band energy / total. Motion, not a static barrier. */
    float hf_ratio;
    /* Lag-1 autocorrelation of the amplitude series. ~1 is static. */
    float corr_lag1;
} noise_filter_doppler_t;

bool noise_filter_validate(const noise_filter_input_t *input);
bool noise_filter_process(const noise_filter_input_t *input,
                          noise_filter_output_t *output);
void noise_filter_reset(void);
bool noise_filter_doppler_ratio(const float *amplitudes,
                                const uint64_t *timestamps_us,
                                uint16_t count,
                                noise_filter_doppler_t *output);

/* Fraction of masked bins whose amplitude is more than drop_db below ref.
 * Returns NAN when no bin has a usable reference. */
float noise_filter_null_ratio(const float *amps,
                              const float *ref,
                              uint16_t count,
                              float drop_db);

#ifdef __cplusplus
}
#endif

#endif /* WIEVAC_NOISE_FILTER_H */
