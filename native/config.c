#include "internal.h"

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
    const char *path_suffix = NULL;
    const HD2CT_Adapter *adapter;
    size_t input_length = hd2ct_bounded_length(input, HD2CT_MAX_URL);
    size_t path_length;
    size_t suffix_length = 0u;
    size_t prefix_length;
    size_t i;
    int wide_length;
    int path_ends_v1;
    if (input_length > HD2CT_MAX_URL ||
        normalized_capacity <= input_length) {
        return 0;
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
    path_ends_v1 =
        (path_length >= 3u &&
         memcmp(path_start + path_length - 3u, "/v1", 3u) == 0) ||
        (path_length >= 4u &&
         memcmp(path_start + path_length - 4u, "/v1/", 4u) == 0);
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
    adapter = hd2ct_adapter_for_id(adapter_id);
    if (adapter == NULL || adapter->base == NULL) return 0;
    if (path_length == 0 || (path_length == 1u && path_start[0] == '/')) {
        if (adapter->default_path != NULL) {
            path_start = adapter->default_path;
            path_length = strlen(path_start);
        }
    } else if (path_ends_v1 && adapter_id == HD2CT_ADAPTER_ANTHROPIC) {
        path_suffix = "/messages";
        suffix_length = sizeof("/messages") - 1u;
        if (path_length >= 4u && path_start[path_length - 1u] == '/') {
            --path_length;
        }
    } else if (path_ends_v1 && adapter->base->ai_v1_completion_path) {
        path_suffix = "/chat/completions";
        suffix_length = sizeof("/chat/completions") - 1u;
        if (path_length >= 4u && path_start[path_length - 1u] == '/') {
            --path_length;
        }
    }
    if (prefix_length + path_length + suffix_length + 1u > normalized_capacity) {
        return 0;
    }
    memcpy(normalized, input, prefix_length);
    memcpy(normalized + prefix_length, path_start, path_length);
    if (suffix_length != 0u) {
        memcpy(normalized + prefix_length + path_length, path_suffix,
               suffix_length);
    }
    normalized[prefix_length + path_length + suffix_length] = '\0';
    return 1;
}

#ifdef HD2CT_TESTING
int hd2ct_validate_test_machine_endpoint(const char *input,
                                         uint32_t adapter_id,
                                         char *normalized,
                                         size_t normalized_capacity)
{
    wchar_t wide_url[HD2CT_MAX_URL + 1u];
    wchar_t host[512];
    wchar_t path[HD2CT_MAX_URL + 1u];
    URL_COMPONENTS parts;
    size_t input_length = hd2ct_bounded_length(input, HD2CT_MAX_URL);
    int wide_length;
    if (input_length == 0u || input_length > HD2CT_MAX_URL ||
        !hd2ct_normalize_url(input, adapter_id,
                             normalized, normalized_capacity) ||
        strcmp(input, normalized) != 0) {
        return 0;
    }
    wide_length = hd2ct_utf8_to_wide(input, input_length, wide_url,
                                     (int)(sizeof(wide_url) / sizeof(wide_url[0])));
    if (wide_length <= 0) return 0;
    memset(&parts, 0, sizeof(parts));
    parts.dwStructSize = sizeof(parts);
    parts.lpszHostName = host;
    parts.dwHostNameLength = (DWORD)(sizeof(host) / sizeof(host[0]));
    parts.lpszUrlPath = path;
    parts.dwUrlPathLength = (DWORD)(sizeof(path) / sizeof(path[0]));
    if (!WinHttpCrackUrl(wide_url, (DWORD)wide_length, 0, &parts) ||
        parts.dwUserNameLength != 0 || parts.dwPasswordLength != 0 ||
        parts.dwExtraInfoLength != 0 || parts.dwHostNameLength == 0 ||
        parts.dwUrlPathLength <= 1u ||
        (parts.nScheme != INTERNET_SCHEME_HTTP &&
         parts.nScheme != INTERNET_SCHEME_HTTPS) ||
        !hd2ct_local_http_host(host)) {
        return 0;
    }
    return 1;
}
#endif

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

int hd2ct_valid_region(const char *value)
{
    size_t length = hd2ct_bounded_length(value, HD2CT_MAX_REGION);
    size_t i;
    if (length > HD2CT_MAX_REGION) return 0;
    for (i = 0; i < length; ++i) {
        unsigned char c = (unsigned char)value[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '-')) {
            return 0;
        }
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

uint32_t hd2ct_prepare_job_config(HD2CT_WorkerJob *job,
                                  uint32_t interface_type)
{
    char url[HD2CT_MAX_URL + 1u] = {0};
    char normalized[HD2CT_MAX_URL + 1u] = {0};
    char model[HD2CT_MAX_MODEL + 1u] = {0};
    char api_key[HD2CT_MAX_KEY + 1u] = {0};
    char app_id[HD2CT_MAX_KEY + 1u] = {0};
    char region[HD2CT_MAX_REGION + 1u] = {0};
    const char *config_url = NULL;
    uint32_t adapter_id = HD2CT_ADAPTER_UNKNOWN;
    uint32_t status = HD2CT_STATUS_INVALID_CONFIG;
    const HD2CT_Adapter *adapter;
    size_t url_length;
    size_t model_length;
    size_t key_length;
    size_t app_id_length;
    size_t region_length;
    int present = 0;
    int needs_model = 0;
    int needs_app_id = 0;
    int needs_region = 0;
    int needs_custom_url = 0;
    int is_machine = 0;

    if (job == NULL || interface_type < HD2CT_INTERFACE_FREE ||
        interface_type > HD2CT_INTERFACE_GOOGLE) {
        return HD2CT_STATUS_INVALID_CONFIG;
    }
    SecureZeroMemory(job->url, sizeof(job->url));
    SecureZeroMemory(job->model, sizeof(job->model));
    SecureZeroMemory(job->api_key, sizeof(job->api_key));
    SecureZeroMemory(job->app_id, sizeof(job->app_id));
    SecureZeroMemory(job->region, sizeof(job->region));
    SecureZeroMemory(job->cache_fingerprint,
                     sizeof(job->cache_fingerprint));
    job->cache_fingerprint_valid = 0u;
    job->interface_type = interface_type;
    job->adapter_id = HD2CT_ADAPTER_UNKNOWN;

    switch (interface_type) {
    case HD2CT_INTERFACE_FREE:
        adapter_id = HD2CT_ADAPTER_FREE;
        break;
    case HD2CT_INTERFACE_CHAT_COMPLETIONS:
        adapter_id = HD2CT_ADAPTER_AI;
        needs_model = 1;
        needs_custom_url = 1;
        break;
    case HD2CT_INTERFACE_ANTHROPIC:
        adapter_id = HD2CT_ADAPTER_ANTHROPIC;
        needs_model = 1;
        needs_custom_url = 1;
        break;
    case HD2CT_INTERFACE_BAIDU:
        adapter_id = HD2CT_ADAPTER_BAIDU;
        needs_app_id = 1;
        is_machine = 1;
        break;
    case HD2CT_INTERFACE_YOUDAO:
        adapter_id = HD2CT_ADAPTER_YOUDAO;
        needs_app_id = 1;
        is_machine = 1;
        break;
    case HD2CT_INTERFACE_BING_CN:
        adapter_id = HD2CT_ADAPTER_BING;
        needs_region = 1;
        is_machine = 1;
        break;
    case HD2CT_INTERFACE_BING_GLOBAL:
        adapter_id = HD2CT_ADAPTER_BING;
        needs_region = 1;
        is_machine = 1;
        break;
    case HD2CT_INTERFACE_GOOGLE:
        adapter_id = HD2CT_ADAPTER_GOOGLE;
        is_machine = 1;
        break;
    default:
        goto cleanup;
    }
    if (needs_custom_url) {
        if (!hd2ct_read_environment_value(L"HD2CT_API_URL", url,
                                          sizeof(url), &present)) {
            goto cleanup;
        }
        config_url = url;
    } else if (is_machine) {
        if (!hd2ct_machine_endpoint_for_interface(interface_type, url,
                                                  sizeof(url))) {
            goto cleanup;
        }
        config_url = url;
    }
    if (needs_model &&
        !hd2ct_read_environment_value(L"HD2CT_MODEL", model,
                                      sizeof(model), &present)) {
        goto cleanup;
    }
    if (interface_type != HD2CT_INTERFACE_FREE) {
        if (!hd2ct_read_environment_value(L"HD2CT_API_KEY", api_key,
                                          sizeof(api_key), &present)) {
            goto cleanup;
        }
    }
    if (needs_app_id &&
        !hd2ct_read_environment_value(L"HD2CT_APP_ID", app_id,
                                      sizeof(app_id), &present)) {
        goto cleanup;
    }
    if (needs_region &&
        !hd2ct_read_environment_value(L"HD2CT_REGION", region,
                                      sizeof(region), &present)) {
        goto cleanup;
    }

    url_length = config_url != NULL ?
        hd2ct_bounded_length(config_url, HD2CT_MAX_URL) : 0u;
    model_length = hd2ct_bounded_length(model, HD2CT_MAX_MODEL);
    key_length = hd2ct_bounded_length(api_key, HD2CT_MAX_KEY);
    app_id_length = hd2ct_bounded_length(app_id, HD2CT_MAX_KEY);
    region_length = hd2ct_bounded_length(region, HD2CT_MAX_REGION);
    if ((interface_type != HD2CT_INTERFACE_FREE && url_length == 0u) ||
        (interface_type != HD2CT_INTERFACE_FREE && key_length == 0u) ||
        (needs_model && model_length == 0u) ||
        (needs_app_id && app_id_length == 0u)) {
        status = HD2CT_STATUS_MISSING_CONFIG;
        goto cleanup;
    }
    if (url_length > HD2CT_MAX_URL || model_length > HD2CT_MAX_MODEL ||
        key_length > HD2CT_MAX_KEY || app_id_length > HD2CT_MAX_KEY ||
        region_length > HD2CT_MAX_REGION ||
        (needs_region && !hd2ct_valid_region(region))) {
        goto cleanup;
    }
    adapter = hd2ct_adapter_for_id(adapter_id);
    if (adapter == NULL || adapter->base == NULL) goto cleanup;
    if (needs_custom_url) {
        if (!hd2ct_normalize_url(config_url, adapter_id,
                                 normalized, sizeof(normalized))) {
            goto cleanup;
        }
    } else if (config_url != NULL) {
        memcpy(normalized, config_url, url_length + 1u);
    }
    if (adapter->base->family == HD2CT_FAMILY_AI) {
        if (!hd2ct_valid_model_key(model, api_key)) goto cleanup;
    } else if ((adapter->base->api_key_required &&
                !hd2ct_valid_secret(api_key, HD2CT_MAX_KEY)) ||
               (needs_app_id &&
                !hd2ct_valid_secret(app_id, HD2CT_MAX_KEY))) {
        goto cleanup;
    }

    memcpy(job->url, normalized, strlen(normalized) + 1u);
    memcpy(job->model, model, model_length + 1u);
    memcpy(job->api_key, api_key, key_length + 1u);
    memcpy(job->app_id, app_id, app_id_length + 1u);
    memcpy(job->region, region, region_length + 1u);
    job->adapter_id = adapter_id;
    status = HD2CT_STATUS_READY;

cleanup:
    SecureZeroMemory(url, sizeof(url));
    SecureZeroMemory(normalized, sizeof(normalized));
    SecureZeroMemory(model, sizeof(model));
    SecureZeroMemory(api_key, sizeof(api_key));
    SecureZeroMemory(app_id, sizeof(app_id));
    SecureZeroMemory(region, sizeof(region));
    return status;
}
