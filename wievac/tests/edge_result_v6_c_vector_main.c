#include "../firmware/receiver/main/edge_result_v5_pipeline.h"
#include <stdio.h>
int edge_result_v6_vector(edge_result_v5_t *, uint8_t *, size_t, size_t *);
int main(void) {
    edge_result_v5_t value; uint8_t packet[EDGE_RESULT_V5_MAX_PACKET]; size_t length = 0;
    if (edge_result_v6_vector(&value, packet, sizeof(packet), &length) != 0) return 2;
    for (size_t i = 0; i < length; ++i) printf("%02x", packet[i]);
    putchar('\n'); return 0;
}
