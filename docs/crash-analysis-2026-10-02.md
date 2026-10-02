# 已知崩溃修复与验收状态（2026-10-02）

本文保留仍影响当前实现的两项修复、复核依据和验收状态。早期诊断产物与失败 ZIP 已清理，相关代码变更可从 Git 历史查阅。

## 文件信息 FFI 越界

首次进程内翻译版本启动时崩溃。Lua 中的 `BY_HANDLE_FILE_INFORMATION` 漏掉 `dwVolumeSerialNumber`，分配 48 字节，而 Windows API 写入的完整结构为 52 字节。

修复补齐字段，并在模块加载前检查大小与关键字段偏移。回归调用真实 Windows API 读取测试临时文件，检查结构外保护字节；旧 48 字节布局会被门禁拒绝。

修订版在分槽部署中完成真实翻译，用户确认原聊天行变成中文。16:22:29 的新会话为 `standalone` / `in_process_winhttp`，原生初始化与最后状态均为 `0`，提交、译文就绪、回写确认各一次，翻译/适配器/回写错误为零。这验证了进程内请求和回写路径的一次实际运行，不代表长期稳定性。

## 合并 archive 的 TOC 格式

后来将 loader 和 addon 合入一个 patch 时，三次启动转储均为 `0xC0000005` 读访问冲突，位置 `helldivers2.exe+0x5F2EB0`。网络 helper DLL 尚未加载，loader 日志与翻译报告没有更新。这与之前已进入 native/Lua 路径的异常不同；有限栈候选扫描不能代替可靠展开的调用栈，也不能证明唯一原因。

核对 [Bingus 官方 archive 构建器](https://github.com/CowboyBingus/BingusSharedLoader/blob/main/scripts/archive.py) 和原有多资源 patch，确认合并器的两处格式错误：头部偏移 4 是类型数、偏移 8 是文件数，之前写反；第二条 file row 的末 DWORD 编号重复为 `0`。

修复写入 `type_count=1, file_count=2`，按资源 hash 排序，编号 `0,1`。独立 parser 回归拒绝错头与重复编号，并核对由官方构建器生成的双资源黄金摘要。对实际两个资源的官方纯函数比对逐字节一致；上游源码快照 SHA-256 为 `564dda73591088ced78be67fdae0c85df4eb1d82d6371cd16224ce7d90b25a12`。

修订主 patch 长 328,368 字节，SHA-256 为 `4cb51d198a387746afbace921dc6f9763e7a587748ea16e97c252defd92097cf`；相较失败 patch，仅偏移 4、8、260 的字节改变，资源与网络 DLL 不变。修复时 112 项测试通过，实际 ZIP CRC 与 LuaJIT 语法通过。

## 当前状态

TOC 修订合并包的实际 Arsenal 更新、稳定启动和聊天翻译仍待确认。格式一致性与离线回归不能代替新游戏会话验证。安装与状态检查见 [使用说明](chat-standalone.md)。

本机必要数值证据已归并到被 Git 忽略的 `artifacts/validation/`：`standalone-confirmed.json`、`package-verified.json`、`official-writer-comparison.json`、`crash-evidence.json` 和原 patch 基线 `base-patches.json`。文件不包含聊天正文或模型配置，历史证据不能当作之后会话的成功报告。
