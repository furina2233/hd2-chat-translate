#include "internal.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
static size_t hd2ct_utf8_character_bytes(unsigned char first)
{
    if (first < 0x80u) return 1u;
    if (first < 0xe0u) return 2u;
    if (first < 0xf0u) return 3u;
    return 4u;
}

static int hd2ct_youdao_input(const char *source, size_t source_bytes,
                              char *input, size_t input_capacity,
                              size_t *input_bytes, size_t *codepoint_count)
{
    size_t offsets[HD2CT_MAX_SOURCE + 1u];
    size_t count = 0;
    size_t cursor = 0;
    size_t first_end;
    size_t last_start;
    char decimal_count[16];
    int decimal_bytes;
    size_t used = 0;
    while (cursor < source_bytes && count < HD2CT_MAX_SOURCE) {
        offsets[count++] = cursor;
        cursor += hd2ct_utf8_character_bytes((unsigned char)source[cursor]);
    }
    offsets[count] = source_bytes;
    *codepoint_count = count;
    if (count <= 20u) {
        if (source_bytes + 1u > input_capacity) return 0;
        memcpy(input, source, source_bytes);
        input[source_bytes] = '\0';
        *input_bytes = source_bytes;
        return 1;
    }
    first_end = offsets[10u];
    last_start = offsets[count - 10u];
    decimal_bytes = snprintf(decimal_count, sizeof(decimal_count), "%lu",
                             (unsigned long)count);
    if (decimal_bytes <= 0 || (size_t)decimal_bytes >= sizeof(decimal_count)) return 0;
    if (first_end + (size_t)decimal_bytes + source_bytes - last_start + 1u > input_capacity) {
        return 0;
    }
    memcpy(input + used, source, first_end);
    used += first_end;
    memcpy(input + used, decimal_count, (size_t)decimal_bytes);
    used += (size_t)decimal_bytes;
    memcpy(input + used, source + last_start, source_bytes - last_start);
    used += source_bytes - last_start;
    input[used] = '\0';
    *input_bytes = used;
    return 1;
}

int hd2ct_build_youdao_request(const HD2CT_WorkerJob *job,
                                      HD2CT_BuiltRequest *request)
{
    char salt[33] = {0};
    char sign[65] = {0};
    char curtime[24] = {0};
    char input[HD2CT_MAX_SOURCE + 32u] = {0};
    const HD2CT_TargetLanguage *target = hd2ct_target_language(job->target_language);
    const char *parts[5];
    size_t lengths[5];
    size_t input_bytes = 0;
    size_t codepoints = 0;
    size_t app_id_length = strlen(job->app_id);
    size_t secret_length = strlen(job->api_key);
    FILETIME file_time;
    ULARGE_INTEGER windows_ticks;
    uint64_t unix_seconds;
    HD2CT_FormBuffer form;
    int curtime_bytes;
    int ok = 0;
    memset(request, 0, sizeof(*request));
    if (!hd2ct_random_salt(salt) ||
        !hd2ct_youdao_input(job->source, job->source_bytes, input, sizeof(input),
                            &input_bytes, &codepoints)) goto cleanup;
    (void)codepoints;
    GetSystemTimeAsFileTime(&file_time);
    windows_ticks.LowPart = file_time.dwLowDateTime;
    windows_ticks.HighPart = file_time.dwHighDateTime;
    if (windows_ticks.QuadPart < 116444736000000000ull) goto cleanup;
    unix_seconds = (windows_ticks.QuadPart - 116444736000000000ull) / 10000000ull;
    curtime_bytes = snprintf(curtime, sizeof(curtime), "%llu",
                             (unsigned long long)unix_seconds);
    if (curtime_bytes <= 0 || (size_t)curtime_bytes >= sizeof(curtime)) goto cleanup;
    parts[0] = job->app_id;
    parts[1] = input;
    parts[2] = salt;
    parts[3] = curtime;
    parts[4] = job->api_key;
    lengths[0] = app_id_length;
    lengths[1] = input_bytes;
    lengths[2] = strlen(salt);
    lengths[3] = (size_t)curtime_bytes;
    lengths[4] = secret_length;
    if (!hd2ct_digest_hex(BCRYPT_SHA256_ALGORITHM, parts, lengths, 5u,
                          sign, sizeof(sign))) goto cleanup;
    form.capacity = 32768u;
    form.data = (char *)malloc(form.capacity);
    form.used = 0;
    if (form.data == NULL) goto cleanup;
    form.data[0] = '\0';
    if (!hd2ct_form_add(&form, "q", job->source, job->source_bytes) ||
        !hd2ct_form_add(&form, "from", "auto", 4u) ||
        !hd2ct_form_add(&form, "to", target->youdao_code,
                        strlen(target->youdao_code)) ||
        !hd2ct_form_add(&form, "appKey", job->app_id, app_id_length) ||
        !hd2ct_form_add(&form, "salt", salt, strlen(salt)) ||
        !hd2ct_form_add(&form, "curtime", curtime, (size_t)curtime_bytes) ||
        !hd2ct_form_add(&form, "signType", "v3", 2u) ||
        !hd2ct_form_add(&form, "sign", sign, strlen(sign)) ||
        !hd2ct_form_add(&form, "strict", "true", 4u) ||
        form.used > MAXDWORD) {
        SecureZeroMemory(form.data, form.capacity);
        free(form.data);
        goto cleanup;
    }
    request->body = form.data;
    request->body_bytes = (DWORD)form.used;
    request->content_type = "application/x-www-form-urlencoded; charset=utf-8";
    ok = 1;

cleanup:
    SecureZeroMemory(salt, sizeof(salt));
    SecureZeroMemory(sign, sizeof(sign));
    SecureZeroMemory(input, sizeof(input));
    SecureZeroMemory(curtime, sizeof(curtime));
    return ok;
}

int hd2ct_parse_youdao_response(const HD2CT_WorkerJob *job, char *body,
                                      size_t body_bytes, char *translation,
                                      uint32_t *translation_bytes,
                                      const char **failure_code)
{
    cJSON *root = NULL;
    cJSON *error_item;
    cJSON *array;
    cJSON *language;
    cJSON *item;
    char code[32];
    size_t first_length;
    const char *first = NULL;
    int array_size;
    int i;
    int ok = 0;
    *failure_code = "BAD_RESPONSE";
    if (!hd2ct_parse_complete_json(body, body_bytes, &root)) return 0;
    error_item = cJSON_GetObjectItemCaseSensitive(root, "errorCode");
    if (!hd2ct_json_error_code(error_item, code, sizeof(code))) goto cleanup;
    if (strcmp(code, "0") != 0) {
        *failure_code = hd2ct_provider_error_code(HD2CT_ADAPTER_YOUDAO, code);
        goto cleanup;
    }
    array = cJSON_GetObjectItemCaseSensitive(root, "translation");
    array_size = cJSON_GetArraySize(array);
    if (!cJSON_IsArray(array) || array_size == 0) goto cleanup;
    for (i = 0; i < array_size; ++i) {
        item = cJSON_GetArrayItem(array, i);
        if (!cJSON_IsString(item) || item->valuestring == NULL) goto cleanup;
        if (i == 0) first = item->valuestring;
    }
    first_length = strlen(first);
    if (first_length == 0 || first_length > HD2CT_MAX_TRANSLATION ||
        hd2ct_is_only_space(first, first_length) ||
        !hd2ct_valid_utf8((const unsigned char *)first, first_length, 1)) goto cleanup;
    language = cJSON_GetObjectItemCaseSensitive(root, "l");
    if (language != NULL && !cJSON_IsString(language)) goto cleanup;
    ok = hd2ct_copy_valid_translation(first, translation, translation_bytes);

cleanup:
    cJSON_Delete(root);
    return ok;
}
