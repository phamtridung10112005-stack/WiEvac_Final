#include "formula_flex.h"

#include <math.h>
#include <stdio.h>

static uint32_t g_rng = 1U;

static float urand(void)
{
    g_rng = g_rng * 1664525U + 1013904223U;
    return (float)(g_rng >> 8) * (1.0f / 16777216.0f);
}

static float gauss(void)
{
    float u1 = urand();
    const float u2 = urand();
    if (u1 < 1.0e-7f) {
        u1 = 1.0e-7f;
    }
    return sqrtf(-2.0f * logf(u1)) * cosf(6.28318530718f * u2);
}

static void run_case(const char *name, float s_true, float sigma_db, int frames)
{
    formula_flex_gain_response_t st;
    float xs[2048];
    float ys[2048];
    uint16_t kept = 0U;
    double amplitude = 1000.0;
    double gain = 1.0;
    double raw = 0.0;
    double full = 0.0;
    double hat = 0.0;
    float estimated;
    int index;
    formula_flex_gain_response_reset(&st);
    for (index = 0; index < frames; ++index) {
        const float noise_db = gauss() * sigma_db;
        const float motion_db = urand() < 0.05f ? (urand() < 0.5f ? 6.0f : -6.0f) : 0.0f;
        double next_gain = gain;
        double next_amplitude;
        if (urand() < 0.50f) {
            double step_db = 0.25 + (double)urand() * 0.75;
            if (urand() < 0.5f) {
                step_db = -step_db;
            }
            next_gain = gain * pow(10.0, step_db / 20.0);
        }
        next_amplitude = amplitude * pow(next_gain / gain, -(double)s_true) *
                         pow(10.0, ((double)noise_db + (double)motion_db) / 20.0);
        if (next_gain != gain && next_amplitude > 0.0 && amplitude > 0.0) {
            const float x_db = (float)(20.0 * log10(next_gain / gain));
            const float y_db = (float)(20.0 * log10(next_amplitude / amplitude));
            formula_flex_gain_response_add(&st, x_db, y_db);
            if (fabsf(y_db) <= FORMULA_FLEX_GAIN_RESP_OUTLIER_DB && kept < 2048U) {
                xs[kept] = x_db;
                ys[kept] = y_db;
                ++kept;
            }
        }
        gain = next_gain;
        amplitude = next_amplitude;
    }
    estimated = formula_flex_gain_response_s(&st);
    for (index = 0; index < (int)kept; ++index) {
        const double y = (double)ys[index];
        const double x = (double)xs[index];
        const double compensated = y + (double)estimated * x;
        const double full_step = y + x;
        raw += y * y;
        full += full_step * full_step;
        hat += compensated * compensated;
    }
    if (kept == 0U) {
        kept = 1U;
    }
    printf("CASE %s s=%.4f events=%u frames=%u raw=%.4f full=%.4f hat=%.4f\n",
           name, estimated, st.events, st.frames,
           sqrt(raw / (double)kept), sqrt(full / (double)kept), sqrt(hat / (double)kept));
}

int main(void)
{
    formula_flex_gain_response_t few;
    uint16_t index;
    static const float truths[] = {0.0f, 0.26f, -0.26f, 0.5f, 1.0f, -1.0f};
    formula_flex_gain_response_reset(&few);
    for (index = 0U; index < 20U; ++index) {
        formula_flex_gain_response_add(&few, 0.5f, -0.5f);
    }
    printf("FEW %.4f %u\n", formula_flex_gain_response_s(&few), few.events);
    g_rng = 7U;
    for (index = 0U; index < 6U; ++index) {
        char name[8];
        snprintf(name, sizeof(name), "s%d", (int)index);
        g_rng = 1000U + (uint32_t)index * 17U;
        run_case(name, truths[index], 0.4f, 1200);
    }
    g_rng = 99U;
    run_case("noise", 0.0f, 2.5f, 400);
    return 0;
}
