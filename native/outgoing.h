#ifndef HD2CT_OUTGOING_H
#define HD2CT_OUTGOING_H

#include <stddef.h>
#include <stdint.h>

void hd2ct_outgoing_controller_start(int service_ready);
int hd2ct_outgoing_pump(void);

#ifdef HD2CT_TESTING
void hd2ct_outgoing_test_reset(void);
void hd2ct_outgoing_test_settings(uint32_t master_enabled,
                                  uint32_t outgoing_enabled,
                                  uint32_t target_language,
                                  uint32_t timeout_seconds);
void hd2ct_outgoing_test_bind_thread(void);
void hd2ct_outgoing_test_set_clock(uint64_t now_ms);
void hd2ct_outgoing_test_refresh_settings(void);
void hd2ct_outgoing_test_set_context(uint64_t root, uintptr_t service,
                                     uint64_t local_id,
                                     uintptr_t network_manager,
                                     const uint64_t *receivers,
                                     uint32_t receiver_count);
void hd2ct_outgoing_test_intercept(uintptr_t service, uintptr_t spaces,
                                   const char *source);
uint32_t hd2ct_outgoing_test_pump(void);
uint32_t hd2ct_outgoing_test_pending(void);
uint32_t hd2ct_outgoing_test_counter(uint32_t counter_id);
uint32_t hd2ct_outgoing_test_hook_active(void);
uint32_t hd2ct_outgoing_test_failure_code(void);
uint32_t hd2ct_outgoing_test_send_count(void);
uint32_t hd2ct_outgoing_test_last_send(char *out, uint32_t capacity);
uint32_t hd2ct_outgoing_test_send_at(uint32_t index, char *out,
                                     uint32_t capacity);
void hd2ct_outgoing_test_set_status_root(const wchar_t *path);
void hd2ct_outgoing_test_write_status(void);
void hd2ct_outgoing_test_queue_raw(const char *source, uintptr_t spaces);
void hd2ct_outgoing_test_patch_owner_conflict(uint32_t enabled);
void hd2ct_outgoing_test_reenter_pump(uint32_t enabled);
void hd2ct_outgoing_test_fail_claim_once(void);
#endif

#endif
