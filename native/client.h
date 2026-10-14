#ifndef HD2CT_CLIENT_H
#define HD2CT_CLIENT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Submit消息标志：其他或未知发送者传0，自身消息设置SELF位。 */
#define HD2CT_MESSAGE_SELF 0x00000001u

__declspec(dllexport) uint32_t __cdecl HD2CT_Submit(
    const char *token,
    const char *body,
    uint32_t bytes,
    uint32_t flags,
    const char *player_name); /* UTF-8本地玩家姓名；NULL或空字符串表示未知。 */
/* 成功以 OK\n 返回；无需改写的消息以 SKIP\n 返回；ERR\n 后仅含固定错误码。 */
/* 结果均以 NUL 结尾，written 不含末尾 NUL。 */
__declspec(dllexport) uint32_t __cdecl HD2CT_Poll(
    const char *token,
    char *out,
    uint32_t capacity,
    uint32_t *written);
/* 单项锁忙返回 0；NULL 取消全部，锁忙时登记延后取消并返回 1。 */
__declspec(dllexport) uint32_t __cdecl HD2CT_Cancel(const char *token);

#ifdef __cplusplus
}
#endif

#endif
