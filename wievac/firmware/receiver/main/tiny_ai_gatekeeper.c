#include "tiny_ai_gatekeeper.h"

#include "tiny_ai_features.h"
#include "tiny_ai_model_weights.h"

#include <string.h>

#define TINY_AI_FRESH_US 5000000ULL

/* ponytail: one extractor; this RX owns one link. A second link resets it. */
static tiny_ai_extractor_t s_extractor;
static uint32_t s_link_id;
static bool s_link_set;

#if TINY_AI_MODEL_PRESENT || defined(TINY_AI_GATE_HOST)
static uint32_t rotr(uint32_t value, uint32_t bits)
{
    return (value >> bits) | (value << (32U - bits));
}

static void sha256_block(uint32_t state[8], const uint8_t block[64])
{
    static const uint32_t k[64] = {
        0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u,
        0x923f82a4u, 0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
        0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u,
        0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
        0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u,
        0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
        0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
        0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
        0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au,
        0x5b9cca4fu, 0x682e6ff3u, 0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
        0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
    };
    uint32_t w[64];
    uint32_t a, b, c, d, e, f, g, h;
    uint32_t index;
    for (index = 0; index < 16U; ++index) {
        w[index] = ((uint32_t)block[index * 4U] << 24) | ((uint32_t)block[index * 4U + 1U] << 16) |
                   ((uint32_t)block[index * 4U + 2U] << 8) | (uint32_t)block[index * 4U + 3U];
    }
    for (index = 16U; index < 64U; ++index) {
        uint32_t s0 = rotr(w[index - 15U], 7U) ^ rotr(w[index - 15U], 18U) ^ (w[index - 15U] >> 3U);
        uint32_t s1 = rotr(w[index - 2U], 17U) ^ rotr(w[index - 2U], 19U) ^ (w[index - 2U] >> 10U);
        w[index] = w[index - 16U] + s0 + w[index - 7U] + s1;
    }
    a = state[0]; b = state[1]; c = state[2]; d = state[3];
    e = state[4]; f = state[5]; g = state[6]; h = state[7];
    for (index = 0; index < 64U; ++index) {
        uint32_t s1 = rotr(e, 6U) ^ rotr(e, 11U) ^ rotr(e, 25U);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t temp1 = h + s1 + ch + k[index] + w[index];
        uint32_t s0 = rotr(a, 2U) ^ rotr(a, 13U) ^ rotr(a, 22U);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t temp2 = s0 + maj;
        h = g; g = f; f = e; e = d + temp1; d = c; c = b; b = a; a = temp1 + temp2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

static void sha256(const uint8_t *message, size_t length, uint8_t out[32])
{
    uint32_t state[8] = {
        0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
        0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u
    };
    uint8_t block[64];
    size_t offset = 0U;
    uint64_t bits = (uint64_t)length * 8ULL;
    uint32_t index;
    while (length - offset >= 64U) {
        sha256_block(state, message + offset);
        offset += 64U;
    }
    memset(block, 0, sizeof(block));
    memcpy(block, message + offset, length - offset);
    block[length - offset] = 0x80u;
    if (length - offset >= 56U) {
        sha256_block(state, block);
        memset(block, 0, sizeof(block));
    }
    for (index = 0; index < 8U; ++index) {
        block[63U - index] = (uint8_t)(bits >> (8U * index));
    }
    sha256_block(state, block);
    for (index = 0; index < 8U; ++index) {
        out[index * 4U] = (uint8_t)(state[index] >> 24);
        out[index * 4U + 1U] = (uint8_t)(state[index] >> 16);
        out[index * 4U + 2U] = (uint8_t)(state[index] >> 8);
        out[index * 4U + 3U] = (uint8_t)state[index];
    }
}
#endif

#if TINY_AI_MODEL_PRESENT
#include <math.h>

typedef struct {
    const uint8_t *bytes;
    size_t length;
    size_t offset;
} reader_t;

static bool read_bytes(reader_t *reader, void *destination, size_t length)
{
    if (reader->offset + length > reader->length) {
        return false;
    }
    memcpy(destination, reader->bytes + reader->offset, length);
    reader->offset += length;
    return true;
}

static bool read_u32(reader_t *reader, uint32_t *out)
{
    uint8_t bytes[4];
    if (!read_bytes(reader, bytes, 4U)) {
        return false;
    }
    *out = (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) | ((uint32_t)bytes[2] << 16) |
           ((uint32_t)bytes[3] << 24);
    return true;
}

static bool read_f32(reader_t *reader, float *out)
{
    uint32_t bits;
    if (!read_u32(reader, &bits)) {
        return false;
    }
    memcpy(out, &bits, sizeof(*out));
    return true;
}

static bool f32_at(const uint8_t *bytes, size_t length, size_t index, float *out)
{
    size_t offset = index * 4U;
    uint32_t bits;
    if (offset + 4U > length) {
        return false;
    }
    bits = (uint32_t)bytes[offset] | ((uint32_t)bytes[offset + 1U] << 8) |
           ((uint32_t)bytes[offset + 2U] << 16) | ((uint32_t)bytes[offset + 3U] << 24);
    memcpy(out, &bits, sizeof(*out));
    return true;
}

static struct {
    bool parsed;
    uint32_t rung;
    float threshold;
    float mu[42];
    const uint8_t *payload;
    size_t payload_len;
} s_model;

static bool hash_matches(void)
{
    uint8_t digest[32];
    char hex[65];
    uint32_t index;
    sha256(tiny_ai_weight_blob, tiny_ai_weight_blob_len, digest);
    for (index = 0; index < 32U; ++index) {
        static const char digits[] = "0123456789abcdef";
        hex[index * 2U] = digits[digest[index] >> 4];
        hex[index * 2U + 1U] = digits[digest[index] & 0x0fu];
    }
    hex[64] = '\0';
    return strcmp(hex, TINY_AI_MODEL_HASH) == 0;
}

static bool parse_model(void)
{
    reader_t reader = {tiny_ai_weight_blob, tiny_ai_weight_blob_len, 0U};
    uint8_t magic[4];
    uint32_t version = 0U;
    uint32_t index;
    memset(&s_model, 0, sizeof(s_model));
    if (!read_bytes(&reader, magic, 4U) || memcmp(magic, "TAI1", 4) != 0) {
        return false;
    }
    if (!read_u32(&reader, &version) || version != 1U || !read_u32(&reader, &s_model.rung)) {
        return false;
    }
    if (s_model.rung < 1U || s_model.rung > 3U || !read_f32(&reader, &s_model.threshold)) {
        return false;
    }
    for (index = 0; index < 42U; ++index) {
        if (!read_f32(&reader, &s_model.mu[index])) {
            return false;
        }
    }
    s_model.payload = reader.bytes + reader.offset;
    s_model.payload_len = reader.length - reader.offset;
    s_model.parsed = true;
    return true;
}

static bool ready_cached(void)
{
    static int checked;
    static int ready;
    if (!checked) {
        checked = 1;
        ready = hash_matches() && parse_model();
    }
    return ready != 0;
}

static bool finite_feature(float value)
{
    return value == value && value < 1.0e30f && value > -1.0e30f;
}

static bool score_rung1(const float features[42], float *novelty, bool *empty_like, float *empty_score)
{
    float delta[42];
    float accumulator = 0.0f;
    uint32_t row;
    uint32_t col;
    if (s_model.payload_len < 42U * 42U * 4U) {
        return false;
    }
    for (row = 0; row < 42U; ++row) {
        delta[row] = features[row] - s_model.mu[row];
    }
    for (row = 0; row < 42U; ++row) {
        for (col = 0; col < 42U; ++col) {
            float weight;
            float prod;
            if (!f32_at(s_model.payload, s_model.payload_len, row * 42U + col, &weight)) {
                return false;
            }
            prod = delta[row] * weight;
            prod = prod * delta[col];
            accumulator = accumulator + prod;
        }
    }
    if (!finite_feature(accumulator)) {
        return false;
    }
    if (accumulator < 0.0f) {
        accumulator = 0.0f;
    }
    *novelty = accumulator;
    *empty_like = !(accumulator > s_model.threshold);
    if (!(s_model.threshold > 0.0f)) {
        *empty_score = accumulator > 0.0f ? 0.0f : 1.0f;
    } else {
        *empty_score = s_model.threshold / (s_model.threshold + accumulator);
    }
    return finite_feature(*empty_score);
}

static bool score_rung2(const float features[42], float *novelty, bool *empty_like, float *empty_score)
{
    reader_t reader = {s_model.payload, s_model.payload_len, 0U};
    uint32_t components = 0U;
    float terms[4];
    float max_term = -1.0e30f;
    float sum = 0.0f;
    float loglik;
    float margin;
    float denom;
    uint32_t component;
    if (!read_u32(&reader, &components) || components < 2U || components > 4U) {
        return false;
    }
    for (component = 0; component < components; ++component) {
        float log_weight;
        float log_norm;
        float dist = 0.0f;
        float mean[42];
        float inv_var[42];
        uint32_t index;
        if (!read_f32(&reader, &log_weight)) {
            return false;
        }
        for (index = 0; index < 42U; ++index) {
            if (!read_f32(&reader, &mean[index])) {
                return false;
            }
        }
        for (index = 0; index < 42U; ++index) {
            if (!read_f32(&reader, &inv_var[index])) {
                return false;
            }
        }
        if (!read_f32(&reader, &log_norm)) {
            return false;
        }
        for (index = 0; index < 42U; ++index) {
            float delta = features[index] - mean[index];
            float quad = delta * delta;
            quad = quad * inv_var[index];
            dist = dist + quad;
        }
        terms[component] = (log_weight + log_norm) - (dist * 0.5f);
        if (terms[component] > max_term) {
            max_term = terms[component];
        }
    }
    for (component = 0; component < components; ++component) {
        sum += expf(terms[component] - max_term);
    }
    if (!(sum > 0.0f)) {
        return false;
    }
    loglik = max_term + logf(sum);
    if (!finite_feature(loglik)) {
        return false;
    }
    *novelty = -loglik;
    *empty_like = !(loglik < s_model.threshold);
    margin = loglik - s_model.threshold;
    denom = 1.0f + (margin < 0.0f ? -margin : margin);
    *empty_score = 0.5f + 0.5f * (margin / denom);
    return finite_feature(*empty_score);
}

static bool score_rung3(const float features[42], float *novelty, bool *empty_like, float *empty_score)
{
    reader_t reader = {s_model.payload, s_model.payload_len, 0U};
    uint32_t bottleneck = 0U;
    float inv_std[42];
    float xstd[42];
    float hidden[16];
    float w1_scale;
    float b1_scale;
    float w2_scale;
    float b2_scale;
    float mse = 0.0f;
    uint32_t index;
    uint32_t unit;
    uint8_t raw;
    if (!read_u32(&reader, &bottleneck) || bottleneck < 8U || bottleneck > 16U) {
        return false;
    }
    for (index = 0; index < 42U; ++index) {
        float delta;
        if (!read_f32(&reader, &inv_std[index])) {
            return false;
        }
        delta = features[index] - s_model.mu[index];
        xstd[index] = delta * inv_std[index];
    }
    if (!read_f32(&reader, &w1_scale) || !read_f32(&reader, &b1_scale) ||
        !read_f32(&reader, &w2_scale) || !read_f32(&reader, &b2_scale)) {
        return false;
    }
    for (unit = 0; unit < bottleneck; ++unit) {
        float acc;
        if (!read_bytes(&reader, &raw, 1U)) {
            return false;
        }
        acc = (float)(int8_t)raw * b1_scale;
        for (index = 0; index < 42U; ++index) {
            float weight;
            float prod;
            if (!read_bytes(&reader, &raw, 1U)) {
                return false;
            }
            weight = (float)(int8_t)raw * w1_scale;
            prod = weight * xstd[index];
            acc = acc + prod;
        }
        hidden[unit] = acc;
    }
    for (index = 0; index < 42U; ++index) {
        float acc;
        float err;
        if (!read_bytes(&reader, &raw, 1U)) {
            return false;
        }
        acc = (float)(int8_t)raw * b2_scale;
        for (unit = 0; unit < bottleneck; ++unit) {
            float weight;
            float prod;
            if (!read_bytes(&reader, &raw, 1U)) {
                return false;
            }
            weight = (float)(int8_t)raw * w2_scale;
            prod = weight * hidden[unit];
            acc = acc + prod;
        }
        err = acc - xstd[index];
        mse = mse + (err * err);
    }
    mse = mse / 42.0f;
    if (!finite_feature(mse)) {
        return false;
    }
    *novelty = mse;
    *empty_like = !(mse > s_model.threshold);
    if (!(s_model.threshold > 0.0f)) {
        *empty_score = mse > 0.0f ? 0.0f : 1.0f;
    } else {
        *empty_score = s_model.threshold / (s_model.threshold + mse);
    }
    return finite_feature(*empty_score);
}
#endif

void tiny_ai_gate_reset(void)
{
    tiny_ai_extractor_init(&s_extractor);
    s_link_id = 0U;
    s_link_set = false;
}

bool tiny_ai_gate_model_ready(void)
{
#if TINY_AI_MODEL_PRESENT
    return ready_cached();
#else
    return false;
#endif
}

const char *tiny_ai_gate_model_version(void)
{
    return tiny_ai_gate_model_ready() ? TINY_AI_MODEL_VERSION : NULL;
}

const char *tiny_ai_gate_model_hash(void)
{
    return tiny_ai_gate_model_ready() ? TINY_AI_MODEL_HASH : "";
}

bool tiny_ai_gate_score_features(const float features[42], float *empty_score, bool *empty_like)
{
#if TINY_AI_MODEL_PRESENT
    float novelty = 0.0f;
    uint32_t index;
    if (!tiny_ai_gate_model_ready() || features == NULL || empty_score == NULL || empty_like == NULL) {
        return false;
    }
    for (index = 0; index < 42U; ++index) {
        if (!finite_feature(features[index])) {
            return false;
        }
    }
    if (s_model.rung == 1U) {
        return score_rung1(features, &novelty, empty_like, empty_score);
    }
    if (s_model.rung == 2U) {
        return score_rung2(features, &novelty, empty_like, empty_score);
    }
    if (s_model.rung == 3U) {
        return score_rung3(features, &novelty, empty_like, empty_score);
    }
    return false;
#else
    (void)features;
    (void)empty_score;
    (void)empty_like;
    return false;
#endif
}

void tiny_ai_gate_status(uint64_t now_us, tiny_ai_gate_status_t *status)
{
    float features[42];
    uint64_t feature_ts = 0U;
    if (status == NULL) {
        return;
    }
    memset(status, 0, sizeof(*status));
    status->ready = tiny_ai_gate_model_ready();
    if (!status->ready || !tiny_ai_extractor_latest(&s_extractor, features, &feature_ts)) {
        return;
    }
    if (now_us < feature_ts || now_us - feature_ts > TINY_AI_FRESH_US) {
        return;
    }
    if (!tiny_ai_gate_score_features(features, &status->empty_score, &status->empty_like)) {
        return;
    }
    status->attested = true;
}

bool tiny_ai_gate_allows_baseline(uint64_t now_us)
{
    tiny_ai_gate_status_t status;
    tiny_ai_gate_status(now_us, &status);
    if (!status.ready) {
        return true;
    }
    return status.attested && status.empty_like;
}

bool tiny_ai_gate_boot_is_quiet(uint64_t now_us)
{
    tiny_ai_gate_status_t status;
    tiny_ai_gate_status(now_us, &status);
    if (!status.ready || !status.attested) {
        return true;
    }
    return status.empty_like;
}

void tiny_ai_gate_ingest(uint32_t link_id, uint64_t timestamp_us,
                         const uint8_t *csi, uint16_t csi_length)
{
#if TINY_AI_MODEL_PRESENT
    float amplitudes[TINY_AI_TONES];
    if (!tiny_ai_gate_model_ready() || csi == NULL) {
        return;
    }
    if (!s_link_set || s_link_id != link_id) {
        tiny_ai_extractor_init(&s_extractor);
        s_link_id = link_id;
        s_link_set = true;
    }
    if (!tiny_ai_amplitudes_from_csi(csi, csi_length, amplitudes)) {
        return;
    }
    (void)tiny_ai_extractor_push(&s_extractor, timestamp_us, amplitudes, NULL);
#else
    (void)link_id;
    (void)timestamp_us;
    (void)csi;
    (void)csi_length;
#endif
}

#if defined(TINY_AI_GATE_HOST)
#include <stdio.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

int main(void)
{
    uint8_t digest[32];
    uint32_t index;
    tiny_ai_gate_reset();
    printf("ready %d\n", tiny_ai_gate_model_ready() ? 1 : 0);
    printf("baseline %d\n", tiny_ai_gate_allows_baseline(0) ? 1 : 0);
    printf("boot %d\n", tiny_ai_gate_boot_is_quiet(0) ? 1 : 0);
    sha256((const uint8_t *)"abc", 3U, digest);
    printf("sha ");
    for (index = 0; index < 32U; ++index) {
        printf("%02x", digest[index]);
    }
    printf("\n");
    if (tiny_ai_gate_model_ready()) {
        float features[42];
        float score = 0.0f;
        bool empty_like = false;
        uint32_t bits = 0U;
#ifdef _WIN32
        _setmode(_fileno(stdin), _O_BINARY);
#endif
        if (fread(features, sizeof(float), 42U, stdin) != 42U) {
            return 2;
        }
        if (!tiny_ai_gate_score_features(features, &score, &empty_like)) {
            return 3;
        }
        memcpy(&bits, &score, sizeof(bits));
        printf("score %08x like %d\n", bits, empty_like ? 1 : 0);
    }
    return 0;
}
#endif
