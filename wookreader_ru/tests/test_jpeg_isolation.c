/* Regression for the two incompatible JPEG ABIs used by WookReader.
 * Compile as separate translation units: the MuPDF consumer sees IJG 9 headers,
 * whereas the application/generator sees libjpeg-turbo's TurboJPEG API.
 * The large test picture is generated here and contains no third-party art.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef WOOK_TEST_IJG
#include <setjmp.h>
#include "jpeglib.h"
typedef void *backing_store_ptr;
#include "jmemcust.h"

#if JPEG_LIB_VERSION != 90
#error This regression must compile the MuPDF consumer against its IJG 9 ABI.
#endif

typedef struct {
    struct jpeg_error_mgr error;
    jmp_buf jump;
    char message[JMSG_LENGTH_MAX];
} TestError;

typedef struct {
    struct jpeg_decompress_struct decoder;
    TestError error;
    jpeg_cust_mem_data memory;
    size_t allocated;
} TestDecoder;

static void fail_jpeg(j_common_ptr decoder)
{
    TestError *error = (TestError *)decoder->err;
    decoder->err->format_message(decoder, error->message);
    longjmp(error->jump, 1);
}

static void *allocate_jpeg(j_common_ptr decoder, size_t size)
{
    TestDecoder *state = (TestDecoder *)GET_CUST_MEM_DATA(decoder)->priv;
    void *result = malloc(size);
    if (result) state->allocated += size;
    return result;
}

static void release_jpeg(j_common_ptr decoder, void *data, size_t size)
{
    TestDecoder *state = (TestDecoder *)GET_CUST_MEM_DATA(decoder)->priv;
    state->allocated -= size;
    free(data);
}

/* This function lives inside the archive that is isolated. Its IJG references,
 * including the custom MuPDF memory interface, must be renamed with providers.
 */
int wook_test_ijg_decode(const unsigned char *data, unsigned long size,
                         int *width, int *height, int *progressive,
                         uint64_t *checksum, char *message, size_t message_size)
{
    TestDecoder *state = (TestDecoder *)calloc(1, sizeof(*state));
    if (!state) return 1;
    state->decoder.err = jpeg_std_error(&state->error.error);
    state->error.error.error_exit = fail_jpeg;
    jpeg_cust_mem_init(&state->memory, state, NULL, NULL, NULL,
                      allocate_jpeg, release_jpeg, allocate_jpeg, release_jpeg,
                      NULL);
    state->decoder.client_data = &state->memory;
    if (setjmp(state->error.jump)) {
        snprintf(message, message_size, "%s", state->error.message);
        jpeg_destroy_decompress(&state->decoder);
        free(state);
        return 2;
    }
    jpeg_create_decompress(&state->decoder);
    /* IJG 9's memory-source declaration predates the const-qualified API. */
    jpeg_mem_src(&state->decoder, (unsigned char *)data, size);
    if (jpeg_read_header(&state->decoder, TRUE) != JPEG_HEADER_OK) {
        jpeg_destroy_decompress(&state->decoder);
        free(state);
        return 3;
    }
    *width = (int)state->decoder.image_width;
    *height = (int)state->decoder.image_height;
    *progressive = state->decoder.progressive_mode != 0;
    state->decoder.out_color_space = JCS_RGB;
    jpeg_start_decompress(&state->decoder);
    unsigned int row_size = state->decoder.output_width * state->decoder.output_components;
    JSAMPARRAY row = state->decoder.mem->alloc_sarray(
        (j_common_ptr)&state->decoder, JPOOL_IMAGE, row_size, 1);
    *checksum = 0;
    while (state->decoder.output_scanline < state->decoder.output_height) {
        if (jpeg_read_scanlines(&state->decoder, row, 1) != 1) {
            jpeg_destroy_decompress(&state->decoder);
            free(state);
            return 4;
        }
        for (unsigned int x = 0; x < row_size; ++x) *checksum += row[0][x];
    }
    jpeg_finish_decompress(&state->decoder);
    jpeg_destroy_decompress(&state->decoder);
    int result = state->allocated != 0 ? 5 : 0;
    free(state);
    return result;
}

#else
#include "turbojpeg.h"

#ifdef WOOK_TEST_GENERATE
int main(int argc, char **argv)
{
    if (argc != 5) return 1;
    int width = atoi(argv[2]), height = atoi(argv[3]);
    int progressive = atoi(argv[4]);
    if (width <= 0 || height <= 0 || width > 7000 || height > 7000) return 2;
    size_t count = (size_t)width * height;
    unsigned char *pixels = (unsigned char *)malloc(count * 3);
    if (!pixels) return 3;
    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            size_t offset = ((size_t)y * width + x) * 3;
            pixels[offset] = (unsigned char)((x / 8 + y / 32) & 255);
            pixels[offset + 1] = (unsigned char)((x / 32 + y / 8) & 255);
            pixels[offset + 2] = (unsigned char)(((x / 64) ^ (y / 64)) * 19);
        }
    }
    tjhandle encoder = tjInitCompress();
    unsigned char *jpeg = NULL;
    unsigned long size = 0;
    int flags = progressive ? TJFLAG_PROGRESSIVE : 0;
    if (!encoder || tjCompress2(encoder, pixels, width, 0, height, TJPF_RGB,
                                &jpeg, &size, TJSAMP_420, 80, flags) != 0) {
        fprintf(stderr, "generator: %s\n", tjGetErrorStr());
        return 4;
    }
    FILE *output = fopen(argv[1], "wb");
    if (!output || fwrite(jpeg, 1, size, output) != size) return 5;
    fclose(output);
    tjFree(jpeg);
    tjDestroy(encoder);
    free(pixels);
    return 0;
}

#else
int wook_test_ijg_decode(const unsigned char *, unsigned long, int *, int *, int *,
                         uint64_t *, char *, size_t);

int main(int argc, char **argv)
{
    if (argc != 6) return 1;
    int old_link = strcmp(argv[1], "old") == 0;
    int expected_width = atoi(argv[3]), expected_height = atoi(argv[4]);
    int expected_progressive = atoi(argv[5]);
    FILE *input = fopen(argv[2], "rb");
    if (!input || fseek(input, 0, SEEK_END)) return 2;
    long length = ftell(input);
    if (length <= 0 || fseek(input, 0, SEEK_SET)) return 3;
    unsigned char *data = (unsigned char *)malloc((size_t)length);
    if (!data || fread(data, 1, (size_t)length, input) != (size_t)length) return 4;
    fclose(input);
    int width = 0, height = 0, progressive = 0;
    uint64_t ijg_checksum = 0;
    char message[256] = {0};
    int ijg_result = wook_test_ijg_decode(data, (unsigned long)length,
        &width, &height, &progressive, &ijg_checksum, message, sizeof(message));
    if (ijg_result || width != expected_width || height != expected_height ||
        progressive != expected_progressive || ijg_checksum == 0) {
        fprintf(stderr, "IJG90 decode failed: result=%d %dx%d progressive=%d %s\n",
                ijg_result, width, height, progressive, message);
        return 5;
    }
    tjhandle decoder = tjInitDecompress();
    if (old_link) {
        if (decoder) { tjDestroy(decoder); return 6; }
        const char *error = tjGetErrorStr();
        if (!strstr(error, "90") || !strstr(error, "62")) {
            fprintf(stderr, "Unexpected initialisation error: %s\n", error);
            return 7;
        }
        printf("{\"ijg90_full_decode\":true,\"turbo_init\":false,"
               "\"error\":\"%s\",\"image_bytes_submitted_to_turbo\":0}\n", error);
        free(data);
        return 0;
    }
    if (!decoder) { fprintf(stderr, "Turbo init: %s\n", tjGetErrorStr()); return 8; }
    int subsampling = 0, colorspace = 0;
    if (tjDecompressHeader3(decoder, data, (unsigned long)length, &width, &height,
                           &subsampling, &colorspace) ||
        width != expected_width || height != expected_height) return 9;
    int factor_count = 0, scaled_width = width, scaled_height = height;
    const tjscalingfactor *factors = tjGetScalingFactors(&factor_count);
    /* Same scale selection and pixel budget as the application cover path. */
    const int target_width = 160, target_height = 240;
    for (int k = 0; factors && k < factor_count; ++k) {
        int fw = TJSCALED(width, factors[k]), fh = TJSCALED(height, factors[k]);
        if (fw >= target_width && fh >= target_height &&
            (uint64_t)fw * fh < (uint64_t)scaled_width * scaled_height) {
            scaled_width = fw;
            scaled_height = fh;
        }
    }
    if ((uint64_t)scaled_width * scaled_height > 12000000ULL) return 10;
    size_t pixel_bytes = (size_t)scaled_width * scaled_height * 4;
    unsigned char *pixels = (unsigned char *)malloc(pixel_bytes);
    if (!pixels || tjDecompress2(decoder, data, (unsigned long)length, pixels,
        scaled_width, 0, scaled_height, TJPF_RGBA, TJFLAG_FASTDCT)) {
        fprintf(stderr, "Turbo decode: %s\n", tjGetErrorStr2(decoder));
        return 11;
    }
    uint64_t turbo_checksum = 0;
    for (size_t i = 0; i < pixel_bytes; ++i) turbo_checksum += pixels[i];
    if (!turbo_checksum) return 12;
    tjDestroy(decoder);
    free(pixels);
    free(data);
    printf("{\"ijg90_full_decode\":true,\"turbo_init\":true,"
           "\"turbo_scaled_decode\":true,\"source_width\":%d,\"source_height\":%d,"
           "\"progressive\":%s,\"scaled_width\":%d,\"scaled_height\":%d,"
           "\"rgba_bytes\":%zu,\"ijg_checksum\":%llu,\"turbo_checksum\":%llu}\n",
           width, height, progressive ? "true" : "false", scaled_width,
           scaled_height, pixel_bytes, (unsigned long long)ijg_checksum,
           (unsigned long long)turbo_checksum);
    return 0;
}
#endif
#endif
