#include "tiny_ai_gatekeeper.h"

#include <stdio.h>

/* Prints the AI confidence for fixed evidence sequences (one value per 1 s
 * window). The deduction on the RX is TINY_AI_SCORE_PENALTY * confidence. */

static void run(const char *name, const float *evidence, int count)
{
    float confidence = 0.0f;
    int index;
    printf("SEQ %s", name);
    for (index = 0; index < count; ++index) {
        confidence = tiny_ai_confidence_step(confidence, evidence[index]);
        printf(" %.4f", (double)confidence);
    }
    printf("\n");
}

int main(void)
{
    static const float blip[] = {1, 0, 0, 0, 0, 0};
    static const float sustained[] = {1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0};
    static const float weak[] = {0.2f, 0.2f, 0.2f, 0.2f, 0.2f, 0.2f, 0.2f, 0.2f, 0.2f, 0.2f};
    static const float flicker[] = {1, 0, 1, 0, 1, 0, 1, 0, 1, 0};
    run("blip", blip, 6);
    run("sustained", sustained, 14);
    run("weak", weak, 10);
    run("flicker", flicker, 10);
    printf("PENALTY %.4f\n", (double)TINY_AI_SCORE_PENALTY);
    return 0;
}
