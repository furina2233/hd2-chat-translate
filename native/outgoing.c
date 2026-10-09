#include "internal.h"
#include "outgoing.h"
#include "target_languages.generated.h"

#include <bcrypt.h>
#include <process.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#define HD2CT_OUTGOING_CALLSITE_RVA 0x1860272u
#define HD2CT_OUTGOING_CALL_SIGNATURE_RVA 0x186025du
#define HD2CT_OUTGOING_SEND_RVA 0x1097560u
#define HD2CT_OUTGOING_SEND_SIGNATURE_RVA 0x1097560u
#define HD2CT_OUTGOING_ROOT_GLOBAL_RVA 0x347cef0u
#define HD2CT_OUTGOING_NETWORK_GLOBAL_RVA 0x347cda0u
#define HD2CT_OUTGOING_SERVICE_OFFSET 0xc418u
#define HD2CT_OUTGOING_LOCAL_ID_OFFSET 0xb398u
#define HD2CT_OUTGOING_RECEIVER_COUNT_OFFSET 0x16390u
#define HD2CT_OUTGOING_RECEIVERS_OFFSET 0x16398u
#define HD2CT_OUTGOING_RECEIVER_STRIDE 0x20u
#define HD2CT_OUTGOING_EXPECTED_TIMESTAMP 1790161983u
#define HD2CT_OUTGOING_EXPECTED_IMAGE_SIZE 74727424u
#define HD2CT_OUTGOING_EXPECTED_FILE_SIZE 15522408ull
#define HD2CT_OUTGOING_FRESH_MS 2000ull
#define HD2CT_OUTGOING_STATUS_PERIOD_MS 5000ull

#if defined(__GNUC__)
#define HD2CT_NOINLINE __attribute__((noinline))
#else
#define HD2CT_NOINLINE __declspec(noinline)
#endif

enum {
    HD2CT_OUTGOING_EMPTY = 0,
    HD2CT_OUTGOING_WAITING = 1,
    HD2CT_OUTGOING_RAW_READY = 2
};

enum {
    HD2CT_OUTGOING_FAILURE_NONE = 0,
    HD2CT_OUTGOING_FAILURE_TARGET_GATE = 1,
    HD2CT_OUTGOING_FAILURE_PATCH_OWNER = 2,
    HD2CT_OUTGOING_FAILURE_NEAR_MEMORY = 3,
    HD2CT_OUTGOING_FAILURE_CONTEXT = 4,
    HD2CT_OUTGOING_FAILURE_CONTROLLER = 5,
    HD2CT_OUTGOING_FAILURE_PUMP_STALE = 6
};

enum {
    HD2CT_OUTGOING_HOOK_STAGE_NONE = 0,
    HD2CT_OUTGOING_HOOK_STAGE_INSTALL_ALIGN = 1,
    HD2CT_OUTGOING_HOOK_STAGE_INSTALL_READ = 2,
    HD2CT_OUTGOING_HOOK_STAGE_INSTALL_WORD_MISMATCH = 3,
    HD2CT_OUTGOING_HOOK_STAGE_INSTALL_PROTECT_WRITE = 4,
    HD2CT_OUTGOING_HOOK_STAGE_INSTALL_CAS_MISMATCH = 5,
    HD2CT_OUTGOING_HOOK_STAGE_INSTALL_PROTECT_RESTORE = 6,
    HD2CT_OUTGOING_HOOK_STAGE_INSTALL_FLUSH = 7,
    HD2CT_OUTGOING_HOOK_STAGE_RESTORE_PROTECT_WRITE = 8,
    HD2CT_OUTGOING_HOOK_STAGE_RESTORE_CAS_MISMATCH = 9,
    HD2CT_OUTGOING_HOOK_STAGE_RESTORE_PROTECT_RESTORE = 10,
    HD2CT_OUTGOING_HOOK_STAGE_RESTORE_FLUSH = 11
};

typedef struct HD2CT_OutgoingContext {
    uint64_t root;
    uintptr_t service;
    uint64_t local_id;
    uintptr_t network_manager;
    uint32_t receiver_count;
    uint64_t receivers[16];
} HD2CT_OutgoingContext;

typedef struct HD2CT_OutgoingItem {
    uint32_t state;
    uint32_t submitted;
    uint32_t source_bytes;
    uint32_t timeout_seconds;
    uint32_t target_language;
    uint64_t sequence;
    uint64_t deadline_ms;
    uintptr_t service;
    uintptr_t spaces;
    HD2CT_OutgoingContext context;
    char token[HD2CT_MAX_TOKEN + 1u];
    char source[HD2CT_OUTGOING_MAX_SOURCE + 1u];
} HD2CT_OutgoingItem;

typedef void (__cdecl *HD2CT_OriginalSend)(void *, uintptr_t, const char *);

static SRWLOCK g_outgoing_lock = SRWLOCK_INIT;
static HD2CT_OutgoingItem g_outgoing_items[HD2CT_OUTGOING_QUEUE_COUNT];
static uint32_t g_outgoing_head;
static uint32_t g_outgoing_count;
static uint64_t g_outgoing_sequence;
static volatile LONG g_outgoing_master_enabled = 1;
static volatile LONG g_outgoing_enabled = 0;
static volatile LONG g_outgoing_target_language = 3;
static volatile LONG g_outgoing_timeout_seconds = 20;
static volatile LONG g_outgoing_service_ready;
static volatile LONG g_outgoing_game_thread;
static volatile LONG64 g_outgoing_last_pump_ms;
static volatile LONG64 g_outgoing_test_now_ms;
static volatile LONG g_outgoing_hook_active;
static volatile LONG g_outgoing_hook_state;
static volatile LONG g_outgoing_pump_active;
static volatile LONG g_outgoing_failure_code;
static volatile LONG g_outgoing_hook_failure_stage;
static volatile LONG g_outgoing_hook_win32_error;
static volatile LONG g_outgoing_gate_checked;
static volatile LONG g_outgoing_controller_started;
static volatile LONG g_outgoing_queue_pending;
static volatile LONG64 g_outgoing_intercepted;
static volatile LONG64 g_outgoing_called_translated;
static volatile LONG64 g_outgoing_called_original;
static volatile LONG64 g_outgoing_passthrough;
static volatile LONG64 g_outgoing_context_cancelled;
static volatile LONG64 g_outgoing_timeouts;
static volatile LONG64 g_outgoing_last_status_ms;
static uintptr_t g_outgoing_module_base;
static uintptr_t g_outgoing_relay;
static uintptr_t g_outgoing_callsite;
static uint64_t g_outgoing_original_word;
static uint64_t g_outgoing_patched_word;
static HD2CT_OriginalSend g_outgoing_original_send;
static HMODULE g_outgoing_pinned_target;
static HMODULE g_outgoing_pinned_self;
static volatile LONG g_outgoing_retry_ready;
static uint64_t g_outgoing_retry_sequence;
static int g_outgoing_retry_translated;
static char g_outgoing_retry_text[HD2CT_OUTGOING_MAX_SOURCE + 1u];

static void __cdecl hd2ct_outgoing_relay(void *service, uintptr_t spaces,
                                        const char *source);

#ifdef HD2CT_TESTING
static HD2CT_OutgoingContext g_outgoing_test_context;
static volatile LONG g_outgoing_test_context_valid;
static volatile LONG64 g_outgoing_test_send_count;
static char g_outgoing_test_last_send[HD2CT_OUTGOING_MAX_SOURCE + 1u];
static char g_outgoing_test_sent_texts[16u][HD2CT_OUTGOING_MAX_SOURCE + 1u];
static wchar_t g_outgoing_test_status_root[HD2CT_MAX_VALUES_PATH];
static volatile LONG g_outgoing_test_status_root_valid;
static volatile LONG g_outgoing_test_patch_owner_conflict;
static volatile LONG g_outgoing_test_reenter_pump;
static volatile LONG g_outgoing_test_fail_claim_once;
static volatile LONG g_outgoing_test_restore_failure_once;
#endif

static void hd2ct_outgoing_clear_retry(void)
{
    InterlockedExchange(&g_outgoing_retry_ready, 0);
    g_outgoing_retry_sequence = 0u;
    g_outgoing_retry_translated = 0;
    SecureZeroMemory(g_outgoing_retry_text, sizeof(g_outgoing_retry_text));
}

static void hd2ct_outgoing_save_retry(uint64_t sequence,
                                      const char *text, int translated)
{
    InterlockedExchange(&g_outgoing_retry_ready, 0);
    memcpy(g_outgoing_retry_text, text, strlen(text) + 1u);
    g_outgoing_retry_sequence = sequence;
    g_outgoing_retry_translated = translated != 0;
    InterlockedExchange(&g_outgoing_retry_ready, 1);
}

static int hd2ct_outgoing_load_retry(uint64_t sequence,
                                     char *text, int *translated)
{
    if (InterlockedCompareExchange(&g_outgoing_retry_ready, 0, 0) == 0) {
        return 0;
    }
    if (g_outgoing_retry_sequence != sequence) {
        hd2ct_outgoing_clear_retry();
        return 0;
    }
    memcpy(text, g_outgoing_retry_text, strlen(g_outgoing_retry_text) + 1u);
    *translated = g_outgoing_retry_translated;
    return 1;
}

static uint64_t hd2ct_outgoing_now(void)
{
#ifdef HD2CT_TESTING
    return (uint64_t)InterlockedCompareExchange64(&g_outgoing_test_now_ms, 0, 0);
#else
    return GetTickCount64();
#endif
}

static void hd2ct_outgoing_set_failure(uint32_t code)
{
    InterlockedExchange(&g_outgoing_failure_code, (LONG)code);
}

static void hd2ct_outgoing_set_hook_failure(uint32_t stage, DWORD error)
{
    InterlockedExchange(&g_outgoing_hook_failure_stage, (LONG)stage);
    InterlockedExchange(&g_outgoing_hook_win32_error, (LONG)error);
    hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_PATCH_OWNER);
}

static void hd2ct_outgoing_clear_hook_failure(void)
{
    InterlockedExchange(&g_outgoing_hook_win32_error, 0);
    InterlockedExchange(&g_outgoing_hook_failure_stage,
                        HD2CT_OUTGOING_HOOK_STAGE_NONE);
    hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_NONE);
}

static int hd2ct_outgoing_memory_protection(DWORD protection)
{
    DWORD base = protection & 0xffu;
    return (protection & PAGE_GUARD) == 0u && base != PAGE_NOACCESS &&
        (base == PAGE_READONLY || base == PAGE_READWRITE ||
         base == PAGE_WRITECOPY || base == PAGE_EXECUTE_READ ||
         base == PAGE_EXECUTE_READWRITE || base == PAGE_EXECUTE_WRITECOPY);
}

static int hd2ct_outgoing_read_memory_ex(uintptr_t address, void *output,
                                         size_t bytes, DWORD *error_out)
{
    unsigned char *destination = (unsigned char *)output;
    size_t copied = 0u;
    if (error_out != NULL) *error_out = ERROR_SUCCESS;
    if (address == 0u || output == NULL || bytes == 0u ||
        address > UINTPTR_MAX - bytes) return 0;
    while (copied < bytes) {
        MEMORY_BASIC_INFORMATION before;
        MEMORY_BASIC_INFORMATION after;
        uintptr_t current = address + copied;
        uintptr_t region_end;
        size_t available;
        size_t requested;
        SIZE_T received = 0u;
        SIZE_T queried = VirtualQuery((const void *)current, &before, sizeof(before));
        if (queried != sizeof(before)) {
            if (queried == 0u) {
                DWORD error = GetLastError();
                if (error_out != NULL) *error_out = error;
            }
            return 0;
        }
        if (before.State != MEM_COMMIT ||
            !hd2ct_outgoing_memory_protection(before.Protect) ||
            (uintptr_t)before.BaseAddress > current ||
            before.RegionSize > UINTPTR_MAX - (uintptr_t)before.BaseAddress) {
            return 0;
        }
        region_end = (uintptr_t)before.BaseAddress + before.RegionSize;
        available = (size_t)(region_end - current);
        requested = bytes - copied < available ? bytes - copied : available;
        if (requested == 0u) return 0;
        if (!ReadProcessMemory(GetCurrentProcess(), (const void *)current,
                               destination + copied, requested, &received)) {
            DWORD error = GetLastError();
            if (error_out != NULL) *error_out = error;
            return 0;
        }
        if (received != requested) return 0;
        queried = VirtualQuery((const void *)current, &after, sizeof(after));
        if (queried != sizeof(after)) {
            if (queried == 0u) {
                DWORD error = GetLastError();
                if (error_out != NULL) *error_out = error;
            }
            return 0;
        }
        if (before.BaseAddress != after.BaseAddress ||
            before.AllocationBase != after.AllocationBase ||
            before.RegionSize != after.RegionSize ||
            before.State != after.State || before.Protect != after.Protect ||
            before.Type != after.Type) {
            return 0;
        }
        copied += requested;
    }
    return 1;
}

static int hd2ct_outgoing_read_memory(uintptr_t address, void *output,
                                     size_t bytes)
{
    return hd2ct_outgoing_read_memory_ex(address, output, bytes, NULL);
}

static int hd2ct_outgoing_context_equal(const HD2CT_OutgoingContext *left,
                                       const HD2CT_OutgoingContext *right)
{
    uint32_t i;
    if (left->root != right->root || left->service != right->service ||
        left->local_id != right->local_id ||
        left->network_manager != right->network_manager ||
        left->receiver_count != right->receiver_count ||
        left->receiver_count > 16u) return 0;
    for (i = 0u; i < left->receiver_count; ++i) {
        if (left->receivers[i] != right->receivers[i]) return 0;
    }
    return 1;
}

static int hd2ct_outgoing_context_once(HD2CT_OutgoingContext *context,
                                       uintptr_t expected_service)
{
#ifdef HD2CT_TESTING
    if (InterlockedCompareExchange(&g_outgoing_test_context_valid, 0, 0) != 0) {
        *context = g_outgoing_test_context;
        return context->root != 0u && context->service != 0u &&
            context->receiver_count <= 16u &&
            (expected_service == 0u || context->service == expected_service);
    }
#endif
    uintptr_t module_base = g_outgoing_module_base;
    uintptr_t root = 0u;
    uintptr_t service;
    uint8_t marker = 0u;
    unsigned char receiver_records[16u * HD2CT_OUTGOING_RECEIVER_STRIDE];
    uint32_t i;
    HD2CT_OutgoingContext current;
    SecureZeroMemory(receiver_records, sizeof(receiver_records));
    if (module_base == 0u ||
        !hd2ct_outgoing_read_memory(module_base + HD2CT_OUTGOING_ROOT_GLOBAL_RVA,
                                    &root, sizeof(root)) || root == 0u ||
        root > UINTPTR_MAX - HD2CT_OUTGOING_SERVICE_OFFSET) return 0;
    service = root + HD2CT_OUTGOING_SERVICE_OFFSET;
    if ((expected_service != 0u && expected_service != service) ||
        !hd2ct_outgoing_read_memory(service, &marker, sizeof(marker)) ||
        marker == 0u) return 0;
    memset(&current, 0, sizeof(current));
    current.root = (uint64_t)root;
    current.service = service;
    if (!hd2ct_outgoing_read_memory(root + HD2CT_OUTGOING_LOCAL_ID_OFFSET,
                                    &current.local_id, sizeof(current.local_id)) ||
        !hd2ct_outgoing_read_memory(root + HD2CT_OUTGOING_RECEIVER_COUNT_OFFSET,
                                    &current.receiver_count,
                                    sizeof(current.receiver_count)) ||
        current.receiver_count > 16u ||
        !hd2ct_outgoing_read_memory(module_base + HD2CT_OUTGOING_NETWORK_GLOBAL_RVA,
                                    &current.network_manager,
                                    sizeof(current.network_manager))) return 0;
    if (current.receiver_count != 0u &&
        (root > UINTPTR_MAX - HD2CT_OUTGOING_RECEIVERS_OFFSET ||
         !hd2ct_outgoing_read_memory(
             root + HD2CT_OUTGOING_RECEIVERS_OFFSET, receiver_records,
             (size_t)current.receiver_count * HD2CT_OUTGOING_RECEIVER_STRIDE))) {
        SecureZeroMemory(receiver_records, sizeof(receiver_records));
        return 0;
    }
    for (i = 0u; i < current.receiver_count; ++i) {
        memcpy(&current.receivers[i],
               receiver_records + (size_t)i * HD2CT_OUTGOING_RECEIVER_STRIDE,
               sizeof(current.receivers[i]));
    }
    SecureZeroMemory(receiver_records, sizeof(receiver_records));
    *context = current;
    return 1;
}

static int hd2ct_outgoing_capture_context(HD2CT_OutgoingContext *context,
                                         uintptr_t expected_service)
{
    HD2CT_OutgoingContext first;
    HD2CT_OutgoingContext second;
    if (!hd2ct_outgoing_context_once(&first, expected_service) ||
        !hd2ct_outgoing_context_once(&second, expected_service) ||
        !hd2ct_outgoing_context_equal(&first, &second)) return 0;
    *context = second;
    return 1;
}

static int hd2ct_outgoing_copy_source(const char *source,
                                     char destination[HD2CT_OUTGOING_MAX_SOURCE + 1u],
                                     uint32_t *bytes_out)
{
    uintptr_t address = (uintptr_t)source;
    uint32_t offset = 0u;
    if (address == 0u) return 0;
    while (offset <= HD2CT_OUTGOING_MAX_SOURCE) {
        unsigned char chunk[128];
        uint32_t requested = HD2CT_OUTGOING_MAX_SOURCE + 1u - offset;
        uint32_t i;
        if (requested > sizeof(chunk)) requested = sizeof(chunk);
        if (address > UINTPTR_MAX - offset ||
            !hd2ct_outgoing_read_memory(address + offset, chunk, requested)) {
            SecureZeroMemory(chunk, sizeof(chunk));
            return 0;
        }
        for (i = 0u; i < requested; ++i) {
            destination[offset + i] = (char)chunk[i];
            if (chunk[i] == 0u) {
                uint32_t length = offset + i;
                SecureZeroMemory(chunk, sizeof(chunk));
                if (length == 0u || !hd2ct_valid_utf8(
                        (const unsigned char *)destination, length, 1)) return 0;
                *bytes_out = length;
                return 1;
            }
        }
        offset += requested;
        SecureZeroMemory(chunk, sizeof(chunk));
    }
    return 0;
}

static uint32_t hd2ct_outgoing_queue_count(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_outgoing_queue_pending, 0, 0);
}

static void hd2ct_outgoing_publish_queue_count(void)
{
    InterlockedExchange(&g_outgoing_queue_pending, (LONG)g_outgoing_count);
}

static void hd2ct_outgoing_call_original(uintptr_t service, uintptr_t spaces,
                                         const char *source)
{
    HD2CT_OriginalSend original = g_outgoing_original_send;
#ifdef HD2CT_TESTING
    if (InterlockedCompareExchange(&g_outgoing_test_context_valid, 0, 0) != 0) {
        size_t length = hd2ct_bounded_length(source, HD2CT_OUTGOING_MAX_SOURCE);
        if (length <= HD2CT_OUTGOING_MAX_SOURCE) {
            LONG64 count;
            memcpy(g_outgoing_test_last_send, source, length + 1u);
            count = InterlockedIncrement64(&g_outgoing_test_send_count);
            memcpy(g_outgoing_test_sent_texts[(size_t)(count - 1) % 16u],
                   source, length + 1u);
        }
        InterlockedIncrement64(&g_outgoing_called_original);
        if (InterlockedCompareExchange(&g_outgoing_test_reenter_pump, 0, 0) != 0) {
            (void)hd2ct_outgoing_pump();
        }
        (void)service;
        (void)spaces;
        return;
    }
#endif
    if (original != NULL) {
        original((void *)service, spaces, source);
        InterlockedIncrement64(&g_outgoing_called_original);
    }
}

static void hd2ct_outgoing_cancel_item(const HD2CT_OutgoingItem *item)
{
    if (item->submitted != 0u && item->token[0] != '\0') {
        (void)HD2CT_Cancel(item->token);
    }
}

static void hd2ct_outgoing_clear_queue(uint32_t count_as_context_cancel)
{
    HD2CT_OutgoingItem cancelled[HD2CT_OUTGOING_QUEUE_COUNT];
    uint32_t count;
    uint32_t i;
    if (!TryAcquireSRWLockExclusive(&g_outgoing_lock)) return;
    count = g_outgoing_count;
    for (i = 0u; i < count; ++i) {
        cancelled[i] = g_outgoing_items[(g_outgoing_head + i) %
                                        HD2CT_OUTGOING_QUEUE_COUNT];
    }
    memset(g_outgoing_items, 0, sizeof(g_outgoing_items));
    g_outgoing_head = 0u;
    g_outgoing_count = 0u;
    hd2ct_outgoing_publish_queue_count();
    ReleaseSRWLockExclusive(&g_outgoing_lock);
    hd2ct_outgoing_clear_retry();
    for (i = 0u; i < count; ++i) {
        hd2ct_outgoing_cancel_item(&cancelled[i]);
        if (count_as_context_cancel != 0u) {
            InterlockedIncrement64(&g_outgoing_context_cancelled);
        }
        SecureZeroMemory(&cancelled[i], sizeof(cancelled[i]));
    }
}

static int hd2ct_outgoing_flush_raw(const HD2CT_OutgoingContext *current)
{
    HD2CT_OutgoingItem pending[HD2CT_OUTGOING_QUEUE_COUNT];
    uint32_t count;
    uint32_t i;
    if (InterlockedCompareExchange(&g_outgoing_pump_active, 1, 0) != 0) return 0;
    if (!TryAcquireSRWLockExclusive(&g_outgoing_lock)) {
        InterlockedExchange(&g_outgoing_pump_active, 0);
        return 0;
    }
    count = g_outgoing_count;
    for (i = 0u; i < count; ++i) {
        pending[i] = g_outgoing_items[(g_outgoing_head + i) %
                                      HD2CT_OUTGOING_QUEUE_COUNT];
        if (!hd2ct_outgoing_context_equal(&pending[i].context, current)) {
            ReleaseSRWLockExclusive(&g_outgoing_lock);
            hd2ct_outgoing_clear_queue(1u);
            hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_CONTEXT);
            InterlockedExchange(&g_outgoing_pump_active, 0);
            return 0;
        }
    }
    memset(g_outgoing_items, 0, sizeof(g_outgoing_items));
    g_outgoing_head = 0u;
    g_outgoing_count = 0u;
    hd2ct_outgoing_publish_queue_count();
    ReleaseSRWLockExclusive(&g_outgoing_lock);
    for (i = 0u; i < count; ++i) {
        HD2CT_OutgoingContext latest;
        hd2ct_outgoing_cancel_item(&pending[i]);
        if (!hd2ct_outgoing_capture_context(&latest, pending[i].service) ||
            !hd2ct_outgoing_context_equal(&pending[i].context, &latest)) {
            uint32_t remaining;
            hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_CONTEXT);
            InterlockedIncrement64(&g_outgoing_context_cancelled);
            SecureZeroMemory(&pending[i], sizeof(pending[i]));
            for (remaining = i + 1u; remaining < count; ++remaining) {
                hd2ct_outgoing_cancel_item(&pending[remaining]);
                InterlockedIncrement64(&g_outgoing_context_cancelled);
                SecureZeroMemory(&pending[remaining], sizeof(pending[remaining]));
            }
            InterlockedExchange(&g_outgoing_pump_active, 0);
            return 0;
        }
        hd2ct_outgoing_call_original(pending[i].service, pending[i].spaces,
                                     pending[i].source);
        SecureZeroMemory(&pending[i], sizeof(pending[i]));
    }
    InterlockedExchange(&g_outgoing_pump_active, 0);
    return 1;
}

static int hd2ct_outgoing_make_token(char *token, size_t capacity,
                                     uint64_t sequence)
{
    int length = snprintf(token, capacity, "out_%llu",
                          (unsigned long long)sequence);
    return length > 0 && (size_t)length < capacity;
}

static int hd2ct_outgoing_intercept(uintptr_t service, uintptr_t spaces,
                                    const char *source, int caller_ok)
{
    HD2CT_OutgoingContext context;
    HD2CT_OutgoingItem item;
    char source_copy[HD2CT_OUTGOING_MAX_SOURCE + 1u];
    uint32_t source_bytes = 0u;
    uint32_t target_language;
    uint32_t timeout_seconds;
    uint64_t sequence;
    uint32_t index;
    uint32_t was_full = 0u;
    int accepted = 0;
    if (!caller_ok) return 0;
    if (InterlockedCompareExchange(&g_outgoing_game_thread, 0, 0) !=
            (LONG)GetCurrentThreadId() ||
        InterlockedCompareExchange(&g_outgoing_service_ready, 0, 0) == 0 ||
        InterlockedCompareExchange(&g_outgoing_master_enabled, 0, 0) == 0 ||
        InterlockedCompareExchange(&g_outgoing_enabled, 0, 0) == 0 ||
        InterlockedCompareExchange(&g_outgoing_hook_active, 0, 0) == 0 ||
        InterlockedCompareExchange(&g_outgoing_hook_state, 0, 0) != 1) {
        return 0;
    }
    {
        uint64_t last_pump = (uint64_t)InterlockedCompareExchange64(
            &g_outgoing_last_pump_ms, 0, 0);
        uint64_t now_ms = hd2ct_outgoing_now();
        if (last_pump == 0u || now_ms < last_pump ||
            now_ms - last_pump > HD2CT_OUTGOING_FRESH_MS) {
            hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_PUMP_STALE);
            return 0;
        }
    }
    if (!hd2ct_outgoing_capture_context(&context, service) ||
        !hd2ct_outgoing_copy_source(source, source_copy, &source_bytes)) {
        SecureZeroMemory(source_copy, sizeof(source_copy));
        hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_CONTEXT);
        return 0;
    }
    if (!TryAcquireSRWLockExclusive(&g_outgoing_lock)) {
        SecureZeroMemory(source_copy, sizeof(source_copy));
        return 0;
    }
    if (g_outgoing_count >= HD2CT_OUTGOING_QUEUE_COUNT) {
        was_full = 1u;
        ReleaseSRWLockExclusive(&g_outgoing_lock);
        if (was_full != 0u && hd2ct_outgoing_flush_raw(&context)) {
            hd2ct_outgoing_call_original(service, spaces, source);
            InterlockedIncrement64(&g_outgoing_passthrough);
            SecureZeroMemory(source_copy, sizeof(source_copy));
            return 2;
        }
        SecureZeroMemory(source_copy, sizeof(source_copy));
        return 0;
    }
    memset(&item, 0, sizeof(item));
    memcpy(item.source, source_copy, source_bytes + 1u);
    sequence = ++g_outgoing_sequence;
    if (sequence == 0u) sequence = ++g_outgoing_sequence;
    target_language = (uint32_t)InterlockedCompareExchange(
        &g_outgoing_target_language, 0, 0);
    timeout_seconds = (uint32_t)InterlockedCompareExchange(
        &g_outgoing_timeout_seconds, 0, 0);
    if (target_language == 0u || target_language > HD2CT_TARGET_LANGUAGE_COUNT) {
        target_language = HD2CT_DEFAULT_OUTGOING_TARGET_LANGUAGE;
    }
    if (timeout_seconds != 10u && timeout_seconds != 20u &&
        timeout_seconds != 30u) timeout_seconds = HD2CT_DEFAULT_TIMEOUT_SECONDS;
    index = (g_outgoing_head + g_outgoing_count) % HD2CT_OUTGOING_QUEUE_COUNT;
    memset(&g_outgoing_items[index], 0, sizeof(g_outgoing_items[index]));
    g_outgoing_items[index].state = HD2CT_OUTGOING_RAW_READY;
    g_outgoing_items[index].source_bytes = source_bytes;
    g_outgoing_items[index].timeout_seconds = timeout_seconds;
    g_outgoing_items[index].target_language = target_language;
    g_outgoing_items[index].sequence = sequence;
    g_outgoing_items[index].deadline_ms = hd2ct_outgoing_now() +
        (uint64_t)timeout_seconds * 1000ull;
    g_outgoing_items[index].service = service;
    g_outgoing_items[index].spaces = spaces;
    g_outgoing_items[index].context = context;
    memcpy(g_outgoing_items[index].source, item.source, source_bytes + 1u);
    if (!hd2ct_outgoing_make_token(g_outgoing_items[index].token,
                                   sizeof(g_outgoing_items[index].token), sequence)) {
        memset(&g_outgoing_items[index], 0, sizeof(g_outgoing_items[index]));
        ReleaseSRWLockExclusive(&g_outgoing_lock);
        SecureZeroMemory(source_copy, sizeof(source_copy));
        return 0;
    }
    ++g_outgoing_count;
    hd2ct_outgoing_publish_queue_count();
    item = g_outgoing_items[index];
    ReleaseSRWLockExclusive(&g_outgoing_lock);
    SecureZeroMemory(source_copy, sizeof(source_copy));

    if (source_bytes <= HD2CT_OUTGOING_SEND_LIMIT &&
        hd2ct_submit_outgoing(item.token, item.source, source_bytes,
                              target_language, timeout_seconds) != 0u) {
        accepted = 1;
    }
    if (TryAcquireSRWLockExclusive(&g_outgoing_lock)) {
        uint32_t i;
        for (i = 0u; i < g_outgoing_count; ++i) {
            HD2CT_OutgoingItem *queued = &g_outgoing_items[
                (g_outgoing_head + i) % HD2CT_OUTGOING_QUEUE_COUNT];
            if (queued->sequence == sequence) {
                queued->state = accepted ? HD2CT_OUTGOING_WAITING :
                    HD2CT_OUTGOING_RAW_READY;
                queued->submitted = accepted ? 1u : 0u;
                break;
            }
        }
        ReleaseSRWLockExclusive(&g_outgoing_lock);
    }
    InterlockedIncrement64(&g_outgoing_intercepted);
    hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_NONE);
    return 1;
}

static int hd2ct_outgoing_read_head(HD2CT_OutgoingItem *item)
{
    int found = 0;
    if (!TryAcquireSRWLockExclusive(&g_outgoing_lock)) return 0;
    if (g_outgoing_count != 0u) {
        *item = g_outgoing_items[g_outgoing_head];
        found = 1;
    }
    ReleaseSRWLockExclusive(&g_outgoing_lock);
    return found;
}

static int hd2ct_outgoing_claim_head(uint64_t sequence,
                                     HD2CT_OutgoingItem *claimed)
{
#ifdef HD2CT_TESTING
    if (InterlockedExchange(&g_outgoing_test_fail_claim_once, 0) != 0) return 0;
#endif
    if (!TryAcquireSRWLockExclusive(&g_outgoing_lock)) return 0;
    if (g_outgoing_count == 0u ||
        g_outgoing_items[g_outgoing_head].sequence != sequence) {
        ReleaseSRWLockExclusive(&g_outgoing_lock);
        return -1;
    }
    *claimed = g_outgoing_items[g_outgoing_head];
    SecureZeroMemory(&g_outgoing_items[g_outgoing_head],
                     sizeof(g_outgoing_items[g_outgoing_head]));
    g_outgoing_head = (g_outgoing_head + 1u) % HD2CT_OUTGOING_QUEUE_COUNT;
    --g_outgoing_count;
    if (g_outgoing_count == 0u) g_outgoing_head = 0u;
    hd2ct_outgoing_publish_queue_count();
    ReleaseSRWLockExclusive(&g_outgoing_lock);
    return 1;
}

static int hd2ct_outgoing_context_is_current(const HD2CT_OutgoingItem *item)
{
    HD2CT_OutgoingContext current;
    if (!hd2ct_outgoing_capture_context(&current, item->service)) return 0;
    return hd2ct_outgoing_context_equal(&item->context, &current);
}

static int hd2ct_outgoing_prepare_head(HD2CT_OutgoingItem *item,
                                      char send_text[HD2CT_OUTGOING_MAX_SOURCE + 1u],
                                      int *translated_out)
{
    char result[HD2CT_MAX_RESULT + 1u];
    uint32_t written = 0u;
    uint64_t now_ms = hd2ct_outgoing_now();
    int enabled = InterlockedCompareExchange(&g_outgoing_master_enabled, 0, 0) != 0 &&
        InterlockedCompareExchange(&g_outgoing_enabled, 0, 0) != 0;
    if (item->state == HD2CT_OUTGOING_WAITING) {
        if (!enabled || now_ms >= item->deadline_ms) {
            hd2ct_outgoing_cancel_item(item);
            item->state = HD2CT_OUTGOING_RAW_READY;
            item->submitted = 0u;
            if (enabled) InterlockedIncrement64(&g_outgoing_timeouts);
        } else if (hd2ct_poll_job(item->token, result, sizeof(result), &written) != 0u) {
            (void)HD2CT_Cancel(item->token);
            item->submitted = 0u;
            item->state = HD2CT_OUTGOING_RAW_READY;
            if (written > 3u && written <= HD2CT_MAX_RESULT &&
                memcmp(result, "OK\n", 3u) == 0 &&
                written - 3u <= HD2CT_OUTGOING_SEND_LIMIT &&
                hd2ct_valid_utf8((const unsigned char *)result + 3u,
                                 written - 3u, 1)) {
                memcpy(send_text, result + 3u, written - 3u);
                send_text[written - 3u] = '\0';
                *translated_out = 1;
                SecureZeroMemory(result, sizeof(result));
                return 1;
            }
            if (written == sizeof("ERR\nTIMEOUT") - 1u &&
                memcmp(result, "ERR\nTIMEOUT", sizeof("ERR\nTIMEOUT") - 1u) == 0) {
                InterlockedIncrement64(&g_outgoing_timeouts);
            }
            SecureZeroMemory(result, sizeof(result));
        } else {
            SecureZeroMemory(result, sizeof(result));
            return 0;
        }
    }
    if (item->state != HD2CT_OUTGOING_RAW_READY) return 0;
    memcpy(send_text, item->source, item->source_bytes + 1u);
    *translated_out = 0;
    return 1;
}

int hd2ct_outgoing_pump(void)
{
    HD2CT_OutgoingItem item;
    HD2CT_OutgoingItem claimed;
    char send_text[HD2CT_OUTGOING_MAX_SOURCE + 1u];
    int translated = 0;
    int claim_result;
    int sent = 0;
    uint64_t now_ms;
    if (InterlockedCompareExchange(&g_outgoing_game_thread, 0, 0) == 0) {
        LONG prior = InterlockedCompareExchange(&g_outgoing_game_thread,
                                                (LONG)GetCurrentThreadId(), 0);
        if (prior != 0 && prior != (LONG)GetCurrentThreadId()) return 0;
    }
    if (InterlockedCompareExchange(&g_outgoing_game_thread, 0, 0) !=
        (LONG)GetCurrentThreadId()) return 0;
    if (InterlockedCompareExchange(&g_outgoing_pump_active, 1, 0) != 0) {
        return 0;
    }
    SecureZeroMemory(&item, sizeof(item));
    SecureZeroMemory(&claimed, sizeof(claimed));
    SecureZeroMemory(send_text, sizeof(send_text));
    now_ms = hd2ct_outgoing_now();
    InterlockedExchange64(&g_outgoing_last_pump_ms, (LONG64)now_ms);
    if (!hd2ct_outgoing_read_head(&item)) {
        hd2ct_outgoing_clear_retry();
        goto cleanup;
    }
    if (!hd2ct_outgoing_context_is_current(&item)) {
        hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_CONTEXT);
        hd2ct_outgoing_clear_queue(1u);
        goto cleanup;
    }
    {
        int enabled = InterlockedCompareExchange(&g_outgoing_master_enabled, 0, 0) != 0 &&
            InterlockedCompareExchange(&g_outgoing_enabled, 0, 0) != 0;
        int expired = item.state == HD2CT_OUTGOING_WAITING &&
            now_ms >= item.deadline_ms;
        if (!enabled || expired) {
            hd2ct_outgoing_clear_retry();
            if (!hd2ct_outgoing_prepare_head(&item, send_text, &translated)) {
                goto cleanup;
            }
        } else if (!hd2ct_outgoing_load_retry(item.sequence, send_text,
                                              &translated) &&
                   !hd2ct_outgoing_prepare_head(&item, send_text,
                                                &translated)) {
            goto cleanup;
        }
    }
    if (!hd2ct_outgoing_context_is_current(&item)) {
        hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_CONTEXT);
        hd2ct_outgoing_clear_queue(1u);
        goto cleanup;
    }
    claim_result = hd2ct_outgoing_claim_head(item.sequence, &claimed);
    if (claim_result != 1) {
        if (claim_result == 0) {
            hd2ct_outgoing_save_retry(item.sequence, send_text, translated);
        } else {
            hd2ct_outgoing_clear_retry();
        }
        goto cleanup;
    }
    hd2ct_outgoing_clear_retry();
    hd2ct_outgoing_call_original(claimed.service, claimed.spaces, send_text);
    if (translated) InterlockedIncrement64(&g_outgoing_called_translated);
    hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_NONE);

    sent = 1;
cleanup:
    SecureZeroMemory(&item, sizeof(item));
    SecureZeroMemory(&claimed, sizeof(claimed));
    SecureZeroMemory(send_text, sizeof(send_text));
    InterlockedExchange(&g_outgoing_pump_active, 0);
    return sent;
}

static int hd2ct_outgoing_bytes_match(const unsigned char *left,
                                      const unsigned char *right,
                                      size_t bytes)
{
    return memcmp(left, right, bytes) == 0;
}

static int hd2ct_outgoing_hash_module_file(HMODULE module)
{
    static const unsigned char expected_hash[32] = {
        0x2e, 0x2c, 0x3b, 0x7c, 0x25, 0x00, 0x64, 0x6d,
        0xad, 0xd5, 0xf2, 0xb4, 0xc6, 0xe0, 0x50, 0x4d,
        0xbb, 0x7e, 0x78, 0x96, 0x13, 0x9f, 0x64, 0xcd,
        0xdc, 0x0d, 0x18, 0x13, 0xc7, 0x18, 0xf5, 0x1e
    };
    wchar_t path[HD2CT_MAX_VALUES_PATH];
    unsigned char input[65536];
    unsigned char digest[32];
    unsigned char *hash_object = NULL;
    DWORD received = 0u;
    ULONG object_bytes = 0u;
    ULONG digest_bytes = 0u;
    ULONG result_bytes = 0u;
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    HANDLE file = INVALID_HANDLE_VALUE;
    LARGE_INTEGER file_size;
    uint64_t total = 0u;
    DWORD path_length;
    int valid = 0;
    path_length = GetModuleFileNameW(module, path,
                                     (DWORD)(sizeof(path) / sizeof(path[0])));
    if (path_length == 0u ||
        path_length >= sizeof(path) / sizeof(path[0])) goto cleanup;
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                       NULL);
    if (file == INVALID_HANDLE_VALUE) goto cleanup;
    {
        BY_HANDLE_FILE_INFORMATION information;
        if (!GetFileInformationByHandle(file, &information) ||
            (information.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY |
              FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DEVICE)) != 0u ||
            !GetFileSizeEx(file, &file_size) || file_size.QuadPart < 0 ||
            (uint64_t)file_size.QuadPart != HD2CT_OUTGOING_EXPECTED_FILE_SIZE) {
            goto cleanup;
        }
    }
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM,
                                    NULL, 0u) < 0 ||
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                          (PUCHAR)&object_bytes, sizeof(object_bytes),
                          &result_bytes, 0u) < 0 || object_bytes == 0u ||
        BCryptGetProperty(algorithm, BCRYPT_HASH_LENGTH,
                          (PUCHAR)&digest_bytes, sizeof(digest_bytes),
                          &result_bytes, 0u) < 0 || digest_bytes != sizeof(digest) ||
        (hash_object = (unsigned char *)HeapAlloc(GetProcessHeap(), 0,
                                                   object_bytes)) == NULL ||
        BCryptCreateHash(algorithm, &hash, hash_object, object_bytes,
                         NULL, 0u, 0u) < 0) goto cleanup;
    for (;;) {
        if (!ReadFile(file, input, sizeof(input), &received, NULL)) goto cleanup;
        if (received == 0u) break;
        total += received;
        if (total > HD2CT_OUTGOING_EXPECTED_FILE_SIZE ||
            BCryptHashData(hash, input, received, 0u) < 0) goto cleanup;
    }
    if (total != HD2CT_OUTGOING_EXPECTED_FILE_SIZE ||
        BCryptFinishHash(hash, digest, sizeof(digest), 0u) < 0 ||
        !hd2ct_outgoing_bytes_match(digest, expected_hash, sizeof(digest))) {
        goto cleanup;
    }
    valid = 1;

cleanup:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (hash != NULL) BCryptDestroyHash(hash);
    if (algorithm != NULL) BCryptCloseAlgorithmProvider(algorithm, 0u);
    if (hash_object != NULL) {
        SecureZeroMemory(hash_object, object_bytes);
        HeapFree(GetProcessHeap(), 0, hash_object);
    }
    SecureZeroMemory(path, sizeof(path));
    SecureZeroMemory(input, sizeof(input));
    SecureZeroMemory(digest, sizeof(digest));
    return valid;
}

static int hd2ct_outgoing_module_path_valid(HMODULE module)
{
    wchar_t path[HD2CT_MAX_VALUES_PATH];
    DWORD length = GetModuleFileNameW(module, path,
        (DWORD)(sizeof(path) / sizeof(path[0])));
    const wchar_t *file;
    const wchar_t *directory_end;
    const wchar_t *directory_start;
    size_t file_length;
    size_t directory_length;
    int valid = 0;
    if (length == 0u || length >= sizeof(path) / sizeof(path[0])) goto cleanup;
    file = wcsrchr(path, L'\\');
    if (file == NULL || file == path) goto cleanup;
    ++file;
    directory_end = file - 2;
    directory_start = directory_end;
    while (directory_start > path && directory_start[-1] != L'\\') {
        --directory_start;
    }
    file_length = wcslen(file);
    directory_length = (size_t)(directory_end - directory_start + 1);
    valid = file_length == 8u && _wcsicmp(file, L"game.dll") == 0 &&
        directory_length == 4u && _wcsnicmp(directory_start, L"game", 4u) == 0;

cleanup:
    SecureZeroMemory(path, sizeof(path));
    return valid;
}

static int hd2ct_outgoing_pin_lifetimes(HMODULE target)
{
    HMODULE pinned_target = NULL;
    HMODULE pinned_self = NULL;
    if (g_outgoing_pinned_target != NULL && g_outgoing_pinned_self != NULL) {
        return 1;
    }
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                            GET_MODULE_HANDLE_EX_FLAG_PIN,
                            (LPCWSTR)(uintptr_t)target, &pinned_target) ||
        pinned_target != target ||
        !GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                            GET_MODULE_HANDLE_EX_FLAG_PIN,
                            (LPCWSTR)(uintptr_t)&hd2ct_outgoing_relay,
                            &pinned_self)) {
        return 0;
    }
    g_outgoing_pinned_target = pinned_target;
    g_outgoing_pinned_self = pinned_self;
    return 1;
}

static int hd2ct_outgoing_validate_target(HMODULE *module_out,
                                         uintptr_t *send_out)
{
    static const unsigned char call_signature[26] = {
        0x48, 0x8b, 0x0d, 0x8c, 0xcc, 0xc1, 0x01,
        0x4c, 0x8d, 0x87, 0xd4, 0x16, 0x00, 0x00,
        0x48, 0x81, 0xc1, 0x18, 0xc4, 0x00, 0x00,
        0xe8, 0xe9, 0x72, 0x83, 0xff
    };
    static const unsigned char send_signature[32] = {
        0x41, 0x56, 0x41, 0x57, 0x48, 0x81, 0xec, 0x78,
        0x04, 0x00, 0x00, 0x48, 0x8b, 0x05, 0x9e, 0x4a,
        0x5a, 0x01, 0x48, 0x33, 0xc4, 0x48, 0x89, 0x84,
        0x24, 0x50, 0x04, 0x00, 0x00, 0x80, 0x39, 0x00
    };
    HMODULE module = GetModuleHandleW(L"game.dll");
    uintptr_t base = (uintptr_t)module;
    IMAGE_DOS_HEADER dos;
    IMAGE_NT_HEADERS64 nt;
    unsigned char call_bytes[sizeof(call_signature)];
    unsigned char send_bytes[sizeof(send_signature)];
    int32_t displacement;
    uintptr_t call_target;
    if (module == NULL || !hd2ct_outgoing_module_path_valid(module) ||
        !hd2ct_outgoing_read_memory(base, &dos, sizeof(dos)) ||
        dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0 ||
        (uint32_t)dos.e_lfanew > 0x100000u ||
        !hd2ct_outgoing_read_memory(base + (uintptr_t)dos.e_lfanew, &nt,
                                    sizeof(nt)) ||
        nt.Signature != IMAGE_NT_SIGNATURE ||
        nt.FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
        nt.FileHeader.TimeDateStamp != HD2CT_OUTGOING_EXPECTED_TIMESTAMP ||
        nt.OptionalHeader.SizeOfImage != HD2CT_OUTGOING_EXPECTED_IMAGE_SIZE ||
        nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
        !hd2ct_outgoing_hash_module_file(module) ||
        HD2CT_OUTGOING_CALL_SIGNATURE_RVA + sizeof(call_bytes) >
            HD2CT_OUTGOING_EXPECTED_IMAGE_SIZE ||
        HD2CT_OUTGOING_SEND_SIGNATURE_RVA + sizeof(send_bytes) >
            HD2CT_OUTGOING_EXPECTED_IMAGE_SIZE ||
        !hd2ct_outgoing_read_memory(base + HD2CT_OUTGOING_CALL_SIGNATURE_RVA,
                                    call_bytes, sizeof(call_bytes)) ||
        !hd2ct_outgoing_read_memory(base + HD2CT_OUTGOING_SEND_SIGNATURE_RVA,
                                    send_bytes, sizeof(send_bytes)) ||
        !hd2ct_outgoing_bytes_match(call_bytes, call_signature,
                                    sizeof(call_signature)) ||
        !hd2ct_outgoing_bytes_match(send_bytes, send_signature,
                                    sizeof(send_signature))) {
        return 0;
    }
    memcpy(&displacement, call_bytes + 22u, sizeof(displacement));
    call_target = base + HD2CT_OUTGOING_CALLSITE_RVA + 5u +
        (intptr_t)displacement;
    if (call_target != base + HD2CT_OUTGOING_SEND_RVA) return 0;
    *module_out = module;
    *send_out = base + HD2CT_OUTGOING_SEND_RVA;
    return 1;
}

static int hd2ct_outgoing_displacement_fits(uintptr_t call_end,
                                           uintptr_t target)
{
    int64_t distance = (int64_t)target - (int64_t)call_end;
    return distance >= INT32_MIN && distance <= INT32_MAX;
}

static uintptr_t hd2ct_outgoing_allocate_near(uintptr_t call_address)
{
    SYSTEM_INFO information;
    uintptr_t granularity;
    uintptr_t aligned;
    uint32_t attempt;
    GetSystemInfo(&information);
    granularity = information.dwAllocationGranularity;
    if (granularity == 0u) return 0u;
    aligned = call_address - call_address % granularity;
    for (attempt = 0u; attempt < 4096u; ++attempt) {
        uint32_t side;
        for (side = 0u; side < 2u; ++side) {
            uintptr_t candidate;
            void *allocation;
            if (attempt == 0u && side == 1u) continue;
            if (side == 0u) {
                if (aligned > UINTPTR_MAX - (uintptr_t)attempt * granularity) continue;
                candidate = aligned + (uintptr_t)attempt * granularity;
            } else {
                if (aligned < (uintptr_t)attempt * granularity) continue;
                candidate = aligned - (uintptr_t)attempt * granularity;
            }
            if (!hd2ct_outgoing_displacement_fits(call_address + 5u,
                                                   candidate)) continue;
            allocation = VirtualAlloc((void *)candidate, granularity,
                                      MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
            if (allocation != NULL) {
                if (!hd2ct_outgoing_displacement_fits(call_address + 5u,
                                                       (uintptr_t)allocation)) {
                    VirtualFree(allocation, 0u, MEM_RELEASE);
                    continue;
                }
                return (uintptr_t)allocation;
            }
        }
    }
    return 0u;
}

static int hd2ct_outgoing_restore_protection(uintptr_t address,
                                             DWORD protection,
                                             DWORD *error_out)
{
#ifdef HD2CT_TESTING
    if (InterlockedExchange(&g_outgoing_test_restore_failure_once, 0) != 0) {
        SetLastError(ERROR_ACCESS_DENIED);
        {
            DWORD error = GetLastError();
            if (error_out != NULL) *error_out = error;
        }
        return 0;
    }
#endif
    if (!VirtualProtect((void *)address, sizeof(uint64_t), protection,
                        &protection)) {
        DWORD error = GetLastError();
        if (error_out != NULL) *error_out = error;
        return 0;
    }
    if (error_out != NULL) *error_out = ERROR_SUCCESS;
    return 1;
}

static int hd2ct_outgoing_page_is_writable(uintptr_t address)
{
    MEMORY_BASIC_INFORMATION information;
    SIZE_T queried = VirtualQuery((const void *)address, &information,
                                  sizeof(information));
    if (queried != sizeof(information)) {
        if (queried == 0u) {
            DWORD error = GetLastError();
            (void)error;
        }
        return 0;
    }
    if (information.State != MEM_COMMIT ||
        (information.Protect & PAGE_GUARD) != 0u) return 0;
    switch (information.Protect & 0xffu) {
    case PAGE_READWRITE:
    case PAGE_WRITECOPY:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return 1;
    default:
        return 0;
    }
}

static int hd2ct_outgoing_swap_word(uintptr_t address, uint64_t expected,
                                    uint64_t replacement, int installing,
                                    int *replacement_present)
{
    DWORD old_protection = 0u;
    DWORD error = ERROR_SUCCESS;
    LONG64 observed;
    uint32_t protect_write_stage = installing ?
        HD2CT_OUTGOING_HOOK_STAGE_INSTALL_PROTECT_WRITE :
        HD2CT_OUTGOING_HOOK_STAGE_RESTORE_PROTECT_WRITE;
    uint32_t cas_mismatch_stage = installing ?
        HD2CT_OUTGOING_HOOK_STAGE_INSTALL_CAS_MISMATCH :
        HD2CT_OUTGOING_HOOK_STAGE_RESTORE_CAS_MISMATCH;
    uint32_t protect_restore_stage = installing ?
        HD2CT_OUTGOING_HOOK_STAGE_INSTALL_PROTECT_RESTORE :
        HD2CT_OUTGOING_HOOK_STAGE_RESTORE_PROTECT_RESTORE;
    uint32_t flush_stage = installing ?
        HD2CT_OUTGOING_HOOK_STAGE_INSTALL_FLUSH :
        HD2CT_OUTGOING_HOOK_STAGE_RESTORE_FLUSH;
    if (replacement_present != NULL) *replacement_present = 0;
    if (!VirtualProtect((void *)address, sizeof(uint64_t),
                        PAGE_EXECUTE_READWRITE, &old_protection)) {
        error = GetLastError();
        hd2ct_outgoing_set_hook_failure(protect_write_stage, error);
        return 0;
    }
    observed = InterlockedCompareExchange64((volatile LONG64 *)address,
                                             (LONG64)replacement,
                                             (LONG64)expected);
    if ((uint64_t)observed == expected && replacement_present != NULL) {
        *replacement_present = 1;
    }
    if (!hd2ct_outgoing_restore_protection(address, old_protection, &error)) {
        if (installing && (uint64_t)observed == expected) {
            if (hd2ct_outgoing_page_is_writable(address)) {
                LONG64 rollback = InterlockedCompareExchange64(
                    (volatile LONG64 *)address, (LONG64)expected,
                    (LONG64)replacement);
                if ((uint64_t)rollback == replacement) {
                    if (replacement_present != NULL) *replacement_present = 0;
                    if (!FlushInstructionCache(GetCurrentProcess(),
                                               (const void *)address,
                                               sizeof(uint64_t))) {
                        DWORD rollback_error = GetLastError();
                        (void)rollback_error;
                    }
                } else if (replacement_present != NULL) {
                    uint64_t current_word = 0u;
                    *replacement_present =
                        hd2ct_outgoing_read_memory(address, &current_word,
                                                   sizeof(current_word)) &&
                        current_word == replacement;
                }
            } else if (replacement_present != NULL) {
                uint64_t current_word = 0u;
                *replacement_present =
                    hd2ct_outgoing_read_memory(address, &current_word,
                                               sizeof(current_word)) &&
                    current_word == replacement;
            }
        }
        {
            DWORD retry_error = ERROR_SUCCESS;
            if (!hd2ct_outgoing_restore_protection(address, old_protection,
                                                   &retry_error)) {
                (void)retry_error;
            }
        }
        hd2ct_outgoing_set_hook_failure(protect_restore_stage, error);
        return 0;
    }
    if ((uint64_t)observed != expected) {
        hd2ct_outgoing_set_hook_failure(cas_mismatch_stage, ERROR_SUCCESS);
        return 0;
    }
    if (!FlushInstructionCache(GetCurrentProcess(), (const void *)address,
                               sizeof(uint64_t))) {
        DWORD flush_error = GetLastError();
        if (installing) {
            DWORD rollback_protection = 0u;
            LONG64 rollback;
            DWORD restore_error = ERROR_SUCCESS;
            if (!VirtualProtect((void *)address, sizeof(uint64_t),
                                PAGE_EXECUTE_READWRITE,
                                &rollback_protection)) {
                DWORD rollback_error = GetLastError();
                hd2ct_outgoing_set_hook_failure(protect_write_stage,
                                                rollback_error);
                return 0;
            }
            rollback = InterlockedCompareExchange64(
                (volatile LONG64 *)address, (LONG64)expected,
                (LONG64)replacement);
            if ((uint64_t)rollback != replacement) {
                int restored = hd2ct_outgoing_restore_protection(
                    address, rollback_protection, &restore_error);
                if (!restored) {
                    hd2ct_outgoing_set_hook_failure(protect_restore_stage,
                                                    restore_error);
                } else {
                    hd2ct_outgoing_set_hook_failure(cas_mismatch_stage,
                                                    ERROR_SUCCESS);
                }
                if (replacement_present != NULL) {
                    uint64_t current_word = 0u;
                    *replacement_present =
                        hd2ct_outgoing_read_memory(address, &current_word,
                                                   sizeof(current_word)) &&
                        current_word == replacement;
                }
                return 0;
            }
            if (replacement_present != NULL) *replacement_present = 0;
            if (!FlushInstructionCache(GetCurrentProcess(),
                                       (const void *)address,
                                       sizeof(uint64_t))) {
                flush_error = GetLastError();
            }
            if (!hd2ct_outgoing_restore_protection(address,
                                                   rollback_protection,
                                                   &restore_error)) {
                hd2ct_outgoing_set_hook_failure(protect_restore_stage,
                                                restore_error);
                return 0;
            }
        }
        hd2ct_outgoing_set_hook_failure(flush_stage, flush_error);
        return 0;
    }
    return 1;
}

static int hd2ct_outgoing_create_relay(uintptr_t call_address)
{
    unsigned char relay[14];
    DWORD old_protection = 0u;
    uintptr_t allocation = hd2ct_outgoing_allocate_near(call_address);
    if (allocation == 0u) return 0;
    relay[0] = 0xffu;
    relay[1] = 0x25u;
    relay[2] = 0u;
    relay[3] = 0u;
    relay[4] = 0u;
    relay[5] = 0u;
    {
        uintptr_t callback = (uintptr_t)&hd2ct_outgoing_relay;
        memcpy(relay + 6u, &callback, sizeof(callback));
    }
    memcpy((void *)allocation, relay, sizeof(relay));
    if (!VirtualProtect((void *)allocation, 4096u, PAGE_EXECUTE_READ,
                        &old_protection)) {
        DWORD error = GetLastError();
        hd2ct_outgoing_set_hook_failure(
            HD2CT_OUTGOING_HOOK_STAGE_INSTALL_PROTECT_WRITE, error);
        VirtualFree((void *)allocation, 0u, MEM_RELEASE);
        return 0;
    }
    if (!FlushInstructionCache(GetCurrentProcess(), (const void *)allocation,
                               sizeof(relay))) {
        DWORD error = GetLastError();
        hd2ct_outgoing_set_hook_failure(
            HD2CT_OUTGOING_HOOK_STAGE_INSTALL_FLUSH, error);
        VirtualFree((void *)allocation, 0u, MEM_RELEASE);
        return 0;
    }
    g_outgoing_relay = allocation;
    return 1;
}

static int hd2ct_outgoing_install_hook(void)
{
#ifdef HD2CT_TESTING
    if (InterlockedCompareExchange(&g_outgoing_test_patch_owner_conflict,
                                   0, 0) != 0) {
        hd2ct_outgoing_set_hook_failure(
            HD2CT_OUTGOING_HOOK_STAGE_INSTALL_WORD_MISMATCH,
            ERROR_SUCCESS);
        InterlockedExchange(&g_outgoing_hook_state, 2);
        return 0;
    }
    g_outgoing_module_base = 1u;
    g_outgoing_original_send = (HD2CT_OriginalSend)(uintptr_t)1u;
    g_outgoing_callsite = 1u;
    g_outgoing_original_word = 1u;
    g_outgoing_patched_word = 2u;
    InterlockedExchange(&g_outgoing_hook_active, 1);
    InterlockedExchange(&g_outgoing_hook_state, 1);
    hd2ct_outgoing_clear_hook_failure();
    return 1;
#else
    HMODULE module = NULL;
    uintptr_t original_send = 0u;
    uintptr_t base;
    uintptr_t call_address;
    uintptr_t word_address;
    int32_t displacement;
    uint64_t original_word;
    uint64_t patched_word;
    DWORD read_error = ERROR_SUCCESS;
    int replacement_present = 0;
    if (InterlockedCompareExchange(&g_outgoing_gate_checked, 1, 0) == 0) {
        if (!hd2ct_outgoing_validate_target(&module, &original_send)) {
            hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_TARGET_GATE);
            InterlockedExchange(&g_outgoing_hook_state, 3);
            return 0;
        }
        if (!hd2ct_outgoing_pin_lifetimes(module)) {
            hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_TARGET_GATE);
            InterlockedExchange(&g_outgoing_hook_state, 3);
            return 0;
        }
        g_outgoing_module_base = (uintptr_t)module;
        g_outgoing_original_send = (HD2CT_OriginalSend)original_send;
    } else if (g_outgoing_module_base == 0u || g_outgoing_original_send == NULL) {
        return 0;
    }
    if (InterlockedCompareExchange(&g_outgoing_hook_state, 0, 0) == 2 ||
        InterlockedCompareExchange(&g_outgoing_hook_state, 0, 0) == 3) return 0;
    base = g_outgoing_module_base;
    call_address = base + HD2CT_OUTGOING_CALLSITE_RVA;
    if (g_outgoing_relay == 0u && !hd2ct_outgoing_create_relay(call_address)) {
        hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_NEAR_MEMORY);
        InterlockedExchange(&g_outgoing_hook_state, 3);
        return 0;
    }
    if (!hd2ct_outgoing_displacement_fits(call_address + 5u,
                                           g_outgoing_relay)) {
        hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_NEAR_MEMORY);
        InterlockedExchange(&g_outgoing_hook_state, 3);
        return 0;
    }
    displacement = (int32_t)((int64_t)g_outgoing_relay -
                              (int64_t)(call_address + 5u));
    word_address = base + HD2CT_OUTGOING_CALLSITE_RVA - 2u;
    if (((uintptr_t)word_address & 7u) != 0u) {
        hd2ct_outgoing_set_hook_failure(
            HD2CT_OUTGOING_HOOK_STAGE_INSTALL_ALIGN, ERROR_SUCCESS);
        InterlockedExchange(&g_outgoing_hook_state, 2);
        return 0;
    }
    if (!hd2ct_outgoing_read_memory_ex(word_address, &original_word,
                                       sizeof(original_word), &read_error)) {
        hd2ct_outgoing_set_hook_failure(
            HD2CT_OUTGOING_HOOK_STAGE_INSTALL_READ, read_error);
        InterlockedExchange(&g_outgoing_hook_state, 2);
        return 0;
    }
    if (original_word != 0xbaff8372e9e80000ull) {
        hd2ct_outgoing_set_hook_failure(
            HD2CT_OUTGOING_HOOK_STAGE_INSTALL_WORD_MISMATCH,
            ERROR_SUCCESS);
        InterlockedExchange(&g_outgoing_hook_state, 2);
        return 0;
    }
    patched_word = (original_word & ~0x00ffffffff000000ull) |
        ((uint64_t)(uint32_t)displacement << 24);
    if (!hd2ct_outgoing_swap_word(word_address, original_word, patched_word,
                                  1, &replacement_present)) {
        if (replacement_present) {
            g_outgoing_callsite = call_address;
            g_outgoing_original_word = original_word;
            g_outgoing_patched_word = patched_word;
            InterlockedExchange(&g_outgoing_hook_active, 1);
        }
        InterlockedExchange(&g_outgoing_hook_state, 2);
        return 0;
    }
    g_outgoing_callsite = call_address;
    g_outgoing_original_word = original_word;
    g_outgoing_patched_word = patched_word;
    InterlockedExchange(&g_outgoing_hook_active, 1);
    InterlockedExchange(&g_outgoing_hook_state, 1);
    hd2ct_outgoing_clear_hook_failure();
    return 1;
#endif
}

static int hd2ct_outgoing_restore_hook(void)
{
#ifdef HD2CT_TESTING
    if (InterlockedCompareExchange(&g_outgoing_hook_active, 0, 0) == 0) return 1;
    InterlockedExchange(&g_outgoing_hook_active, 0);
    InterlockedExchange(&g_outgoing_hook_state, 0);
    hd2ct_outgoing_clear_hook_failure();
    return 1;
#else
    uintptr_t word_address;
    if (InterlockedCompareExchange(&g_outgoing_hook_active, 0, 0) == 0) return 1;
    word_address = g_outgoing_module_base + HD2CT_OUTGOING_CALLSITE_RVA - 2u;
    if (!hd2ct_outgoing_swap_word(word_address, g_outgoing_patched_word,
                                  g_outgoing_original_word, 0, NULL)) {
        InterlockedExchange(&g_outgoing_hook_state, 2);
        return 0;
    }
    InterlockedExchange(&g_outgoing_hook_active, 0);
    InterlockedExchange(&g_outgoing_hook_state, 0);
    hd2ct_outgoing_clear_hook_failure();
    return 1;
#endif
}

static HD2CT_NOINLINE void __cdecl hd2ct_outgoing_relay(
    void *service, uintptr_t spaces, const char *source)
{
    int game_thread = InterlockedCompareExchange(&g_outgoing_game_thread, 0, 0) ==
        (LONG)GetCurrentThreadId();
    int caller_ok = (uintptr_t)__builtin_return_address(0) ==
        g_outgoing_module_base + HD2CT_OUTGOING_CALLSITE_RVA + 5u;
    int handled = hd2ct_outgoing_intercept((uintptr_t)service, spaces,
                                            source, caller_ok);
    if (handled == 1 || handled == 2) return;
    if (caller_ok && game_thread && hd2ct_outgoing_queue_count() != 0u) {
        HD2CT_OutgoingContext current;
        if (hd2ct_outgoing_capture_context(&current, (uintptr_t)service)) {
            (void)hd2ct_outgoing_flush_raw(&current);
        } else {
            hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_CONTEXT);
            hd2ct_outgoing_clear_queue(1u);
        }
    }
    InterlockedIncrement64(&g_outgoing_passthrough);
    hd2ct_outgoing_call_original((uintptr_t)service, spaces, source);
}

static int hd2ct_outgoing_plain_path(const wchar_t *path, int directory)
{
    HANDLE handle;
    BY_HANDLE_FILE_INFORMATION information;
    DWORD flags = directory ? FILE_FLAG_BACKUP_SEMANTICS : 0u;
    handle = CreateFileW(path, FILE_READ_ATTRIBUTES,
                         FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                         NULL, OPEN_EXISTING,
                         flags | FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (handle == INVALID_HANDLE_VALUE) return 0;
    if (!GetFileInformationByHandle(handle, &information) ||
        (information.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT |
         FILE_ATTRIBUTE_DEVICE)) != 0u ||
        ((information.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0u) !=
            (directory != 0)) {
        CloseHandle(handle);
        return 0;
    }
    CloseHandle(handle);
    return 1;
}

static int hd2ct_outgoing_destination_safe(const wchar_t *path)
{
    DWORD attributes = GetFileAttributesW(path);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        DWORD error = GetLastError();
        return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND;
    }
    if ((attributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT |
                       FILE_ATTRIBUTE_DEVICE)) != 0u) return 0;
    return hd2ct_outgoing_plain_path(path, 0);
}

static int hd2ct_outgoing_append_path(wchar_t *path, size_t capacity,
                                      const wchar_t *suffix)
{
    size_t path_length = wcslen(path);
    size_t suffix_length = wcslen(suffix);
    if (path_length + suffix_length + 1u > capacity) return 0;
    memcpy(path + path_length, suffix,
           (suffix_length + 1u) * sizeof(path[0]));
    return 1;
}

static int hd2ct_outgoing_status_paths(wchar_t *directory, size_t capacity,
                                       wchar_t *path, wchar_t *temporary)
{
#ifdef HD2CT_TESTING
    if (InterlockedCompareExchange(&g_outgoing_test_status_root_valid, 0, 0) != 0) {
        size_t root_length = wcslen(g_outgoing_test_status_root);
        if (root_length < 3u || root_length >= capacity ||
            !hd2ct_outgoing_plain_path(g_outgoing_test_status_root, 1)) return 0;
        memcpy(directory, g_outgoing_test_status_root,
               (root_length + 1u) * sizeof(directory[0]));
    } else
#endif
    {
    DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", directory,
                                           (DWORD)capacity);
    if (length < 3u || length >= capacity ||
        !hd2ct_outgoing_plain_path(directory, 1)) return 0;
    }
    if (!hd2ct_outgoing_append_path(directory, capacity,
                                    L"\\HD2ChatTranslate") ||
        (!hd2ct_outgoing_plain_path(directory, 1) &&
         !CreateDirectoryW(directory, NULL)) ||
        !hd2ct_outgoing_plain_path(directory, 1) ||
        !hd2ct_outgoing_append_path(directory, capacity, L"\\mailbox") ||
        (!hd2ct_outgoing_plain_path(directory, 1) &&
         !CreateDirectoryW(directory, NULL)) ||
        !hd2ct_outgoing_plain_path(directory, 1)) return 0;
    if (wcslen(directory) + sizeof(L"\\chat-outgoing-status.json") /
            sizeof(wchar_t) > capacity) return 0;
    memcpy(path, directory, (wcslen(directory) + 1u) * sizeof(path[0]));
    memcpy(temporary, directory, (wcslen(directory) + 1u) * sizeof(path[0]));
    return hd2ct_outgoing_append_path(path, capacity,
                                      L"\\chat-outgoing-status.json") &&
        hd2ct_outgoing_append_path(temporary, capacity,
                                   L"\\chat-outgoing-status.tmp");
}

static void hd2ct_outgoing_write_status(void)
{
    wchar_t directory[HD2CT_MAX_VALUES_PATH];
    wchar_t path[HD2CT_MAX_VALUES_PATH];
    wchar_t temporary[HD2CT_MAX_VALUES_PATH];
    char json[768];
    int length;
    HANDLE file = INVALID_HANDLE_VALUE;
    BY_HANDLE_FILE_INFORMATION information;
    DWORD written = 0u;
    DWORD flags = MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH;
    SecureZeroMemory(directory, sizeof(directory));
    SecureZeroMemory(path, sizeof(path));
    SecureZeroMemory(temporary, sizeof(temporary));
    if (!hd2ct_outgoing_status_paths(directory,
                                     sizeof(directory) / sizeof(directory[0]),
                                     path, temporary)) goto cleanup;
    if (!hd2ct_outgoing_destination_safe(path)) goto cleanup;
    length = snprintf(json, sizeof(json),
        "{\"schema_version\":1,\"enabled\":%ld,\"hookactive\":%ld,"
        "\"intercepted\":%lld,\"called_translated\":%lld,"
        "\"called_original\":%lld,\"passthrough\":%lld,"
        "\"queue_pending\":%u,\"context_cancelled\":%lld,"
        "\"timeouts\":%lld,\"last_failure_code\":%ld,"
        "\"hook_failure_stage\":%ld,\"hook_win32_error\":%lu}\n",
        (long)(InterlockedCompareExchange(&g_outgoing_master_enabled, 0, 0) &&
               InterlockedCompareExchange(&g_outgoing_enabled, 0, 0)),
        (long)InterlockedCompareExchange(&g_outgoing_hook_active, 0, 0),
        (long long)InterlockedCompareExchange64(&g_outgoing_intercepted, 0, 0),
        (long long)InterlockedCompareExchange64(&g_outgoing_called_translated, 0, 0),
        (long long)InterlockedCompareExchange64(&g_outgoing_called_original, 0, 0),
        (long long)InterlockedCompareExchange64(&g_outgoing_passthrough, 0, 0),
        hd2ct_outgoing_queue_count(),
        (long long)InterlockedCompareExchange64(&g_outgoing_context_cancelled, 0, 0),
        (long long)InterlockedCompareExchange64(&g_outgoing_timeouts, 0, 0),
        (long)InterlockedCompareExchange(&g_outgoing_failure_code, 0, 0),
        (long)InterlockedCompareExchange(&g_outgoing_hook_failure_stage, 0, 0),
        (unsigned long)InterlockedCompareExchange(
            &g_outgoing_hook_win32_error, 0, 0));
    if (length <= 0 || (size_t)length >= sizeof(json)) goto cleanup;
    file = CreateFileW(temporary, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                       CREATE_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
                       NULL);
    if (file == INVALID_HANDLE_VALUE ||
        !GetFileInformationByHandle(file, &information) ||
        (information.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY |
         FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DEVICE)) != 0u ||
        !WriteFile(file, json, (DWORD)length, &written, NULL) ||
        written != (DWORD)length) goto cleanup;
    CloseHandle(file);
    file = INVALID_HANDLE_VALUE;
    if (!MoveFileExW(temporary, path, flags)) DeleteFileW(temporary);

cleanup:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    SecureZeroMemory(directory, sizeof(directory));
    SecureZeroMemory(path, sizeof(path));
    SecureZeroMemory(temporary, sizeof(temporary));
    SecureZeroMemory(json, sizeof(json));
}

static void hd2ct_outgoing_refresh_settings(int service_ready)
{
    HD2CT_RuntimeSettings settings;
    hd2ct_read_applied_settings(&settings);
    if (settings.target_language == 0u ||
        settings.target_language > HD2CT_TARGET_LANGUAGE_COUNT) {
        settings.target_language = HD2CT_DEFAULT_TARGET_LANGUAGE;
    }
    if (settings.outgoing_target_language == 0u ||
        settings.outgoing_target_language > HD2CT_TARGET_LANGUAGE_COUNT) {
        settings.outgoing_target_language = HD2CT_DEFAULT_OUTGOING_TARGET_LANGUAGE;
    }
    if (settings.timeout_seconds != 10u && settings.timeout_seconds != 20u &&
        settings.timeout_seconds != 30u) {
        settings.timeout_seconds = HD2CT_DEFAULT_TIMEOUT_SECONDS;
    }
    InterlockedExchange(&g_outgoing_master_enabled, (LONG)(settings.enabled != 0u));
    InterlockedExchange(&g_outgoing_enabled,
                        (LONG)(settings.outgoing_enabled != 0u));
    InterlockedExchange(&g_outgoing_target_language,
                        (LONG)settings.outgoing_target_language);
    InterlockedExchange(&g_outgoing_timeout_seconds,
                        (LONG)settings.timeout_seconds);
    InterlockedExchange(&g_outgoing_service_ready, service_ready != 0);
    SecureZeroMemory(&settings, sizeof(settings));
}

static unsigned __stdcall hd2ct_outgoing_controller_main(void *parameter)
{
    uint64_t last_status_ms = 0u;
    (void)parameter;
    for (;;) {
        uint64_t now_ms;
        uint64_t last_pump;
        int should_hook;
        hd2ct_outgoing_refresh_settings(
            InterlockedCompareExchange(&g_outgoing_service_ready, 0, 0));
        now_ms = GetTickCount64();
        last_pump = (uint64_t)InterlockedCompareExchange64(
            &g_outgoing_last_pump_ms, 0, 0);
        {
            uint32_t has_pending = hd2ct_outgoing_queue_count();
            int settings_enabled =
                InterlockedCompareExchange(&g_outgoing_master_enabled, 0, 0) != 0 &&
                InterlockedCompareExchange(&g_outgoing_enabled, 0, 0) != 0;
            int pump_fresh = last_pump != 0u && now_ms >= last_pump &&
                now_ms - last_pump <= HD2CT_OUTGOING_FRESH_MS;
            int keep_pending_hook = has_pending != 0u &&
                InterlockedCompareExchange(&g_outgoing_hook_active, 0, 0) != 0;
            should_hook =
                InterlockedCompareExchange(&g_outgoing_game_thread, 0, 0) != 0 &&
                (settings_enabled || has_pending != 0u) &&
                (pump_fresh || keep_pending_hook) &&
                (InterlockedCompareExchange(&g_outgoing_service_ready, 0, 0) != 0 ||
                 keep_pending_hook);
            if (!pump_fresh && keep_pending_hook) {
                hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_PUMP_STALE);
            }
        }
        if (should_hook) {
            if (last_pump != 0u && now_ms >= last_pump &&
                now_ms - last_pump <= HD2CT_OUTGOING_FRESH_MS &&
                InterlockedCompareExchange(&g_outgoing_hook_active, 0, 0) == 0 &&
                InterlockedCompareExchange(&g_outgoing_hook_state, 0, 0) == 0) {
                (void)hd2ct_outgoing_install_hook();
            }
        } else {
            if (InterlockedCompareExchange(&g_outgoing_enabled, 0, 0) != 0 &&
                InterlockedCompareExchange(&g_outgoing_game_thread, 0, 0) != 0 &&
                last_pump != 0u && now_ms >= last_pump &&
                now_ms - last_pump > HD2CT_OUTGOING_FRESH_MS) {
                hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_PUMP_STALE);
            }
            if (hd2ct_outgoing_queue_count() == 0u) {
                (void)hd2ct_outgoing_restore_hook();
            }
        }
        if (last_status_ms == 0u || now_ms < last_status_ms ||
            now_ms - last_status_ms >= HD2CT_OUTGOING_STATUS_PERIOD_MS) {
            hd2ct_outgoing_write_status();
            last_status_ms = now_ms;
            InterlockedExchange64(&g_outgoing_last_status_ms,
                                  (LONG64)last_status_ms);
        }
        Sleep(1000u);
    }
    return 0u;
}

void hd2ct_outgoing_controller_start(int service_ready)
{
    InterlockedExchange(&g_outgoing_service_ready, service_ready != 0);
#ifdef HD2CT_TESTING
    (void)service_ready;
    InterlockedExchange(&g_outgoing_controller_started, 1);
#else
    if (InterlockedCompareExchange(&g_outgoing_controller_started, 1, 0) != 0) {
        return;
    }
    {
        uintptr_t thread = _beginthreadex(NULL, 0,
            hd2ct_outgoing_controller_main, NULL, 0, NULL);
        if (thread == 0u) {
            InterlockedExchange(&g_outgoing_controller_started, 0);
            hd2ct_outgoing_set_failure(HD2CT_OUTGOING_FAILURE_CONTROLLER);
            return;
        }
        CloseHandle((HANDLE)thread);
    }
#endif
}

#ifdef HD2CT_TESTING
void hd2ct_outgoing_test_reset(void)
{
    hd2ct_outgoing_clear_queue(0u);
    SecureZeroMemory(&g_outgoing_test_context, sizeof(g_outgoing_test_context));
    SecureZeroMemory(g_outgoing_test_last_send, sizeof(g_outgoing_test_last_send));
    SecureZeroMemory(g_outgoing_test_sent_texts, sizeof(g_outgoing_test_sent_texts));
    SecureZeroMemory(g_outgoing_test_status_root, sizeof(g_outgoing_test_status_root));
    InterlockedExchange(&g_outgoing_test_status_root_valid, 0);
    InterlockedExchange(&g_outgoing_test_context_valid, 0);
    InterlockedExchange64(&g_outgoing_test_send_count, 0);
    InterlockedExchange64(&g_outgoing_intercepted, 0);
    InterlockedExchange64(&g_outgoing_called_translated, 0);
    InterlockedExchange64(&g_outgoing_called_original, 0);
    InterlockedExchange64(&g_outgoing_passthrough, 0);
    InterlockedExchange64(&g_outgoing_context_cancelled, 0);
    InterlockedExchange64(&g_outgoing_timeouts, 0);
    InterlockedExchange64(&g_outgoing_test_now_ms, 1);
    InterlockedExchange64(&g_outgoing_last_pump_ms, 0);
    InterlockedExchange(&g_outgoing_game_thread, 0);
    InterlockedExchange(&g_outgoing_hook_active, 0);
    InterlockedExchange(&g_outgoing_hook_state, 0);
    InterlockedExchange(&g_outgoing_failure_code,
                        HD2CT_OUTGOING_FAILURE_NONE);
    InterlockedExchange(&g_outgoing_hook_failure_stage,
                        HD2CT_OUTGOING_HOOK_STAGE_NONE);
    InterlockedExchange(&g_outgoing_hook_win32_error, ERROR_SUCCESS);
    InterlockedExchange(&g_outgoing_service_ready, 0);
    InterlockedExchange(&g_outgoing_master_enabled, 1);
    InterlockedExchange(&g_outgoing_enabled, 0);
    InterlockedExchange(&g_outgoing_target_language,
                        HD2CT_DEFAULT_OUTGOING_TARGET_LANGUAGE);
    InterlockedExchange(&g_outgoing_timeout_seconds,
                        HD2CT_DEFAULT_TIMEOUT_SECONDS);
    InterlockedExchange(&g_outgoing_test_patch_owner_conflict, 0);
    InterlockedExchange(&g_outgoing_test_restore_failure_once, 0);
    InterlockedExchange(&g_outgoing_test_reenter_pump, 0);
    InterlockedExchange(&g_outgoing_test_fail_claim_once, 0);
    InterlockedExchange(&g_outgoing_pump_active, 0);
    hd2ct_outgoing_clear_retry();
}

void hd2ct_outgoing_test_settings(uint32_t master_enabled,
                                  uint32_t outgoing_enabled,
                                  uint32_t target_language,
                                  uint32_t timeout_seconds)
{
    InterlockedExchange(&g_outgoing_master_enabled, master_enabled != 0u);
    InterlockedExchange(&g_outgoing_enabled, outgoing_enabled != 0u);
    InterlockedExchange(&g_outgoing_target_language,
                        (LONG)target_language);
    InterlockedExchange(&g_outgoing_timeout_seconds,
                        (LONG)timeout_seconds);
    InterlockedExchange(&g_outgoing_service_ready, 1);
    if (master_enabled != 0u && outgoing_enabled != 0u) {
        (void)hd2ct_outgoing_install_hook();
    } else if (hd2ct_outgoing_queue_count() == 0u) {
        (void)hd2ct_outgoing_restore_hook();
    }
}

void hd2ct_outgoing_test_bind_thread(void)
{
    InterlockedExchange(&g_outgoing_game_thread, (LONG)GetCurrentThreadId());
    InterlockedExchange64(&g_outgoing_last_pump_ms,
        InterlockedCompareExchange64(&g_outgoing_test_now_ms, 0, 0));
}

void hd2ct_outgoing_test_set_clock(uint64_t now_ms)
{
    InterlockedExchange64(&g_outgoing_test_now_ms, (LONG64)now_ms);
}

void hd2ct_outgoing_test_refresh_settings(void)
{
    hd2ct_outgoing_refresh_settings(1);
    if (InterlockedCompareExchange(&g_outgoing_master_enabled, 0, 0) != 0 &&
        InterlockedCompareExchange(&g_outgoing_enabled, 0, 0) != 0) {
        (void)hd2ct_outgoing_install_hook();
    } else if (hd2ct_outgoing_queue_count() == 0u) {
        (void)hd2ct_outgoing_restore_hook();
    }
}

void hd2ct_outgoing_test_set_context(uint64_t root, uintptr_t service,
                                     uint64_t local_id,
                                     uintptr_t network_manager,
                                     const uint64_t *receivers,
                                     uint32_t receiver_count)
{
    uint32_t copy_count = receiver_count > 16u ? 16u : receiver_count;
    memset(&g_outgoing_test_context, 0, sizeof(g_outgoing_test_context));
    g_outgoing_test_context.root = root;
    g_outgoing_test_context.service = service;
    g_outgoing_test_context.local_id = local_id;
    g_outgoing_test_context.network_manager = network_manager;
    g_outgoing_test_context.receiver_count = receiver_count;
    if (receivers != NULL && copy_count != 0u) {
        memcpy(g_outgoing_test_context.receivers, receivers,
               copy_count * sizeof(receivers[0]));
    }
    InterlockedExchange(&g_outgoing_test_context_valid, 1);
}

void hd2ct_outgoing_test_intercept(uintptr_t service, uintptr_t spaces,
                                   const char *source)
{
    int handled;
    if (InterlockedCompareExchange(&g_outgoing_game_thread, 0, 0) == 0) {
        hd2ct_outgoing_test_bind_thread();
    }
    handled = hd2ct_outgoing_intercept(service, spaces, source, 1);
    if (handled == 0) {
        HD2CT_OutgoingContext current;
        if (InterlockedCompareExchange(&g_outgoing_game_thread, 0, 0) ==
                (LONG)GetCurrentThreadId() &&
            hd2ct_outgoing_queue_count() != 0u &&
            hd2ct_outgoing_capture_context(&current, service)) {
            (void)hd2ct_outgoing_flush_raw(&current);
        }
        InterlockedIncrement64(&g_outgoing_passthrough);
        hd2ct_outgoing_call_original(service, spaces, source);
    }
}

uint32_t hd2ct_outgoing_test_pump(void)
{
    return hd2ct_outgoing_pump() ? 1u : 0u;
}

uint32_t hd2ct_outgoing_test_pending(void)
{
    return hd2ct_outgoing_queue_count();
}

uint32_t hd2ct_outgoing_test_counter(uint32_t counter_id)
{
    LONG64 value = 0;
    switch (counter_id) {
    case 0u: value = InterlockedCompareExchange64(&g_outgoing_intercepted, 0, 0); break;
    case 1u: value = InterlockedCompareExchange64(&g_outgoing_called_translated, 0, 0); break;
    case 2u: value = InterlockedCompareExchange64(&g_outgoing_called_original, 0, 0); break;
    case 3u: value = InterlockedCompareExchange64(&g_outgoing_passthrough, 0, 0); break;
    case 4u: value = InterlockedCompareExchange64(&g_outgoing_context_cancelled, 0, 0); break;
    case 5u: value = InterlockedCompareExchange64(&g_outgoing_timeouts, 0, 0); break;
    default: return 0u;
    }
    return value > 0 ? (uint32_t)value : 0u;
}

uint32_t hd2ct_outgoing_test_hook_active(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_outgoing_hook_active, 0, 0);
}

uint32_t hd2ct_outgoing_test_failure_code(void)
{
    return (uint32_t)InterlockedCompareExchange(&g_outgoing_failure_code, 0, 0);
}

uint32_t hd2ct_outgoing_test_hook_failure_stage(void)
{
    return (uint32_t)InterlockedCompareExchange(
        &g_outgoing_hook_failure_stage, 0, 0);
}

uint32_t hd2ct_outgoing_test_hook_win32_error(void)
{
    return (uint32_t)InterlockedCompareExchange(
        &g_outgoing_hook_win32_error, 0, 0);
}

static DWORD hd2ct_outgoing_test_page_protection(uintptr_t address)
{
    MEMORY_BASIC_INFORMATION information;
    SIZE_T queried = VirtualQuery((const void *)address, &information,
                                  sizeof(information));
    if (queried != sizeof(information)) {
        if (queried == 0u) {
            DWORD error = GetLastError();
            (void)error;
        }
        return 0u;
    }
    return information.Protect & 0xffu;
}

uint32_t hd2ct_outgoing_test_hook_page(uint32_t scenario)
{
    SYSTEM_INFO system_information;
    uint64_t original_word = 0x1122334455667788ull;
    uint64_t patched_word = 0x8877665544332211ull;
    uint64_t foreign_word = 0x123456789abcdef0ull;
    uint64_t *word;
    uintptr_t address;
    void *page;
    DWORD old_protection = 0u;
    DWORD error;
    uint32_t result = 0u;
    int replacement_present = 0;
    GetSystemInfo(&system_information);
    if (system_information.dwPageSize == 0u) return 0u;
    page = VirtualAlloc(NULL, system_information.dwPageSize,
                        MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (page == NULL) {
        error = GetLastError();
        (void)error;
        return 0u;
    }
    word = (uint64_t *)((uintptr_t)page + 8u);
    address = (uintptr_t)word;
    *word = original_word;
    if (!VirtualProtect(page, system_information.dwPageSize,
                        PAGE_EXECUTE_READ, &old_protection)) {
        error = GetLastError();
        (void)error;
        goto cleanup;
    }
    if (scenario == HD2CT_OUTGOING_TEST_HOOK_RESTORE_FAILURE) {
        hd2ct_outgoing_clear_hook_failure();
        InterlockedExchange(&g_outgoing_test_restore_failure_once, 1);
        if (!hd2ct_outgoing_swap_word(address, original_word, patched_word,
                                      1, &replacement_present) &&
            hd2ct_outgoing_test_hook_failure_stage() ==
                HD2CT_OUTGOING_HOOK_STAGE_INSTALL_PROTECT_RESTORE &&
            hd2ct_outgoing_test_hook_win32_error() == ERROR_ACCESS_DENIED &&
            hd2ct_outgoing_test_failure_code() ==
                HD2CT_OUTGOING_FAILURE_PATCH_OWNER &&
            *word == original_word && replacement_present == 0 &&
            hd2ct_outgoing_test_page_protection(address) == PAGE_EXECUTE_READ) {
            result = 1u;
        }
    } else if (scenario == HD2CT_OUTGOING_TEST_HOOK_OWNER_MISMATCH) {
        if (!VirtualProtect(page, system_information.dwPageSize,
                            PAGE_READWRITE, &old_protection)) {
            error = GetLastError();
            (void)error;
            goto cleanup;
        }
        *word = foreign_word;
        if (!VirtualProtect(page, system_information.dwPageSize,
                            PAGE_EXECUTE_READ, &old_protection)) {
            error = GetLastError();
            (void)error;
            goto cleanup;
        }
        hd2ct_outgoing_clear_hook_failure();
        if (!hd2ct_outgoing_swap_word(address, original_word, patched_word,
                                      1, &replacement_present) &&
            hd2ct_outgoing_test_hook_failure_stage() ==
                HD2CT_OUTGOING_HOOK_STAGE_INSTALL_CAS_MISMATCH &&
            hd2ct_outgoing_test_hook_win32_error() == ERROR_SUCCESS &&
            hd2ct_outgoing_test_failure_code() ==
                HD2CT_OUTGOING_FAILURE_PATCH_OWNER &&
            *word == foreign_word && replacement_present == 0 &&
            hd2ct_outgoing_test_page_protection(address) == PAGE_EXECUTE_READ) {
            result = 1u;
        }
    } else if (scenario == HD2CT_OUTGOING_TEST_HOOK_SUCCESS_RESET) {
        hd2ct_outgoing_set_hook_failure(
            HD2CT_OUTGOING_HOOK_STAGE_INSTALL_READ, ERROR_INVALID_DATA);
        if (hd2ct_outgoing_swap_word(address, original_word, patched_word,
                                     1, &replacement_present)) {
            hd2ct_outgoing_clear_hook_failure();
            if (*word == patched_word && replacement_present != 0 &&
                hd2ct_outgoing_test_hook_failure_stage() ==
                    HD2CT_OUTGOING_HOOK_STAGE_NONE &&
                hd2ct_outgoing_test_hook_win32_error() == ERROR_SUCCESS &&
                hd2ct_outgoing_test_failure_code() ==
                    HD2CT_OUTGOING_FAILURE_NONE &&
                hd2ct_outgoing_test_page_protection(address) ==
                    PAGE_EXECUTE_READ) {
                result |= 1u;
            }
            if (hd2ct_outgoing_swap_word(address, patched_word, original_word,
                                         0, NULL)) {
                hd2ct_outgoing_clear_hook_failure();
                if (*word == original_word &&
                    hd2ct_outgoing_test_hook_failure_stage() ==
                        HD2CT_OUTGOING_HOOK_STAGE_NONE &&
                    hd2ct_outgoing_test_hook_win32_error() == ERROR_SUCCESS &&
                    hd2ct_outgoing_test_failure_code() ==
                        HD2CT_OUTGOING_FAILURE_NONE &&
                    hd2ct_outgoing_test_page_protection(address) ==
                        PAGE_EXECUTE_READ) {
                    result |= 2u;
                }
            }
        }
    }

cleanup:
    if (!VirtualFree(page, 0u, MEM_RELEASE)) {
        error = GetLastError();
        (void)error;
        result = 0u;
    }
    return result;
}

uint32_t hd2ct_outgoing_test_send_count(void)
{
    LONG64 value = InterlockedCompareExchange64(&g_outgoing_test_send_count, 0, 0);
    return value > 0 ? (uint32_t)value : 0u;
}

uint32_t hd2ct_outgoing_test_last_send(char *out, uint32_t capacity)
{
    size_t length = hd2ct_bounded_length(g_outgoing_test_last_send,
                                         HD2CT_OUTGOING_MAX_SOURCE);
    if (out == NULL || capacity <= length || length > HD2CT_OUTGOING_MAX_SOURCE) {
        return 0u;
    }
    memcpy(out, g_outgoing_test_last_send, length + 1u);
    return (uint32_t)length;
}

uint32_t hd2ct_outgoing_test_send_at(uint32_t index, char *out,
                                     uint32_t capacity)
{
    size_t length;
    if (index >= 16u || out == NULL || capacity == 0u) return 0u;
    length = hd2ct_bounded_length(g_outgoing_test_sent_texts[index],
                                  HD2CT_OUTGOING_MAX_SOURCE);
    if (length > HD2CT_OUTGOING_MAX_SOURCE || capacity <= length) return 0u;
    memcpy(out, g_outgoing_test_sent_texts[index], length + 1u);
    return (uint32_t)length;
}

void hd2ct_outgoing_test_set_status_root(const wchar_t *path)
{
    size_t length;
    if (path == NULL) {
        InterlockedExchange(&g_outgoing_test_status_root_valid, 0);
        SecureZeroMemory(g_outgoing_test_status_root,
                         sizeof(g_outgoing_test_status_root));
        return;
    }
    length = wcslen(path);
    if (length < 3u || length >= HD2CT_MAX_VALUES_PATH) {
        InterlockedExchange(&g_outgoing_test_status_root_valid, 0);
        return;
    }
    memcpy(g_outgoing_test_status_root, path,
           (length + 1u) * sizeof(path[0]));
    InterlockedExchange(&g_outgoing_test_status_root_valid, 1);
}

void hd2ct_outgoing_test_write_status(void)
{
    hd2ct_outgoing_write_status();
}

void hd2ct_outgoing_test_queue_raw(const char *source, uintptr_t spaces)
{
    HD2CT_OutgoingItem item;
    char source_copy[HD2CT_OUTGOING_MAX_SOURCE + 1u];
    uint32_t source_bytes = 0u;
    uint32_t index;
    if (InterlockedCompareExchange(&g_outgoing_test_context_valid, 0, 0) == 0 ||
        !hd2ct_outgoing_copy_source(source, source_copy, &source_bytes) ||
        !TryAcquireSRWLockExclusive(&g_outgoing_lock)) {
        SecureZeroMemory(source_copy, sizeof(source_copy));
        return;
    }
    if (g_outgoing_count >= HD2CT_OUTGOING_QUEUE_COUNT) {
        ReleaseSRWLockExclusive(&g_outgoing_lock);
        SecureZeroMemory(source_copy, sizeof(source_copy));
        return;
    }
    memset(&item, 0, sizeof(item));
    index = (g_outgoing_head + g_outgoing_count) % HD2CT_OUTGOING_QUEUE_COUNT;
    item.state = HD2CT_OUTGOING_RAW_READY;
    item.source_bytes = source_bytes;
    item.sequence = ++g_outgoing_sequence;
    item.deadline_ms = hd2ct_outgoing_now() +
        (uint64_t)HD2CT_DEFAULT_TIMEOUT_SECONDS * 1000ull;
    item.service = (uintptr_t)g_outgoing_test_context.service;
    item.spaces = spaces;
    item.context = g_outgoing_test_context;
    memcpy(item.source, source_copy, source_bytes + 1u);
    g_outgoing_items[index] = item;
    ++g_outgoing_count;
    hd2ct_outgoing_publish_queue_count();
    ReleaseSRWLockExclusive(&g_outgoing_lock);
    SecureZeroMemory(&item, sizeof(item));
    SecureZeroMemory(source_copy, sizeof(source_copy));
}

void hd2ct_outgoing_test_patch_owner_conflict(uint32_t enabled)
{
    InterlockedExchange(&g_outgoing_test_patch_owner_conflict, enabled != 0u);
}

void hd2ct_outgoing_test_reenter_pump(uint32_t enabled)
{
    InterlockedExchange(&g_outgoing_test_reenter_pump, enabled != 0u);
}

void hd2ct_outgoing_test_fail_claim_once(void)
{
    InterlockedExchange(&g_outgoing_test_fail_claim_once, 1);
}
#endif
