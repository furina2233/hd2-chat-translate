#include "../internal.h"

#include <string.h>
#include <stdio.h>

static const char HD2CT_BING_CN_ENDPOINT[] =
    "https://api.translator.azure.cn/translate";
static const char HD2CT_BING_GLOBAL_ENDPOINT[] =
    "https://api.cognitive.microsofttranslator.com/translate";

const char *hd2ct_bing_endpoint(uint32_t interface_type)
{
    if (interface_type == HD2CT_INTERFACE_BING_CN) {
        return HD2CT_BING_CN_ENDPOINT;
    }
    if (interface_type == HD2CT_INTERFACE_BING_GLOBAL) {
        return HD2CT_BING_GLOBAL_ENDPOINT;
    }
    return NULL;
}

static const char *hd2ct_bing_target_code(const HD2CT_WorkerJob *job)
{
    const HD2CT_TargetLanguage *target = hd2ct_target_language(job->target_language);
    if (strcmp(target->id, "zh_cn") == 0) return "zh-Hans";
    if (strcmp(target->id, "zh_tw") == 0) return "zh-Hant";
    return target->google_code;
}

int hd2ct_build_bing_request(const HD2CT_WorkerJob *job,
                             HD2CT_BuiltRequest *request)
{
    cJSON *root = NULL;
    cJSON *item = NULL;
    char *printed = NULL;
    const char *target = hd2ct_bing_target_code(job);
    size_t body_length;
    int query_length;
    int ok = 0;
    memset(request, 0, sizeof(*request));
    if (target == NULL || target[0] == '\0') goto cleanup;
    root = cJSON_CreateArray();
    item = cJSON_CreateObject();
    if (root == NULL || item == NULL ||
        cJSON_AddStringToObject(item, "Text", job->source) == NULL ||
        cJSON_AddItemToArray(root, item) == 0) {
        goto cleanup;
    }
    item = NULL;
    printed = cJSON_PrintUnformatted(root);
    if (printed == NULL) goto cleanup;
    body_length = strlen(printed);
    if (body_length == 0u || body_length > 32768u || body_length > MAXDWORD) {
        goto cleanup;
    }
    query_length = snprintf(request->query, sizeof(request->query),
                            "api-version=3.0&to=%s", target);
    if (query_length <= 0 || (size_t)query_length >= sizeof(request->query)) {
        goto cleanup;
    }
    request->body = printed;
    request->body_bytes = (DWORD)body_length;
    request->content_type = "application/json";
    request->header_name = "Ocp-Apim-Subscription-Key";
    request->header_value = job->api_key;
    if (job->region[0] != '\0') {
        request->header2_name = "Ocp-Apim-Subscription-Region";
        request->header2_value = job->region;
    }
    printed = NULL;
    ok = 1;

cleanup:
    if (printed != NULL) cJSON_free(printed);
    cJSON_Delete(item);
    cJSON_Delete(root);
    if (!ok) SecureZeroMemory(request, sizeof(*request));
    return ok;
}

static int hd2ct_bing_member_count(const cJSON *object, const char *name)
{
    const cJSON *item;
    int count = 0;
    if (!cJSON_IsObject(object)) return 0;
    for (item = object->child; item != NULL; item = item->next) {
        if (item->string != NULL && strcmp(item->string, name) == 0) ++count;
    }
    return count;
}

static int hd2ct_bing_parse_array(char *body, size_t body_bytes, cJSON **root_out)
{
    cJSON *root;
    const char *parse_end = NULL;
    body[body_bytes] = '\0';
    if (body_bytes == 0u || body_bytes > HD2CT_MAX_RESPONSE ||
        !hd2ct_valid_utf8((const unsigned char *)body, body_bytes, 0) ||
        hd2ct_has_escaped_nul(body, body_bytes)) {
        return 0;
    }
    root = cJSON_ParseWithLengthOpts(body, body_bytes, &parse_end, 0);
    if (root == NULL || parse_end == NULL) {
        cJSON_Delete(root);
        return 0;
    }
    while ((size_t)(parse_end - body) < body_bytes &&
           (*parse_end == ' ' || *parse_end == '\t' ||
            *parse_end == '\r' || *parse_end == '\n')) {
        ++parse_end;
    }
    if ((size_t)(parse_end - body) != body_bytes) {
        cJSON_Delete(root);
        return 0;
    }
    *root_out = root;
    return 1;
}

int hd2ct_parse_bing_response(const HD2CT_WorkerJob *job, char *body,
                              size_t body_bytes, char *translation,
                              uint32_t *translation_bytes,
                              const char **failure_code)
{
    cJSON *root = NULL;
    cJSON *response;
    cJSON *translations;
    cJSON *item;
    cJSON *text;
    cJSON *to;
    const char *expected_target = hd2ct_bing_target_code(job);
    int ok = 0;
    *failure_code = "BAD_RESPONSE";
    if (!hd2ct_bing_parse_array(body, body_bytes, &root)) {
        return 0;
    }
    if (cJSON_IsObject(root) &&
        hd2ct_bing_member_count(root, "error") != 0) {
        *failure_code = "SERVICE_ERROR";
        goto cleanup;
    }
    if (!cJSON_IsArray(root) || cJSON_GetArraySize(root) != 1) goto cleanup;
    response = cJSON_GetArrayItem(root, 0);
    if (cJSON_IsObject(response) &&
        hd2ct_bing_member_count(response, "error") != 0) {
        *failure_code = "SERVICE_ERROR";
        goto cleanup;
    }
    if (!cJSON_IsObject(response) ||
        hd2ct_bing_member_count(response, "translations") != 1) {
        goto cleanup;
    }
    translations = cJSON_GetObjectItemCaseSensitive(response, "translations");
    if (!cJSON_IsArray(translations) || cJSON_GetArraySize(translations) != 1) {
        goto cleanup;
    }
    item = cJSON_GetArrayItem(translations, 0);
    if (!cJSON_IsObject(item) ||
        hd2ct_bing_member_count(item, "text") != 1 ||
        hd2ct_bing_member_count(item, "to") != 1) {
        goto cleanup;
    }
    text = cJSON_GetObjectItemCaseSensitive(item, "text");
    to = cJSON_GetObjectItemCaseSensitive(item, "to");
    if (!cJSON_IsString(to) || to->valuestring == NULL ||
        expected_target == NULL || strcmp(to->valuestring, expected_target) != 0) {
        goto cleanup;
    }
    ok = hd2ct_copy_valid_translation(
        cJSON_IsString(text) ? text->valuestring : NULL,
        translation, translation_bytes);

cleanup:
    cJSON_Delete(root);
    return ok;
}
