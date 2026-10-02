#ifndef HD2CT_HTTP_H
#define HD2CT_HTTP_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* 状态值：0 就绪，1 缺少配置，2 配置无效，3 已禁用，4 worker 初始化失败。 */
__declspec(dllexport) uint32_t __cdecl HD2CT_ABIVersion(void);
__declspec(dllexport) uint32_t __cdecl HD2CT_InitializeEnvironment(void);
__declspec(dllexport) uint32_t __cdecl HD2CT_InitializeConfig(
    const char *url,
    const char *model,
    const char *api_key,
    uint32_t timeout_seconds);
__declspec(dllexport) uint32_t __cdecl HD2CT_IsEnabled(void);
__declspec(dllexport) uint32_t __cdecl HD2CT_LastStatus(void);
__declspec(dllexport) uint32_t __cdecl HD2CT_Submit(
    const char *token,
    const char *body,
    uint32_t bytes);
/* 成功时写入以 NUL 结尾的 OK\n... 或 ERR\n...；written 不含末尾 NUL。 */
__declspec(dllexport) uint32_t __cdecl HD2CT_Poll(
    const char *token,
    char *out,
    uint32_t capacity,
    uint32_t *written);
/* 成功取消或 token 不存在返回 1；SRW 锁忙时返回 0，调用方可稍后重试。 */
__declspec(dllexport) uint32_t __cdecl HD2CT_Cancel(const char *token);
__declspec(dllexport) void __cdecl HD2CT_Disable(void);

#ifdef __cplusplus
}
#endif

#endif
