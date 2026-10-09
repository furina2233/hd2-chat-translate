#include "internal.h"
#include "target_languages.generated.h"

#include <string.h>
#include <wchar.h>

#ifdef HD2CT_TESTING
static wchar_t g_test_values_file_path[HD2CT_MAX_VALUES_PATH];
#endif

typedef struct HD2CT_ParsedValues {
    uint32_t any_valid_line;
    uint32_t target_language;
    uint32_t target_valid;
    uint32_t enabled;
    uint32_t enabled_valid;
    uint32_t timeout_index;
    uint32_t timeout_valid;
    uint32_t outgoing_enabled;
    uint32_t outgoing_enabled_valid;
    uint32_t outgoing_target_language;
    uint32_t outgoing_target_valid;
} HD2CT_ParsedValues;

const HD2CT_TargetLanguage *hd2ct_target_language(uint32_t index)
{
    if (index == 0u || index > HD2CT_TARGET_LANGUAGE_COUNT) {
        index = HD2CT_DEFAULT_TARGET_LANGUAGE;
    }
    return &g_hd2ct_target_languages[index - 1u];
}

uint32_t hd2ct_default_target_language(void)
{
    return HD2CT_DEFAULT_TARGET_LANGUAGE;
}

#ifdef HD2CT_TESTING
int hd2ct_test_set_values_file_path(const wchar_t *path)
{
    size_t length = path == NULL ? 0u : wcslen(path);
    if (length >= HD2CT_MAX_VALUES_PATH) return 0;
    SecureZeroMemory(g_test_values_file_path, sizeof(g_test_values_file_path));
    if (length != 0u) {
        memcpy(g_test_values_file_path, path,
               (length + 1u) * sizeof(g_test_values_file_path[0]));
    }
    return 1;
}
#endif

static int hd2ct_values_primary_path(wchar_t *path, size_t capacity)
{
#ifdef HD2CT_TESTING
    size_t length = wcslen(g_test_values_file_path);
    if (length == 0u || length >= capacity) return 0;
    memcpy(path, g_test_values_file_path, (length + 1u) * sizeof(path[0]));
    return 1;
#else
    static const wchar_t suffix[] =
        L"\\CowboyBingus\\Helldivers2\\Logs\\ModOptionsMenu.values";
    wchar_t local_app_data[HD2CT_MAX_VALUES_PATH];
    DWORD length = GetEnvironmentVariableW(
        L"LOCALAPPDATA", local_app_data, HD2CT_MAX_VALUES_PATH);
    size_t base_length;
    size_t suffix_length = sizeof(suffix) / sizeof(suffix[0]) - 1u;
    if (length == 0u || length >= HD2CT_MAX_VALUES_PATH) return 0;
    base_length = (size_t)length;
    while (base_length != 0u && local_app_data[base_length - 1u] == L'\\') {
        --base_length;
    }
    if (base_length + suffix_length + 1u > capacity) return 0;
    memcpy(path, local_app_data, base_length * sizeof(path[0]));
    memcpy(path + base_length, suffix,
           (suffix_length + 1u) * sizeof(path[0]));
    SecureZeroMemory(local_app_data, sizeof(local_app_data));
    return 1;
#endif
}

static int hd2ct_read_values_file(const wchar_t *path, unsigned char *buffer,
                                  size_t *bytes_out)
{
    HANDLE file = INVALID_HANDLE_VALUE;
    BY_HANDLE_FILE_INFORMATION information;
    LARGE_INTEGER size;
    size_t total = 0u;
    int valid = 0;
    file = CreateFileW(path, GENERIC_READ,
                       FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                       NULL, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                       NULL);
    if (file == INVALID_HANDLE_VALUE) goto cleanup;
    if (!GetFileInformationByHandle(file, &information) ||
        (information.dwFileAttributes &
         (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DEVICE)) != 0u ||
        !GetFileSizeEx(file, &size) || size.QuadPart < 0 ||
        (uint64_t)size.QuadPart > HD2CT_MAX_VALUES_FILE_BYTES) {
        goto cleanup;
    }
    for (;;) {
        DWORD requested;
        DWORD received = 0u;
        size_t remaining = (size_t)HD2CT_MAX_VALUES_FILE_BYTES + 1u - total;
        requested = remaining > 65536u ? 65536u : (DWORD)remaining;
        if (requested == 0u ||
            !ReadFile(file, buffer + total, requested, &received, NULL)) {
            goto cleanup;
        }
        if (received == 0u) break;
        total += received;
        if (total > HD2CT_MAX_VALUES_FILE_BYTES) goto cleanup;
    }
    if (!hd2ct_valid_utf8(buffer, total, 0)) goto cleanup;
    *bytes_out = total;
    valid = 1;

cleanup:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    return valid;
}

static int hd2ct_parse_index(const unsigned char *text, size_t length,
                             uint32_t maximum, uint32_t *index_out)
{
    uint32_t value = 0u;
    size_t i;
    if (length == 0u) return 0;
    for (i = 0u; i < length; ++i) {
        unsigned char digit = text[i];
        if (digit < '0' || digit > '9') return 0;
        digit = (unsigned char)(digit - '0');
        if ((uint32_t)digit > maximum ||
            value > (maximum - (uint32_t)digit) / 10u) return 0;
        value = value * 10u + (uint32_t)digit;
    }
    if (value == 0u) return 0;
    *index_out = value;
    return 1;
}

static int hd2ct_parse_boolean(const unsigned char *text, size_t length,
                               uint32_t *enabled_out)
{
    static const char enabled_text[] = "true";
    static const char disabled_text[] = "false";
    if (length == sizeof(enabled_text) - 1u &&
        memcmp(text, enabled_text, sizeof(enabled_text) - 1u) == 0) {
        *enabled_out = 1u;
        return 1;
    }
    if (length == sizeof(disabled_text) - 1u &&
        memcmp(text, disabled_text, sizeof(disabled_text) - 1u) == 0) {
        *enabled_out = 0u;
        return 1;
    }
    return 0;
}

static int hd2ct_parse_values_text(const unsigned char *text, size_t length,
                                   HD2CT_ParsedValues *parsed)
{
    static const char target_option_id[] = HD2CT_TARGET_LANGUAGE_OPTION_ID;
    static const char enabled_option_id[] = HD2CT_ENABLED_OPTION_ID;
    static const char timeout_option_id[] = HD2CT_TIMEOUT_OPTION_ID;
    static const char outgoing_enabled_option_id[] = HD2CT_OUTGOING_ENABLED_OPTION_ID;
    static const char outgoing_target_option_id[] = HD2CT_OUTGOING_TARGET_OPTION_ID;
    size_t cursor = 0u;
    memset(parsed, 0, sizeof(*parsed));
    parsed->target_language = HD2CT_DEFAULT_TARGET_LANGUAGE;
    parsed->enabled = HD2CT_ENABLED_DEFAULT;
    parsed->timeout_index = HD2CT_DEFAULT_TIMEOUT_INDEX;
    parsed->outgoing_enabled = HD2CT_OUTGOING_ENABLED_DEFAULT;
    parsed->outgoing_target_language = HD2CT_DEFAULT_OUTGOING_TARGET_LANGUAGE;
    while (cursor < length) {
        size_t line_start = cursor;
        size_t line_end;
        size_t tab = SIZE_MAX;
        size_t i;
        int extra_tab = 0;
        const unsigned char *value;
        size_t value_length;
        int target_line;
        int enabled_line;
        int timeout_line;
        int outgoing_enabled_line;
        int outgoing_target_line;
        while (cursor < length && text[cursor] != '\r' && text[cursor] != '\n') {
            if (text[cursor] == '\t' && tab == SIZE_MAX) tab = cursor;
            ++cursor;
        }
        line_end = cursor;
        while (cursor < length && (text[cursor] == '\r' || text[cursor] == '\n')) {
            ++cursor;
        }
        if (tab == SIZE_MAX || tab == line_start) continue;
        value = text + tab + 1u;
        value_length = line_end - tab - 1u;
        target_line = tab - line_start == sizeof(target_option_id) - 1u &&
            memcmp(text + line_start, target_option_id,
                   sizeof(target_option_id) - 1u) == 0;
        enabled_line = tab - line_start == sizeof(enabled_option_id) - 1u &&
            memcmp(text + line_start, enabled_option_id,
                   sizeof(enabled_option_id) - 1u) == 0;
        timeout_line = tab - line_start == sizeof(timeout_option_id) - 1u &&
            memcmp(text + line_start, timeout_option_id,
                   sizeof(timeout_option_id) - 1u) == 0;
        outgoing_enabled_line = tab - line_start == sizeof(outgoing_enabled_option_id) - 1u &&
            memcmp(text + line_start, outgoing_enabled_option_id,
                   sizeof(outgoing_enabled_option_id) - 1u) == 0;
        outgoing_target_line = tab - line_start == sizeof(outgoing_target_option_id) - 1u &&
            memcmp(text + line_start, outgoing_target_option_id,
                   sizeof(outgoing_target_option_id) - 1u) == 0;
        for (i = tab + 1u; i < line_end; ++i) {
            if (text[i] == '\t') {
                extra_tab = 1;
                break;
            }
        }
        /* MOM对其它模组值只按id和值分隔符读取，不限制值的数据类型。 */
        parsed->any_valid_line = 1u;
        if (target_line) {
            parsed->target_language = HD2CT_DEFAULT_TARGET_LANGUAGE;
            parsed->target_valid = !extra_tab && hd2ct_parse_index(
                value, value_length, HD2CT_TARGET_LANGUAGE_COUNT,
                &parsed->target_language);
        } else if (enabled_line) {
            parsed->enabled = HD2CT_ENABLED_DEFAULT;
            parsed->enabled_valid = !extra_tab && hd2ct_parse_boolean(
                value, value_length, &parsed->enabled);
        } else if (timeout_line) {
            parsed->timeout_index = HD2CT_DEFAULT_TIMEOUT_INDEX;
            parsed->timeout_valid = !extra_tab && hd2ct_parse_index(
                value, value_length, HD2CT_TIMEOUT_CHOICE_COUNT,
                &parsed->timeout_index);
        } else if (outgoing_enabled_line) {
            parsed->outgoing_enabled = HD2CT_OUTGOING_ENABLED_DEFAULT;
            parsed->outgoing_enabled_valid = !extra_tab && hd2ct_parse_boolean(
                value, value_length, &parsed->outgoing_enabled);
        } else if (outgoing_target_line) {
            parsed->outgoing_target_language = HD2CT_DEFAULT_OUTGOING_TARGET_LANGUAGE;
            parsed->outgoing_target_valid = !extra_tab && hd2ct_parse_index(
                value, value_length, HD2CT_TARGET_LANGUAGE_COUNT,
                &parsed->outgoing_target_language);
        }
    }
    return parsed->any_valid_line != 0u;
}

static int hd2ct_append_backup_suffix(wchar_t *path, size_t capacity)
{
    static const wchar_t suffix[] = L".bak";
    size_t length = wcslen(path);
    size_t suffix_length = sizeof(suffix) / sizeof(suffix[0]) - 1u;
    if (length + suffix_length + 1u > capacity) return 0;
    memcpy(path + length, suffix, (suffix_length + 1u) * sizeof(path[0]));
    return 1;
}

static void hd2ct_apply_parsed_values(const HD2CT_ParsedValues *parsed,
                                      HD2CT_RuntimeSettings *settings)
{
    if (parsed->target_valid != 0u) {
        settings->target_language = parsed->target_language;
    }
    if (parsed->enabled_valid != 0u) {
        settings->enabled = parsed->enabled;
    }
    if (parsed->timeout_valid != 0u) {
        settings->timeout_seconds =
            g_hd2ct_timeout_seconds[parsed->timeout_index - 1u];
    }
    if (parsed->outgoing_enabled_valid != 0u) {
        settings->outgoing_enabled = parsed->outgoing_enabled;
    }
    if (parsed->outgoing_target_valid != 0u) {
        settings->outgoing_target_language = parsed->outgoing_target_language;
    }
}

void hd2ct_read_applied_settings(HD2CT_RuntimeSettings *settings)
{
    wchar_t path[HD2CT_MAX_VALUES_PATH];
    unsigned char *buffer;
    size_t bytes = 0u;
    HD2CT_ParsedValues parsed;
    int read_ok;
    if (settings == NULL) return;
    settings->target_language = HD2CT_DEFAULT_TARGET_LANGUAGE;
    settings->timeout_seconds = HD2CT_DEFAULT_TIMEOUT_SECONDS;
    settings->enabled = HD2CT_ENABLED_DEFAULT;
    settings->outgoing_enabled = HD2CT_OUTGOING_ENABLED_DEFAULT;
    settings->outgoing_target_language = HD2CT_DEFAULT_OUTGOING_TARGET_LANGUAGE;
    if (!hd2ct_values_primary_path(path,
                                  sizeof(path) / sizeof(path[0]))) {
        return;
    }
    buffer = (unsigned char *)HeapAlloc(
        GetProcessHeap(), HEAP_ZERO_MEMORY,
        (SIZE_T)HD2CT_MAX_VALUES_FILE_BYTES + 1u);
    if (buffer == NULL) return;

    read_ok = hd2ct_read_values_file(path, buffer, &bytes);
    if (read_ok && hd2ct_parse_values_text(buffer, bytes, &parsed)) {
        hd2ct_apply_parsed_values(&parsed, settings);
        goto cleanup;
    }

    if (!hd2ct_append_backup_suffix(
            path, sizeof(path) / sizeof(path[0]))) {
        goto cleanup;
    }
    bytes = 0u;
    if (hd2ct_read_values_file(path, buffer, &bytes) &&
        hd2ct_parse_values_text(buffer, bytes, &parsed)) {
        hd2ct_apply_parsed_values(&parsed, settings);
    }

cleanup:
    SecureZeroMemory(buffer, (SIZE_T)HD2CT_MAX_VALUES_FILE_BYTES + 1u);
    HeapFree(GetProcessHeap(), 0, buffer);
    SecureZeroMemory(path, sizeof(path));
    SecureZeroMemory(&parsed, sizeof(parsed));
}
