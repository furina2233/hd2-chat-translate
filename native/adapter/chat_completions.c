#include "../internal.h"

#include <string.h>

static const char HD2CT_RESPONSE_FORMAT[] = "{\"type\":\"json_object\"}";

static int hd2ct_make_request_json(const HD2CT_WorkerJob *job,
                                  char **json_out, DWORD *json_bytes_out)
{
    cJSON *root = NULL;
    cJSON *response_format = NULL;
    cJSON *messages = NULL;
    cJSON *system_message = NULL;
    cJSON *user_message = NULL;
    char *printed = NULL;
    char prompt_buffer[2048];
    size_t printed_length;
    int ok = 0;
    if (!hd2ct_make_ai_system_prompt(job, prompt_buffer,
                                     sizeof(prompt_buffer))) {
        goto cleanup;
    }
    root = cJSON_CreateObject();
    response_format = cJSON_Parse(HD2CT_RESPONSE_FORMAT);
    messages = cJSON_CreateArray();
    system_message = cJSON_CreateObject();
    user_message = cJSON_CreateObject();
    if (root == NULL || response_format == NULL || messages == NULL ||
        system_message == NULL || user_message == NULL) {
        goto cleanup;
    }
    if (cJSON_AddStringToObject(root, "model", job->model) == NULL ||
        cJSON_AddItemToObject(root, "response_format", response_format) == 0) {
        goto cleanup;
    }
    response_format = NULL;
    if (cJSON_AddStringToObject(system_message, "role", "system") == NULL ||
        cJSON_AddStringToObject(system_message, "content", prompt_buffer) == NULL ||
        cJSON_AddItemToArray(messages, system_message) == 0) {
        goto cleanup;
    }
    system_message = NULL;
    if (cJSON_AddStringToObject(user_message, "role", "user") == NULL ||
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
    cJSON_Delete(system_message);
    cJSON_Delete(messages);
    cJSON_Delete(response_format);
    cJSON_Delete(root);
    SecureZeroMemory(prompt_buffer, sizeof(prompt_buffer));
    return ok;
}

int hd2ct_build_ai_request(const HD2CT_WorkerJob *job,
                           HD2CT_BuiltRequest *request)
{
    if (!hd2ct_make_request_json(job, &request->body, &request->body_bytes)) {
        return 0;
    }
    request->content_type = "application/json";
    request->header_name = "Authorization";
    request->header_prefix = "Bearer ";
    request->header_value = job->api_key;
    return 1;
}

int hd2ct_parse_ai_response(const HD2CT_WorkerJob *job, char *body,
                            size_t body_bytes, char *translation,
                            uint32_t *translation_bytes,
                            const char **failure_code)
{
    cJSON *provider = NULL;
    cJSON *choices;
    cJSON *choice;
    cJSON *finish_reason;
    cJSON *message;
    cJSON *content;
    size_t content_length;
    int ok = 0;
    *failure_code = "BAD_RESPONSE";
    if (!hd2ct_parse_complete_json(body, body_bytes, &provider)) return 0;
    choices = cJSON_GetObjectItemCaseSensitive(provider, "choices");
    if (!cJSON_IsArray(choices) || cJSON_GetArraySize(choices) == 0) goto cleanup;
    choice = cJSON_GetArrayItem(choices, 0);
    if (!cJSON_IsObject(choice)) goto cleanup;
    finish_reason = cJSON_GetObjectItemCaseSensitive(choice, "finish_reason");
    if (cJSON_IsString(finish_reason) && finish_reason->valuestring != NULL &&
        strcmp(finish_reason->valuestring, "length") == 0) {
        goto cleanup;
    }
    message = cJSON_GetObjectItemCaseSensitive(choice, "message");
    if (!cJSON_IsObject(message)) goto cleanup;
    content = cJSON_GetObjectItemCaseSensitive(message, "content");
    if (!cJSON_IsString(content) || content->valuestring == NULL) goto cleanup;
    content_length = strlen(content->valuestring);
    if (content_length == 0u || content_length > HD2CT_MAX_RESPONSE) goto cleanup;
    ok = hd2ct_parse_ai_translation_json(
        job->source, job->source_bytes, content->valuestring, content_length,
        translation, translation_bytes);

cleanup:
    cJSON_Delete(provider);
    return ok;
}
