#include "internal.h"
#include "adapter/free_auth.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int hd2ct_build_free_request(const HD2CT_WorkerJob *job,
                             HD2CT_BuiltRequest *request)
{
    const HD2CT_TargetLanguage *target = hd2ct_target_language(job->target_language);
    const char *digest_parts[1];
    size_t digest_lengths[1];
    size_t player_name_bytes = strlen(job->player_name);
    char key2[65] = {0};
    cJSON *root = NULL;
    char *printed = NULL;
    size_t length;
    int ok = 0;
    memset(request, 0, sizeof(*request));
    if (target == NULL || target->youdao_code == NULL ||
        player_name_bytes == 0 || player_name_bytes > HD2CT_MAX_PLAYER_NAME ||
        !hd2ct_valid_utf8((const unsigned char *)job->player_name,
                          player_name_bytes, 0)) {
        goto cleanup;
    }
    digest_parts[0] = job->player_name;
    digest_lengths[0] = player_name_bytes;
    if (!hd2ct_digest_hex(BCRYPT_SHA256_ALGORITHM, digest_parts,
                          digest_lengths, 1u, key2, sizeof(key2))) {
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
    if (strcmp(reason, "玩家身份哈希无效") == 0) return "FREE_PLAYER_INVALID";
    if (strcmp(reason, "DAILY_LIMIT_REACHED") == 0 ||
        strcmp(reason, "今日免费次数已用尽，请明天再试") == 0) {
        return "FREE_DAILY_LIMIT";
    }
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
