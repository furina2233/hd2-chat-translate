#include "internal.h"

#include <string.h>
#include <stdio.h>
size_t hd2ct_bounded_length(const char *value, size_t maximum)
{
    size_t length;
    if (value == NULL) {
        return maximum + 1u;
    }
    for (length = 0; length <= maximum; ++length) {
        if (value[length] == '\0') {
            return length;
        }
    }
    return maximum + 1u;
}

int hd2ct_valid_utf8(const unsigned char *bytes, size_t length, int reject_controls)
{
    size_t i = 0;
    while (i < length) {
        uint32_t cp;
        unsigned char first = bytes[i++];
        size_t continuation;
        if (first == 0) {
            return 0;
        }
        if (first < 0x80u) {
            cp = first;
            continuation = 0;
        } else if (first >= 0xc2u && first <= 0xdfu) {
            cp = first & 0x1fu;
            continuation = 1;
        } else if (first >= 0xe0u && first <= 0xefu) {
            cp = first & 0x0fu;
            continuation = 2;
        } else if (first >= 0xf0u && first <= 0xf4u) {
            cp = first & 0x07u;
            continuation = 3;
        } else {
            return 0;
        }
        if (continuation > length - i) {
            return 0;
        }
        while (continuation-- != 0) {
            unsigned char next = bytes[i++];
            if ((next & 0xc0u) != 0x80u) {
                return 0;
            }
            cp = (cp << 6) | (uint32_t)(next & 0x3fu);
        }
        if ((first >= 0xe0u && first <= 0xefu && cp < 0x800u) ||
            (first >= 0xf0u && first <= 0xf4u && cp < 0x10000u) ||
            (cp >= 0xd800u && cp <= 0xdfffu) || cp > 0x10ffffu) {
            return 0;
        }
        if (reject_controls &&
            ((cp < 0x20u && cp != 0x09u && cp != 0x0au && cp != 0x0du) ||
             (cp >= 0x7fu && cp <= 0x9fu))) {
            return 0;
        }
    }
    return 1;
}

int hd2ct_utf8_to_wide(const char *input, size_t bytes, wchar_t *output, int output_count)
{
    int needed;
    if (input == NULL || bytes > INT_MAX || output_count <= 0) {
        return 0;
    }
    needed = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input, (int)bytes,
                                 output, output_count - 1);
    if (needed <= 0 || needed >= output_count) {
        return 0;
    }
    output[needed] = L'\0';
    return needed;
}

int hd2ct_wide_to_utf8(const wchar_t *input, size_t characters, char *output, int output_count)
{
    int needed;
    if (input == NULL || characters > INT_MAX || output_count <= 0) {
        return 0;
    }
    if (characters == 0) {
        output[0] = '\0';
        return 0;
    }
    needed = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input, (int)characters,
                                 output, output_count - 1, NULL, NULL);
    if (needed <= 0 || needed >= output_count) {
        return 0;
    }
    output[needed] = '\0';
    return needed;
}

int hd2ct_valid_token(const char *token, size_t *length_out)
{
    size_t length = hd2ct_bounded_length(token, HD2CT_MAX_TOKEN);
    size_t i;
    if (length == 0 || length > HD2CT_MAX_TOKEN) {
        return 0;
    }
    for (i = 0; i < length; ++i) {
        unsigned char c = (unsigned char)token[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-')) {
            return 0;
        }
    }
    *length_out = length;
    return 1;
}

void hd2ct_build_error(char *out, uint32_t capacity, uint32_t *bytes,
                              const char *code)
{
    static const char prefix[] = "ERR\n";
    size_t code_length = strlen(code);
    size_t total = sizeof(prefix) - 1u + code_length;
    if (total >= capacity) {
        total = 0;
    }
    memcpy(out, prefix, sizeof(prefix) - 1u);
    if (total != 0) {
        memcpy(out + sizeof(prefix) - 1u, code, code_length);
    }
    out[total] = '\0';
    *bytes = (uint32_t)total;
}

int hd2ct_is_only_space(const char *text, size_t length)
{
    wchar_t wide[HD2CT_MAX_TRANSLATION + 1u];
    int count = hd2ct_utf8_to_wide(text, length, wide,
                                   (int)(sizeof(wide) / sizeof(wide[0])));
    int i;
    if (count <= 0) {
        return length == 0;
    }
    for (i = 0; i < count; ++i) {
        wchar_t code = wide[i];
        int unicode_space = iswspace(code) != 0 || code == 0x0085 ||
            code == 0x00a0 || code == 0x1680 ||
            (code >= 0x2000 && code <= 0x200a) ||
            code == 0x2028 || code == 0x2029 || code == 0x202f ||
            code == 0x205f || code == 0x3000;
        if (!unicode_space) {
            return 0;
        }
    }
    return 1;
}

int hd2ct_has_escaped_nul(const char *json, size_t length)
{
    size_t i = 0;
    int in_string = 0;
    while (i < length) {
        unsigned char c = (unsigned char)json[i];
        if (!in_string) {
            if (c == '"') {
                in_string = 1;
            }
            ++i;
            continue;
        }
        if (c == '"') {
            in_string = 0;
            ++i;
            continue;
        }
        if (c == '\\') {
            if (i + 5u < length && json[i + 1u] == 'u' &&
                json[i + 2u] == '0' && json[i + 3u] == '0' &&
                json[i + 4u] == '0' && json[i + 5u] == '0') {
                return 1;
            }
            if (i + 1u < length) {
                i += 2u;
            } else {
                ++i;
            }
            continue;
        }
        ++i;
    }
    return 0;
}

int hd2ct_parse_complete_json(char *body, size_t body_bytes, cJSON **root_out)
{
    cJSON *root;
    const char *parse_end = NULL;
    body[body_bytes] = '\0';
    if (body_bytes == 0 || body_bytes > HD2CT_MAX_RESPONSE ||
        !hd2ct_valid_utf8((const unsigned char *)body, body_bytes, 0) ||
        hd2ct_has_escaped_nul(body, body_bytes)) return 0;
    root = cJSON_ParseWithLengthOpts(body, body_bytes, &parse_end, 0);
    if (root == NULL || parse_end == NULL) {
        cJSON_Delete(root);
        return 0;
    }
    while ((size_t)(parse_end - body) < body_bytes &&
           (*parse_end == ' ' || *parse_end == '\t' ||
            *parse_end == '\r' || *parse_end == '\n')) ++parse_end;
    if ((size_t)(parse_end - body) != body_bytes || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return 0;
    }
    *root_out = root;
    return 1;
}

int hd2ct_copy_valid_translation(const char *text, char *translation,
                                        uint32_t *translation_bytes)
{
    size_t length;
    if (text == NULL) return 0;
    length = strlen(text);
    if (length == 0 || length > HD2CT_MAX_TRANSLATION ||
        hd2ct_is_only_space(text, length) ||
        !hd2ct_valid_utf8((const unsigned char *)text, length, 1)) return 0;
    memcpy(translation, text, length);
    translation[length] = '\0';
    *translation_bytes = (uint32_t)length;
    return 1;
}

int hd2ct_json_error_code(const cJSON *item, char *code, size_t capacity)
{
    if (item == NULL || capacity < 2u) return 0;
    if (cJSON_IsString(item) && item->valuestring != NULL) {
        size_t length = strlen(item->valuestring);
        size_t i;
        if (length == 0 || length >= capacity) return 0;
        for (i = 0; i < length; ++i) {
            if (item->valuestring[i] < '0' || item->valuestring[i] > '9') return 0;
        }
        memcpy(code, item->valuestring, length + 1u);
        return 1;
    }
    if (cJSON_IsNumber(item) && item->valuedouble >= 0.0 &&
        item->valuedouble <= 99999999.0 &&
        item->valuedouble == (double)(uint32_t)item->valuedouble) {
        int written = snprintf(code, capacity, "%lu",
                               (unsigned long)(uint32_t)item->valuedouble);
        return written > 0 && (size_t)written < capacity;
    }
    return 0;
}
