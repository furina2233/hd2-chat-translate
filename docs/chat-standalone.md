# 无伴随程序的聊天翻译

独立模式把模型网络请求放入游戏进程内的两个后台线程。Lua 插件只提交正文、轮询结果并执行已经核验的聊天回写，不启动 Python、外部服务或子进程。游戏仍先显示原文，译文成功后更新对应的本机聊天行。

独立模式已完成构建、离线验收和一次真实游戏翻译核验。首次真实游戏启动发现文件信息结构的 FFI 越界写入，已补齐字段并增加布局门禁及真实 Windows API 回归；修订版成功在进程内请求模型并替换原聊天行，详情见下方验收记录及 [崩溃调查记录](crash-analysis-2026-10-02.md)。

最新修订包为 `HD2ChatTranslate20261002173335.zip`。此前 `HD2ChatTranslate20261002170232.zip` 存在合并 TOC 格式错误，本机测试在启动时崩溃，现已按上游官方构建器修正；请更新包并重新部署，修订包的游戏启动仍待本轮确认。

## 安装与配置

在游戏正常关闭时，向 HD2Arsenal 导入最新独立版 `HD2ChatTranslate年月日时分秒.zip`，更新同名模组，启用并部署。新包将 Bingus Shared Loader v18 的原始启动资源与聊天 addon 合入同一个 patch，无需另外导入加载器。默认优先级时将本模组放在列表最底端；若启用“第一个模组优先”，则放最顶端。不要同时启用旧聊天翻译 addon；独立版保留相同 Guid 与 Lua 资源作为升级。

Arsenal 的 [导入说明](https://docs.rsnl.gg/mod-management/adding-mods) 支持包内的 patch 文件。新包保持 V1 manifest 的单个 `Include: ["Addon"]` 选项，只部署一个含两条 Lua 资源的 patch，避免两份同名 patch 覆盖。`Include` 仅选择包内文件夹，不会自动安装外部依赖；参见 [manifest 说明](https://docs.rsnl.gg/mod-builder/manifest)。loader 的 [发现器](https://github.com/CowboyBingus/BingusSharedLoader/blob/main/src/discover.lua) 会逐条扫描 patch 中的 Lua 资源，支持在同一个 TOC 内找到聊天 addon。

已配置环境变量的用户只需更新安装包并重新部署，随后启动游戏，等待约一分钟再发送英文测试消息。出现问题时先检查本说明中的状态报告是否生成了新会话，避免把旧的成功报告当成本次安装结果。

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

正常关闭游戏后，在 Arsenal 禁用或移除独立包并重新部署。新包移除时其内置启动资源一并移除；其他模组如果依赖 Shared Loader，需保留或恢复它们的独立加载器。提取的本插件 DLL 缓存可以在游戏关闭后删除，它只位于本插件专用的 `native` 目录。环境变量可以自行删除或将 `HD2CT_ENABLED` 设为 `0`。

此前本机游戏核验使用独立槽位与指纹收据部署。如仍存在这类历史部署，改由 Arsenal 管理前，先正常退出游戏并执行 `pwsh -NoProfile -File tools/deploy_probe.ps1 -Standalone -Rollback`，再导入和部署新包，避免重复部署。该脚本仅删除历史收据拥有且摘要匹配的六个文件，不管理新的 Arsenal 合并包。旧伴随程序版的来源包仍保留。

2026-10-02 核验完成后，按用户要求移除了本机部署：patch 21 的聊天 addon、patch 22 的本次配套 loader，共六个文件，以及活动部署收据；另删除了摘要匹配的网络 DLL 缓存。原 Arsenal patch 0–20 的 21 个主文件摘要未变。源码、来源 ZIP、验收记录和模型环境变量均保留；游戏不会继续加载本次翻译插件，需要使用时可重新安装。

## 构建与验证

开发机使用现有 MinGW 工具链编译网络模块，再生成 addon；用户安装和运行不需要编译器或 Python：

```pwsh
python tools/build_native_http.py
python tools/build_chat_probe.py --standalone
```

输出统一为 `artifacts/HD2ChatTranslateYYYYMMDDHHMMSS.zip`，时间取北京时间；同秒同名包已存在时拒绝覆盖。CLI 的 `--output` 也需遵守该文件名格式。网络模块静态编译官方 [cJSON v1.7.19](https://github.com/DaveGamble/cJSON/tree/v1.7.19)，在源码和 ZIP 中保留其 MIT 许可。合并包构建还需本机已有的固定官方 `artifacts/Bingus-Shared-Loader-v18.zip`；构建器核验其摘要并原样保留启动资源及上游说明、元数据，不自动下载或改写加载器。

以下大小、摘要及 101 项测试为此前单 addon 包的验收记录，新的 Arsenal 合并包另见下方修复记录：

- 修订 ZIP：70,292 字节，SHA-256 `5cbe8bc56ae50d73148a7f814cc8d17b6940efa566c10f2045edd736628f4948`。
- addon 主 patch：307,664 字节，SHA-256 `1d6bc3b6fb77fc715b8fe6e6280b372bf0bb3451603e2f71d9fe07e83b41ec58`。
- 内嵌 Lua 源码：307,451 字节，低于 512 KiB 上限，完整 LuaJIT 语法编译通过。
- 网络 DLL：52,224 字节，SHA-256 `02936117181a72270c06287d00e5fe49988d32f9ba25cf7829bf63252eacd30d`；ABI 导出和 Windows 系统依赖已核验。

全套 `python -m unittest discover -s tests -v` 的 101 项通过，零失败、零跳过，计时 27.448 秒。新增回归使用本机模拟端点与假密钥，覆盖 URL 补全与自定义路径保留、中文原文保留、UTF-8、无效响应、重定向拒绝、缓存、取消、停用、每分钟限流、非阻塞提交、超时及时交付与晚结果处理；Lua 加载覆盖哈希、长度、重解析点、临时文件失败清理和文件信息 ABI。没有针对原生队列满、完整 60 秒 TTL 或全部退避时序单独做耗时回归；共用 Lua core 的队列和过期用例仍在全套中执行。

ZIP CRC、内嵌 DLL 指纹、许可条目及 manifest 已核验。临时目录实际安装、重复部署和实际回滚已通过；本机真实游戏核验使用收据脚本部署，Arsenal 的实际导入尚未单独操作验证。

## 真实游戏核验（2026-10-02）

修订版部署后，用户按直接启动游戏的流程验证，并确认“原聊天行已变成中文”。16:22:29 的新会话报告为 `mode: standalone`、`transport: in_process_winhttp`，原生初始化和最后状态均为 `0`。共执行 6,308 个更新步，提交 1 条消息、收到 1 条译文、确认原位回写 1 次；翻译错误、适配器错误和回写错误均为 `0`，待处理数为 `0`。这确认了环境变量配置与进程内翻译路径在真实游戏中完成一次请求和替换。

不含聊天、地址或密钥的报告已保存到本机忽略目录 `artifacts/standalone-runtime/confirmed.json`，SHA-256 为 `0e74c582224ccdf3ecfe4a455d884bce3b9e221dd732a4e78da48b0eee7c9b41`。这是单次功能验收，尚未覆盖长时间游玩、多人连续消息或所有服务商；此前转储也不能单独证明所有启动异常都来自已修复的结构越界。

## Arsenal 安装修复（2026-10-02）

移除临时部署后，用户仅导入旧的单 addon ZIP。取证确认 Arsenal 的 `patch_21` 与旧包聊天资源完全匹配，但游戏目录没有 Shared Loader 的对应资源，加载器日志和翻译报告仍停在之前的成功会话；三个模型环境变量均已配置。因此聊天代码虽然被部署，缺少负责启动它的加载器。

本次修复只调整构建与包装：把经固定摘要验证的原始 v18 启动资源和原聊天资源放入一个 archive，保持上游启动内容、聊天逻辑与原生网络 DLL 不变，并保留上游说明及来源信息。新包的实际 Arsenal 导入与部署已经核验，游戏内翻译仍需新会话验证；此前临时脚本部署的成功不能代替这个验证。

新包为 `HD2ChatTranslate20261002170232.zip`，83,094 字节，SHA-256 `9053e33dba45a6d54b6114ef43f68e14d5e682588b4411565e030bc0a09af071`。唯一主 patch 为 328,368 字节，SHA-256 `81b4fde7e8bfedaad7ee507cd21312c3d22c2612278d0e477b954d77024d180f`。两条资源位于对齐的偏移 272 与 20,896，边界和填充均已检查；启动资源与原始 v18 包逐字节一致，聊天资源与此前游戏验证过的单 addon 包逐字节一致。内嵌 DLL 指纹、实际包内完整 LuaJIT 语法、ZIP CRC、单个 Include 选项和上游原文文件均通过检查。数值证据保存在本机忽略目录 `artifacts/arsenal-repair/package-verified.json`。

本轮全套 `python -X utf8 -m unittest discover -s tests -v` 为 111 项通过，零失败、零跳过，28.598 秒；新增覆盖双资源 TOC、原始 loader 内容保留、错误来源包与非空 sidecar 拒绝、构建输入保护、北京时间命名，以及文件预检查后出现同名包时拒绝覆盖。源码语法和 `git diff --check` 通过，完整 UTF-8 日志位于 `artifacts/arsenal-repair/full-tests.log`。本机 Arsenal 0.36.2 没有查证可用的外部 ZIP 导入、部署接口；需要在其界面导入更新，启用并 Deploy，再检查实际部署指纹及新游戏会话。

17:10:21 的 Arsenal 部署快照及模组库确认新包已导入同一 Guid，当前 `default` profile 的选项已启用，来源主 patch 与游戏中的 `patch_21` 摘要均为 `81b4fde7e8bfedaad7ee507cd21312c3d22c2612278d0e477b954d77024d180f`。两个空 sidecar 也存在且摘要匹配，原 patch 0–20 的 21 个主文件摘要未变，游戏 DLL 指纹仍匹配目标构建。此次导入与部署通过；核验时尚未生成比 16:22 成功报告更新的游戏会话，不能据旧计数宣布新包的游戏内翻译已完成。

### 合并 TOC 崩溃修复

后续 17:10、17:15 和 17:18 的三次启动在 `helldivers2.exe+0x5F2EB0` 读访问冲突，网络 DLL 尚未加载，loader 日志与翻译报告未更新。独立核对 [上游 archive.py](https://github.com/CowboyBingus/BingusSharedLoader/blob/main/scripts/archive.py) 及本机原有多资源 patch，发现合并构建器将头部的 `type_count` 与 `file_count` 写反，且第二条资源的末尾编号重复为 `0`。单资源时两个计数都是 `1`，此前测试又沿用生产代码的错误假设，未发现这两处问题。

修复将头部写为 `type_count=1, file_count=2`，资源按 hash 排序，文件行编号为 `0,1`。独立解析回归拒绝旧错头和重复编号，并核对由上游构建器生成的双资源黄金摘要。另使用审阅过的上游 `make_archive` 纯函数，对本包的两个实际资源生成参考 archive；修订主 patch 与该结果逐字节一致。上游源码快照 SHA-256 为 `564dda73591088ced78be67fdae0c85df4eb1d82d6371cd16224ce7d90b25a12`。

修订 ZIP `HD2ChatTranslate20261002173335.zip` 为 83,092 字节，SHA-256 `5d8064b0a4ebb3d27cf4e131b4b1f5cac91485a3a1f2e04de3b23340ef11092a`；主 patch 仍为 328,368 字节，SHA-256 `4cb51d198a387746afbace921dc6f9763e7a587748ea16e97c252defd92097cf`。它与失败 patch 仅在字节偏移 4、8、260 不同；两个资源内容、DLL、manifest、许可及上游说明均未变。ZIP CRC、实际包内 LuaJIT 语法、独立 TOC 和官方构建器比对通过，全套 112 项测试通过，零失败、零跳过，28.628 秒。数值证据与日志保存在 `artifacts/arsenal-repair/toc-fixed-package-verified.json`、`official-writer-comparison.json` 和 `toc-fixed-full-tests.log`。

失败包保留供比对，新的实际 Arsenal 更新与游戏启动需要本轮重新核验。新转储表明异常发生在网络 DLL 加载前，不能单凭转储证明唯一原因；修订包的结构一致性也不能代替真实启动验证。
