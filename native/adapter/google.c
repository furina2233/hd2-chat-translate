#include "internal.h"

#include <string.h>
#include <stdio.h>
int hd2ct_build_google_request(const HD2CT_WorkerJob *job,
                                      HD2CT_BuiltRequest *request)
{
    const HD2CT_TargetLanguage *target = hd2ct_target_language(job->target_language);
    cJSON *root = cJSON_CreateObject();
    char *printed = NULL;
    size_t length;
    int ok = 0;
    if (root == NULL ||
        cJSON_AddStringToObject(root, "q", job->source) == NULL ||
        cJSON_AddStringToObject(root, "target", target->google_code) == NULL ||
        cJSON_AddStringToObject(root, "format", "text") == NULL) {
        goto cleanup;
    }
    printed = cJSON_PrintUnformatted(root);
    if (printed == NULL) goto cleanup;
    length = strlen(printed);
    if (length == 0 || length > 32768u || length > MAXDWORD) goto cleanup;
    request->body = printed;
    request->body_bytes = (DWORD)length;
    request->content_type = "application/json";
    request->header_name = "x-goog-api-key";
    request->header_prefix = "";
    request->header_value = job->api_key;
    printed = NULL;
    ok = 1;

cleanup:
    if (printed != NULL) cJSON_free(printed);
    cJSON_Delete(root);
    return ok;
}

static int hd2ct_html_entity(const char *entity, size_t length, uint32_t *codepoint)
{
    struct HD2CT_Entity { const char *name; uint32_t value; };
    static const struct HD2CT_Entity named[] = {
        {"amp", '&'}, {"lt", '<'}, {"gt", '>'}, {"quot", '"'},
        {"apos", '\''}, {"nbsp", 0x00a0u}, {"copy", 0x00a9u},
        {"reg", 0x00aeu}, {"hellip", 0x2026u}, {"ndash", 0x2013u},
        {"mdash", 0x2014u}, {"lsquo", 0x2018u}, {"rsquo", 0x2019u},
        {"ldquo", 0x201cu}, {"rdquo", 0x201du}, {"bull", 0x2022u},
        {"trade", 0x2122u}
    };
    size_t i;
    if (length >= 2u && entity[0] == '#') {
        size_t cursor = 1u;
        uint32_t value = 0;
        unsigned base = 10u;
        size_t digits = 0;
        if (cursor < length && (entity[cursor] == 'x' || entity[cursor] == 'X')) {
            base = 16u;
            ++cursor;
        }
        for (; cursor < length; ++cursor) {
            unsigned digit;
            unsigned char c = (unsigned char)entity[cursor];
            if (c >= '0' && c <= '9') digit = c - '0';
            else if (base == 16u && c >= 'a' && c <= 'f') digit = c - 'a' + 10u;
            else if (base == 16u && c >= 'A' && c <= 'F') digit = c - 'A' + 10u;
            else return 0;
            if (digit >= base || value > (0x10ffffu - digit) / base) return 0;
            value = value * base + digit;
            ++digits;
        }
        if (digits == 0 || value == 0 || value > 0x10ffffu ||
            (value >= 0xd800u && value <= 0xdfffu)) return 0;
        *codepoint = value;
        return 1;
    }
    for (i = 0; i < sizeof(named) / sizeof(named[0]); ++i) {
        if (strlen(named[i].name) == length &&
            memcmp(entity, named[i].name, length) == 0) {
            *codepoint = named[i].value;
            return 1;
        }
    }
    return 0;
}

static size_t hd2ct_encode_codepoint(uint32_t codepoint, char output[4])
{
    if (codepoint < 0x80u) {
        output[0] = (char)codepoint;
        return 1u;
    }
    if (codepoint < 0x800u) {
        output[0] = (char)(0xc0u | (codepoint >> 6));
        output[1] = (char)(0x80u | (codepoint & 0x3fu));
        return 2u;
    }
    if (codepoint < 0x10000u) {
        output[0] = (char)(0xe0u | (codepoint >> 12));
        output[1] = (char)(0x80u | ((codepoint >> 6) & 0x3fu));
        output[2] = (char)(0x80u | (codepoint & 0x3fu));
        return 3u;
    }
    output[0] = (char)(0xf0u | (codepoint >> 18));
    output[1] = (char)(0x80u | ((codepoint >> 12) & 0x3fu));
    output[2] = (char)(0x80u | ((codepoint >> 6) & 0x3fu));
    output[3] = (char)(0x80u | (codepoint & 0x3fu));
    return 4u;
}

static int hd2ct_google_decode_entities(const char *input, char *output,
                                        size_t output_capacity, size_t *output_bytes)
{
    size_t input_bytes = strlen(input);
    size_t i = 0;
    size_t used = 0;
    while (i < input_bytes) {
        if (input[i] == '&') {
            size_t end = i + 1u;
            uint32_t codepoint;
            while (end < input_bytes && end - i <= 12u && input[end] != ';') ++end;
            if (end < input_bytes && input[end] == ';' &&
                hd2ct_html_entity(input + i + 1u, end - i - 1u, &codepoint)) {
                char encoded[4];
                size_t encoded_bytes = hd2ct_encode_codepoint(codepoint, encoded);
                if (encoded_bytes > output_capacity - used - 1u) return 0;
                memcpy(output + used, encoded, encoded_bytes);
                used += encoded_bytes;
                i = end + 1u;
                continue;
            }
        }
        if (used + 1u >= output_capacity) return 0;
        output[used++] = input[i++];
    }
    output[used] = '\0';
    *output_bytes = used;
    return 1;
}

int hd2ct_parse_google_response(const HD2CT_WorkerJob *job, char *body,
                                      size_t body_bytes, char *translation,
                                      uint32_t *translation_bytes,
                                      const char **failure_code)
{
    cJSON *root = NULL;
    cJSON *data;
    cJSON *translations;
    cJSON *item;
    cJSON *translated;
    cJSON *detected;
    char decoded[HD2CT_MAX_TRANSLATION + 1u];
    size_t decoded_bytes = 0;
    int array_size;
    int i;
    int ok = 0;
    *failure_code = "BAD_RESPONSE";
    if (!hd2ct_parse_complete_json(body, body_bytes, &root)) return 0;
    data = cJSON_GetObjectItemCaseSensitive(root, "data");
    translations = cJSON_IsObject(data) ?
        cJSON_GetObjectItemCaseSensitive(data, "translations") : NULL;
    array_size = cJSON_GetArraySize(translations);
    if (!cJSON_IsArray(translations) || array_size == 0) goto cleanup;
    for (i = 0; i < array_size; ++i) {
        item = cJSON_GetArrayItem(translations, i);
        if (!cJSON_IsObject(item)) goto cleanup;
        translated = cJSON_GetObjectItemCaseSensitive(item, "translatedText");
        if (!cJSON_IsString(translated) || translated->valuestring == NULL) goto cleanup;
        detected = cJSON_GetObjectItemCaseSensitive(item, "detectedSourceLanguage");
        if (detected != NULL &&
            (!cJSON_IsString(detected) || detected->valuestring == NULL)) goto cleanup;
    }
    item = cJSON_GetArrayItem(translations, 0);
    translated = cJSON_GetObjectItemCaseSensitive(item, "translatedText");
    if (!hd2ct_google_decode_entities(translated->valuestring, decoded,
                                     sizeof(decoded), &decoded_bytes) ||
        decoded_bytes == 0 || decoded_bytes > HD2CT_MAX_TRANSLATION ||
        hd2ct_is_only_space(decoded, decoded_bytes) ||
        !hd2ct_valid_utf8((const unsigned char *)decoded, decoded_bytes, 1)) goto cleanup;
    detected = cJSON_GetObjectItemCaseSensitive(item, "detectedSourceLanguage");
    ok = hd2ct_copy_valid_translation(decoded, translation, translation_bytes);

cleanup:
    SecureZeroMemory(decoded, sizeof(decoded));
    cJSON_Delete(root);
    return ok;
}
