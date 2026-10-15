#include "../internal.h"

#include <string.h>
#include <stdlib.h>

static const char HD2CT_ANTHROPIC_VERSION[] = "2023-06-01";

static int hd2ct_make_anthropic_request_json(const HD2CT_WorkerJob *job,
                                             char **json_out,
                                             DWORD *json_bytes_out)
{
    cJSON *root = NULL;
    cJSON *messages = NULL;
    cJSON *user_message = NULL;
    char prompt_buffer[2048];
    char *printed = NULL;
    size_t printed_length;
    int ok = 0;
    if (!hd2ct_make_ai_system_prompt(job, prompt_buffer,
                                     sizeof(prompt_buffer))) {
        goto cleanup;
    }
    root = cJSON_CreateObject();
    messages = cJSON_CreateArray();
    user_message = cJSON_CreateObject();
    if (root == NULL || messages == NULL || user_message == NULL ||
        cJSON_AddStringToObject(root, "model", job->model) == NULL ||
        cJSON_AddNumberToObject(root, "max_tokens", 4096) == NULL ||
        cJSON_AddStringToObject(root, "system", prompt_buffer) == NULL ||
        cJSON_AddStringToObject(user_message, "role", "user") == NULL ||
        cJSON_AddStringToObject(user_message, "content", job->source) == NULL ||
        cJSON_AddItemToArray(messages, user_message) == 0) {
        goto cleanup;
    }
    user_message = NULL;
    if (cJSON_AddItemToObject(root, "messages", messages) == 0) goto cleanup;
    messages = NULL;
    printed = cJSON_PrintUnformatted(root);
    if (printed == NULL) goto cleanup;
    printed_length = strlen(printed);
    if (printed_length == 0u || printed_length > 32768u ||
        printed_length > MAXDWORD) {
        goto cleanup;
    }
    *json_out = printed;
    *json_bytes_out = (DWORD)printed_length;
    printed = NULL;
    ok = 1;

cleanup:
    if (printed != NULL) cJSON_free(printed);
    cJSON_Delete(user_message);
    cJSON_Delete(messages);
    cJSON_Delete(root);
    SecureZeroMemory(prompt_buffer, sizeof(prompt_buffer));
    return ok;
}

int hd2ct_build_anthropic_request(const HD2CT_WorkerJob *job,
                                  HD2CT_BuiltRequest *request)
{
    memset(request, 0, sizeof(*request));
    if (!hd2ct_make_anthropic_request_json(job, &request->body,
                                           &request->body_bytes)) {
        return 0;
    }
    request->content_type = "application/json";
    request->header_name = "x-api-key";
    request->header_value = job->api_key;
    request->header2_name = "anthropic-version";
    request->header2_value = HD2CT_ANTHROPIC_VERSION;
    return 1;
}

static int hd2ct_anthropic_member_count(const cJSON *object, const char *name)
{
    const cJSON *item;
    int count = 0;
    if (!cJSON_IsObject(object)) return 0;
    for (item = object->child; item != NULL; item = item->next) {
        if (item->string != NULL && strcmp(item->string, name) == 0) ++count;
    }
    return count;
}

int hd2ct_parse_anthropic_response(const HD2CT_WorkerJob *job, char *body,
                                   size_t body_bytes, char *translation,
                                   uint32_t *translation_bytes,
                                   const char **failure_code)
{
    cJSON *root = NULL;
    cJSON *type;
    cJSON *role;
    cJSON *stop_reason;
    cJSON *stop_details;
    cJSON *details_type;
    cJSON *content;
    cJSON *block;
    cJSON *block_type;
    cJSON *text;
    char *joined = NULL;
    size_t joined_length = 0u;
    int content_count;
    int i;
    int ok = 0;
    *failure_code = "BAD_RESPONSE";
    if (!hd2ct_parse_complete_json(body, body_bytes, &root)) return 0;
    if (hd2ct_anthropic_member_count(root, "error") != 0) {
        *failure_code = "SERVICE_ERROR";
        goto cleanup;
    }
    if (hd2ct_anthropic_member_count(root, "type") != 1 ||
        hd2ct_anthropic_member_count(root, "role") != 1 ||
        hd2ct_anthropic_member_count(root, "stop_reason") != 1 ||
        hd2ct_anthropic_member_count(root, "content") != 1 ||
        hd2ct_anthropic_member_count(root, "stop_details") > 1) {
        goto cleanup;
    }
    type = cJSON_GetObjectItemCaseSensitive(root, "type");
    role = cJSON_GetObjectItemCaseSensitive(root, "role");
    stop_reason = cJSON_GetObjectItemCaseSensitive(root, "stop_reason");
    stop_details = cJSON_GetObjectItemCaseSensitive(root, "stop_details");
    content = cJSON_GetObjectItemCaseSensitive(root, "content");
    if (!cJSON_IsString(type) || type->valuestring == NULL ||
        strcmp(type->valuestring, "message") != 0 ||
        !cJSON_IsString(role) || role->valuestring == NULL ||
        strcmp(role->valuestring, "assistant") != 0 ||
        !cJSON_IsString(stop_reason) || stop_reason->valuestring == NULL ||
        (strcmp(stop_reason->valuestring, "end_turn") != 0 &&
         strcmp(stop_reason->valuestring, "stop_sequence") != 0) ||
        !cJSON_IsArray(content)) {
        goto cleanup;
    }
    if (stop_details != NULL && cJSON_IsObject(stop_details)) {
        if (hd2ct_anthropic_member_count(stop_details, "type") > 1) goto cleanup;
        details_type = cJSON_GetObjectItemCaseSensitive(stop_details, "type");
        if (details_type != NULL) {
            if (!cJSON_IsString(details_type) || details_type->valuestring == NULL) {
                goto cleanup;
            }
            if (strcmp(details_type->valuestring, "refusal") == 0) goto cleanup;
        }
    }
    content_count = cJSON_GetArraySize(content);
    if (content_count <= 0) goto cleanup;
    joined = (char *)malloc(HD2CT_MAX_RESPONSE + 1u);
    if (joined == NULL) {
        *failure_code = "INTERNAL";
        goto cleanup;
    }
    for (i = 0; i < content_count; ++i) {
        size_t text_length;
        block = cJSON_GetArrayItem(content, i);
        if (!cJSON_IsObject(block) ||
            hd2ct_anthropic_member_count(block, "type") != 1 ||
            hd2ct_anthropic_member_count(block, "text") != 1) {
            goto cleanup;
        }
        block_type = cJSON_GetObjectItemCaseSensitive(block, "type");
        text = cJSON_GetObjectItemCaseSensitive(block, "text");
        if (!cJSON_IsString(block_type) || block_type->valuestring == NULL ||
            strcmp(block_type->valuestring, "text") != 0 ||
            !cJSON_IsString(text) || text->valuestring == NULL) {
            goto cleanup;
        }
        text_length = strlen(text->valuestring);
        if (text_length > HD2CT_MAX_RESPONSE - joined_length) goto cleanup;
        memcpy(joined + joined_length, text->valuestring, text_length);
        joined_length += text_length;
    }
    if (joined_length == 0u) goto cleanup;
    joined[joined_length] = '\0';
    ok = hd2ct_parse_ai_translation_json(
        job->source, job->source_bytes, joined, joined_length,
        translation, translation_bytes);

cleanup:
    if (joined != NULL) {
        SecureZeroMemory(joined, HD2CT_MAX_RESPONSE + 1u);
        free(joined);
    }
    cJSON_Delete(root);
    return ok;
}
