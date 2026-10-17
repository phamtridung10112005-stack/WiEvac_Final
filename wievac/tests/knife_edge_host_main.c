#include "formula_flex.h"

#include <math.h>
#include <stdio.h>

static void print_fraction(const char *name, float value)
{
    if (!isfinite((double)value)) {
        printf("FRAC %s nan\n", name);
        return;
    }
    printf("FRAC %s %.6f\n", name, value);
}

int main(void)
{
    static const float table[] = {0.0f, 1.0f, 3.0f, 6.0f, 10.0f, 13.0f, 16.3f, 20.0f};
    uint16_t i;
    float ref[8];
    float now[8];
    for (i = 0U; i < 8U; ++i) {
        const float blocked = formula_flex_knife_edge_blocked(table[i]);
        printf("KE %.1f %.6f %.6f\n", table[i], blocked, 100.0f * (1.0f - blocked));
    }
    for (i = 0U; i < 8U; ++i) {
        ref[i] = 10.0f;
        now[i] = 20.0f;
    }
    print_fraction("stronger", formula_flex_blocked_fraction(now, ref, 8U));
    for (i = 0U; i < 8U; ++i) {
        now[i] = ref[i] / powf(10.0f, 1.0f / 20.0f);
    }
    print_fraction("deadband", formula_flex_blocked_fraction(now, ref, 8U));
    for (i = 0U; i < 8U; ++i) {
        now[i] = NAN;
    }
    now[0] = 0.0f;
    now[1] = -2.0f;
    now[2] = ref[2] / powf(10.0f, 11.0f / 20.0f);
    now[3] = now[2];
    now[4] = now[2];
    now[5] = now[2];
    print_fraction("ten_db", formula_flex_blocked_fraction(now, ref, 8U));
    now[5] = NAN;
    print_fraction("sparse", formula_flex_blocked_fraction(now, ref, 8U));
    return 0;
}
