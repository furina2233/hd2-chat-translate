#include "internal.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

typedef struct HD2CT_UrlParts {
    wchar_t *wide_url;
    wchar_t host[512];
    wchar_t path[HD2CT_MAX_URL + 1u];
    URL_COMPONENTS components;
    DWORD port;
    int secure;
} HD2CT_UrlParts;

uint32_t hd2ct_remaining_ms(uint64_t deadline)
{
    ULONGLONG now = GetTickCount64();
    ULONGLONG remaining;
    if (now >= deadline) {
        return 0;
    }
    remaining = deadline - now;
    if (remaining > 0x7fffffffu) {
        remaining = 0x7fffffffu;
    }
    return (uint32_t)remaining;
}

int hd2ct_set_remaining_timeouts(HINTERNET handle, uint64_t deadline)
{
    uint32_t remaining = hd2ct_remaining_ms(deadline);
    DWORD timeout_value = remaining;
    if (remaining == 0) {
        return 0;
    }
    if (!WinHttpSetTimeouts(handle, (int)remaining, (int)remaining,
                            (int)remaining, (int)remaining)) {
        return 0;
    }
    /* 头部和数据读取的选项让请求句柄遵守本 job 的剩余总时限。 */
    if (!WinHttpSetOption(handle, WINHTTP_OPTION_RECEIVE_TIMEOUT,
                          &timeout_value, sizeof(timeout_value)) ||
        !WinHttpSetOption(handle, WINHTTP_OPTION_RECEIVE_RESPONSE_TIMEOUT,
                          &timeout_value, sizeof(timeout_value))) {
        return 0;
    }
    return 1;
}

const char *hd2ct_winhttp_failure(uint64_t deadline, DWORD error)
{
    if (error == ERROR_WINHTTP_TIMEOUT || hd2ct_remaining_ms(deadline) == 0) {
        return "TIMEOUT";
    }
    return "NETWORK";
}

static int hd2ct_prepare_url_parts(const char *url, HD2CT_UrlParts *out)
{
    size_t length = strlen(url);
    memset(out, 0, sizeof(*out));
    out->wide_url = (wchar_t *)calloc(length + 1u, sizeof(wchar_t));
    if (out->wide_url == NULL ||
        hd2ct_utf8_to_wide(url, length, out->wide_url, (int)(length + 1u)) <= 0) {
        free(out->wide_url);
        out->wide_url = NULL;
        return 0;
    }
    out->components.dwStructSize = sizeof(out->components);
    out->components.lpszHostName = out->host;
    out->components.dwHostNameLength = (DWORD)(sizeof(out->host) / sizeof(out->host[0]));
    out->components.lpszUrlPath = out->path;
    out->components.dwUrlPathLength = (DWORD)(sizeof(out->path) / sizeof(out->path[0]));
    if (!WinHttpCrackUrl(out->wide_url, 0, 0, &out->components) ||
        out->components.dwUserNameLength != 0 ||
        out->components.dwPasswordLength != 0 ||
        out->components.dwExtraInfoLength != 0 ||
        out->components.dwHostNameLength == 0 ||
        (out->components.nScheme != INTERNET_SCHEME_HTTP &&
         out->components.nScheme != INTERNET_SCHEME_HTTPS)) {
        free(out->wide_url);
        out->wide_url = NULL;
        return 0;
    }
    if (out->components.nScheme == INTERNET_SCHEME_HTTP &&
        !hd2ct_local_http_host(out->host)) {
        free(out->wide_url);
        out->wide_url = NULL;
        return 0;
    }
    out->port = out->components.nPort;
    out->secure = out->components.nScheme == INTERNET_SCHEME_HTTPS;
    return 1;
}

static void hd2ct_free_url_parts(HD2CT_UrlParts *parts)
{
    if (parts->wide_url != NULL) {
        free(parts->wide_url);
        parts->wide_url = NULL;
    }
}

void hd2ct_http_worker_request(HINTERNET session, const HD2CT_WorkerJob *job,
                                      char *result, uint32_t *result_bytes,
                                      int *successful, int *request_attempted,
                                      uint64_t request_deadline)
{
    HD2CT_UrlParts url_parts;
    HD2CT_BuiltRequest built_request;
    const HD2CT_Adapter *adapter = hd2ct_adapter_for_id(job->adapter_id);
    HINTERNET connection = NULL;
    HINTERNET request = NULL;
    wchar_t headers_wide[HD2CT_MAX_KEY + 256u];
    char headers_utf8[HD2CT_MAX_KEY + 256u];
    char *response_body = NULL;
    size_t response_used = 0;
    DWORD status_code = 0;
    DWORD status_size = sizeof(status_code);
    DWORD disable_features = WINHTTP_DISABLE_COOKIES |
                             WINHTTP_DISABLE_AUTHENTICATION |
                             WINHTTP_DISABLE_REDIRECTS;
    size_t headers_utf8_bytes = 0;
    int header_chars;
    int ok = 0;
    const char *failure_code = "NETWORK";
    memset(&url_parts, 0, sizeof(url_parts));
    memset(&built_request, 0, sizeof(built_request));
    *successful = 0;
    *request_attempted = 0;
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        failure_code = "CANCELLED";
        goto cleanup;
    }
    if (!hd2ct_prepare_url_parts(job->url, &url_parts)) {
        failure_code = "INVALID_URL";
        goto cleanup;
    }
    if (adapter == NULL || adapter->build_request == NULL ||
        adapter->parse_response == NULL ||
        !adapter->build_request(job, &built_request) ||
        built_request.body == NULL || built_request.body_bytes == 0 ||
        built_request.content_type == NULL) {
        failure_code = "INTERNAL";
        goto cleanup;
    }
    if (built_request.header_name != NULL && built_request.header_value != NULL) {
        int written = snprintf(headers_utf8, sizeof(headers_utf8),
                               "Content-Type: %s\r\nAccept: application/json\r\n%s: %s%s\r\n",
                               built_request.content_type, built_request.header_name,
                               built_request.header_prefix != NULL ? built_request.header_prefix : "",
                               built_request.header_value);
        if (written <= 0 || (size_t)written >= sizeof(headers_utf8)) {
            failure_code = "INTERNAL";
            goto cleanup;
        }
        headers_utf8_bytes = (size_t)written;
    } else {
        int written = snprintf(headers_utf8, sizeof(headers_utf8),
                               "Content-Type: %s\r\nAccept: application/json\r\n",
                               built_request.content_type);
        if (written <= 0 || (size_t)written >= sizeof(headers_utf8)) {
            failure_code = "INTERNAL";
            goto cleanup;
        }
        headers_utf8_bytes = (size_t)written;
    }
    header_chars = hd2ct_utf8_to_wide(headers_utf8, headers_utf8_bytes,
                                      headers_wide,
                                      (int)(sizeof(headers_wide) / sizeof(headers_wide[0])));
    if (header_chars <= 0) {
        failure_code = "INVALID_CONFIG";
        goto cleanup;
    }
    if (!hd2ct_set_remaining_timeouts(session, request_deadline)) {
        failure_code = hd2ct_remaining_ms(request_deadline) == 0 ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    connection = WinHttpConnect(session, url_parts.host, url_parts.port, 0);
    if (connection == NULL) {
        DWORD error = GetLastError();
        failure_code = hd2ct_winhttp_failure(request_deadline, error);
        goto cleanup;
    }
    request = WinHttpOpenRequest(connection, L"POST", url_parts.path, NULL,
                                 WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                 url_parts.secure ? WINHTTP_FLAG_SECURE : 0);
    if (request == NULL) {
        DWORD error = GetLastError();
        failure_code = hd2ct_winhttp_failure(request_deadline, error);
        goto cleanup;
    }
    if (!WinHttpSetOption(request, WINHTTP_OPTION_DISABLE_FEATURE,
                          &disable_features, sizeof(disable_features))) {
        failure_code = "NETWORK";
        goto cleanup;
    }
    if (!hd2ct_set_remaining_timeouts(request, request_deadline)) {
        failure_code = hd2ct_remaining_ms(request_deadline) == 0 ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        failure_code = "CANCELLED";
        goto cleanup;
    }
    if (!hd2ct_take_rate_slot()) {
        failure_code = "RATE_LIMITED";
        goto cleanup;
    }
    *request_attempted = 1;
    if (!hd2ct_set_remaining_timeouts(request, request_deadline)) {
        failure_code = hd2ct_remaining_ms(request_deadline) == 0 ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    if (!WinHttpSendRequest(request, headers_wide, (DWORD)header_chars,
                            built_request.body, built_request.body_bytes,
                            built_request.body_bytes, 0)) {
        DWORD error = GetLastError();
        failure_code = hd2ct_winhttp_failure(request_deadline, error);
        goto cleanup;
    }
    if (!hd2ct_set_remaining_timeouts(request, request_deadline)) {
        failure_code = hd2ct_remaining_ms(request_deadline) == 0 ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        failure_code = "CANCELLED";
        goto cleanup;
    }
    if (!WinHttpReceiveResponse(request, NULL)) {
        DWORD error = GetLastError();
        failure_code = hd2ct_winhttp_failure(request_deadline, error);
        goto cleanup;
    }
    if (!hd2ct_set_remaining_timeouts(request, request_deadline)) {
        failure_code = hd2ct_remaining_ms(request_deadline) == 0 ? "TIMEOUT" : "NETWORK";
        goto cleanup;
    }
    if (!WinHttpQueryHeaders(request,
                             WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                             WINHTTP_HEADER_NAME_BY_INDEX,
                             &status_code, &status_size, WINHTTP_NO_HEADER_INDEX)) {
        DWORD error = GetLastError();
        failure_code = hd2ct_winhttp_failure(request_deadline, error);
        goto cleanup;
    }
    if (status_code < 200u || status_code >= 300u) {
        char code[32];
        (void)snprintf(code, sizeof(code), "HTTP_%lu", (unsigned long)status_code);
        hd2ct_build_error(result, HD2CT_MAX_RESULT + 1u, result_bytes, code);
        ok = 1;
        goto cleanup;
    }
    response_body = (char *)malloc(HD2CT_MAX_RESPONSE + 1u);
    if (response_body == NULL) {
        failure_code = "INTERNAL";
        goto cleanup;
    }
    for (;;) {
        DWORD available = 0;
        DWORD read_count = 0;
        if (!hd2ct_set_remaining_timeouts(request, request_deadline)) {
            failure_code = hd2ct_remaining_ms(request_deadline) == 0 ? "TIMEOUT" : "NETWORK";
            goto cleanup;
        }
        if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
            failure_code = "CANCELLED";
            goto cleanup;
        }
        if (!WinHttpQueryDataAvailable(request, &available)) {
            DWORD error = GetLastError();
            failure_code = hd2ct_winhttp_failure(request_deadline, error);
            goto cleanup;
        }
        if (available == 0) {
            break;
        }
        if ((uint64_t)response_used + available > HD2CT_MAX_RESPONSE) {
            failure_code = "RESPONSE_TOO_LARGE";
            goto cleanup;
        }
        if (!hd2ct_set_remaining_timeouts(request, request_deadline)) {
            failure_code = hd2ct_remaining_ms(request_deadline) == 0 ? "TIMEOUT" : "NETWORK";
            goto cleanup;
        }
        if (!WinHttpReadData(request, response_body + response_used, available, &read_count)) {
            DWORD error = GetLastError();
            failure_code = hd2ct_winhttp_failure(request_deadline, error);
            goto cleanup;
        }
        if (read_count == 0) {
            break;
        }
        response_used += read_count;
    }
    response_body[response_used] = '\0';
    if (!adapter->parse_response(job, response_body, response_used,
                                 result + 3u, result_bytes, &failure_code)) {
        goto cleanup;
    }
    memcpy(result, hd2ct_success_prefix(job->adapter_id), 3u);
    *result_bytes += 3u;
    result[*result_bytes] = '\0';
    *successful = 1;
    ok = 1;

cleanup:
    if (request != NULL) {
        WinHttpCloseHandle(request);
    }
    if (connection != NULL) {
        WinHttpCloseHandle(connection);
    }
    hd2ct_free_url_parts(&url_parts);
    if (built_request.body != NULL) {
        SecureZeroMemory(built_request.body, built_request.body_bytes);
        cJSON_free(built_request.body);
    }
    if (response_body != NULL) {
        SecureZeroMemory(response_body, HD2CT_MAX_RESPONSE + 1u);
        free(response_body);
    }
    SecureZeroMemory(headers_wide, sizeof(headers_wide));
    SecureZeroMemory(headers_utf8, sizeof(headers_utf8));
    if (!ok) {
        hd2ct_build_error(result, HD2CT_MAX_RESULT + 1u, result_bytes, failure_code);
    }
}
