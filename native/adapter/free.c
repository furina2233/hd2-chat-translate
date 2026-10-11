#include "internal.h"
#include "adapter/free_auth.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int hd2ct_hex_nibble(char value, unsigned char *nibble)
{
    if (value >= '0' && value <= '9') {
        *nibble = (unsigned char)(value - '0');
        return 1;
    }
    if (value >= 'a' && value <= 'f') {
        *nibble = (unsigned char)(value - 'a' + 10);
        return 1;
    }
    if (value >= 'A' && value <= 'F') {
        *nibble = (unsigned char)(value - 'A' + 10);
        return 1;
    }
    return 0;
}

static int hd2ct_free_totp(const unsigned char *secret, size_t secret_bytes,
                           uint64_t unix_seconds, char output[7])
{
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    PUCHAR hash_object = NULL;
    DWORD object_bytes = 0;
    DWORD returned = 0;
    UCHAR counter[8];
    UCHAR digest[20];
    uint64_t step = unix_seconds / 30u;
    uint32_t binary;
    uint32_t offset;
    uint32_t code;
    NTSTATUS status;
    size_t i;
    int written;
    int ok = 0;

    memset(counter, 0, sizeof(counter));
    memset(digest, 0, sizeof(digest));
    output[0] = '\0';
    if (secret == NULL || secret_bytes == 0 || secret_bytes > 0xffffffffu) {
        goto cleanup;
    }
    for (i = 0; i < sizeof(counter); ++i) {
        counter[sizeof(counter) - 1u - i] = (UCHAR)(step & 0xffu);
        step >>= 8;
    }
    status = BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA1_ALGORITHM,
                                         NULL, BCRYPT_ALG_HANDLE_HMAC_FLAG);
    if (status < 0) goto cleanup;
    status = BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                               (PUCHAR)&object_bytes, sizeof(object_bytes),
                               &returned, 0);
    if (status < 0 || object_bytes == 0) goto cleanup;
    hash_object = (PUCHAR)malloc(object_bytes);
    if (hash_object == NULL) goto cleanup;
    status = BCryptCreateHash(algorithm, &hash, hash_object, object_bytes,
                              (PUCHAR)(void *)secret, (ULONG)secret_bytes, 0);
    if (status < 0) goto cleanup;
    status = BCryptHashData(hash, counter, sizeof(counter), 0);
    if (status < 0) goto cleanup;
    status = BCryptFinishHash(hash, digest, sizeof(digest), 0);
    if (status < 0) goto cleanup;
    offset = digest[sizeof(digest) - 1u] & 0x0fu;
    binary = ((uint32_t)(digest[offset] & 0x7fu) << 24) |
             ((uint32_t)digest[offset + 1u] << 16) |
             ((uint32_t)digest[offset + 2u] << 8) |
             (uint32_t)digest[offset + 3u];
    code = binary % 1000000u;
    written = snprintf(output, 7u, "%06lu", (unsigned long)code);
    if (written != 6) goto cleanup;
    ok = 1;

cleanup:
    if (hash != NULL) BCryptDestroyHash(hash);
    if (hash_object != NULL) {
        SecureZeroMemory(hash_object, object_bytes);
        free(hash_object);
    }
    if (algorithm != NULL) BCryptCloseAlgorithmProvider(algorithm, 0);
    SecureZeroMemory(counter, sizeof(counter));
    SecureZeroMemory(digest, sizeof(digest));
    if (!ok) SecureZeroMemory(output, 7u);
    return ok;
}

static int hd2ct_free_secret(unsigned char secret[20])
{
    size_t i;
    for (i = 0; i < 20u; ++i) {
        unsigned char high;
        unsigned char low;
        if (!hd2ct_hex_nibble(HD2CT_FREE_TOTP_SECRET_HEX[i * 2u], &high) ||
            !hd2ct_hex_nibble(HD2CT_FREE_TOTP_SECRET_HEX[i * 2u + 1u], &low)) {
            SecureZeroMemory(secret, 20u);
            return 0;
        }
        secret[i] = (unsigned char)((high << 4) | low);
    }
    return HD2CT_FREE_TOTP_SECRET_HEX[40] == '\0';
}

static int hd2ct_free_unix_seconds(uint64_t *unix_seconds)
{
    FILETIME file_time;
    ULARGE_INTEGER windows_ticks;
    GetSystemTimeAsFileTime(&file_time);
    windows_ticks.LowPart = file_time.dwLowDateTime;
    windows_ticks.HighPart = file_time.dwHighDateTime;
    if (windows_ticks.QuadPart < 116444736000000000ull) return 0;
    *unix_seconds = (windows_ticks.QuadPart - 116444736000000000ull) / 10000000ull;
    return 1;
}

int hd2ct_build_free_request(const HD2CT_WorkerJob *job,
                             HD2CT_BuiltRequest *request)
{
    const HD2CT_TargetLanguage *target = hd2ct_target_language(job->target_language);
    unsigned char secret[20];
    uint64_t unix_seconds;
    char key2[7] = {0};
    cJSON *root = NULL;
    char *printed = NULL;
    size_t length;
    int ok = 0;
    memset(request, 0, sizeof(*request));
    memset(secret, 0, sizeof(secret));
    if (target == NULL || target->youdao_code == NULL ||
        !hd2ct_free_secret(secret) || !hd2ct_free_unix_seconds(&unix_seconds) ||
        !hd2ct_free_totp(secret, sizeof(secret), unix_seconds, key2)) {
        goto cleanup;
    }
    root = cJSON_CreateObject();
    if (root == NULL ||
        cJSON_AddStringToObject(root, "key1", HD2CT_FREE_KEY1) == NULL ||
        cJSON_AddStringToObject(root, "key2", key2) == NULL ||
        cJSON_AddStringToObject(root, "text", job->source) == NULL ||
        cJSON_AddStringToObject(root, "target_language", target->youdao_code) == NULL) {
        goto cleanup;
    }
    printed = cJSON_PrintUnformatted(root);
    if (printed == NULL) goto cleanup;
    length = strlen(printed);
    if (length == 0 || length > 32768u || length > MAXDWORD) goto cleanup;
    request->body = printed;
    request->body_bytes = (DWORD)length;
    request->content_type = "application/json";
    printed = NULL;
    ok = 1;

cleanup:
    if (printed != NULL) cJSON_free(printed);
    cJSON_Delete(root);
    SecureZeroMemory(secret, sizeof(secret));
    SecureZeroMemory(key2, sizeof(key2));
    return ok;
}

static int hd2ct_free_member_count(const cJSON *object, const char *name)
{
    const cJSON *item;
    int count = 0;
    for (item = object->child; item != NULL; item = item->next) {
        if (item->string != NULL && strcmp(item->string, name) == 0) ++count;
    }
    return count;
}

static const char *hd2ct_free_reason_code(const char *reason)
{
    if (strcmp(reason, "翻译凭据或签名无效") == 0) return "AUTH_INVALID";
    if (strcmp(reason, "翻译配置不完整") == 0) return "MISSING_CONFIG";
    if (strcmp(reason, "请求太频繁，请稍后再试") == 0) return "RATE_LIMITED";
    if (strcmp(reason, "请求的文本过长") == 0) return "FREE_REQUEST_TEXT_TOO_LONG";
    if (strcmp(reason, "请升级hd2-chat-translate以继续使用免费版服务") == 0) return "FREE_UPGRADE_REQUIRED";
    if (strcmp(reason, "翻译额度不足") == 0) return "QUOTA_EXCEEDED";
    if (strcmp(reason, "免费版的余额已用尽，请自行配置提供商") == 0) return "FREE_QUOTA_EXCEEDED";
    if (strcmp(reason, "服务器错误，请联系作者") == 0) return "FREE_SERVER_ERROR";
    if (strcmp(reason, "不支持此语言") == 0) return "UNSUPPORTED_LANGUAGE";
    if (strcmp(reason, "请求参数有误") == 0) return "REQUEST_INVALID";
    if (strcmp(reason, "请求超时") == 0) return "TIMEOUT";
    if (strcmp(reason, "网络连接失败") == 0) return "NETWORK";
    if (strcmp(reason, "返回内容无效") == 0) return "BAD_RESPONSE";
    if (strcmp(reason, "返回内容过长") == 0) return "RESPONSE_TOO_LARGE";
    if (strcmp(reason, "翻译服务异常") == 0) return "SERVICE_ERROR";
    if (strcmp(reason, "无权使用此翻译服务") == 0) return "ACCESS_DENIED";
    return "SERVICE_ERROR";
}

int hd2ct_parse_free_response(const HD2CT_WorkerJob *job, char *body,
                              size_t body_bytes, char *translation,
                              uint32_t *translation_bytes,
                              const char **failure_code)
{
    cJSON *root = NULL;
    cJSON *success;
    cJSON *data;
    cJSON *text;
    cJSON *reason;
    const char *reason_text;
    size_t reason_bytes;
    int ok = 0;
    (void)job;
    *failure_code = "FREE_SERVER_ERROR";
    if (!hd2ct_parse_complete_json(body, body_bytes, &root)) return 0;
    if (hd2ct_free_member_count(root, "success") != 1) goto cleanup;
    success = cJSON_GetObjectItemCaseSensitive(root, "success");
    if (!cJSON_IsBool(success) || hd2ct_free_member_count(root, "data") != 1) goto cleanup;
    data = cJSON_GetObjectItemCaseSensitive(root, "data");
    if (!cJSON_IsObject(data) ||
        hd2ct_free_member_count(data, "text") != 1 ||
        hd2ct_free_member_count(data, "reason") != 1) goto cleanup;
    text = cJSON_GetObjectItemCaseSensitive(data, "text");
    reason = cJSON_GetObjectItemCaseSensitive(data, "reason");
    if (!cJSON_IsString(text) || text->valuestring == NULL ||
        !cJSON_IsString(reason) || reason->valuestring == NULL) goto cleanup;
    if (cJSON_IsTrue(success)) {
        if (reason->valuestring[0] != '\0') goto cleanup;
        ok = hd2ct_copy_valid_translation(text->valuestring, translation,
                                          translation_bytes);
    } else {
        if (text->valuestring[0] != '\0') goto cleanup;
        reason_text = reason->valuestring;
        reason_bytes = strlen(reason_text);
        if (reason_bytes == 0 || reason_bytes > 128u ||
            !hd2ct_valid_utf8((const unsigned char *)reason_text, reason_bytes, 1)) {
            goto cleanup;
        }
        *failure_code = hd2ct_free_reason_code(reason_text);
    }

cleanup:
    cJSON_Delete(root);
    return ok;
}

#ifdef HD2CT_TESTING
int hd2ct_test_totp(const unsigned char *secret, size_t secret_bytes,
                    uint64_t unix_seconds, char output[7])
{
    return hd2ct_free_totp(secret, secret_bytes, unix_seconds, output);
}
#endif
