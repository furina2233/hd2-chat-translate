#include "internal.h"

#include <process.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef struct HD2CT_JobSlot {
    uint32_t state;
    uint32_t cancelled;
    uint32_t timeout_reported;
    uint32_t flags;
    uint64_t serial;
    uint64_t submitted_ms;
    uint64_t request_deadline_ms;
    char token[HD2CT_MAX_TOKEN + 1u];
    char source[HD2CT_MAX_SOURCE + 1u];
    uint32_t source_bytes;
    char result[HD2CT_MAX_RESULT + 1u];
    uint32_t result_bytes;
} HD2CT_JobSlot;

typedef struct HD2CT_CacheEntry {
    uint64_t age;
    uint32_t used;
    uint32_t source_bytes;
    uint32_t target_language;
    uint32_t result_bytes;
    char config_fingerprint[HD2CT_CACHE_FINGERPRINT_LENGTH + 1u];
    char source[HD2CT_MAX_SOURCE + 1u];
    char result[HD2CT_MAX_RESULT + 1u];
} HD2CT_CacheEntry;

static SRWLOCK g_lock = SRWLOCK_INIT;
static CONDITION_VARIABLE g_work_available = CONDITION_VARIABLE_INIT;
static HD2CT_JobSlot g_jobs[HD2CT_JOB_COUNT];
static HD2CT_CacheEntry g_cache[HD2CT_CACHE_COUNT];
static uint64_t g_rate_times[HD2CT_REQUESTS_PER_PERIOD];
static uint32_t g_rate_count;
static uint64_t g_next_serial = 1;
static uint64_t g_cache_age;
static volatile LONG g_status = HD2CT_STATUS_READY;
/* 0 尚未启动，1 正在启动 worker，2 worker 初始化已结束。 */
static volatile LONG g_initialized;
static volatile LONG g_cancel_all_pending;
static volatile LONG g_failure_count;
static volatile LONG64 g_backoff_until;
static volatile LONG g_last_request_status;
static volatile LONG g_request_status_set;
static LONG g_stop_workers;
#ifdef HD2CT_TESTING
static volatile LONG g_test_fail_worker_session;
static volatile LONG g_test_bootstrap_count;
static volatile LONG g_test_fail_bootstrap_create;
static volatile LONG g_test_fail_worker_create_at;
#endif

static void hd2ct_set_request_status(uint32_t status)
{
    InterlockedExchange(&g_last_request_status, (LONG)status);
    InterlockedExchange(&g_request_status_set, 1);
}

static int hd2ct_error_is(const char *result, uint32_t bytes, const char *code)
{
    size_t code_length = strlen(code);
    return bytes == 4u + code_length &&
           memcmp(result, "ERR\n", 4u) == 0 &&
           memcmp(result + 4u, code, code_length) == 0;
}

static uint32_t hd2ct_status_from_result(const char *result, uint32_t bytes)
{
    static const char http_prefix[] = "ERR\nHTTP_";
    size_t prefix_length = sizeof(http_prefix) - 1u;
    if (bytes >= prefix_length && memcmp(result, http_prefix, prefix_length) == 0) {
        uint32_t status = 0;
        uint32_t i;
        for (i = (uint32_t)prefix_length; i < bytes; ++i) {
            unsigned char digit = (unsigned char)result[i];
            if (digit < '0' || digit > '9') {
                return 1000u;
            }
            status = status * 10u + (uint32_t)(digit - '0');
        }
        if (status >= 100u && status <= 599u) {
            return status;
        }
        return 1000u;
    }
    if (bytes >= 3u && memcmp(result, "OK\n", 3u) == 0) {
        return 0u;
    }
    if (hd2ct_error_is(result, bytes, "TIMEOUT")) {
        return 1002u;
    }
    if (hd2ct_error_is(result, bytes, "BAD_RESPONSE")) {
        return 1001u;
    }
    if (hd2ct_error_is(result, bytes, "RATE_LIMITED")) {
        return 1003u;
    }
    if (hd2ct_error_is(result, bytes, "BACKOFF")) {
        return 1004u;
    }
    if (hd2ct_error_is(result, bytes, "EXPIRED")) {
        return 1005u;
    }
    if (hd2ct_error_is(result, bytes, "RESPONSE_TOO_LARGE")) {
        return 1007u;
    }
    if (hd2ct_error_is(result, bytes, "INVALID_URL")) {
        return 1008u;
    }
    if (hd2ct_error_is(result, bytes, "INTERNAL")) {
        return 1009u;
    }
    return 1000u;
}

static int hd2ct_pin_module(void)
{
    HMODULE module = NULL;
    return GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                              GET_MODULE_HANDLE_EX_FLAG_PIN,
                              (LPCWSTR)(const void *)&HD2CT_Submit,
                              &module) != 0;
}

static void hd2ct_apply_cancel_all_locked(void)
{
    uint32_t i;
    if (InterlockedExchange(&g_cancel_all_pending, 0) == 0) return;
    for (i = 0; i < HD2CT_JOB_COUNT; ++i) {
        if (g_jobs[i].state == HD2CT_SLOT_ACTIVE) {
            g_jobs[i].cancelled = 1;
            g_jobs[i].timeout_reported = 0;
            SecureZeroMemory(g_jobs[i].result, sizeof(g_jobs[i].result));
            g_jobs[i].result_bytes = 0;
        } else {
            SecureZeroMemory(&g_jobs[i], sizeof(g_jobs[i]));
        }
    }
}

static void hd2ct_mark_worker_failure(void)
{
    InterlockedExchange(&g_status, HD2CT_STATUS_WORKER_FAILURE);
    InterlockedExchange(&g_initialized, 2);
    InterlockedExchange(&g_stop_workers, 1);
    if (TryAcquireSRWLockExclusive(&g_lock)) {
        hd2ct_apply_cancel_all_locked();
        ReleaseSRWLockExclusive(&g_lock);
    }
    WakeAllConditionVariable(&g_work_available);
}

static uint64_t hd2ct_job_deadline(const HD2CT_WorkerJob *job)
{
    return job->submitted_ms + HD2CT_JOB_TTL_MS;
}

static void hd2ct_record_failure(void)
{
    LONG failure;
    uint64_t delay;
    uint64_t deadline;
    failure = InterlockedIncrement(&g_failure_count);
    if (failure > 6) {
        InterlockedExchange(&g_failure_count, 6);
        failure = 6;
    }
    delay = 1ull << (failure - 1);
    if (delay > 30u) {
        delay = 30u;
    }
    deadline = GetTickCount64() + delay * 1000ull;
    InterlockedExchange64(&g_backoff_until, (LONG64)deadline);
}

static void hd2ct_record_success(void)
{
    InterlockedExchange(&g_failure_count, 0);
    InterlockedExchange64(&g_backoff_until, 0);
}

static int hd2ct_find_cache_locked(const HD2CT_WorkerJob *job, char *result,
                                   uint32_t *result_bytes)
{
    uint32_t i;
    if (job->cache_fingerprint_valid == 0u) return 0;
    for (i = 0; i < HD2CT_CACHE_COUNT; ++i) {
        HD2CT_CacheEntry *entry = &g_cache[i];
        if (entry->used && entry->source_bytes == job->source_bytes &&
            entry->target_language == job->target_language &&
            memcmp(entry->config_fingerprint, job->cache_fingerprint,
                   sizeof(entry->config_fingerprint)) == 0 &&
            memcmp(entry->source, job->source, job->source_bytes) == 0) {
            entry->age = ++g_cache_age;
            memcpy(result, entry->result, entry->result_bytes);
            result[entry->result_bytes] = '\0';
            *result_bytes = entry->result_bytes;
            return 1;
        }
    }
    return 0;
}

static void hd2ct_put_cache_locked(const HD2CT_WorkerJob *job,
                                   const char *result, uint32_t result_bytes)
{
    uint32_t i;
    uint32_t target = 0;
    uint64_t oldest = UINT64_MAX;
    if (job->cache_fingerprint_valid == 0u) return;
    for (i = 0; i < HD2CT_CACHE_COUNT; ++i) {
        HD2CT_CacheEntry *entry = &g_cache[i];
        if (entry->used && entry->source_bytes == job->source_bytes &&
            entry->target_language == job->target_language &&
            memcmp(entry->config_fingerprint, job->cache_fingerprint,
                   sizeof(entry->config_fingerprint)) == 0 &&
            memcmp(entry->source, job->source, job->source_bytes) == 0) {
            target = i;
            oldest = 0;
            break;
        }
        if (!entry->used) {
            target = i;
            oldest = 0;
            break;
        }
        if (entry->age < oldest) {
            oldest = entry->age;
            target = i;
        }
    }
    SecureZeroMemory(&g_cache[target], sizeof(g_cache[target]));
    g_cache[target].used = 1;
    g_cache[target].age = ++g_cache_age;
    g_cache[target].source_bytes = job->source_bytes;
    g_cache[target].target_language = job->target_language;
    g_cache[target].result_bytes = result_bytes;
    memcpy(g_cache[target].config_fingerprint, job->cache_fingerprint,
           sizeof(g_cache[target].config_fingerprint));
    memcpy(g_cache[target].source, job->source, job->source_bytes);
    g_cache[target].source[job->source_bytes] = '\0';
    memcpy(g_cache[target].result, result, result_bytes);
    g_cache[target].result[result_bytes] = '\0';
}

int hd2ct_take_rate_slot(void)
{
    uint64_t now = GetTickCount64();
    uint32_t i;
    uint32_t retained = 0;
    int allowed = 0;
    AcquireSRWLockExclusive(&g_lock);
    hd2ct_apply_cancel_all_locked();
    for (i = 0; i < g_rate_count; ++i) {
        if (now - g_rate_times[i] < HD2CT_RATE_PERIOD_MS) {
            g_rate_times[retained++] = g_rate_times[i];
        }
    }
    g_rate_count = retained;
    if (g_rate_count < HD2CT_REQUESTS_PER_PERIOD) {
        g_rate_times[g_rate_count++] = now;
        allowed = 1;
    }
    ReleaseSRWLockExclusive(&g_lock);
    return allowed;
}

int hd2ct_job_cancelled(uint32_t slot_index, uint64_t serial)
{
    int cancelled = 1;
    AcquireSRWLockExclusive(&g_lock);
    hd2ct_apply_cancel_all_locked();
    if (slot_index < HD2CT_JOB_COUNT &&
        g_jobs[slot_index].serial == serial &&
        g_jobs[slot_index].state == HD2CT_SLOT_ACTIVE &&
        g_jobs[slot_index].cancelled == 0) {
        cancelled = 0;
    }
    ReleaseSRWLockExclusive(&g_lock);
    return cancelled;
}

static void hd2ct_complete_job(const HD2CT_WorkerJob *job, const char *result,
                               uint32_t result_bytes, int successful,
                               int remember_success)
{
    HD2CT_JobSlot *slot;
    AcquireSRWLockExclusive(&g_lock);
    hd2ct_apply_cancel_all_locked();
    if (job->slot_index >= HD2CT_JOB_COUNT) {
        ReleaseSRWLockExclusive(&g_lock);
        return;
    }
    slot = &g_jobs[job->slot_index];
    if (slot->serial != job->serial || slot->state != HD2CT_SLOT_ACTIVE) {
        ReleaseSRWLockExclusive(&g_lock);
        return;
    }
    if (slot->timeout_reported != 0) {
        slot->state = HD2CT_SLOT_DONE;
        ReleaseSRWLockExclusive(&g_lock);
        return;
    }
    if (slot->cancelled != 0) {
        SecureZeroMemory(slot, sizeof(*slot));
        ReleaseSRWLockExclusive(&g_lock);
        return;
    }
    if (InterlockedCompareExchange(&g_status, 0, 0) == HD2CT_STATUS_WORKER_FAILURE) {
        result = "ERR\nSERVICE_ERROR";
        result_bytes = (uint32_t)sizeof("ERR\nSERVICE_ERROR") - 1u;
        successful = 0;
        remember_success = 0;
    }
    if (result_bytes > HD2CT_MAX_RESULT) {
        result = "ERR\nINTERNAL";
        result_bytes = (uint32_t)sizeof("ERR\nINTERNAL") - 1u;
        successful = 0;
        remember_success = 0;
    }
    if (successful && remember_success &&
        ((result_bytes >= 3u && memcmp(result, "OK\n", 3u) == 0) ||
         (result_bytes == 5u && memcmp(result, "SKIP\n", 5u) == 0))) {
        hd2ct_put_cache_locked(job, result, result_bytes);
    }
    memcpy(slot->result, result, result_bytes);
    slot->result[result_bytes] = '\0';
    slot->result_bytes = result_bytes;
    slot->state = HD2CT_SLOT_DONE;
    ReleaseSRWLockExclusive(&g_lock);
}

static void hd2ct_set_slot_request_deadline(const HD2CT_WorkerJob *job,
                                           uint64_t deadline)
{
    HD2CT_JobSlot *slot;
    AcquireSRWLockExclusive(&g_lock);
    hd2ct_apply_cancel_all_locked();
    if (job->slot_index < HD2CT_JOB_COUNT) {
        slot = &g_jobs[job->slot_index];
        if (slot->serial == job->serial &&
            slot->state == HD2CT_SLOT_ACTIVE && slot->cancelled == 0) {
            slot->request_deadline_ms = deadline;
        }
    }
    ReleaseSRWLockExclusive(&g_lock);
}

static int hd2ct_take_next_job_locked(HD2CT_WorkerJob *copy)
{
    uint32_t i;
    uint32_t selected = HD2CT_JOB_COUNT;
    uint64_t sequence = UINT64_MAX;
    for (i = 0; i < HD2CT_JOB_COUNT; ++i) {
        if (g_jobs[i].state == HD2CT_SLOT_QUEUED &&
            g_jobs[i].serial < sequence) {
            selected = i;
            sequence = g_jobs[i].serial;
        }
    }
    if (selected == HD2CT_JOB_COUNT ||
        InterlockedCompareExchange(&g_initialized, 0, 0) != 2 ||
        InterlockedCompareExchange(&g_status, 0, 0) == HD2CT_STATUS_WORKER_FAILURE ||
        InterlockedCompareExchange(&g_stop_workers, 0, 0) != 0) {
        return 0;
    }
    g_jobs[selected].state = HD2CT_SLOT_ACTIVE;
    memset(copy, 0, sizeof(*copy));
    copy->slot_index = selected;
    copy->serial = g_jobs[selected].serial;
    copy->submitted_ms = g_jobs[selected].submitted_ms;
    copy->flags = g_jobs[selected].flags;
    memcpy(copy->token, g_jobs[selected].token, sizeof(copy->token));
    memcpy(copy->source, g_jobs[selected].source, sizeof(copy->source));
    copy->source_bytes = g_jobs[selected].source_bytes;
    return 1;
}

static int hd2ct_set_job_cache_fingerprint(HD2CT_WorkerJob *job)
{
    unsigned char numeric_fields[2][sizeof(uint32_t)];
    unsigned char length_prefixes[8][sizeof(uint64_t)];
    const char *fields[8];
    size_t field_lengths[8];
    const char *parts[16];
    size_t part_lengths[16];
    char ai_system_prompt[2048];
    size_t field_count = 0u;
    size_t part_count = 0u;
    size_t field_index;
    size_t i;
    char digest_hex[HD2CT_CACHE_FINGERPRINT_LENGTH + 1u];
    memset(numeric_fields, 0, sizeof(numeric_fields));
    memset(length_prefixes, 0, sizeof(length_prefixes));
    memset(fields, 0, sizeof(fields));
    memset(field_lengths, 0, sizeof(field_lengths));
    memset(parts, 0, sizeof(parts));
    memset(part_lengths, 0, sizeof(part_lengths));
    memset(digest_hex, 0, sizeof(digest_hex));
    memset(ai_system_prompt, 0, sizeof(ai_system_prompt));
    for (i = 0; i < 2u; ++i) {
        uint32_t value = i == 0u ? job->adapter_id : job->interface_type;
        numeric_fields[i][0] = (unsigned char)(value & 0xffu);
        numeric_fields[i][1] = (unsigned char)((value >> 8) & 0xffu);
        numeric_fields[i][2] = (unsigned char)((value >> 16) & 0xffu);
        numeric_fields[i][3] = (unsigned char)((value >> 24) & 0xffu);
    }
    fields[field_count] = (const char *)numeric_fields[0];
    field_lengths[field_count++] = sizeof(uint32_t);
    fields[field_count] = (const char *)numeric_fields[1];
    field_lengths[field_count++] = sizeof(uint32_t);
    fields[field_count] = job->url;
    field_lengths[field_count++] = strlen(job->url);
    switch (job->adapter_id) {
    case HD2CT_ADAPTER_AI:
    case HD2CT_ADAPTER_ANTHROPIC:
        fields[field_count] = job->model;
        field_lengths[field_count++] = strlen(job->model);
        fields[field_count] = job->api_key;
        field_lengths[field_count++] = strlen(job->api_key);
        if (!hd2ct_make_ai_system_prompt(
                job, ai_system_prompt, sizeof(ai_system_prompt))) {
            goto fail;
        }
        fields[field_count] = ai_system_prompt;
        field_lengths[field_count++] = strlen(ai_system_prompt);
        break;
    case HD2CT_ADAPTER_BAIDU:
    case HD2CT_ADAPTER_YOUDAO:
        fields[field_count] = job->api_key;
        field_lengths[field_count++] = strlen(job->api_key);
        fields[field_count] = job->app_id;
        field_lengths[field_count++] = strlen(job->app_id);
        break;
    case HD2CT_ADAPTER_BING:
        fields[field_count] = job->api_key;
        field_lengths[field_count++] = strlen(job->api_key);
        fields[field_count] = job->region;
        field_lengths[field_count++] = strlen(job->region);
        break;
    case HD2CT_ADAPTER_GOOGLE:
        fields[field_count] = job->api_key;
        field_lengths[field_count++] = strlen(job->api_key);
        break;
    case HD2CT_ADAPTER_FREE:
        break;
    default:
        goto fail;
    }
    for (field_index = 0; field_index < field_count; ++field_index) {
        uint64_t field_length = (uint64_t)field_lengths[field_index];
        for (i = 0; i < sizeof(uint64_t); ++i) {
            length_prefixes[field_index][i] =
                (unsigned char)(field_length >> ((sizeof(uint64_t) - i - 1u) * 8u));
        }
        parts[part_count] = (const char *)length_prefixes[field_index];
        part_lengths[part_count++] = sizeof(length_prefixes[field_index]);
        parts[part_count] = fields[field_index];
        part_lengths[part_count++] = field_lengths[field_index];
    }
    if (!hd2ct_digest_hex(BCRYPT_SHA256_ALGORITHM, parts, part_lengths,
                          part_count,
                          digest_hex, sizeof(digest_hex))) {
        goto fail;
    }
    memcpy(job->cache_fingerprint, digest_hex, sizeof(digest_hex));
    job->cache_fingerprint_valid = 1u;
    SecureZeroMemory(numeric_fields, sizeof(numeric_fields));
    SecureZeroMemory(length_prefixes, sizeof(length_prefixes));
    SecureZeroMemory(fields, sizeof(fields));
    SecureZeroMemory(field_lengths, sizeof(field_lengths));
    SecureZeroMemory(parts, sizeof(parts));
    SecureZeroMemory(part_lengths, sizeof(part_lengths));
    SecureZeroMemory(digest_hex, sizeof(digest_hex));
    SecureZeroMemory(ai_system_prompt, sizeof(ai_system_prompt));
    return 1;

fail:
    SecureZeroMemory(job->cache_fingerprint, sizeof(job->cache_fingerprint));
    job->cache_fingerprint_valid = 0u;
    SecureZeroMemory(numeric_fields, sizeof(numeric_fields));
    SecureZeroMemory(length_prefixes, sizeof(length_prefixes));
    SecureZeroMemory(fields, sizeof(fields));
    SecureZeroMemory(field_lengths, sizeof(field_lengths));
    SecureZeroMemory(parts, sizeof(parts));
    SecureZeroMemory(part_lengths, sizeof(part_lengths));
    SecureZeroMemory(digest_hex, sizeof(digest_hex));
    SecureZeroMemory(ai_system_prompt, sizeof(ai_system_prompt));
    return 0;
}

static void hd2ct_process_job(HINTERNET session, HD2CT_BingState *bing_state,
                              HD2CT_WorkerJob *job)
{
    char result[HD2CT_MAX_RESULT + 1u];
    HD2CT_RuntimeSettings settings;
    uint32_t result_bytes = 0;
    uint32_t config_status;
    uint64_t deadline = hd2ct_job_deadline(job);
    uint64_t request_limit;
    uint64_t request_deadline;
    LONG64 backoff_until;
    int successful = 0;
    int request_attempted = 0;
    int cache_hit;
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        hd2ct_complete_job(job, "", 0, 0, 0);
        return;
    }
    if (hd2ct_remaining_ms(deadline) == 0u) {
        hd2ct_build_error(result, sizeof(result), &result_bytes, "EXPIRED");
        hd2ct_set_request_status(1005u);
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        return;
    }
    hd2ct_read_applied_settings(&settings);
    job->target_language = settings.target_language;
    job->interface_type = settings.interface_type;
    job->timeout_seconds = settings.timeout_seconds;
    job->menu_enabled = settings.enabled;
    job->ignore_self = settings.ignore_self;
    job->enemy_slang = settings.enemy_slang;
    if (job->menu_enabled == 0u ||
        (job->ignore_self != 0u && (job->flags & HD2CT_MESSAGE_SELF) != 0u)) {
        hd2ct_complete_job(job, "SKIP\n", 5u, 0, 0);
        return;
    }
    config_status = (uint32_t)InterlockedCompareExchange(&g_status, 0, 0);
    if (config_status == HD2CT_STATUS_WORKER_FAILURE) {
        hd2ct_build_error(result, sizeof(result), &result_bytes, "SERVICE_ERROR");
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        SecureZeroMemory(result, sizeof(result));
        return;
    }
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        hd2ct_complete_job(job, "", 0, 0, 0);
        return;
    }
    config_status = hd2ct_prepare_job_config(job, job->interface_type);
    if (InterlockedCompareExchange(&g_status, 0, 0) ==
        HD2CT_STATUS_WORKER_FAILURE) {
        hd2ct_build_error(result, sizeof(result), &result_bytes, "SERVICE_ERROR");
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        SecureZeroMemory(result, sizeof(result));
        return;
    }
    if (config_status != HD2CT_STATUS_READY) {
        const char *code = config_status == HD2CT_STATUS_MISSING_CONFIG ?
            "MISSING_CONFIG" :
            config_status == HD2CT_STATUS_INVALID_CONFIG ? "INVALID_CONFIG" :
            "SERVICE_ERROR";
        hd2ct_build_error(result, sizeof(result), &result_bytes, code);
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        SecureZeroMemory(result, sizeof(result));
        return;
    }
    (void)hd2ct_set_job_cache_fingerprint(job);
    if (hd2ct_remaining_ms(deadline) == 0) {
        hd2ct_build_error(result, sizeof(result), &result_bytes, "EXPIRED");
        hd2ct_set_request_status(1005u);
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        return;
    }
    AcquireSRWLockExclusive(&g_lock);
    cache_hit = hd2ct_find_cache_locked(job, result, &result_bytes);
    ReleaseSRWLockExclusive(&g_lock);
    if (cache_hit) {
        hd2ct_complete_job(job, result, result_bytes, 1, 0);
        SecureZeroMemory(result, sizeof(result));
        return;
    }
    backoff_until = InterlockedCompareExchange64(&g_backoff_until, 0, 0);
    if ((uint64_t)backoff_until > GetTickCount64()) {
        hd2ct_build_error(result, sizeof(result), &result_bytes, "BACKOFF");
        hd2ct_set_request_status(1004u);
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        return;
    }
    request_limit = GetTickCount64() + (uint64_t)job->timeout_seconds * 1000ull;
    request_deadline = request_limit < deadline ? request_limit : deadline;
    if (InterlockedCompareExchange(&g_status, 0, 0) == HD2CT_STATUS_WORKER_FAILURE) {
        hd2ct_build_error(result, sizeof(result), &result_bytes, "SERVICE_ERROR");
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        SecureZeroMemory(result, sizeof(result));
        return;
    }
    if (hd2ct_remaining_ms(request_deadline) == 0 ||
        hd2ct_job_cancelled(job->slot_index, job->serial)) {
        hd2ct_build_error(result, sizeof(result), &result_bytes, "EXPIRED");
        hd2ct_set_request_status(1005u);
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        return;
    }
    hd2ct_set_slot_request_deadline(job, request_deadline);
    hd2ct_http_worker_request(session, bing_state, job, result, &result_bytes,
                              &successful, &request_attempted, request_deadline);
    if (hd2ct_job_cancelled(job->slot_index, job->serial)) {
        hd2ct_complete_job(job, result, result_bytes, 0, 0);
        SecureZeroMemory(result, sizeof(result));
        return;
    }
    if (hd2ct_remaining_ms(request_deadline) == 0) {
        hd2ct_build_error(result, sizeof(result), &result_bytes, "TIMEOUT");
        successful = 0;
        hd2ct_set_request_status(1002u);
    }
    if (successful) {
        hd2ct_record_success();
    } else if (request_attempted) {
        hd2ct_record_failure();
    }
    if (successful || request_attempted ||
        hd2ct_error_is(result, result_bytes, "RATE_LIMITED")) {
        hd2ct_set_request_status(hd2ct_status_from_result(result, result_bytes));
    }
    if (successful && result_bytes >= 3u && memcmp(result, "OK\n", 3u) == 0 &&
        result_bytes - 3u == job->source_bytes &&
        memcmp(result + 3u, job->source, job->source_bytes) == 0) {
        memcpy(result, "SKIP\n", 5u);
        result[5] = '\0';
        result_bytes = 5u;
    }
    hd2ct_complete_job(job, result, result_bytes, successful, successful);
    SecureZeroMemory(result, sizeof(result));
}

static unsigned __stdcall hd2ct_worker_main(void *parameter)
{
    HINTERNET session = NULL;
    HD2CT_BingState bing_state;
    (void)parameter;
    (void)hd2ct_bing_state_init(&bing_state);
#ifdef HD2CT_TESTING
    if (InterlockedCompareExchange(&g_test_fail_worker_session, 0, 0) != 0) {
        hd2ct_mark_worker_failure();
        return 0;
    }
#endif
    session = WinHttpOpen(L"HD2 Chat Translate/1", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                          WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (session == NULL) {
        hd2ct_mark_worker_failure();
        return 0;
    }
    for (;;) {
        HD2CT_WorkerJob job;
        int have_job = 0;
        AcquireSRWLockExclusive(&g_lock);
        hd2ct_apply_cancel_all_locked();
        while (!have_job && InterlockedCompareExchange(&g_stop_workers, 0, 0) == 0) {
            if (InterlockedCompareExchange(&g_initialized, 0, 0) == 2 &&
                InterlockedCompareExchange(&g_status, 0, 0) != HD2CT_STATUS_WORKER_FAILURE) {
                have_job = hd2ct_take_next_job_locked(&job);
                if (have_job) {
                    break;
                }
            }
            SleepConditionVariableSRW(&g_work_available, &g_lock, INFINITE, 0);
            hd2ct_apply_cancel_all_locked();
        }
        if (InterlockedCompareExchange(&g_stop_workers, 0, 0) != 0) {
            ReleaseSRWLockExclusive(&g_lock);
            break;
        }
        ReleaseSRWLockExclusive(&g_lock);
        if (have_job) {
            hd2ct_process_job(session, &bing_state, &job);
            SecureZeroMemory(&job, sizeof(job));
        }
    }
    if (session != NULL) WinHttpCloseHandle(session);
    hd2ct_bing_state_destroy(&bing_state);
    return 0;
}

static int hd2ct_start_workers(void)
{
    uintptr_t workers[2] = {0, 0};
    unsigned i;
    for (i = 0; i < 2u; ++i) {
#ifdef HD2CT_TESTING
        if (InterlockedCompareExchange(&g_test_fail_worker_create_at, 0, 0) ==
            (LONG)(i + 1u)) {
            workers[i] = 0;
        } else {
            workers[i] = _beginthreadex(NULL, 0, hd2ct_worker_main, NULL, 0, NULL);
        }
#else
        workers[i] = _beginthreadex(NULL, 0, hd2ct_worker_main, NULL, 0, NULL);
#endif
        if (workers[i] == 0) {
            hd2ct_mark_worker_failure();
            if (workers[0] != 0) {
                CloseHandle((HANDLE)workers[0]);
            }
            return 0;
        }
    }
    CloseHandle((HANDLE)workers[0]);
    CloseHandle((HANDLE)workers[1]);
    return 1;
}

static unsigned __stdcall hd2ct_bootstrap_main(void *parameter)
{
    (void)parameter;
#ifdef HD2CT_TESTING
    InterlockedIncrement(&g_test_bootstrap_count);
#endif
    if (!hd2ct_start_workers()) return 0;
    InterlockedExchange(&g_initialized, 2);
    WakeAllConditionVariable(&g_work_available);
    return 0;
}

static void hd2ct_start_bootstrap(void)
{
    uintptr_t thread;
    LONG prior = InterlockedCompareExchange(&g_initialized, 1, 0);
    if (prior != 0) return;
    if (!hd2ct_pin_module()) {
        hd2ct_mark_worker_failure();
        return;
    }
#ifdef HD2CT_TESTING
    if (InterlockedCompareExchange(&g_test_fail_bootstrap_create, 0, 0) != 0) {
        thread = 0;
    } else {
        thread = _beginthreadex(NULL, 0, hd2ct_bootstrap_main, NULL, 0, NULL);
    }
#else
    thread = _beginthreadex(NULL, 0, hd2ct_bootstrap_main, NULL, 0, NULL);
#endif
    if (thread == 0) {
        hd2ct_mark_worker_failure();
        return;
    }
    CloseHandle((HANDLE)thread);
}

uint32_t HD2CT_Submit(const char *token, const char *body, uint32_t bytes,
                      uint32_t flags, const char *reserved)
{
    size_t token_length;
    uint32_t i;
    uint32_t free_slot = HD2CT_JOB_COUNT;
    uint64_t serial;
    (void)reserved;
    if ((flags & ~HD2CT_MESSAGE_SELF) != 0u ||
        body == NULL || bytes == 0 ||
        bytes > HD2CT_MAX_SOURCE || !hd2ct_valid_token(token, &token_length) ||
        !hd2ct_valid_utf8((const unsigned char *)body, bytes, 1)) {
        return 0u;
    }
    if (!TryAcquireSRWLockExclusive(&g_lock)) {
        return 0u;
    }
    hd2ct_apply_cancel_all_locked();
    for (i = 0; i < HD2CT_JOB_COUNT; ++i) {
        if (g_jobs[i].state != HD2CT_SLOT_FREE &&
            strlen(g_jobs[i].token) == token_length &&
            memcmp(g_jobs[i].token, token, token_length) == 0) {
            ReleaseSRWLockExclusive(&g_lock);
            return 0u;
        }
        if (free_slot == HD2CT_JOB_COUNT && g_jobs[i].state == HD2CT_SLOT_FREE) {
            free_slot = i;
        }
    }
    if (free_slot == HD2CT_JOB_COUNT) {
        ReleaseSRWLockExclusive(&g_lock);
        return 0u;
    }
    serial = g_next_serial++;
    if (serial == 0) {
        serial = g_next_serial++;
    }
    SecureZeroMemory(&g_jobs[free_slot], sizeof(g_jobs[free_slot]));
    g_jobs[free_slot].state = HD2CT_SLOT_QUEUED;
    g_jobs[free_slot].serial = serial;
    g_jobs[free_slot].submitted_ms = GetTickCount64();
    g_jobs[free_slot].flags = flags;
    memcpy(g_jobs[free_slot].token, token, token_length);
    g_jobs[free_slot].token[token_length] = '\0';
    memcpy(g_jobs[free_slot].source, body, bytes);
    g_jobs[free_slot].source[bytes] = '\0';
    g_jobs[free_slot].source_bytes = bytes;
    WakeConditionVariable(&g_work_available);
    ReleaseSRWLockExclusive(&g_lock);
    hd2ct_start_bootstrap();
    return 1u;
}

uint32_t HD2CT_Poll(const char *token, char *out, uint32_t capacity, uint32_t *written)
{
    size_t token_length;
    uint32_t i;
    if (written != NULL) {
        *written = 0;
    }
    if (out == NULL || written == NULL || capacity == 0 ||
        !hd2ct_valid_token(token, &token_length)) {
        return 0u;
    }
    if (!TryAcquireSRWLockExclusive(&g_lock)) {
        return 0u;
    }
    hd2ct_apply_cancel_all_locked();
    for (i = 0; i < HD2CT_JOB_COUNT; ++i) {
        HD2CT_JobSlot *slot = &g_jobs[i];
        if (slot->state != HD2CT_SLOT_FREE &&
            strlen(slot->token) == token_length &&
            memcmp(slot->token, token, token_length) == 0) {
            if (slot->state != HD2CT_SLOT_DONE &&
                InterlockedCompareExchange(&g_status, 0, 0) ==
                    HD2CT_STATUS_WORKER_FAILURE) {
                if (slot->state == HD2CT_SLOT_ACTIVE) slot->cancelled = 1;
                hd2ct_build_error(slot->result, sizeof(slot->result),
                                  &slot->result_bytes, "SERVICE_ERROR");
                slot->state = HD2CT_SLOT_DONE;
            }
            if (slot->state == HD2CT_SLOT_ACTIVE &&
                slot->cancelled == 0 &&
                slot->request_deadline_ms != 0 &&
                GetTickCount64() >= slot->request_deadline_ms) {
                slot->cancelled = 1;
                slot->timeout_reported = 1;
                hd2ct_build_error(slot->result, sizeof(slot->result),
                                  &slot->result_bytes, "TIMEOUT");
                hd2ct_set_request_status(1002u);
                hd2ct_record_failure();
            }
            if (slot->state != HD2CT_SLOT_DONE &&
                GetTickCount64() >= slot->submitted_ms + HD2CT_JOB_TTL_MS) {
                hd2ct_build_error(slot->result, sizeof(slot->result),
                                  &slot->result_bytes, "EXPIRED");
                slot->state = HD2CT_SLOT_DONE;
            }
            if (slot->state != HD2CT_SLOT_DONE && slot->timeout_reported == 0) {
                break;
            }
            if (capacity <= slot->result_bytes) {
                ReleaseSRWLockExclusive(&g_lock);
                return 0u;
            }
            memcpy(out, slot->result, slot->result_bytes);
            out[slot->result_bytes] = '\0';
            *written = slot->result_bytes;
            ReleaseSRWLockExclusive(&g_lock);
            return 1u;
        }
    }
    ReleaseSRWLockExclusive(&g_lock);
    return 0u;
}

uint32_t HD2CT_Cancel(const char *token)
{
    size_t token_length;
    uint32_t i;
    if (token == NULL) {
        if (!TryAcquireSRWLockExclusive(&g_lock)) {
            InterlockedExchange(&g_cancel_all_pending, 1);
            WakeAllConditionVariable(&g_work_available);
            return 1u;
        }
        hd2ct_apply_cancel_all_locked();
        InterlockedExchange(&g_cancel_all_pending, 1);
        hd2ct_apply_cancel_all_locked();
        ReleaseSRWLockExclusive(&g_lock);
        WakeAllConditionVariable(&g_work_available);
        return 1u;
    }
    if (!hd2ct_valid_token(token, &token_length)) {
        return 1u;
    }
    if (!TryAcquireSRWLockExclusive(&g_lock)) {
        return 0u;
    }
    hd2ct_apply_cancel_all_locked();
    for (i = 0; i < HD2CT_JOB_COUNT; ++i) {
        HD2CT_JobSlot *slot = &g_jobs[i];
        if (slot->state != HD2CT_SLOT_FREE &&
            strlen(slot->token) == token_length &&
            memcmp(slot->token, token, token_length) == 0) {
            if (slot->state == HD2CT_SLOT_ACTIVE) {
                slot->cancelled = 1;
                slot->timeout_reported = 0;
                SecureZeroMemory(slot->result, sizeof(slot->result));
                slot->result_bytes = 0;
            } else {
                SecureZeroMemory(slot, sizeof(*slot));
            }
            ReleaseSRWLockExclusive(&g_lock);
            return 1u;
        }
    }
    ReleaseSRWLockExclusive(&g_lock);
    return 1u;
}
