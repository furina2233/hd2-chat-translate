#include "../internal.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#if defined(HD2CT_BING_TEST_BASE_URL) && !defined(HD2CT_TESTING)
#error HD2CT_BING_TEST_BASE_URL is only available in testing builds
#endif

#define HD2CT_BING_CACHE_MS 600000ull
#define HD2CT_BING_COOLDOWN_MS 60000ull
#define HD2CT_BING_CACHE_REQUESTS 75u
#define HD2CT_BING_MAX_URL 2048u
#define HD2CT_BING_FORM_CAPACITY \
    (HD2CT_MAX_SOURCE * 3u + (HD2CT_BING_TOKEN_CAPACITY - 1u) * 3u + 256u)

typedef struct HD2CT_BingUrl {
    wchar_t *wide_url;
    wchar_t host[256];
    DWORD port;
    int secure;
    char origin[512];
} HD2CT_BingUrl;

static const wchar_t g_hd2ct_bing_user_agent[] =
    L"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    L"(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

static const char *hd2ct_bing_base_url(const HD2CT_BingState *state)
{
#ifdef HD2CT_BING_TEST_BASE_URL
    (void)state;
    return HD2CT_BING_TEST_BASE_URL;
#else
    return state != NULL && state->use_china_endpoint != 0u
               ? "https://cn.bing.com"
               : "https://www.bing.com";
#endif
}

static void hd2ct_bing_clear_credentials(HD2CT_BingState *state)
{
    SecureZeroMemory(state->ig, sizeof(state->ig));
    SecureZeroMemory(state->iid, sizeof(state->iid));
    SecureZeroMemory(state->key, sizeof(state->key));
    SecureZeroMemory(state->token, sizeof(state->token));
    state->initialized_ms = 0;
    state->translation_count = 0;
    state->ready = 0;
}

static void hd2ct_bing_close_session(HD2CT_BingState *state)
{
    if (state->session != NULL) {
        WinHttpCloseHandle(state->session);
        state->session = NULL;
    }
}

int hd2ct_bing_state_init(HD2CT_BingState *state)
{
    if (state == NULL) return 0;
    memset(state, 0, sizeof(*state));
    return 1;
}

void hd2ct_bing_state_destroy(HD2CT_BingState *state)
{
    if (state == NULL) return;
    hd2ct_bing_close_session(state);
    SecureZeroMemory(state, sizeof(*state));
}

static int hd2ct_bing_prepare_base(const HD2CT_BingState *state,
                                   HD2CT_BingUrl *url)
{
    const char *base = hd2ct_bing_base_url(state);
    size_t length = hd2ct_bounded_length(base, 511u);
    URL_COMPONENTS components;
    wchar_t username[128];
    wchar_t password[128];
    wchar_t path[HD2CT_BING_MAX_URL + 1u];
    wchar_t extra[512];
    if (length == 0u || length > 511u || strchr(base, '?') != NULL ||
        strchr(base, '#') != NULL || strchr(base, '\\') != NULL ||
        strchr(base, '@') != NULL) return 0;
    memset(url, 0, sizeof(*url));
    url->wide_url = (wchar_t *)calloc(length + 1u, sizeof(wchar_t));
    if (url->wide_url == NULL ||
        hd2ct_utf8_to_wide(base, length, url->wide_url, (int)(length + 1u)) <= 0) {
        free(url->wide_url);
        url->wide_url = NULL;
        return 0;
    }
    memset(&components, 0, sizeof(components));
    memset(username, 0, sizeof(username));
    memset(password, 0, sizeof(password));
    memset(path, 0, sizeof(path));
    memset(extra, 0, sizeof(extra));
    components.dwStructSize = sizeof(components);
    components.lpszHostName = url->host;
    components.dwHostNameLength = (DWORD)(sizeof(url->host) / sizeof(url->host[0]));
    components.lpszUserName = username;
    components.dwUserNameLength = (DWORD)(sizeof(username) / sizeof(username[0]));
    components.lpszPassword = password;
    components.dwPasswordLength = (DWORD)(sizeof(password) / sizeof(password[0]));
    components.lpszUrlPath = path;
    components.dwUrlPathLength = (DWORD)(sizeof(path) / sizeof(path[0]));
    components.lpszExtraInfo = extra;
    components.dwExtraInfoLength = (DWORD)(sizeof(extra) / sizeof(extra[0]));
    if (!WinHttpCrackUrl(url->wide_url, 0, 0, &components) ||
        components.dwHostNameLength == 0u || components.dwUserNameLength != 0u ||
        components.dwPasswordLength != 0u || components.dwExtraInfoLength != 0u ||
        (components.dwUrlPathLength != 0u &&
         !(components.dwUrlPathLength == 1u && components.lpszUrlPath[0] == L'/'))) {
        free(url->wide_url);
        url->wide_url = NULL;
        return 0;
    }
#ifdef HD2CT_BING_TEST_BASE_URL
    if (components.nScheme != INTERNET_SCHEME_HTTP ||
        (wcscmp(url->host, L"127.0.0.1") != 0 &&
         wcscmp(url->host, L"localhost") != 0 &&
         wcscmp(url->host, L"::1") != 0 &&
         wcscmp(url->host, L"[::1]") != 0)) {
        free(url->wide_url);
        url->wide_url = NULL;
        return 0;
    }
#else
    if (components.nScheme != INTERNET_SCHEME_HTTPS ||
        wcscmp(url->host, state != NULL && state->use_china_endpoint != 0u
                              ? L"cn.bing.com" : L"www.bing.com") != 0 ||
        components.nPort != INTERNET_DEFAULT_HTTPS_PORT) {
        free(url->wide_url);
        url->wide_url = NULL;
        return 0;
    }
#endif
    url->port = components.nPort;
    url->secure = components.nScheme == INTERNET_SCHEME_HTTPS;
    if (length + 1u > sizeof(url->origin)) {
        free(url->wide_url);
        url->wide_url = NULL;
        return 0;
    }
    memcpy(url->origin, base, length + 1u);
    while (length > 0u && url->origin[length - 1u] == '/') {
        url->origin[--length] = '\0';
    }
    return 1;
}

static void hd2ct_bing_free_base(HD2CT_BingUrl *url)
{
    if (url->wide_url != NULL) {
        free(url->wide_url);
        url->wide_url = NULL;
    }
}

static int hd2ct_bing_open_session(HD2CT_BingState *state)
{
    HD2CT_BingUrl base;
    memset(&base, 0, sizeof(base));
    if (!hd2ct_bing_prepare_base(state, &base)) return 0;
    state->session = WinHttpOpen(g_hd2ct_bing_user_agent,
                                 WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                                 WINHTTP_NO_PROXY_NAME,
                                 WINHTTP_NO_PROXY_BYPASS, 0);
    hd2ct_bing_free_base(&base);
    if (state->session == NULL) return 0;
    return 1;
}

static void hd2ct_bing_note_failure(HD2CT_BingState *state)
{
    hd2ct_bing_close_session(state);
    hd2ct_bing_clear_credentials(state);
    state->cooldown_until_ms = GetTickCount64() + HD2CT_BING_COOLDOWN_MS;
}

static int hd2ct_bing_ensure_session(HD2CT_BingState *state)
{
    if (state->session != NULL) return 1;
    if (!hd2ct_bing_open_session(state)) {
        state->cooldown_until_ms = GetTickCount64() + HD2CT_BING_COOLDOWN_MS;
        return 0;
    }
    return 1;
}

static int hd2ct_bing_ascii_space(unsigned char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

static const char *hd2ct_bing_skip_space(const char *text, const char *end)
{
    while (text < end && hd2ct_bing_ascii_space((unsigned char)*text)) ++text;
    return text;
}

static int hd2ct_bing_safe_id(const char *value, size_t length)
{
    size_t i;
    if (length == 0u || length >= HD2CT_BING_IID_CAPACITY) return 0;
    for (i = 0; i < length; ++i) {
        unsigned char c = (unsigned char)value[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-')) {
            return 0;
        }
    }
    return 1;
}

static int hd2ct_bing_extract_marker(const char *body, size_t body_bytes,
                                     const char *marker, char *value,
                                     size_t capacity, int require_unique)
{
    size_t marker_bytes = strlen(marker);
    size_t i;
    size_t found = SIZE_MAX;
    size_t count = 0;
    for (i = 0; i + marker_bytes <= body_bytes; ++i) {
        if (memcmp(body + i, marker, marker_bytes) == 0) {
            if (found == SIZE_MAX) found = i + marker_bytes;
            ++count;
        }
    }
    if (count == 0u || (require_unique && count != 1u) || found >= body_bytes) return 0;
    for (i = found; i < body_bytes && body[i] != '"'; ++i) {
        if (body[i] == '\\') return 0;
    }
    if (i >= body_bytes || i == found || i - found >= capacity ||
        !hd2ct_bing_safe_id(body + found, i - found)) return 0;
    memcpy(value, body + found, i - found);
    value[i - found] = '\0';
    return 1;
}

static int hd2ct_bing_unique_tree(const cJSON *node, unsigned depth,
                                 unsigned *node_count)
{
    const cJSON *item;
    const cJSON *other;
    unsigned child_count = 0;
    if (node == NULL || node_count == NULL || depth > 32u ||
        *node_count >= 4096u) return 0;
    ++*node_count;
    if (cJSON_IsObject(node)) {
        for (item = node->child; item != NULL; item = item->next) {
            if (++child_count > 64u) return 0;
        }
        for (item = node->child; item != NULL; item = item->next) {
            if (item->string == NULL) return 0;
            for (other = item->next; other != NULL; other = other->next) {
                if (other->string != NULL && strcmp(item->string, other->string) == 0) {
                    return 0;
                }
            }
            if (!hd2ct_bing_unique_tree(item, depth + 1u, node_count)) return 0;
        }
    } else if (cJSON_IsArray(node)) {
        for (item = node->child; item != NULL; item = item->next) {
            if (++child_count > 128u ||
                !hd2ct_bing_unique_tree(item, depth + 1u, node_count)) return 0;
        }
    }
    return 1;
}

static int hd2ct_bing_identifier_byte(unsigned char c)
{
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
           (c >= '0' && c <= '9') || c == '_' || c == '$';
}

static int hd2ct_bing_is_var_assignment(const char *body, size_t body_bytes,
                                        size_t name_start, size_t name_bytes,
                                        size_t *value_start)
{
    size_t before = name_start;
    const char *cursor;
    const char *end = body + body_bytes;
    if ((name_start > 0u &&
         hd2ct_bing_identifier_byte((unsigned char)body[name_start - 1u])) ||
        (name_start + name_bytes < body_bytes &&
         hd2ct_bing_identifier_byte((unsigned char)body[name_start + name_bytes]))) {
        return 0;
    }
    while (before > 0u && hd2ct_bing_ascii_space((unsigned char)body[before - 1u])) {
        --before;
    }
    if (before < 3u || memcmp(body + before - 3u, "var", 3u) != 0 ||
        (before > 3u &&
         hd2ct_bing_identifier_byte((unsigned char)body[before - 4u]))) return 0;
    cursor = hd2ct_bing_skip_space(body + name_start + name_bytes, end);
    if (cursor >= end || *cursor++ != '=') return 0;
    cursor = hd2ct_bing_skip_space(cursor, end);
    if (cursor >= end || *cursor != '[') return 0;
    *value_start = (size_t)(cursor - body);
    return 1;
}

static int hd2ct_bing_member_count(const cJSON *object, const char *name)
{
    const cJSON *item;
    int count = 0;
    for (item = object->child; item != NULL; item = item->next) {
        if (item->string != NULL && strcmp(item->string, name) == 0) ++count;
    }
    return count;
}

static int hd2ct_bing_find_abuse_params(const char *body, size_t body_bytes,
                                        char *key, size_t key_capacity,
                                        char *token, size_t token_capacity)
{
    static const char name[] = "params_AbusePreventionHelper";
    size_t name_bytes = sizeof(name) - 1u;
    size_t i;
    size_t occurrences = 0;
    size_t value_start = 0;
    const char *cursor;
    const char *array_start;
    const char *end = body + body_bytes;
    const char *parse_end = NULL;
    cJSON *array = NULL;
    cJSON *timestamp;
    cJSON *token_item;
    cJSON *lifetime;
    char timestamp_text[HD2CT_BING_KEY_CAPACITY];
    size_t timestamp_bytes = 0;
    uint64_t timestamp_value = 0;
    size_t token_bytes;
    size_t j;
    unsigned node_count = 0;
    int ok = 0;
    for (i = 0; i + name_bytes <= body_bytes; ++i) {
        size_t candidate_start = 0;
        if (memcmp(body + i, name, name_bytes) == 0 &&
            hd2ct_bing_is_var_assignment(body, body_bytes, i, name_bytes,
                                         &candidate_start)) {
            value_start = candidate_start;
            ++occurrences;
        }
    }
    if (occurrences != 1u) return 0;
    cursor = body + value_start;
    array_start = cursor;
    ++cursor;
    cursor = hd2ct_bing_skip_space(cursor, end);
    while (cursor < end && *cursor >= '0' && *cursor <= '9') {
        if (timestamp_bytes < sizeof(timestamp_text) - 1u) {
            timestamp_text[timestamp_bytes] = *cursor;
        }
        ++timestamp_bytes;
        ++cursor;
    }
    if (timestamp_bytes != 13u || timestamp_text[0] == '0') return 0;
    timestamp_text[13] = '\0';
    for (j = 0; j < timestamp_bytes; ++j) {
        timestamp_value = timestamp_value * 10u +
                          (uint64_t)(timestamp_text[j] - '0');
    }
    cursor = hd2ct_bing_skip_space(cursor, end);
    if (cursor >= end || *cursor != ',') return 0;
    array = cJSON_ParseWithLengthOpts(array_start, (size_t)(end - array_start),
                                      &parse_end, 0);
    if (array == NULL || parse_end == NULL || !cJSON_IsArray(array) ||
        cJSON_GetArraySize(array) != 3 ||
        hd2ct_has_escaped_nul(array_start, (size_t)(parse_end - array_start)) ||
        !hd2ct_bing_unique_tree(array, 0u, &node_count)) {
        goto cleanup;
    }
    timestamp = cJSON_GetArrayItem(array, 0);
    token_item = cJSON_GetArrayItem(array, 1);
    lifetime = cJSON_GetArrayItem(array, 2);
    if (!cJSON_IsNumber(timestamp) || timestamp->valuedouble != (double)timestamp_value ||
        !cJSON_IsString(token_item) || token_item->valuestring == NULL ||
        !cJSON_IsNumber(lifetime) || lifetime->valuedouble < 0.0 ||
        lifetime->valuedouble > 2147483647.0 ||
        lifetime->valuedouble != (double)(uint32_t)lifetime->valuedouble) {
        goto cleanup;
    }
    cursor = hd2ct_bing_skip_space(parse_end, end);
    if (cursor >= end || *cursor != ';') goto cleanup;
    token_bytes = hd2ct_bounded_length(token_item->valuestring,
                                      token_capacity == 0u ? 0u : token_capacity - 1u);
    if (token_bytes == 0u || token_bytes >= token_capacity ||
        !hd2ct_valid_utf8((const unsigned char *)token_item->valuestring,
                          token_bytes, 1)) goto cleanup;
    (void)snprintf(key, key_capacity, "%s", timestamp_text);
    memcpy(token, token_item->valuestring, token_bytes + 1u);
    ok = 1;

cleanup:
    cJSON_Delete(array);
    SecureZeroMemory(timestamp_text, sizeof(timestamp_text));
    return ok;
}

static int hd2ct_bing_parse_page(char *body, size_t body_bytes,
                                 HD2CT_BingState *state)
{
    char ig[HD2CT_BING_IG_CAPACITY] = {0};
    char iid[HD2CT_BING_IID_CAPACITY] = {0};
    char key[HD2CT_BING_KEY_CAPACITY] = {0};
    char token[HD2CT_BING_TOKEN_CAPACITY] = {0};
    int ok = 0;
    if (body == NULL || body_bytes == 0u || body_bytes > HD2CT_MAX_RESPONSE ||
        !hd2ct_valid_utf8((const unsigned char *)body, body_bytes, 0)) goto cleanup;
    /* 页面多个控件可以各有IID，使用第一个；会话IG仍要求唯一。 */
    if (!hd2ct_bing_extract_marker(body, body_bytes, "\",IG:\"", ig, sizeof(ig), 1) ||
        !hd2ct_bing_extract_marker(body, body_bytes, "data-iid=\"", iid, sizeof(iid), 0) ||
        !hd2ct_bing_find_abuse_params(body, body_bytes, key, sizeof(key),
                                      token, sizeof(token))) goto cleanup;
    memcpy(state->ig, ig, sizeof(ig));
    memcpy(state->iid, iid, sizeof(iid));
    memcpy(state->key, key, sizeof(key));
    memcpy(state->token, token, sizeof(token));
    state->initialized_ms = GetTickCount64();
    state->translation_count = 0;
    state->ready = 1;
    ok = 1;

cleanup:
    SecureZeroMemory(ig, sizeof(ig));
    SecureZeroMemory(iid, sizeof(iid));
    SecureZeroMemory(key, sizeof(key));
    SecureZeroMemory(token, sizeof(token));
    return ok;
}

static int hd2ct_bing_parse_json(char *body, size_t body_bytes, cJSON **root_out)
{
    const char *parse_end = NULL;
    const char *end;
    cJSON *root;
    unsigned node_count = 0;
    if (body == NULL || body_bytes == 0u || body_bytes > HD2CT_MAX_RESPONSE ||
        !hd2ct_valid_utf8((const unsigned char *)body, body_bytes, 0) ||
        hd2ct_has_escaped_nul(body, body_bytes)) return 0;
    body[body_bytes] = '\0';
    root = cJSON_ParseWithLengthOpts(body, body_bytes, &parse_end, 0);
    if (root == NULL || parse_end == NULL) {
        cJSON_Delete(root);
        return 0;
    }
    end = body + body_bytes;
    parse_end = hd2ct_bing_skip_space(parse_end, end);
    if (parse_end != end || !hd2ct_bing_unique_tree(root, 0u, &node_count)) {
        cJSON_Delete(root);
        return 0;
    }
    *root_out = root;
    return 1;
}

static int hd2ct_bing_parse_response(char *body, size_t body_bytes,
                                     const char *target, char *translation,
                                     uint32_t *translation_bytes,
                                     const char **failure_code)
{
    cJSON *root = NULL;
    cJSON *first;
    cJSON *translations;
    cJSON *item;
    cJSON *text;
    cJSON *to;
    int ok = 0;
    *failure_code = "BAD_RESPONSE";
    if (!hd2ct_bing_parse_json(body, body_bytes, &root) ||
        !cJSON_IsArray(root) || cJSON_GetArraySize(root) != 1) goto cleanup;
    first = cJSON_GetArrayItem(root, 0);
    if (!cJSON_IsObject(first) ||
        hd2ct_bing_member_count(first, "translations") != 1) goto cleanup;
    translations = cJSON_GetObjectItemCaseSensitive(first, "translations");
    if (!cJSON_IsArray(translations) || cJSON_GetArraySize(translations) != 1) goto cleanup;
    item = cJSON_GetArrayItem(translations, 0);
    if (!cJSON_IsObject(item) || hd2ct_bing_member_count(item, "text") != 1 ||
        hd2ct_bing_member_count(item, "to") != 1) goto cleanup;
    text = cJSON_GetObjectItemCaseSensitive(item, "text");
    to = cJSON_GetObjectItemCaseSensitive(item, "to");
    if (!cJSON_IsString(text) || text->valuestring == NULL ||
        !cJSON_IsString(to) || to->valuestring == NULL ||
        strcmp(to->valuestring, target) != 0) goto cleanup;
    ok = hd2ct_copy_valid_translation(text->valuestring, translation,
                                      translation_bytes);

cleanup:
    cJSON_Delete(root);
    return ok;
}

static int hd2ct_bing_build_path(const HD2CT_BingState *state,
                                 uint32_t sequence, char *path,
                                 size_t path_capacity)
{
    char iid[HD2CT_BING_IID_CAPACITY + 16u];
    int written;
    written = snprintf(iid, sizeof(iid), "%s.%lu", state->iid,
                       (unsigned long)sequence);
    if (written <= 0 || (size_t)written >= sizeof(iid)) return 0;
    written = snprintf(path, path_capacity,
                       "/ttranslatev3?isVertical=1&&IG=%s&IID=%s",
                       state->ig, iid);
    SecureZeroMemory(iid, sizeof(iid));
    return written > 0 && (size_t)written < path_capacity;
}

static int hd2ct_bing_build_form(const HD2CT_BingState *state,
                                 const HD2CT_WorkerJob *job,
                                 const char *target, char **body_out,
                                 DWORD *body_bytes_out)
{
    HD2CT_FormBuffer form;
    size_t source_bytes = hd2ct_bounded_length(job->source, HD2CT_MAX_SOURCE);
    char *data;
    memset(&form, 0, sizeof(form));
    if (source_bytes == 0u || source_bytes > HD2CT_MAX_SOURCE ||
        source_bytes != job->source_bytes ||
        !hd2ct_valid_utf8((const unsigned char *)job->source, source_bytes, 0)) return 0;
    data = (char *)calloc(HD2CT_BING_FORM_CAPACITY, 1u);
    if (data == NULL) return 0;
    form.data = data;
    form.capacity = HD2CT_BING_FORM_CAPACITY;
    if (!hd2ct_form_add(&form, "fromLang", "auto-detect", 11u) ||
        !hd2ct_form_add(&form, "text", job->source, source_bytes) ||
        !hd2ct_form_add(&form, "to", target, strlen(target)) ||
        !hd2ct_form_add(&form, "key", state->key, strlen(state->key)) ||
        !hd2ct_form_add(&form, "token", state->token, strlen(state->token)) ||
        form.used == 0u || form.used > MAXDWORD) {
        SecureZeroMemory(data, HD2CT_BING_FORM_CAPACITY);
        free(data);
        return 0;
    }
    *body_out = data;
    *body_bytes_out = (DWORD)form.used;
    return 1;
}

#ifndef HD2CT_BING_TEST_BASE_URL
static int hd2ct_bing_host_is_cn(const wchar_t *host, size_t host_length)
{
    static const wchar_t expected[] = L"cn.bing.com";
    size_t i;
    if (host_length != sizeof(expected) / sizeof(expected[0]) - 1u) return 0;
    for (i = 0; i < host_length; ++i) {
        wchar_t c = host[i];
        if (c >= L'A' && c <= L'Z') c = (wchar_t)(c - L'A' + L'a');
        if (c != expected[i]) return 0;
    }
    return 1;
}

static int hd2ct_bing_location_is_cn_translator(HINTERNET request)
{
    wchar_t location[HD2CT_BING_MAX_URL + 1u];
    wchar_t scheme[16];
    wchar_t host[256];
    wchar_t username[128];
    wchar_t password[128];
    wchar_t path[HD2CT_BING_MAX_URL + 1u];
    wchar_t extra[HD2CT_BING_MAX_URL + 1u];
    URL_COMPONENTS components;
    DWORD location_bytes = sizeof(location);
    size_t location_length = 0;
    DWORD i;
    int valid = 0;
    memset(location, 0, sizeof(location));
    memset(scheme, 0, sizeof(scheme));
    memset(host, 0, sizeof(host));
    memset(username, 0, sizeof(username));
    memset(password, 0, sizeof(password));
    memset(path, 0, sizeof(path));
    memset(extra, 0, sizeof(extra));
    if (!WinHttpQueryHeaders(request, WINHTTP_QUERY_LOCATION,
                             WINHTTP_HEADER_NAME_BY_INDEX, location,
                             &location_bytes, WINHTTP_NO_HEADER_INDEX) ||
        location_bytes > sizeof(location) ||
        location_bytes < sizeof(wchar_t) ||
        location_bytes % sizeof(wchar_t) != 0u) goto cleanup;
    while (location_length < sizeof(location) / sizeof(location[0]) &&
           location[location_length] != L'\0') ++location_length;
    if (location_length == 0u ||
        location_length == sizeof(location) / sizeof(location[0])) goto cleanup;
    memset(&components, 0, sizeof(components));
    components.dwStructSize = sizeof(components);
    components.lpszScheme = scheme;
    components.dwSchemeLength = (DWORD)(sizeof(scheme) / sizeof(scheme[0]));
    components.lpszHostName = host;
    components.dwHostNameLength = (DWORD)(sizeof(host) / sizeof(host[0]));
    components.lpszUserName = username;
    components.dwUserNameLength = (DWORD)(sizeof(username) / sizeof(username[0]));
    components.lpszPassword = password;
    components.dwPasswordLength = (DWORD)(sizeof(password) / sizeof(password[0]));
    components.lpszUrlPath = path;
    components.dwUrlPathLength = (DWORD)(sizeof(path) / sizeof(path[0]));
    components.lpszExtraInfo = extra;
    components.dwExtraInfoLength = (DWORD)(sizeof(extra) / sizeof(extra[0]));
    if (!WinHttpCrackUrl(location, 0, 0, &components) ||
        components.nScheme != INTERNET_SCHEME_HTTPS ||
        components.nPort != INTERNET_DEFAULT_HTTPS_PORT ||
        !hd2ct_bing_host_is_cn(host, components.dwHostNameLength) ||
        components.dwUserNameLength != 0u || components.dwPasswordLength != 0u ||
        components.dwUrlPathLength != 11u || wcscmp(path, L"/translator") != 0) {
        goto cleanup;
    }
    for (i = 0; i < components.dwExtraInfoLength; ++i) {
        if (extra[i] == L'#' || (i == 0u && extra[i] != L'?')) goto cleanup;
    }
    valid = 1;

cleanup:
    SecureZeroMemory(location, sizeof(location));
    SecureZeroMemory(scheme, sizeof(scheme));
    SecureZeroMemory(host, sizeof(host));
    SecureZeroMemory(username, sizeof(username));
    SecureZeroMemory(password, sizeof(password));
    SecureZeroMemory(path, sizeof(path));
    SecureZeroMemory(extra, sizeof(extra));
    return valid;
}
#endif

static int hd2ct_bing_http(HD2CT_BingState *state, const HD2CT_WorkerJob *job,
                           const wchar_t *method, const char *path,
                           const char *body, DWORD body_bytes,
                           uint32_t post_sequence, int *request_attempted,
                           uint64_t deadline, char **response_out,
                           size_t *response_bytes_out, const char **failure_code)
{
    HD2CT_BingUrl base;
    HINTERNET connection = NULL;
    HINTERNET request = NULL;
    wchar_t path_wide[HD2CT_BING_MAX_URL + 1u];
    wchar_t headers_wide[2048];
    char headers_utf8[2048];
    char *response = NULL;
    size_t response_used = 0;
    DWORD status_code = 0;
    DWORD status_size = sizeof(status_code);
    DWORD disable_features = WINHTTP_DISABLE_AUTHENTICATION |
                             WINHTTP_DISABLE_REDIRECTS;
    int path_chars;
    int header_chars;
    int is_post = wcscmp(method, L"POST") == 0;
    int ok = 0;
    memset(&base, 0, sizeof(base));
    memset(path_wide, 0, sizeof(path_wide));
    memset(headers_wide, 0, sizeof(headers_wide));
    memset(headers_utf8, 0, sizeof(headers_utf8));
    *response_out = NULL;
    *response_bytes_out = 0;
    *failure_code = "NETWORK";
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        *failure_code = "CANCELLED";
        goto cleanup;
    }
    if (state->session == NULL || !hd2ct_bing_prepare_base(state, &base)) goto cleanup;
    path_chars = hd2ct_utf8_to_wide(path, strlen(path), path_wide,
                                    (int)(sizeof(path_wide) / sizeof(path_wide[0])));
    if (path_chars <= 0) goto cleanup;
    if (is_post) {
        int written = snprintf(headers_utf8, sizeof(headers_utf8),
                               "Accept: */*\r\nAccept-Language: en-US,en;q=0.9\r\n"
                               "Referer: %s/translator\r\nOrigin: %s\r\n"
                               "Content-Type: application/x-www-form-urlencoded; charset=UTF-8\r\n",
                               base.origin, base.origin);
        if (written <= 0 || (size_t)written >= sizeof(headers_utf8)) goto cleanup;
        header_chars = hd2ct_utf8_to_wide(headers_utf8, (size_t)written,
                                          headers_wide,
                                          (int)(sizeof(headers_wide) / sizeof(headers_wide[0])));
    } else {
        int written = snprintf(headers_utf8, sizeof(headers_utf8),
                               "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r\n"
                               "Accept-Language: en-US,en;q=0.9\r\n");
        if (written <= 0 || (size_t)written >= sizeof(headers_utf8)) goto cleanup;
        header_chars = hd2ct_utf8_to_wide(headers_utf8, (size_t)written,
                                          headers_wide,
                                          (int)(sizeof(headers_wide) / sizeof(headers_wide[0])));
    }
    if (header_chars <= 0 || !hd2ct_set_remaining_timeouts(state->session, deadline)) {
        *failure_code = hd2ct_remaining_ms(deadline) == 0u ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    connection = WinHttpConnect(state->session, base.host, base.port, 0);
    if (connection == NULL) {
        *failure_code = hd2ct_winhttp_failure(deadline, GetLastError());
        goto cleanup;
    }
    request = WinHttpOpenRequest(connection, method, path_wide,
                                 NULL, WINHTTP_NO_REFERER,
                                 WINHTTP_DEFAULT_ACCEPT_TYPES,
                                 base.secure ? WINHTTP_FLAG_SECURE : 0);
    if (request == NULL) {
        *failure_code = hd2ct_winhttp_failure(deadline, GetLastError());
        goto cleanup;
    }
    if (!WinHttpSetOption(request, WINHTTP_OPTION_DISABLE_FEATURE,
                          &disable_features, sizeof(disable_features))) goto cleanup;
    if (!hd2ct_set_remaining_timeouts(request, deadline)) {
        *failure_code = hd2ct_remaining_ms(deadline) == 0u ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        *failure_code = "CANCELLED";
        goto cleanup;
    }
    if (is_post) {
        state->translation_count = post_sequence;
        if (request_attempted != NULL) *request_attempted = 1;
    }
    if (!WinHttpSendRequest(request, headers_wide, (DWORD)header_chars,
                            is_post ? (LPVOID)body : WINHTTP_NO_REQUEST_DATA,
                            body_bytes, body_bytes, 0)) {
        *failure_code = hd2ct_winhttp_failure(deadline, GetLastError());
        goto cleanup;
    }
    if (!hd2ct_set_remaining_timeouts(request, deadline)) {
        *failure_code = hd2ct_remaining_ms(deadline) == 0u ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        *failure_code = "CANCELLED";
        goto cleanup;
    }
    if (!WinHttpReceiveResponse(request, NULL)) {
        *failure_code = hd2ct_winhttp_failure(deadline, GetLastError());
        goto cleanup;
    }
    if (!hd2ct_set_remaining_timeouts(request, deadline)) {
        *failure_code = hd2ct_remaining_ms(deadline) == 0u ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    if (!WinHttpQueryHeaders(request,
                             WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                             WINHTTP_HEADER_NAME_BY_INDEX,
                             &status_code, &status_size, WINHTTP_NO_HEADER_INDEX)) {
        *failure_code = hd2ct_winhttp_failure(deadline, GetLastError());
        goto cleanup;
    }
    if (status_code != 200u) {
#ifndef HD2CT_BING_TEST_BASE_URL
        if (!is_post && strcmp(path, "/translator") == 0 &&
            state->use_china_endpoint == 0u &&
            (status_code == 301u || status_code == 302u || status_code == 303u ||
             status_code == 307u || status_code == 308u) &&
            hd2ct_bing_location_is_cn_translator(request)) {
            state->use_china_endpoint = 1u;
            *failure_code = "REGION_REDIRECT";
            goto cleanup;
        }
#endif
        *failure_code = "HTTP_ERROR";
        goto cleanup;
    }
    response = (char *)malloc(HD2CT_MAX_RESPONSE + 1u);
    if (response == NULL) {
        *failure_code = "INTERNAL";
        goto cleanup;
    }
    for (;;) {
        DWORD available = 0;
        DWORD received = 0;
        if (!hd2ct_set_remaining_timeouts(request, deadline)) {
            *failure_code = hd2ct_remaining_ms(deadline) == 0u ? "TIMEOUT" : "NETWORK";
            goto cleanup;
        }
        if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
            *failure_code = "CANCELLED";
            goto cleanup;
        }
        if (!WinHttpQueryDataAvailable(request, &available)) {
            *failure_code = hd2ct_winhttp_failure(deadline, GetLastError());
            goto cleanup;
        }
        if (available == 0u) break;
        if ((uint64_t)response_used + available > HD2CT_MAX_RESPONSE) {
            *failure_code = "RESPONSE_TOO_LARGE";
            goto cleanup;
        }
        if (!hd2ct_set_remaining_timeouts(request, deadline)) {
            *failure_code = hd2ct_remaining_ms(deadline) == 0u ? "TIMEOUT" : "NETWORK";
            goto cleanup;
        }
        if (!WinHttpReadData(request, response + response_used, available, &received)) {
            *failure_code = hd2ct_winhttp_failure(deadline, GetLastError());
            goto cleanup;
        }
        if (received == 0u) break;
        response_used += received;
    }
    if (response_used == 0u) {
        *failure_code = "BAD_RESPONSE";
        goto cleanup;
    }
    response[response_used] = '\0';
    *response_out = response;
    *response_bytes_out = response_used;
    response = NULL;
    *failure_code = NULL;
    ok = 1;

cleanup:
    if (request != NULL) WinHttpCloseHandle(request);
    if (connection != NULL) WinHttpCloseHandle(connection);
    hd2ct_bing_free_base(&base);
    if (response != NULL) {
        SecureZeroMemory(response, HD2CT_MAX_RESPONSE + 1u);
        free(response);
    }
    SecureZeroMemory(path_wide, sizeof(path_wide));
    SecureZeroMemory(headers_wide, sizeof(headers_wide));
    SecureZeroMemory(headers_utf8, sizeof(headers_utf8));
    return ok;
}

static int hd2ct_bing_target(const HD2CT_WorkerJob *job,
                             const char **target_out)
{
    const HD2CT_TargetLanguage *language = hd2ct_target_language(job->target_language);
    if (language == NULL || language->id == NULL || language->google_code == NULL) return 0;
    if (strcmp(language->id, "zh_cn") == 0) *target_out = "zh-Hans";
    else if (strcmp(language->id, "zh_tw") == 0) *target_out = "zh-Hant";
    else *target_out = language->google_code;
    return 1;
}

static int hd2ct_bing_load_credentials(HD2CT_BingContext *context)
{
    HD2CT_BingState *state = context->state;
    char *html = NULL;
    size_t html_bytes = 0;
    const char *failure = "NETWORK";
    int ok = 0;
    if (!hd2ct_bing_http(state, context->job, L"GET", "/translator",
                         NULL, 0u, 0u, NULL, context->deadline,
                         &html, &html_bytes,
                         &failure)) {
        if (failure == NULL || strcmp(failure, "REGION_REDIRECT") != 0 ||
            state->use_china_endpoint == 0u) {
            *context->failure_code = failure != NULL ? failure : "NETWORK";
            goto cleanup;
        }
        failure = "NETWORK";
        if (!hd2ct_bing_http(state, context->job, L"GET", "/translator",
                             NULL, 0u, 0u, NULL, context->deadline,
                             &html, &html_bytes, &failure)) {
            *context->failure_code = failure != NULL ? failure : "NETWORK";
            goto cleanup;
        }
    }
    if (!hd2ct_bing_parse_page(html, html_bytes, state)) {
        *context->failure_code = "BAD_RESPONSE";
        goto cleanup;
    }
    ok = 1;

cleanup:
    if (html != NULL) {
        SecureZeroMemory(html, HD2CT_MAX_RESPONSE + 1u);
        free(html);
    }
    return ok;
}

int hd2ct_bing_translate(HD2CT_BingContext *context, char *translation,
                         uint32_t *translation_bytes)
{
    HD2CT_BingState *state;
    const char *target = NULL;
    char path[HD2CT_BING_MAX_URL + 1u];
    char *form = NULL;
    DWORD form_bytes = 0;
    char *response = NULL;
    size_t response_bytes = 0;
    const char *failure = "NETWORK";
    int result = HD2CT_BING_FALLBACK;
    uint32_t sequence;
    if (context == NULL || context->state == NULL || context->job == NULL ||
        context->request_attempted == NULL || context->failure_code == NULL ||
        translation == NULL || translation_bytes == NULL) return HD2CT_BING_FALLBACK;
    state = context->state;
    *context->request_attempted = 0;
    *context->failure_code = "NETWORK";
    if (hd2ct_job_cancelled(context->job->slot_index, context->job->serial)) {
        *context->failure_code = "CANCELLED";
        return HD2CT_BING_CANCELLED;
    }
    if (hd2ct_remaining_ms(context->deadline) == 0u) {
        *context->failure_code = "TIMEOUT";
        hd2ct_bing_note_failure(state);
        return HD2CT_BING_FALLBACK;
    }
    if (state->cooldown_until_ms > GetTickCount64()) return HD2CT_BING_FALLBACK;
    if (state->ready != 0u &&
        (GetTickCount64() - state->initialized_ms >= HD2CT_BING_CACHE_MS ||
         state->translation_count >= HD2CT_BING_CACHE_REQUESTS)) {
        hd2ct_bing_close_session(state);
        hd2ct_bing_clear_credentials(state);
    }
    if (!hd2ct_bing_ensure_session(state)) {
        *context->failure_code = "NETWORK";
        return HD2CT_BING_FALLBACK;
    }
    if (state->ready == 0u && !hd2ct_bing_load_credentials(context)) {
        if (strcmp(*context->failure_code, "CANCELLED") == 0 ||
            hd2ct_job_cancelled(context->job->slot_index, context->job->serial)) {
            *context->failure_code = "CANCELLED";
            return HD2CT_BING_CANCELLED;
        }
        hd2ct_bing_note_failure(state);
        return HD2CT_BING_FALLBACK;
    }
    if (hd2ct_job_cancelled(context->job->slot_index, context->job->serial)) {
        *context->failure_code = "CANCELLED";
        return HD2CT_BING_CANCELLED;
    }
    if (hd2ct_remaining_ms(context->deadline) == 0u) {
        *context->failure_code = "TIMEOUT";
        hd2ct_bing_note_failure(state);
        return HD2CT_BING_FALLBACK;
    }
    if (!hd2ct_bing_target(context->job, &target) ||
        !hd2ct_bing_build_form(state, context->job, target, &form, &form_bytes)) {
        *context->failure_code = "BAD_RESPONSE";
        hd2ct_bing_note_failure(state);
        goto cleanup;
    }
    if (!hd2ct_take_rate_slot()) {
        *context->failure_code = "RATE_LIMITED";
        result = HD2CT_BING_RATE_LIMITED;
        goto cleanup;
    }
    sequence = state->translation_count + 1u;
    if (sequence == 0u || !hd2ct_bing_build_path(state, sequence,
                                                 path, sizeof(path))) {
        *context->failure_code = "BAD_RESPONSE";
        hd2ct_bing_note_failure(state);
        goto cleanup;
    }
    if (!hd2ct_bing_http(state, context->job, L"POST", path,
                         form, form_bytes, sequence,
                         context->request_attempted, context->deadline,
                         &response, &response_bytes, &failure)) {
        *context->failure_code = failure != NULL ? failure : "NETWORK";
        if (strcmp(*context->failure_code, "CANCELLED") == 0 ||
            hd2ct_job_cancelled(context->job->slot_index, context->job->serial)) {
            *context->failure_code = "CANCELLED";
            result = HD2CT_BING_CANCELLED;
            goto cleanup;
        }
        hd2ct_bing_note_failure(state);
        goto cleanup;
    }
    if (!hd2ct_bing_parse_response(response, response_bytes, target,
                                   translation, translation_bytes, &failure)) {
        *context->failure_code = failure != NULL ? failure : "BAD_RESPONSE";
        hd2ct_bing_note_failure(state);
        goto cleanup;
    }
    *context->failure_code = NULL;
    result = HD2CT_BING_TRANSLATED;

cleanup:
    if (form != NULL) {
        SecureZeroMemory(form, HD2CT_BING_FORM_CAPACITY);
        free(form);
    }
    if (response != NULL) {
        SecureZeroMemory(response, HD2CT_MAX_RESPONSE + 1u);
        free(response);
    }
    SecureZeroMemory(path, sizeof(path));
    return result;
}

#ifdef HD2CT_TESTING
int hd2ct_bing_test_parse_page(const char *html, size_t html_bytes,
                               char *ig, size_t ig_capacity,
                               char *iid, size_t iid_capacity,
                               char *key, size_t key_capacity,
                               char *token, size_t token_capacity)
{
    HD2CT_BingState state;
    char *copy;
    int ok;
    if (html == NULL || html_bytes == 0u || html_bytes > HD2CT_MAX_RESPONSE ||
        ig == NULL || iid == NULL || key == NULL || token == NULL ||
        ig_capacity < HD2CT_BING_IG_CAPACITY ||
        iid_capacity < HD2CT_BING_IID_CAPACITY ||
        key_capacity < HD2CT_BING_KEY_CAPACITY ||
        token_capacity < HD2CT_BING_TOKEN_CAPACITY) return 0;
    copy = (char *)malloc(html_bytes + 1u);
    if (copy == NULL) return 0;
    memcpy(copy, html, html_bytes);
    copy[html_bytes] = '\0';
    memset(&state, 0, sizeof(state));
    ok = hd2ct_bing_parse_page(copy, html_bytes, &state);
    if (ok) {
        memcpy(ig, state.ig, sizeof(state.ig));
        memcpy(iid, state.iid, sizeof(state.iid));
        memcpy(key, state.key, sizeof(state.key));
        memcpy(token, state.token, sizeof(state.token));
    }
    SecureZeroMemory(&state, sizeof(state));
    SecureZeroMemory(copy, html_bytes + 1u);
    free(copy);
    return ok;
}

int hd2ct_bing_test_parse_response(const char *body, size_t body_bytes,
                                   const char *target, char *translation,
                                   uint32_t *translation_bytes,
                                   const char **failure_code)
{
    char *copy;
    int ok;
    if (body == NULL || target == NULL || translation == NULL ||
        translation_bytes == NULL || failure_code == NULL ||
        body_bytes == 0u || body_bytes > HD2CT_MAX_RESPONSE) return 0;
    copy = (char *)malloc(body_bytes + 1u);
    if (copy == NULL) return 0;
    memcpy(copy, body, body_bytes);
    copy[body_bytes] = '\0';
    ok = hd2ct_bing_parse_response(copy, body_bytes, target,
                                   translation, translation_bytes,
                                   failure_code);
    SecureZeroMemory(copy, body_bytes + 1u);
    free(copy);
    return ok;
}
#endif
