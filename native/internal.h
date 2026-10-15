#ifndef HD2CT_INTERNAL_H
#define HD2CT_INTERNAL_H

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601
#endif
#ifndef WINVER
#define WINVER 0x0601
#endif

#include "client.h"
#include "vendor/cjson/cJSON.h"

#include <windows.h>
#include <winhttp.h>
#include <winreg.h>
#include <bcrypt.h>
#include <stddef.h>
#include <stdint.h>

#define HD2CT_MAX_TOKEN 128u
#define HD2CT_MAX_PLAYER_NAME 128u
#define HD2CT_MAX_SOURCE 1023u
#define HD2CT_MAX_TRANSLATION 16384u
#define HD2CT_MAX_RESULT (HD2CT_MAX_TRANSLATION + 3u)
#define HD2CT_MAX_URL 2048u
#define HD2CT_MAX_MODEL 256u
#define HD2CT_MAX_KEY 4096u
#define HD2CT_MAX_REGION 64u
#define HD2CT_CACHE_FINGERPRINT_LENGTH 64u
#define HD2CT_MAX_RESPONSE 1000000u
#define HD2CT_MAX_VALUES_FILE_BYTES (512u * 1024u)
#define HD2CT_MAX_VALUES_PATH 32768u
#define HD2CT_JOB_COUNT 32u
#define HD2CT_CACHE_COUNT 512u
#define HD2CT_JOB_TTL_MS 60000ull
#define HD2CT_RATE_PERIOD_MS 60000ull
#define HD2CT_REQUESTS_PER_PERIOD 30u

enum {
    HD2CT_STATUS_READY = 0,
    HD2CT_STATUS_MISSING_CONFIG = 1,
    HD2CT_STATUS_INVALID_CONFIG = 2,
    HD2CT_STATUS_DISABLED = 3,
    HD2CT_STATUS_WORKER_FAILURE = 4
};

enum {
    HD2CT_SLOT_FREE = 0,
    HD2CT_SLOT_QUEUED = 1,
    HD2CT_SLOT_ACTIVE = 2,
    HD2CT_SLOT_DONE = 3
};

enum {
    HD2CT_FAMILY_AI = 1,
    HD2CT_FAMILY_MACHINE = 2
};

enum {
    HD2CT_ADAPTER_AI = 1,
    HD2CT_ADAPTER_GOOGLE = 2,
    HD2CT_ADAPTER_BAIDU = 3,
    HD2CT_ADAPTER_YOUDAO = 4,
    HD2CT_ADAPTER_UNKNOWN = 5,
    HD2CT_ADAPTER_FREE = 6,
    HD2CT_ADAPTER_BING = 7,
    HD2CT_ADAPTER_ANTHROPIC = 8
};

enum {
    HD2CT_INTERFACE_FREE = 1,
    HD2CT_INTERFACE_CHAT_COMPLETIONS = 2,
    HD2CT_INTERFACE_ANTHROPIC = 3,
    HD2CT_INTERFACE_BAIDU = 4,
    HD2CT_INTERFACE_YOUDAO = 5,
    HD2CT_INTERFACE_BING_CN = 6,
    HD2CT_INTERFACE_BING_GLOBAL = 7,
    HD2CT_INTERFACE_GOOGLE = 8
};

typedef struct HD2CT_WorkerJob {
    uint32_t slot_index;
    uint64_t serial;
    uint64_t submitted_ms;
    uint32_t flags;
    char token[HD2CT_MAX_TOKEN + 1u];
    char player_name[HD2CT_MAX_PLAYER_NAME + 1u];
    char source[HD2CT_MAX_SOURCE + 1u];
    uint32_t source_bytes;
    char url[HD2CT_MAX_URL + 1u];
    char model[HD2CT_MAX_MODEL + 1u];
    char api_key[HD2CT_MAX_KEY + 1u];
    char app_id[HD2CT_MAX_KEY + 1u];
    char region[HD2CT_MAX_REGION + 1u];
    uint32_t adapter_id;
    uint32_t interface_type;
    uint32_t cache_fingerprint_valid;
    char cache_fingerprint[HD2CT_CACHE_FINGERPRINT_LENGTH + 1u];
    uint32_t target_language;
    uint32_t timeout_seconds;
    uint32_t menu_enabled;
    uint32_t ignore_self;
} HD2CT_WorkerJob;

#define HD2CT_BING_IG_CAPACITY 129u
#define HD2CT_BING_IID_CAPACITY 129u
#define HD2CT_BING_KEY_CAPACITY 14u
#define HD2CT_BING_TOKEN_CAPACITY 513u

typedef struct HD2CT_BingState {
    HINTERNET session;
    uint32_t use_china_endpoint;
    char ig[HD2CT_BING_IG_CAPACITY];
    char iid[HD2CT_BING_IID_CAPACITY];
    char key[HD2CT_BING_KEY_CAPACITY];
    char token[HD2CT_BING_TOKEN_CAPACITY];
    uint64_t initialized_ms;
    uint64_t cooldown_until_ms;
    uint32_t translation_count;
    uint32_t ready;
} HD2CT_BingState;

typedef struct HD2CT_BingContext {
    HD2CT_BingState *state;
    const HD2CT_WorkerJob *job;
    uint64_t deadline;
    int *request_attempted;
    const char **failure_code;
} HD2CT_BingContext;

enum {
    HD2CT_BING_FALLBACK = 0,
    HD2CT_BING_TRANSLATED = 1,
    HD2CT_BING_CANCELLED = 2,
    HD2CT_BING_RATE_LIMITED = 3
};

typedef struct HD2CT_RuntimeSettings {
    uint32_t target_language;
    uint32_t interface_type;
    uint32_t timeout_seconds;
    uint32_t enabled;
    uint32_t ignore_self;
} HD2CT_RuntimeSettings;

typedef struct HD2CT_TargetLanguage {
    const char *id;
    const char *label;
    const char *ai_target;
    const char *google_code;
    const char *baidu_code;
    const char *youdao_code;
    uint32_t is_chinese;
} HD2CT_TargetLanguage;

typedef struct HD2CT_BuiltRequest {
    char *body;
    DWORD body_bytes;
    const char *content_type;
    const char *header_name;
    const char *header_prefix;
    const char *header_value;
    const char *header2_name;
    const char *header2_value;
    char query[128];
} HD2CT_BuiltRequest;

typedef struct HD2CT_AdapterBase {
    uint32_t family;
    int model_required;
    int api_key_required;
    int ai_v1_completion_path;
} HD2CT_AdapterBase;

typedef struct HD2CT_Adapter {
    uint32_t id;
    const HD2CT_AdapterBase *base;
    int app_id_required;
    const char *default_path;
    int (*build_request)(const HD2CT_WorkerJob *job, HD2CT_BuiltRequest *request);
    int (*parse_response)(const HD2CT_WorkerJob *job, char *body, size_t body_bytes,
                          char *translation, uint32_t *translation_bytes,
                          const char **failure_code);
} HD2CT_Adapter;

typedef struct HD2CT_FormBuffer {
    char *data;
    size_t used;
    size_t capacity;
} HD2CT_FormBuffer;

/* 共用文本、JSON 与短错误处理。 */
size_t hd2ct_bounded_length(const char *value, size_t maximum);
int hd2ct_valid_utf8(const unsigned char *bytes, size_t length, int reject_controls);
int hd2ct_utf8_to_wide(const char *input, size_t bytes, wchar_t *output, int output_count);
int hd2ct_wide_to_utf8(const wchar_t *input, size_t characters, char *output, int output_count);
int hd2ct_valid_token(const char *token, size_t *length_out);
int hd2ct_is_only_space(const char *text, size_t length);
int hd2ct_has_escaped_nul(const char *json, size_t length);
int hd2ct_parse_complete_json(char *body, size_t body_bytes, cJSON **root_out);
int hd2ct_copy_valid_translation(const char *text, char *translation,
                                 uint32_t *translation_bytes);
int hd2ct_json_error_code(const cJSON *item, char *code, size_t capacity);
void hd2ct_build_error(char *out, uint32_t capacity, uint32_t *bytes, const char *code);

/* 无状态配置读取和校验。 */
int hd2ct_local_http_host(const wchar_t *host);
int hd2ct_normalize_url(const char *input, uint32_t adapter_id,
                        char *normalized, size_t normalized_capacity);
int hd2ct_valid_secret(const char *value, size_t maximum);
int hd2ct_valid_region(const char *value);
int hd2ct_valid_model_key(const char *model, const char *api_key);
int hd2ct_read_environment_value(const wchar_t *name, char *out,
                                 size_t out_capacity, int *present);
uint32_t hd2ct_prepare_job_config(HD2CT_WorkerJob *job,
                                  uint32_t interface_type);
#ifdef HD2CT_TESTING
int hd2ct_validate_test_machine_endpoint(const char *input,
                                         uint32_t adapter_id,
                                         char *normalized,
                                         size_t normalized_capacity);
#endif

/* 共享菜单目录与MOM已应用设置读取。 */
const HD2CT_TargetLanguage *hd2ct_target_language(uint32_t index);
uint32_t hd2ct_default_target_language(void);
void hd2ct_read_applied_settings(HD2CT_RuntimeSettings *settings);
#ifdef HD2CT_TESTING
int hd2ct_test_set_values_file_path(const wchar_t *path);
#endif

/* 服务选择、适配器描述及共享签名与表单工具。 */
const HD2CT_Adapter *hd2ct_adapter_for_id(uint32_t adapter_id);
int hd2ct_machine_endpoint_for_interface(uint32_t interface_type,
                                         char *output,
                                         size_t output_capacity);
const char *hd2ct_google_endpoint(void);
const char *hd2ct_baidu_endpoint(void);
const char *hd2ct_youdao_endpoint(void);
const char *hd2ct_bing_endpoint(uint32_t interface_type);
#ifdef HD2CT_TESTING
int hd2ct_test_set_machine_endpoint(uint32_t interface_type,
                                    const char *url);
#endif
const char *hd2ct_provider_error_code(uint32_t adapter_id, const char *code);
int hd2ct_form_add(HD2CT_FormBuffer *form, const char *name,
                   const char *value, size_t value_length);
int hd2ct_random_salt(char salt[33]);
int hd2ct_digest_hex(LPCWSTR algorithm, const char *const *parts,
                     const size_t *part_lengths, size_t part_count,
                     char *hex_output, size_t hex_capacity);

/* 具体协议适配器。 */
int hd2ct_build_ai_request(const HD2CT_WorkerJob *job, HD2CT_BuiltRequest *request);
int hd2ct_parse_ai_response(const HD2CT_WorkerJob *job, char *body,
                            size_t body_bytes, char *translation,
                            uint32_t *translation_bytes, const char **failure_code);
int hd2ct_make_ai_system_prompt(const HD2CT_WorkerJob *job, char *output,
                                size_t output_capacity);
int hd2ct_parse_ai_translation_json(const char *source, uint32_t source_bytes,
                                    char *json, size_t json_bytes,
                                    char *translation,
                                    uint32_t *translation_bytes);
int hd2ct_build_bing_request(const HD2CT_WorkerJob *job, HD2CT_BuiltRequest *request);
int hd2ct_parse_bing_response(const HD2CT_WorkerJob *job, char *body,
                              size_t body_bytes, char *translation,
                              uint32_t *translation_bytes,
                              const char **failure_code);
int hd2ct_build_anthropic_request(const HD2CT_WorkerJob *job,
                                  HD2CT_BuiltRequest *request);
int hd2ct_parse_anthropic_response(const HD2CT_WorkerJob *job, char *body,
                                   size_t body_bytes, char *translation,
                                   uint32_t *translation_bytes,
                                   const char **failure_code);
int hd2ct_build_google_request(const HD2CT_WorkerJob *job, HD2CT_BuiltRequest *request);
int hd2ct_parse_google_response(const HD2CT_WorkerJob *job, char *body,
                               size_t body_bytes, char *translation,
                               uint32_t *translation_bytes, const char **failure_code);
int hd2ct_build_baidu_request(const HD2CT_WorkerJob *job, HD2CT_BuiltRequest *request);
int hd2ct_parse_baidu_response(const HD2CT_WorkerJob *job, char *body,
                               size_t body_bytes, char *translation,
                               uint32_t *translation_bytes, const char **failure_code);
int hd2ct_build_youdao_request(const HD2CT_WorkerJob *job, HD2CT_BuiltRequest *request);
int hd2ct_parse_youdao_response(const HD2CT_WorkerJob *job, char *body,
                                size_t body_bytes, char *translation,
                                uint32_t *translation_bytes, const char **failure_code);
int hd2ct_build_free_request(const HD2CT_WorkerJob *job, HD2CT_BuiltRequest *request);
int hd2ct_parse_free_response(const HD2CT_WorkerJob *job, char *body,
                              size_t body_bytes, char *translation,
                              uint32_t *translation_bytes, const char **failure_code);
int hd2ct_bing_state_init(HD2CT_BingState *state);
void hd2ct_bing_state_destroy(HD2CT_BingState *state);
int hd2ct_bing_translate(HD2CT_BingContext *context, char *translation,
                         uint32_t *translation_bytes);
#ifdef HD2CT_TESTING
int hd2ct_bing_test_parse_page(const char *html, size_t html_bytes,
                               char *ig, size_t ig_capacity,
                               char *iid, size_t iid_capacity,
                               char *key, size_t key_capacity,
                               char *token, size_t token_capacity);
int hd2ct_bing_test_parse_response(const char *body, size_t body_bytes,
                                   const char *target, char *translation,
                                   uint32_t *translation_bytes,
                                   const char **failure_code);
#endif
/* 传输层只调用核心提供的取消与限流钩子。 */
int hd2ct_job_cancelled(uint32_t slot_index, uint64_t serial);
int hd2ct_take_rate_slot(void);
uint32_t hd2ct_remaining_ms(uint64_t deadline);
int hd2ct_set_remaining_timeouts(HINTERNET handle, uint64_t deadline);
const char *hd2ct_winhttp_failure(uint64_t deadline, DWORD error);
void hd2ct_http_worker_request(HINTERNET session, HD2CT_BingState *bing_state,
                               const HD2CT_WorkerJob *job,
                               char *result, uint32_t *result_bytes,
                               int *successful, int *request_attempted,
                               uint64_t request_deadline);

#endif
