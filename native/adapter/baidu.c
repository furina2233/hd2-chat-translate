#include "internal.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>
int hd2ct_build_baidu_request(const HD2CT_WorkerJob *job,
                                     HD2CT_BuiltRequest *request)
{
    char salt[33] = {0};
    char sign[33] = {0};
    const HD2CT_TargetLanguage *target = hd2ct_target_language(job->target_language);
    const char *parts[4];
    size_t lengths[4];
    size_t q_length = job->source_bytes;
    HD2CT_FormBuffer form;
    size_t app_id_length = strlen(job->app_id);
    size_t secret_length = strlen(job->api_key);
    int ok = 0;
    memset(request, 0, sizeof(*request));
    if (!hd2ct_random_salt(salt)) goto cleanup;
    parts[0] = job->app_id;
    parts[1] = job->source;
    parts[2] = salt;
    parts[3] = job->api_key;
    lengths[0] = app_id_length;
    lengths[1] = q_length;
    lengths[2] = strlen(salt);
    lengths[3] = secret_length;
    if (!hd2ct_digest_hex(BCRYPT_MD5_ALGORITHM, parts, lengths, 4u,
                          sign, sizeof(sign))) goto cleanup;
    form.capacity = 32768u;
    form.data = (char *)malloc(form.capacity);
    form.used = 0;
    if (form.data == NULL) goto cleanup;
    form.data[0] = '\0';
    if (!hd2ct_form_add(&form, "q", job->source, q_length) ||
        !hd2ct_form_add(&form, "from", "auto", 4u) ||
        !hd2ct_form_add(&form, "to", target->baidu_code,
                        strlen(target->baidu_code)) ||
        !hd2ct_form_add(&form, "appid", job->app_id, app_id_length) ||
        !hd2ct_form_add(&form, "salt", salt, strlen(salt)) ||
        !hd2ct_form_add(&form, "sign", sign, strlen(sign)) ||
        form.used > MAXDWORD) {
        SecureZeroMemory(form.data, form.capacity);
        free(form.data);
        goto cleanup;
    }
    request->body = form.data;
    request->body_bytes = (DWORD)form.used;
    request->content_type = "application/x-www-form-urlencoded; charset=utf-8";
    ok = 1;

cleanup:
    SecureZeroMemory(salt, sizeof(salt));
    SecureZeroMemory(sign, sizeof(sign));
    return ok;
}

int hd2ct_parse_baidu_response(const HD2CT_WorkerJob *job, char *body,
                                     size_t body_bytes, char *translation,
                                     uint32_t *translation_bytes,
                                     const char **failure_code)
{
    cJSON *root = NULL;
    cJSON *error_item;
    cJSON *from;
    cJSON *array;
    cJSON *item;
    cJSON *dst;
    char code[32];
    char joined[HD2CT_MAX_TRANSLATION + 1u];
    size_t used = 0;
    int array_size;
    int i;
    int ok = 0;
    *failure_code = "BAD_RESPONSE";
    if (!hd2ct_parse_complete_json(body, body_bytes, &root)) return 0;
    error_item = cJSON_GetObjectItemCaseSensitive(root, "error_code");
    if (error_item != NULL) {
        if (!hd2ct_json_error_code(error_item, code, sizeof(code))) goto cleanup;
        if (strcmp(code, "52000") != 0) {
            *failure_code = hd2ct_provider_error_code(HD2CT_ADAPTER_BAIDU, code);
            goto cleanup;
        }
    }
    from = cJSON_GetObjectItemCaseSensitive(root, "from");
    array = cJSON_GetObjectItemCaseSensitive(root, "trans_result");
    array_size = cJSON_GetArraySize(array);
    if ((from != NULL && (!cJSON_IsString(from) || from->valuestring == NULL)) ||
        !cJSON_IsArray(array) || array_size == 0) goto cleanup;
    joined[0] = '\0';
    for (i = 0; i < array_size; ++i) {
        size_t dst_bytes;
        item = cJSON_GetArrayItem(array, i);
        dst = cJSON_IsObject(item) ? cJSON_GetObjectItemCaseSensitive(item, "dst") : NULL;
        if (!cJSON_IsString(dst) || dst->valuestring == NULL) goto cleanup;
        dst_bytes = strlen(dst->valuestring);
        if (dst_bytes == 0 || dst_bytes > HD2CT_MAX_TRANSLATION ||
            used + dst_bytes + (i != 0 ? 1u : 0u) > HD2CT_MAX_TRANSLATION) goto cleanup;
        if (i != 0) joined[used++] = '\n';
        memcpy(joined + used, dst->valuestring, dst_bytes);
        used += dst_bytes;
    }
    joined[used] = '\0';
    if (!hd2ct_valid_utf8((const unsigned char *)joined, used, 1) ||
        hd2ct_is_only_space(joined, used)) goto cleanup;
    ok = hd2ct_copy_valid_translation(joined, translation, translation_bytes);

cleanup:
    SecureZeroMemory(joined, sizeof(joined));
    cJSON_Delete(root);
    return ok;
}
