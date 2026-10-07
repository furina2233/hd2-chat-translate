#include "hd2ct_http_internal.h"

#include <string.h>
#include <stdio.h>
#include <wchar.h>
static int hd2ct_ascii_equal_wide(const wchar_t *left, const wchar_t *right)
{
    return CompareStringOrdinal(left, -1, right, -1, TRUE) == CSTR_EQUAL;
}

static int hd2ct_local_ipv4(const wchar_t *host)
{
    unsigned values[4] = {0, 0, 0, 0};
    unsigned part = 0;
    const wchar_t *cursor = host;
    size_t digits = 0;
    while (*cursor != L'\0') {
        if (*cursor >= L'0' && *cursor <= L'9') {
            values[part] = values[part] * 10u + (unsigned)(*cursor - L'0');
            if (values[part] > 255u || ++digits > 3u) {
                return 0;
            }
        } else if (*cursor == L'.' && part < 3u && digits != 0) {
            ++part;
            digits = 0;
        } else {
            return 0;
        }
        ++cursor;
    }
    return part == 3u && digits != 0 && values[0] == 127u;
}

int hd2ct_local_http_host(const wchar_t *host)
{
    return hd2ct_ascii_equal_wide(host, L"localhost") ||
           hd2ct_local_ipv4(host) ||
           hd2ct_ascii_equal_wide(host, L"::1") ||
           hd2ct_ascii_equal_wide(host, L"[::1]");
}

int hd2ct_normalize_url(const char *input, uint32_t adapter_id,
                               char *normalized, size_t normalized_capacity)
{
    wchar_t wide_url[HD2CT_MAX_URL + 1u];
    wchar_t host[512];
    URL_COMPONENTS parts;
    const char *scheme_end;
    const char *authority_start;
    const char *authority_end;
    const char *path_start;
    size_t input_length = hd2ct_bounded_length(input, HD2CT_MAX_URL);
    size_t path_length;
    size_t prefix_length;
    size_t i;
    int wide_length;
    if (input_length > HD2CT_MAX_URL ||
        normalized_capacity <= input_length) {
        return 0;
    }
    if (adapter_id == HD2CT_ADAPTER_UNKNOWN) {
        memcpy(normalized, input, input_length + 1u);
        return 1;
    }
    if (input_length == 0 ||
        !hd2ct_valid_utf8((const unsigned char *)input, input_length, 1)) {
        return 0;
    }
    for (i = 0; i < input_length; ++i) {
        if (input[i] == '?' || input[i] == '#' || input[i] == '\\' ||
            (unsigned char)input[i] <= 0x20u || (unsigned char)input[i] == 0x7fu) {
            return 0;
        }
    }
    scheme_end = strstr(input, "://");
    if (scheme_end == NULL || scheme_end == input) {
        return 0;
    }
    if (!((scheme_end - input == 4 && _strnicmp(input, "http", 4) == 0) ||
          (scheme_end - input == 5 && _strnicmp(input, "https", 5) == 0))) {
        return 0;
    }
    authority_start = scheme_end + 3;
    authority_end = authority_start;
    while (*authority_end != '\0' && *authority_end != '/') {
        if (*authority_end == '@') {
            return 0;
        }
        ++authority_end;
    }
    if (authority_end == authority_start) {
        return 0;
    }
    prefix_length = (size_t)(authority_end - input);
    path_start = authority_end;
    path_length = strlen(path_start);
    wide_length = hd2ct_utf8_to_wide(input, input_length, wide_url,
                                     (int)(sizeof(wide_url) / sizeof(wide_url[0])));
    if (wide_length <= 0) {
        return 0;
    }
    memset(&parts, 0, sizeof(parts));
    parts.dwStructSize = sizeof(parts);
    parts.lpszHostName = host;
    parts.dwHostNameLength = (DWORD)(sizeof(host) / sizeof(host[0]));
    if (!WinHttpCrackUrl(wide_url, (DWORD)wide_length, 0, &parts) ||
        parts.dwUserNameLength != 0 || parts.dwPasswordLength != 0 ||
        parts.dwExtraInfoLength != 0 || parts.dwHostNameLength == 0 ||
        (parts.nScheme != INTERNET_SCHEME_HTTP && parts.nScheme != INTERNET_SCHEME_HTTPS)) {
        return 0;
    }
    if (parts.nScheme == INTERNET_SCHEME_HTTP &&
        !hd2ct_local_http_host(host)) {
        return 0;
    }
    if (path_length == 0 || (path_length == 1u && path_start[0] == '/')) {
        const HD2CT_Adapter *adapter = hd2ct_adapter_for_id(adapter_id);
        if (adapter == NULL || adapter->default_path == NULL) return 0;
        path_start = adapter->default_path;
        path_length = strlen(path_start);
    } else if (hd2ct_adapter_for_id(adapter_id) != NULL &&
               hd2ct_adapter_for_id(adapter_id)->base->ai_v1_completion_path &&
               ((path_length == 3u && memcmp(path_start, "/v1", 3u) == 0) ||
                (path_length == 4u && memcmp(path_start, "/v1/", 4u) == 0))) {
        path_start = "/v1/chat/completions";
        path_length = sizeof("/v1/chat/completions") - 1u;
    }
    if (prefix_length + path_length + 1u > normalized_capacity) {
        return 0;
    }
    memcpy(normalized, input, prefix_length);
    memcpy(normalized + prefix_length, path_start, path_length);
    normalized[prefix_length + path_length] = '\0';
    return 1;
}

int hd2ct_valid_secret(const char *value, size_t maximum)
{
    size_t length = hd2ct_bounded_length(value, maximum);
    size_t i;
    if (length == 0 || length > maximum ||
        !hd2ct_valid_utf8((const unsigned char *)value, length, 1)) {
        return 0;
    }
    for (i = 0; i < length; ++i) {
        unsigned char c = (unsigned char)value[i];
        if (c <= 0x20u || c == 0x7fu) return 0;
    }
    return 1;
}

int hd2ct_valid_model_key(const char *model, const char *api_key)
{
    size_t model_length = hd2ct_bounded_length(model, HD2CT_MAX_MODEL);
    if (model_length == 0 || model_length > HD2CT_MAX_MODEL ||
        !hd2ct_valid_utf8((const unsigned char *)model, model_length, 1) ||
        hd2ct_is_only_space(model, model_length) ||
        !hd2ct_valid_secret(api_key, HD2CT_MAX_KEY)) {
        return 0;
    }
    return 1;
}

static int hd2ct_read_registry_value(HKEY root, const wchar_t *subkey,
                                     const wchar_t *name, wchar_t *out,
                                     size_t out_count, int *present)
{
    DWORD type = 0;
    DWORD bytes = (DWORD)(out_count * sizeof(wchar_t));
    LSTATUS status;
    *present = 0;
    status = RegGetValueW(root, subkey, name,
                          RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ | RRF_NOEXPAND,
                          &type, out, &bytes);
    if (status == ERROR_FILE_NOT_FOUND || status == ERROR_PATH_NOT_FOUND) {
        return 1;
    }
    if (status != ERROR_SUCCESS || (type != REG_SZ && type != REG_EXPAND_SZ) ||
        bytes < sizeof(wchar_t) || bytes > out_count * sizeof(wchar_t)) {
        return 0;
    }
    out[out_count - 1u] = L'\0';
    if (wcsnlen(out, out_count) == out_count) {
        return 0;
    }
    *present = 1;
    return 1;
}

static int hd2ct_read_env_registry(const wchar_t *name, wchar_t *out, size_t out_count,
                                   int *present)
{
    static const wchar_t user_key[] = L"Environment";
    static const wchar_t machine_key[] =
        L"SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment";
    int found = 0;
    if (!hd2ct_read_registry_value(HKEY_CURRENT_USER, user_key, name, out, out_count, &found)) {
        return 0;
    }
    if (found) {
        *present = 1;
        return 1;
    }
    if (!hd2ct_read_registry_value(HKEY_LOCAL_MACHINE, machine_key, name, out, out_count, &found)) {
        return 0;
    }
    *present = found;
    return 1;
}

int hd2ct_read_environment_value(const wchar_t *name, char *out,
                                        size_t out_capacity, int *present)
{
    wchar_t wide_value[HD2CT_MAX_KEY + 1u];
    size_t wide_length;
    int bytes;
    int found = 0;
    if (!hd2ct_read_env_registry(name, wide_value,
                                 sizeof(wide_value) / sizeof(wide_value[0]), &found)) {
        return 0;
    }
    *present = found;
    if (!found) {
        out[0] = '\0';
        return 1;
    }
    wide_length = wcsnlen(wide_value, sizeof(wide_value) / sizeof(wide_value[0]));
    if (wide_length == 0) {
        out[0] = '\0';
        return 1;
    }
    bytes = hd2ct_wide_to_utf8(wide_value, wide_length, out, (int)out_capacity);
    return bytes > 0 && (size_t)bytes < out_capacity;
}

int hd2ct_parse_timeout(const char *value, uint32_t *timeout_out)
{
    uint32_t result = 0;
    size_t i;
    size_t length = strlen(value);
    if (length == 0 || length > 3u) {
        return 0;
    }
    for (i = 0; i < length; ++i) {
        if (value[i] < '0' || value[i] > '9') {
            return 0;
        }
        result = result * 10u + (uint32_t)(value[i] - '0');
    }
    if (result < 1u || result > 120u) {
        return 0;
    }
    *timeout_out = result;
    return 1;
}
