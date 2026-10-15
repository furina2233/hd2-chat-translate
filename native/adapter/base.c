#include "internal.h"

#include <string.h>
#include <stdlib.h>

static const HD2CT_AdapterBase g_ai_translation_base = {
    HD2CT_FAMILY_AI, 1, 1, 1
};
static const HD2CT_AdapterBase g_anthropic_translation_base = {
    HD2CT_FAMILY_AI, 1, 1, 0
};
static const HD2CT_AdapterBase g_machine_translation_base = {
    HD2CT_FAMILY_MACHINE, 0, 1, 0
};
static const HD2CT_AdapterBase g_free_translation_base = {
    HD2CT_FAMILY_MACHINE, 0, 0, 0
};

#ifdef HD2CT_TESTING
static SRWLOCK g_test_endpoint_lock = SRWLOCK_INIT;
static char g_test_machine_endpoints[HD2CT_INTERFACE_GOOGLE + 1u]
                                    [HD2CT_MAX_URL + 1u];
#endif

static int hd2ct_machine_adapter_id(uint32_t interface_type,
                                    uint32_t *adapter_id)
{
    switch (interface_type) {
    case HD2CT_INTERFACE_BAIDU:
        *adapter_id = HD2CT_ADAPTER_BAIDU;
        return 1;
    case HD2CT_INTERFACE_YOUDAO:
        *adapter_id = HD2CT_ADAPTER_YOUDAO;
        return 1;
    case HD2CT_INTERFACE_BING_CN:
    case HD2CT_INTERFACE_BING_GLOBAL:
        *adapter_id = HD2CT_ADAPTER_BING;
        return 1;
    case HD2CT_INTERFACE_GOOGLE:
        *adapter_id = HD2CT_ADAPTER_GOOGLE;
        return 1;
    default:
        return 0;
    }
}

int hd2ct_machine_endpoint_for_interface(uint32_t interface_type,
                                         char *output,
                                         size_t output_capacity)
{
    const char *endpoint = NULL;
    size_t endpoint_length;
    uint32_t adapter_id;
    if (output == NULL || output_capacity == 0u ||
        !hd2ct_machine_adapter_id(interface_type, &adapter_id)) {
        return 0;
    }
#ifdef HD2CT_TESTING
    AcquireSRWLockShared(&g_test_endpoint_lock);
    endpoint_length = strlen(g_test_machine_endpoints[interface_type]);
    if (endpoint_length != 0u) {
        if (endpoint_length >= output_capacity) {
            ReleaseSRWLockShared(&g_test_endpoint_lock);
            return 0;
        }
        memcpy(output, g_test_machine_endpoints[interface_type],
               endpoint_length + 1u);
        ReleaseSRWLockShared(&g_test_endpoint_lock);
        return 1;
    }
    ReleaseSRWLockShared(&g_test_endpoint_lock);
#endif
    switch (interface_type) {
    case HD2CT_INTERFACE_BAIDU:
        endpoint = hd2ct_baidu_endpoint();
        break;
    case HD2CT_INTERFACE_YOUDAO:
        endpoint = hd2ct_youdao_endpoint();
        break;
    case HD2CT_INTERFACE_BING_CN:
    case HD2CT_INTERFACE_BING_GLOBAL:
        endpoint = hd2ct_bing_endpoint(interface_type);
        break;
    case HD2CT_INTERFACE_GOOGLE:
        endpoint = hd2ct_google_endpoint();
        break;
    default:
        return 0;
    }
    if (endpoint == NULL) return 0;
    endpoint_length = strlen(endpoint);
    if (endpoint_length >= output_capacity) return 0;
    memcpy(output, endpoint, endpoint_length + 1u);
    (void)adapter_id;
    return 1;
}

#ifdef HD2CT_TESTING
int hd2ct_test_set_machine_endpoint(uint32_t interface_type,
                                    const char *url)
{
    uint32_t adapter_id;
    char normalized[HD2CT_MAX_URL + 1u];
    size_t endpoint_length;
    if (interface_type == 0u && url == NULL) {
        AcquireSRWLockExclusive(&g_test_endpoint_lock);
        SecureZeroMemory(g_test_machine_endpoints,
                         sizeof(g_test_machine_endpoints));
        ReleaseSRWLockExclusive(&g_test_endpoint_lock);
        return 1;
    }
    if (!hd2ct_machine_adapter_id(interface_type, &adapter_id)) return 0;
    if (url == NULL) {
        AcquireSRWLockExclusive(&g_test_endpoint_lock);
        SecureZeroMemory(g_test_machine_endpoints[interface_type],
                         sizeof(g_test_machine_endpoints[interface_type]));
        ReleaseSRWLockExclusive(&g_test_endpoint_lock);
        return 1;
    }
    if (!hd2ct_validate_test_machine_endpoint(url, adapter_id,
                                               normalized,
                                               sizeof(normalized))) {
        SecureZeroMemory(normalized, sizeof(normalized));
        return 0;
    }
    endpoint_length = strlen(normalized);
    AcquireSRWLockExclusive(&g_test_endpoint_lock);
    SecureZeroMemory(g_test_machine_endpoints[interface_type],
                     sizeof(g_test_machine_endpoints[interface_type]));
    memcpy(g_test_machine_endpoints[interface_type], normalized,
           endpoint_length + 1u);
    ReleaseSRWLockExclusive(&g_test_endpoint_lock);
    SecureZeroMemory(normalized, sizeof(normalized));
    return 1;
}
#endif

static int hd2ct_form_append(HD2CT_FormBuffer *form, const char *value, size_t length)
{
    if (length > form->capacity - form->used - 1u) return 0;
    memcpy(form->data + form->used, value, length);
    form->used += length;
    form->data[form->used] = '\0';
    return 1;
}

static int hd2ct_form_append_encoded(HD2CT_FormBuffer *form,
                                     const unsigned char *value, size_t length)
{
    static const char hex[] = "0123456789ABCDEF";
    size_t i;
    for (i = 0; i < length; ++i) {
        unsigned char c = value[i];
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_' || c == '*') {
            char plain = (char)c;
            if (!hd2ct_form_append(form, &plain, 1u)) return 0;
        } else if (c == ' ') {
            if (!hd2ct_form_append(form, "+", 1u)) return 0;
        } else {
            char escaped[3] = {'%', hex[c >> 4], hex[c & 0x0fu]};
            if (!hd2ct_form_append(form, escaped, sizeof(escaped))) return 0;
        }
    }
    return 1;
}

int hd2ct_form_add(HD2CT_FormBuffer *form, const char *name,
                          const char *value, size_t value_length)
{
    static const char equal[] = "=";
    if (form->used != 0 && !hd2ct_form_append(form, "&", 1u)) return 0;
    return hd2ct_form_append(form, name, strlen(name)) &&
           hd2ct_form_append(form, equal, sizeof(equal) - 1u) &&
           hd2ct_form_append_encoded(form, (const unsigned char *)value, value_length);
}

int hd2ct_random_salt(char salt[33])
{
    unsigned char random[16];
    static const char hex[] = "0123456789abcdef";
    size_t i;
    if (BCryptGenRandom(NULL, random, (ULONG)sizeof(random),
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG) < 0) {
        SecureZeroMemory(random, sizeof(random));
        return 0;
    }
    for (i = 0; i < sizeof(random); ++i) {
        salt[i * 2u] = hex[random[i] >> 4];
        salt[i * 2u + 1u] = hex[random[i] & 0x0fu];
    }
    salt[32] = '\0';
    SecureZeroMemory(random, sizeof(random));
    return 1;
}

int hd2ct_digest_hex(LPCWSTR algorithm, const char *const *parts,
                            const size_t *part_lengths, size_t part_count,
                            char *hex_output, size_t hex_capacity)
{
    BCRYPT_ALG_HANDLE algorithm_handle = NULL;
    BCRYPT_HASH_HANDLE hash_handle = NULL;
    PUCHAR hash_object = NULL;
    DWORD object_bytes = 0;
    DWORD hash_bytes = 0;
    DWORD returned = 0;
    UCHAR digest[64];
    static const char hex[] = "0123456789abcdef";
    NTSTATUS status;
    size_t i;
    int ok = 0;
    memset(digest, 0, sizeof(digest));
    status = BCryptOpenAlgorithmProvider(&algorithm_handle, algorithm, NULL, 0);
    if (status < 0) goto cleanup;
    status = BCryptGetProperty(algorithm_handle, BCRYPT_OBJECT_LENGTH,
                               (PUCHAR)&object_bytes, sizeof(object_bytes),
                               &returned, 0);
    if (status < 0 || object_bytes == 0) goto cleanup;
    status = BCryptGetProperty(algorithm_handle, BCRYPT_HASH_LENGTH,
                               (PUCHAR)&hash_bytes, sizeof(hash_bytes),
                               &returned, 0);
    if (status < 0 || hash_bytes == 0 || hash_bytes > sizeof(digest) ||
        (size_t)hash_bytes * 2u + 1u > hex_capacity) goto cleanup;
    hash_object = (PUCHAR)malloc(object_bytes);
    if (hash_object == NULL) goto cleanup;
    status = BCryptCreateHash(algorithm_handle, &hash_handle, hash_object,
                              object_bytes, NULL, 0, 0);
    if (status < 0) goto cleanup;
    for (i = 0; i < part_count; ++i) {
        if (part_lengths[i] > 0xffffffffu) goto cleanup;
        status = BCryptHashData(hash_handle, (PUCHAR)(const void *)parts[i],
                                (ULONG)part_lengths[i], 0);
        if (status < 0) goto cleanup;
    }
    status = BCryptFinishHash(hash_handle, digest, hash_bytes, 0);
    if (status < 0) goto cleanup;
    for (i = 0; i < hash_bytes; ++i) {
        hex_output[i * 2u] = hex[digest[i] >> 4];
        hex_output[i * 2u + 1u] = hex[digest[i] & 0x0fu];
    }
    hex_output[(size_t)hash_bytes * 2u] = '\0';
    ok = 1;

cleanup:
    if (hash_handle != NULL) BCryptDestroyHash(hash_handle);
    if (hash_object != NULL) {
        SecureZeroMemory(hash_object, object_bytes);
        free(hash_object);
    }
    if (algorithm_handle != NULL) BCryptCloseAlgorithmProvider(algorithm_handle, 0);
    SecureZeroMemory(digest, sizeof(digest));
    return ok;
}

const char *hd2ct_provider_error_code(uint32_t adapter_id, const char *code)
{
    if (adapter_id == HD2CT_ADAPTER_BAIDU) {
        if (strcmp(code, "52003") == 0 || strcmp(code, "54001") == 0) return "AUTH_INVALID";
        if (strcmp(code, "58000") == 0 || strcmp(code, "58002") == 0 ||
            strcmp(code, "90107") == 0) return "ACCESS_DENIED";
        if (strcmp(code, "54004") == 0) return "QUOTA_EXCEEDED";
        if (strcmp(code, "58001") == 0) return "UNSUPPORTED_LANGUAGE";
        if (strcmp(code, "54003") == 0 || strcmp(code, "54005") == 0) return "RATE_LIMITED";
        if (strcmp(code, "52001") == 0) return "TIMEOUT";
        if (strcmp(code, "54000") == 0) return "REQUEST_INVALID";
    } else if (adapter_id == HD2CT_ADAPTER_YOUDAO) {
        if (strcmp(code, "108") == 0 || strcmp(code, "111") == 0 ||
            strcmp(code, "202") == 0 || strcmp(code, "206") == 0 ||
            strcmp(code, "207") == 0) return "AUTH_INVALID";
        if (strcmp(code, "110") == 0 || strcmp(code, "112") == 0 ||
            strcmp(code, "203") == 0 || strcmp(code, "205") == 0) return "ACCESS_DENIED";
        if (strcmp(code, "401") == 0) return "QUOTA_EXCEEDED";
        if (strcmp(code, "102") == 0) return "UNSUPPORTED_LANGUAGE";
        if (strcmp(code, "411") == 0 || strcmp(code, "412") == 0) return "RATE_LIMITED";
        if (strcmp(code, "101") == 0 || strcmp(code, "103") == 0 ||
            strcmp(code, "105") == 0 || strcmp(code, "113") == 0 ||
            strcmp(code, "116") == 0) return "REQUEST_INVALID";
    }
    return "SERVICE_ERROR";
}

static const HD2CT_Adapter g_adapters[] = {
    {HD2CT_ADAPTER_AI, &g_ai_translation_base, 0, "/chat/completions",
     hd2ct_build_ai_request, hd2ct_parse_ai_response},
    {HD2CT_ADAPTER_GOOGLE, &g_machine_translation_base, 0, NULL,
     hd2ct_build_google_request, hd2ct_parse_google_response},
    {HD2CT_ADAPTER_BAIDU, &g_machine_translation_base, 1, NULL,
     hd2ct_build_baidu_request, hd2ct_parse_baidu_response},
    {HD2CT_ADAPTER_YOUDAO, &g_machine_translation_base, 1, NULL,
     hd2ct_build_youdao_request, hd2ct_parse_youdao_response},
    {HD2CT_ADAPTER_FREE, &g_free_translation_base, 0, "/translate",
     hd2ct_build_free_request, hd2ct_parse_free_response},
    {HD2CT_ADAPTER_BING, &g_machine_translation_base, 0, NULL,
     hd2ct_build_bing_request, hd2ct_parse_bing_response},
    {HD2CT_ADAPTER_ANTHROPIC, &g_anthropic_translation_base, 0, "/v1/messages",
     hd2ct_build_anthropic_request, hd2ct_parse_anthropic_response}
};

const HD2CT_Adapter *hd2ct_adapter_for_id(uint32_t adapter_id)
{
    size_t i;
    for (i = 0; i < sizeof(g_adapters) / sizeof(g_adapters[0]); ++i) {
        if (g_adapters[i].id == adapter_id) return &g_adapters[i];
    }
    return NULL;
}
