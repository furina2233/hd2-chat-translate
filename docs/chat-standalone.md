# 无伴随程序的聊天翻译

独立模式把模型网络请求放入游戏进程内的两个后台线程。Lua 插件只提交正文、轮询结果并执行已经核验的聊天回写，不启动 Python、外部服务或子进程。游戏仍先显示原文，译文成功后更新对应的本机聊天行。

独立模式已完成构建、离线验收和一次真实游戏翻译核验。首次真实游戏启动发现文件信息结构的 FFI 越界写入，已补齐字段并增加布局门禁及真实 Windows API 回归；修订版成功在进程内请求模型并替换原聊天行，详情见下方验收记录及 [崩溃调查记录](crash-analysis-2026-10-02.md)。

## 安装与配置

在游戏正常关闭时，向 HD2Arsenal 导入 `HD2ChatTranslateStandalone.zip`，启用并部署。依赖已经兼容的 Bingus Shared Loader v18，它作为独立模组安装并保持足够高的优先级。不要同时启用旧聊天翻译 addon；独立版使用同一个 Lua 资源作为升级。

Arsenal 的 [导入说明](https://docs.rsnl.gg/mod-management/adding-mods) 支持 archive 内的 patch 文件；本包继续使用已采用的单 addon patch 和 V1 manifest 格式，不合并 loader 的 patch 或 manifest。

在 Windows 的“环境变量”设置中添加：

| 变量 | 含义 | 示例 |
| --- | --- | --- |
| `HD2CT_API_URL` | 服务商地址或完整 Chat Completions 请求地址 | `https://api.deepseek.com` |
| `HD2CT_MODEL` | 账户可用的模型名 | `deepseek-flash` |
| `HD2CT_API_KEY` | 服务商 API Key | 仅在本机填写 |
| `HD2CT_TIMEOUT_SECONDS` | 可选，请求超时，1–120 秒 | 默认 `20` |
| `HD2CT_ENABLED` | 可选，`0` 禁用翻译 | 默认 `1` |

可以使用系统或当前用户环境变量。同名配置优先取用户变量，再取系统变量，最后取进程继承的环境；插件只读取这五个名称。它在游戏启动时直接读取持久配置，因此不依赖已经运行的 Steam 是否刷新了环境。配置改动需要重新启动游戏。

环境变量以明文保存，独立模式不使用旧配置窗口的 DPAPI 密文。密钥不进入安装包、聊天正文、报告或错误提示。缺少或无效配置时保留原聊天，不启动网络请求。

## URL 补全

| 填写的地址 | 实际请求地址 |
| --- | --- |
| `https://api.deepseek.com` | `https://api.deepseek.com/chat/completions` |
| `https://api.deepseek.com/` | `https://api.deepseek.com/chat/completions` |
| `https://api.deepseek.com/v1` | `https://api.deepseek.com/v1/chat/completions` |
| `https://api.deepseek.com/v1/` | `https://api.deepseek.com/v1/chat/completions` |
| 完整接口或其他自定义路径 | 原样使用 |

按用户最新要求，只补全根基础地址和 `/v1`；不纠正 `/chat/compltetion` 等拼写，不改写其他路径。地址需以 `https://` 开头；仅本机回环地址可以使用 `http://`。用户名、密码、查询参数和 URL 片段不允许出现在地址中。服务端需要支持 Chat Completions 和 JSON mode。

## 运行边界

启动时先核验游戏构建与原生函数，再加载本插件携带的网络模块。模块从 addon 中提取至 `%LOCALAPPDATA%/HD2ChatTranslate/native/<SHA256>.dll`，只接受固定长度和 SHA-256 相符的文件。加载时使用绝对路径，并限制依赖到系统目录；参见 [LoadLibraryExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexw)。后台线程启动后模块保留到游戏进程结束，避免正在执行的代码被卸载；参见 [GetModuleHandleExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getmodulehandleexw)。

网络仍保留正常 HTTPS 证书验证，禁用重定向，避免把密钥交给另一地址；WinHTTP 的重定向行为与风险见 [Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/winhttp/winhttp-security-considerations)。网络不在游戏更新线程执行，也不会从后台线程调用 Lua 或操作聊天控件。

正文长度、UTF-8 校验、控件与事件复核、60 秒过期、32 条待处理上限、每分钟 30 次实际请求、成功缓存和译文缓冲区保活仍保留。失败时显示原文，中文由模型判断后强制保留原文。超时到期后游戏可及时获得失败结果；同步 WinHTTP worker 仍可能继续等待在途请求结束，对应槽位保留至其自行清理，晚结果不会覆盖超时结果。详细回写边界见 [原位翻译说明](chat-translate.md)。

## 状态检查

插件仍在 `%LOCALAPPDATA%/HD2ChatTranslate/mailbox/chat-translate-<session>.json` 写入不含聊天或配置的状态报告。独立版显示 `mode: standalone` 和 `transport: in_process_winhttp`；这里的 mailbox 目录只用于状态报告，模型请求和响应在进程内传递。

`native_init_status` 为 `0` 表示初始化就绪，`1` 为缺少配置，`2` 为配置或模块无效，`3` 为关闭翻译，`4` 为后台线程初始化失败。`native_last_status` 只用于诊断：正常成功为 `0`，HTTP 失败使用状态码（如 `401` 或 `429`），`1000` 为网络错误，`1001` 为响应格式无效，`1002` 为请求超时。服务失败不会关闭后续翻译，会按有限退避处理之后的新请求。

## 回滚

正常关闭游戏后，在 Arsenal 禁用或移除独立 addon 并重新部署；按需保留其他模组使用的 Shared Loader。提取的本插件 DLL 缓存可以在游戏关闭后删除，它只位于本插件专用的 `native` 目录。环境变量可以自行删除或将 `HD2CT_ENABLED` 设为 `0`。

本机游戏核验使用独立槽位与指纹收据部署。如仍存在这类部署，改由 Arsenal 管理前，先正常退出游戏并执行 `pwsh -NoProfile -File tools/deploy_probe.ps1 -Standalone -Rollback`，再导入和部署本包及 loader，避免重复部署。该脚本仅删除收据拥有且摘要匹配的六个文件。旧伴随程序版的来源包仍保留。

2026-10-02 核验完成后，按用户要求移除了本机部署：patch 21 的聊天 addon、patch 22 的本次配套 loader，共六个文件，以及活动部署收据；另删除了摘要匹配的网络 DLL 缓存。原 Arsenal patch 0–20 的 21 个主文件摘要未变。源码、来源 ZIP、验收记录和模型环境变量均保留；游戏不会继续加载本次翻译插件，需要使用时可重新安装。

## 构建与验证

开发机使用现有 MinGW 工具链编译网络模块，再生成 addon；用户安装和运行不需要编译器或 Python：

```pwsh
python tools/build_native_http.py
python tools/build_chat_probe.py --standalone
```

网络模块静态编译官方 [cJSON v1.7.19](https://github.com/DaveGamble/cJSON/tree/v1.7.19)，在源码和 ZIP 中保留其 MIT 许可。

- 修订 ZIP：70,292 字节，SHA-256 `5cbe8bc56ae50d73148a7f814cc8d17b6940efa566c10f2045edd736628f4948`。
- addon 主 patch：307,664 字节，SHA-256 `1d6bc3b6fb77fc715b8fe6e6280b372bf0bb3451603e2f71d9fe07e83b41ec58`。
- 内嵌 Lua 源码：307,451 字节，低于 512 KiB 上限，完整 LuaJIT 语法编译通过。
- 网络 DLL：52,224 字节，SHA-256 `02936117181a72270c06287d00e5fe49988d32f9ba25cf7829bf63252eacd30d`；ABI 导出和 Windows 系统依赖已核验。

全套 `python -m unittest discover -s tests -v` 的 101 项通过，零失败、零跳过，计时 27.448 秒。新增回归使用本机模拟端点与假密钥，覆盖 URL 补全与自定义路径保留、中文原文保留、UTF-8、无效响应、重定向拒绝、缓存、取消、停用、每分钟限流、非阻塞提交、超时及时交付与晚结果处理；Lua 加载覆盖哈希、长度、重解析点、临时文件失败清理和文件信息 ABI。没有针对原生队列满、完整 60 秒 TTL 或全部退避时序单独做耗时回归；共用 Lua core 的队列和过期用例仍在全套中执行。

ZIP CRC、内嵌 DLL 指纹、许可条目及 manifest 已核验。临时目录实际安装、重复部署和实际回滚已通过；本机真实游戏核验使用收据脚本部署，Arsenal 的实际导入尚未单独操作验证。

## 真实游戏核验（2026-10-02）

修订版部署后，用户按直接启动游戏的流程验证，并确认“原聊天行已变成中文”。16:22:29 的新会话报告为 `mode: standalone`、`transport: in_process_winhttp`，原生初始化和最后状态均为 `0`。共执行 6,308 个更新步，提交 1 条消息、收到 1 条译文、确认原位回写 1 次；翻译错误、适配器错误和回写错误均为 `0`，待处理数为 `0`。这确认了环境变量配置与进程内翻译路径在真实游戏中完成一次请求和替换。

不含聊天、地址或密钥的报告已保存到本机忽略目录 `artifacts/standalone-runtime/confirmed.json`，SHA-256 为 `0e74c582224ccdf3ecfe4a455d884bce3b9e221dd732a4e78da48b0eee7c9b41`。这是单次功能验收，尚未覆盖长时间游玩、多人连续消息或所有服务商；此前转储也不能单独证明所有启动异常都来自已修复的结构越界。
