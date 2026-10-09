# 当前实现与技术边界

本文记录当前独立翻译包的实现约束。源码是具体行为的最终依据。

## 模块与兼容门禁

- [game/chat_probe.lua](../game/chat_probe.lua) 连接 LuaJIT FFI、聊天适配器、翻译状态机、布局和状态报告。
- [game/chat_probe_core.lua](../game/chat_probe_core.lua) 校验固定游戏构建及代码签名，并限制内存读取。
- [game/chat_translate_core.lua](../game/chat_translate_core.lua) 管理新消息、请求队列、响应、过期和回写状态。
- [game/chat_http_native.lua](../game/chat_http_native.lua) 校验并加载随包嵌入的原生 DLL。
- [game/settings.lua](../game/settings.lua) 向 Mod Options Menu 注册目标语言选项，不读取或控制翻译服务配置。
- [native/client.c](../native/client.c) 管理公开 ABI、后台线程、队列、缓存与限流；原生客户端各模块共同链接为一个 DLL。
- [tools/build_package.py](../tools/build_package.py) 将固定 loader 资源、Lua addon 和原生模块合成 Arsenal 安装包。

当前兼容范围为 Steam build 25480438、游戏 EXE 1.8.46015.0。门禁同时核对 game.dll SHA-256 2e2c3b7c2500646dadd5f2b4c6e0504dbb7e7896139f64cddc0d1813c718f51e、文件长度 15,522,408、PE timestamp 1790161983、SizeOfImage 74,727,424，以及可执行节 RVA 0x1000、长度 34,667,155、标志 0x60000020 和已知指令签名。指纹或签名不匹配时不会进入聊天回写路径。

## 临时发送代码窗口探针

[tools/build_outgoing_probe.py](../tools/build_outgoing_probe.py) 生成一个临时只读诊断包，保留现有 Arsenal GUID、patch 名称、资源布局及固定 loader 和 Mod Options Menu 上游输入。该入口只嵌入扫描核心，不启用观察、显示测试、翻译、设置或原生网络模块；正常入口中的 `OUTGOING_PROBE_ENABLED` 默认为 false，普通包不启用此模式。诊断包会暂时替换同 GUID 模组，需在采集后重新导入正常版本。

探针先复用磁盘 `game.dll` 的 SHA-256/文件长度门禁，再核对运行时 PE timestamp、SizeOfImage 和可执行节信息。通过后只读取七个固定代码窗口，总计 47,104 B：`(0x1097500, 0x3000)`、`(0x185F000, 0x2000)`、`(0xBEAF00, 0x1800)`、`(0xBDE300, 0x1000)`、`(0x1327F00, 0x2000)`、`(0x174FA00, 0x800)` 和 `(0x20BBA00, 0x1800)`。运行时区域及权限在读取前双重查询；每次内存读取最多 1 KiB，每个游戏更新步最多请求 4 KiB，最多进行四次受检读取迭代。完整的 4 KiB PE 头也分四次读取并计入首步预算。磁盘哈希 I/O 沿用原实现，独立于运行时内存读取预算，并在报告中注明。拒绝不匹配构建、无效节、区域空洞、权限变化及短读；某窗口读取失败后停止，不退回全节扫描。完整窗口才标为 `complete`，已读前缀标为 `partial`，未读或失败窗口不会伪装成完成。

报告通过现有 `probe/chat-probe-*.json` 通道输出模式 `outgoing_send_code_probe`、构建指纹、七个窗口的 RVA/长度/状态/十六进制内容，以及 `function_verification: unverified`。它不读取聊天正文、输入框内容或堆指针，不写进程内存，也不调用候选函数。候选 RVA、已知签名比较以及 31 字节候选签名都不构成已验证函数或 ABI；磁盘映像签名不能替代运行时调用约定、参数和所有权验证。

## 原生客户端模块

| 文件 | 职责 |
| --- | --- |
| [client.h](../native/client.h) | 提交、轮询、取消三个公开任务接口 |
| [internal.h](../native/internal.h) | 模块间的私有类型、上限与函数声明 |
| [client.c](../native/client.c) | 配置初始化、线程生命周期及集中管理的队列、缓存、限流状态 |
| [common.c](../native/common.c) | UTF-8、文本、JSON 与结果格式的共用工具 |
| [config.c](../native/config.c) | 持久环境变量读取、配置校验和 URL 补全 |
| [languages.c](../native/languages.c) | 后台读取已应用的入站和出站设置并提供服务商语言码 |
| [outgoing.c](../native/outgoing.c) | 固定构建门禁、发送入口 relay、出站 FIFO、上下文复核与状态 mailbox |
| [outgoing.h](../native/outgoing.h) | 出站控制器与 pump 的私有声明 |
| [adapter/base.c](../native/adapter/base.c) | 基适配器、服务选型、适配器表与共用表单、签名工具 |
| [adapter/chat_completions.c](../native/adapter/chat_completions.c) | Chat Completions 请求、提示词与响应解析 |
| [adapter/google.c](../native/adapter/google.c) | Google Basic v2 请求与响应解析 |
| [adapter/baidu.c](../native/adapter/baidu.c) | 百度通用翻译请求与响应解析 |
| [adapter/youdao.c](../native/adapter/youdao.c) | 有道文本翻译请求与响应解析 |
| [transport.c](../native/transport.c) | WinHTTP 传输、请求截止时间、取消检查与网络资源清理 |

各 `.c` 文件独立编译，私有函数只在 DLL 内链接。队列及线程共享状态集中在入口模块，通过少量私有函数协作；JSON 解析使用仓库内的 cJSON v1.7.19。

原生 DLL 以 GCC `-Os` 构建，以便完整 addon entry 留在 512 KiB 源码上限内；fake native fixture 使用相同优化级别。`tests/test_native_http.py` 在既有核心测试中检查构建 flag 和最终 entry 大小。构建 metadata 保持 ABI 2 schema，不增加导出；DLL 仍只导出 `HD2CT_Submit`、`HD2CT_Poll` 与 `HD2CT_Cancel`。

客户端基础模块放在 `native/`，翻译适配器及其共用实现放在 `native/adapter/`，第三方依赖放在 `native/vendor/`。源码文件名使用按职责或接口命名的小写 `snake_case`；内部符号保留 `hd2ct_` 前缀，公开 ABI 保留 `HD2CT_` 前缀，交付 DLL 名为 `hd2ct_http.dll`。

## 聊天正文与布局路径

聊天根指针位于 game.dll+0x346D538；事件环位于 root+0x4F7080，共 64 项、每项 0x4B4 字节，正文缓冲区位于项内 +0xB4，容量为 0x400 字节；控件属性表中的字符串指针指向该缓冲区。事件环项与 UI 控件槽不假定同索引。

聊天 manager 位于 root+0x14498。UI 有 64 个槽，从 manager+0x4390 开始，槽步长 0x3D8。控件属性表位于槽内 +0x220，count 在表内 +0x158，entries 从 +8 开始、每项 0x18 字节；count 最多接受 14。正文属性 key 为 0x7518C954，属性类型必须为已核验的字符串类型，正文指针还须匹配活动事件环中的有效项。当前事件类型 tag 为 0x1C12037F。

原生正文 setter wrapper 位于 game.dll+0x1441CA0，调用参数使用控件槽 +0x110、正文 key 和 UTF-8 字符串。底层字符串 setter（game.dll+0x143A1B0）借用传入指针，因此译文缓冲区需保持存活至进程结束；每进程最多保留 512 个缓冲区、合计 8 MiB。

布局回写限于同一固定构建。helper 位于 game.dll+0x18610C0，直接定位 wrapper 位于 +0x1860DA0；两处都先核对代码签名。历史 head/count 在 manager+0x13990/+0x139C0，历史槽仍从 +0x4390 开始、步长 0x3D8、最多 64 项。行高度在 +0x10，纵向缩放在 +0x20，位置缓存从 +0x3CC 开始；行间隔 float 来自 game.dll+0x23C7554。只使用已核验的位置直接分支（标志 0）。回写前后核对正文、历史、内存范围和行几何；正文属性确认后才重新测量目标行并重排当前历史。位置缓存的8字节完全包含在0x3D8字节整行内，整行范围核验同时覆盖位置字段。计算位置后先精确读取每行的8字节缓存；译文目标行始终调用定位 helper，以保留引擎副作用。其他行只有在新读缓存与目标字节完全相同时才跳过 helper，跳过前仍验证整行权限；实际 helper 调用前逐行重新验证权限，调用后再次读取并核对8字节位置。布局预检失败时不会调用正文 setter，setter 调用、正文确认或重排未能确认时会禁用该会话后续布局回写。

布局预检与 setter 前复核的三个无原生调用批次，按连续物理行合并权限查询，环回处分段；每个批次重新查询区域并检查 allocation，跨度跨 allocation 时回退到逐行检查。重排时测量前、测量后几何读取前、计算后定位前也分别新鲜合并查询；双查发现区域变化、或预期 allocation 改变时直接拒绝。每次实际原生定位前仍单独复核该行权限，不复用跨阶段或跨帧的权限结果。

四轮几何快照（prepare 的两轮、verify_prepared、测量后的复核）按最多4个连续物理行组成一次 read_exact 请求；只合并已知 allocation 相同的行，历史环回和 allocation 边界会分段，allocation 未知时按单行读取。四行跨度最多为 3×0x3D8+20=2,972 字节，单次请求仍小于4 KiB。返回跨度中只切取并比较每行高度与缩放的20字节，不比较间隔内其他字段；读取、区域核验或短读失败仍拒绝该次操作。受检读取会按4 KiB页面继续拆分，因此这里的逻辑请求数不等同于 ReadProcessMemory 调用次数。

翻译启用时，每步受检读取预算为256 KiB，普通观察模式仍为16 KiB。布局应用开始前预留224 KiB；现有预算不足时在正文 setter 之前返回 deferred。64行、同一allocation且连续的fake历史中，四轮几何的逻辑 read_exact 请求从逐行方案的256次降到64次；几何读取总字节从5,120增至190,208，单个批次跨度最多2,972字节。完整fake apply 在预置2,048字节预算下共读取200,729字节，其中包括64行位置缓存比较的512字节；不计预置预算，应用读取198,681字节，低于预留224 KiB。fake预算剩余61,415字节。以上是fake读长度和逻辑请求数，不代表实际游戏耗时或页面级系统调用次数。

每条待处理消息最多尝试一次写回。写回失败、过期或预算不足（deferred）都会结束该任务并清理请求，不会在后续帧重新尝试；stale 写回保留已见消息标记。另按64个槽位分别保留最近一次写回的owner/event/body身份，暂时读取失败或重连不会清除该记录，避免间接重新提交同一消息；观察到有效的新身份后才清除对应旧记录。槽位扫描的预算不足仍延迟到后续扫描，不影响这一写回终止规则。

## 配置与 HTTP/JSON 协议

首次有效提交由原生客户端启动一次后台初始化，读取服务配置的 Windows 持久环境变量：先读 HKCU\Environment，再读 HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment。若用户变量存在，即使值为空或无效，也不回退到系统变量；不读取进程继承值。每个进程只初始化一次；服务选择、地址、凭据和配置有效性由 C 管理，入站及出站启用状态由已应用的 Mod Options Menu 设置控制。

HD2CT_MODEL 非空时选择 AI 翻译；缺失、为空或仅含空白时选择机器翻译，按 URL 中忽略大小写的 google、baidu、youdao 依次匹配。已支持服务需要 HD2CT_API_URL 与 HD2CT_API_KEY，百度、有道还需要 HD2CT_APP_ID。模型名最多 256 字节，密钥与应用 ID 各最多 4096 字节，URL 最多 2048 字节。旧变量 HD2CT_ENABLED 与 HD2CT_TIMEOUT_SECONDS 不再读取，缺失或无效的 Mod Options Menu 启用项默认开启，超时项默认20秒，可选10、20、30秒。密钥保存在进程内供请求使用；配置无效或后台初始化失败时会清零，菜单关闭不会清理凭据或停止 worker。

入站 worker 每次开始处理任务时，从 `%LOCALAPPDATA%\CowboyBingus\Helldivers2\Logs\ModOptionsMenu.values` 的同一份 primary/backup 文件快照读取入站目标语言、主启用状态与超时。后台 controller 每秒读取一次同一文件中的主启用状态、出站开关、出站目标与超时；出站目标和超时在消息拦截入队时快照。缺失或无效时，入站设置分别回退到简体中文、开启和20秒，出站开关默认关闭、目标默认英语。启用值只接受小写 `true`/`false`，超时值是1起始的三项 choice 索引。可读且有记录的 primary 中若缺少某项或该项值无效，使用该项默认值，不从旧 backup 覆盖；primary 不可读、损坏或没有有效记录时才尝试 backup。提交、轮询和游戏线程不做该文件 I/O。

AI 适配器把地址根路径补全为 /chat/completions，或把精确的 /v1、/v1/ 补全为 /v1/chat/completions。机器翻译适配器仅把根路径补全为各自接口；其他路径保持原样。已支持服务要求 HTTPS，只有 localhost、127.0.0.0/8 和 ::1 可使用 HTTP。拒绝 URL 凭据、查询、片段、反斜杠、空白和控制字符；禁用重定向、cookies 和自动认证。

AI 适配器使用 OpenAI-compatible Chat Completions 请求，只发送 `model`、`messages` 和 `response_format: {"type":"json_object"}`。请求不显式设置采样或思考参数，也不发送 `stream`；模型使用服务端默认策略及非流式默认行为。端点需同时实现兼容的 Chat Completions 与 JSON mode；供应商原生 API 不一定支持这两者。JSON mode 保证输出是合法 JSON 语法，不保证对象字段结构；提示词要求只返回 `is_target_language`（布尔值）和 `translation`（字符串），客户端再严格校验字段、类型、重复字段和额外字段。[DeepSeek Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)；[通义千问结构化输出文档](https://help.aliyun.com/zh/model-studio/qwen-structured-output)。完整简体中文请求示例见 [ai-request-example.json](ai-request-example.json)。

系统提示词由通用部分和简体中文专属术语部分组成。通用部分填入目标语言并要求只翻译待处理聊天文本；仅 `zh_cn` 追加网络缩写、敌名和游戏术语对照，例如 Charger=牛、Spore Charger=孢子牛。繁体中文及其他目标语言不追加该段。

响应只接受 `choices[0].message.content` 中的完整 JSON 字符串；缺少 content 的工具调用响应会以 `BAD_RESPONSE` 拒绝，不执行工具、不发起后续请求，也不回退到其他响应字段。客户端校验 UTF-8、禁止 NUL、完整 JSON 消费、两个必需字段唯一出现、字段类型和译文长度。`is_target_language` 为 true 时返回原文保持结果；否则译文必须合法、非空且不超过 16,384 字节。译文与原文按字节完全相同时也返回 `SKIP`。请求超时由既有 10、20、30 秒设置控制。

## 原生任务接口

ABI 2 的 DLL 只导出三个函数，签名见 [client.h](../native/client.h)：

| 接口 | 语义 |
| --- | --- |
| `HD2CT_Submit(token, body, bytes)` | 校验并接收任务，返回 1 表示入队；无效参数、重复 token、队列已满或锁忙返回 0。首次提交触发后台初始化，调用方不等待配置读取或网络请求。 |
| `HD2CT_Poll(token, out, capacity, written)` | 非阻塞轮询；返回 1 时写入结果，written 不含末尾 NUL。未完成、token 不存在、锁忙或缓冲区不足返回 0。读取结果后由调用方取消该 token 以释放槽位。 |
| `HD2CT_Cancel(token)` | 取消公开提交的任务或释放已完成槽位；token 不存在也返回 1，单项取消遇锁忙返回 0，可重试。传入 NULL 取消全部公开提交任务，锁忙时由 C 记录并延后处理，保留服务配置与工作线程；私有出站任务由 FIFO 按 token 管理。 |

结果统一为 `OK\n译文`、精确的 `SKIP\n` 或 `ERR\n固定错误码`。Lua 对 OK 组合“原文 + 换行 + 译文： + 译文”，对 SKIP 保留原文并结束任务，对 ERR 显示短提示，不依据翻译方式或文本相等作决定。C 对服务响应解析后的 UTF-8 译文和原文按字节长度及内容作精确比较，相同则返回 SKIP，不去除空白、转换大小写或做 Unicode 归一化。菜单关闭后，开始处理的任务返回 SKIP，不查缓存、不占限流、不发 HTTP，也不缓存该结果；服务配置仍保留，重新启用后即可继续。普通缺失、无效或未知服务配置由 worker 对已接收任务返回固定错误；bootstrap、线程创建或 WinHTTP session 硬失败由 Poll 返回 `SERVICE_ERROR`，任务 token 保留供取消。

后台初始化在普通线程中执行，不在 DllMain 中读取注册表或执行网络工作。初始化后总是尝试启动两个 WinHTTP worker，包括服务配置缺失、无效或未知时；配置错误任务不会创建 HTTP 请求。worker 为每个任务快照启用状态和超时，已开始的任务不会被后续 APPLY 改写。请求超时从开始网络请求时起算，截止时间是该任务设置的请求超时与提交后60秒总时限两者中的较早值；Lua pending TTL 也为60秒。Lua 的心跳只表示传输模块已加载且本地时钟有效。异常、结束或复用 Lua 实例时，通过取消接口清理任务；C 服务生命周期独立于菜单启用项。

## 出站聊天路径

出站路径只在 standalone Lua addon 中运行。`chat_probe.lua` 返回独立的 outgoing pump 闭包；`chat_translate_core.lua` 包装游戏 update 时先调用原 update，再以不超过每 200 ms 一次的频率轮询 native reserved token `__hd2ct_outgoing_pump_v1`，然后再推进入站 probe。pump 不依赖入站 probe 是否已完成；一次返回 `SENT\n` 时本帧跳过入站扫描与回写，`SKIP\n` 则继续入站工作。pump 的时钟或 FFI 异常只停止该泵，不向游戏 update 抛出错误。Lua 不读取菜单配置；非 standalone 和诊断入口不启用该泵。

原生后台 controller 每秒读取一次 MOM 已保存设置并缓存 master/outgoing enabled、独立发送目标和超时。入站任务沿用原规则，在 worker 开始处理时读取入站目标、启用和超时；出站任务在拦截入队时快照发送目标与超时，worker 使用该快照并在处理时复核启用状态。缺失或无效的发送开关默认关闭，发送目标默认语言索引 3（英语）。

游戏发送调用点为 `game.dll+0x1860272` 的五字节 `CALL`。安装只接受已加载、文件名为 `game.dll` 且上级目录名为 `game` 的模块，并核对该构建的磁盘 SHA-256、文件长度、PE timestamp、SizeOfImage、调用点签名及原发送函数签名。调用点使用受限的近地址 RX relay，通过 `FF 25` 间接跳转到 C callback；原发送函数不改写，relay 不使用 trampoline。安装把 8 字节对齐字的 CALL 位移通过 `InterlockedCompareExchange64` 一次置换；恢复仅在该字仍等于本模块 patch 时 CAS 回原值。若版本门禁、近地址分配或 patch owner 检查失败，则保留原调用路径。目标游戏模块和包含 callback 的本模块在改写前 pin 到进程结束；relay 的独立 RX 分配也保留到进程结束，避免执行中的代码被卸载或释放。

C callback 仅在调用返回地址、已绑定的 Lua frame pump 线程、服务对象和缓存设置均有效时拦截。它通过受检内存读取最多复制 804 字节（含 NUL），要求正文非空且 UTF-8 有效；每条 FIFO 项使用自己的缓冲区，不保存游戏输入框指针。上下文快照包含游戏 root、发送 service、本地 uint64 ID、网络 manager 和最多 16 个 uint64 接收者；出站 replay 前再次核对完整上下文。队列最多 8 条，每次 pump 最多重放一条。入队失败、job pool 满、HTTP 错误、超时、无效/超长译文或发送开关关闭时回退到原文。游戏线程 pump 超过 2 秒未刷新时停止接收；若仍有同会话 pending，hook 保留队列并等待下一次安全 pump 或聊天调用，由后者先顺序发送 pending 原文再透传当前消息。上下文已变化、失效或无法安全读取/核验时会取消旧队列。翻译结果到达后先确认上下文并原子 claim FIFO head，再调用游戏原函数，重入时不会重复发送；claim 暂时失败会缓存结果，后续 pump 重试，并在开关关闭或期限已到时丢弃该译文、改发原文。

出站状态由原生后台 controller 最多每 5 秒写入 `%LOCALAPPDATA%\HD2ChatTranslate\mailbox\chat-outgoing-status.json`。状态只含固定计数器、固定失败码和开关/钩子状态，以及数值字段 `hook_failure_stage`、`hook_win32_error`；不含正文、UID、地址、密钥或指针。阶段编号固定为：0 NONE，1 INSTALL_ALIGN（安装对齐），2 INSTALL_READ（安装读取），3 INSTALL_WORD_MISMATCH（安装字不匹配），4 INSTALL_PROTECT_WRITE（安装保护设为可写），5 INSTALL_CAS_MISMATCH（安装 CAS 不匹配），6 INSTALL_PROTECT_RESTORE（安装恢复保护），7 INSTALL_FLUSH（安装刷新指令缓存），8 RESTORE_PROTECT_WRITE（恢复保护设为可写），9 RESTORE_CAS_MISMATCH（恢复 CAS 不匹配），10 RESTORE_PROTECT_RESTORE（恢复保护），11 RESTORE_FLUSH（恢复刷新指令缓存）。Win32 失败的错误码在调用返回后立即读取；普通比较不匹配的错误码为 0。安装 CAS 成功但恢复旧保护失败时，会在页面仍可写且字仍等于本模块 patch 的条件下 CAS 回原值、刷新指令缓存，再尽力恢复旧保护；若字已被其他代码改写则不覆盖。此失败路径保留模块与 relay 的 pin 生命周期。hook 与游戏线程 pump 不执行状态文件 I/O。目录、临时文件与目标文件均拒绝 reparse point，更新通过临时文件写入后替换。该文件与 Lua 状态报告相互独立。

fake native fixture 覆盖队列、超时、设置回退、上下文取消、patch owner 冲突、重入和 loopback HTTP；独立 VirtualAlloc 测试页还覆盖保护恢复失败后的 owner-match 回滚、foreign word 冲突和成功后诊断重置。这不能验证真实游戏中的接收者内存上下文、relay 安装与发送结果。真实游戏行为尚未验证。

## 翻译适配器

原生客户端用 AI、机器翻译两个基适配器组织具体适配器；基适配器负责共同的配置要求及路径规则，具体适配器负责请求构造、认证、接口路径与响应解析。WinHTTP 传输、工作线程、缓存、限流、超时、取消和队列共用。服务选型、凭据、配置校验和是否输出译文均由 C 处理；Lua 使用统一的三个任务接口。

| 适配器 | 协议与认证 | 成功响应 |
| --- | --- | --- |
| [Google Basic v2](https://docs.cloud.google.com/translate/docs/reference/rest/v2/translate) | POST /language/translate/v2；JSON q、target、format=text；[x-goog-api-key 请求头](https://docs.cloud.google.com/docs/authentication/api-keys-use)，不把密钥写入 URL | data.translations 的 translatedText、detectedSourceLanguage |
| [百度通用翻译](https://fanyi-api.baidu.com/doc_bd/21) | POST /api/trans/vip/translate；UTF-8 表单 q、from=auto、to、appid、salt、sign；sign=MD5(appid+原始 q+salt+密钥)，小写十六进制 | from、trans_result 中的 dst |
| [有道文本翻译](https://ai.youdao.com/DOCSIRMA/html/trans/api/wbfy/index.html) | POST /api；UTF-8 表单 q、from=auto、to、appKey、salt、curtime、signType=v3、sign、strict=true；sign=SHA256(appKey+input+salt+curtime+密钥) | errorCode=0、l、translation |

有道 input 按 Unicode 码点计数：最多 20 个码点时为完整 q，否则为前 10 个码点、十进制码点总数、后 10 个码点。签名使用尚未 URL 编码的 UTF-8 文本，表单各字段随后编码；curtime 为 UTC Unix 秒。签名及随机 salt 使用 Windows BCrypt。

机器翻译不发送 AI 提示词；Google 省略 source 参数启用自动检测，百度、有道使用 from=auto。target/to 使用目标语言目录中的服务商语言码，默认分别为 zh-CN、zh、zh-CHS。有道 strict=true 保证按指定目标处理，避免默认自动中译英。成功结果按实际译文与原文比较，不按源语言字段替换为原文；完全相同返回 SKIP，不同返回 OK。缓存保存完整的 OK 或 SKIP 结果，键包含原文与目标语言，命中时保持同一显示决定。

三家文本接口的公开文档没有给出自动检测同语种时可用于确定源文本为中文的专用错误码。[Google 文档](https://docs.cloud.google.com/translate/docs/languages)的同语言限制位于 AutoML 自定义模型范围，不能用于断言 Basic v2 的自动检测行为；百度 58001、有道 102 都可能表示其他不支持的语言。因此按实际响应判断成功或失败，通用语言错误显示短提示，不据此吞掉消息。同语种服务端响应需用有效凭据另行实测，离线回环只验证适配器解析与显示规则。

未知机器翻译服务收到消息后返回 UNSUPPORTED_SERVICE，不发 HTTP 请求，不占用限流或缓存错误。已支持服务缺少配置时返回 MISSING_CONFIG，配置无效时返回 INVALID_CONFIG，后台初始化失败时返回 SERVICE_ERROR；显式禁用返回 SKIP。错误码映射为固定短提示，响应原文、签名、密钥及堆栈不进入聊天或状态报告。

## 游戏内设置

菜单使用 [Mod Options Menu v1.2](https://github.com/CowboyBingus/ModOptionsMenu/releases/tag/v1.2) 的原生 MODS 页、toggle/choice 类型及 APPLY 保存行为。安装包合并其未经修改的 Lua 资源和 0BSD 许可；五个选项 ID 为收到消息目标语言、主启用、请求超时、发送前翻译和发送目标语言，均共用 mod_id `hd2chattranslate`。默认值分别为索引1（简体中文）、true、索引2（20秒）、false 和索引3（英语）。使用说明见[游戏内设置](settings.md)。

菜单文本由[resources/menu_locales.json](../resources/menu_locales.json)提供15种游戏UI语言的完整字符串；[tools/menu_locales.py](../tools/menu_locales.py)在构建时严格校验locale集合、字段、长度和超时占位符。打包器将一次读取的本地化快照编码进设置Lua模块。每个展示回调只用 rawget 读取 `_G.BingusTranslations` 的 version 与 canonical `game_language`，按精确locale、基础语言、英语的顺序选择文本；未知或无效注册表安全回退英语。回调不创建或修改注册表，也不读取游戏内存或Steam设置。MOM在每次打开ESC菜单时更新语言并重新求值函数文本，因此游戏语言变更在重新打开菜单后生效；游戏运行期间不读取语言文件。

[resources/target_languages.json](../resources/target_languages.json) 的 schema 3 是五项菜单定义、AI 目标语言与三个机器翻译语言码的共同来源。[tools/target_languages.py](../tools/target_languages.py) 校验目录并在原生构建目录生成 C 表头，包含全部选项 ID、默认值和超时映射；打包时从同一目录生成 Lua 菜单项。语言保存值是从 1 开始的索引，已有语言顺序必须保持稳定。构建 metadata 的 `target_languages_sha256` 覆盖整个目录，安装包构建器拒绝 DLL 与菜单目录不一致的组合。

C 在后台 worker 开始处理入站任务时读取 `%LOCALAPPDATA%\CowboyBingus\Helldivers2\Logs\ModOptionsMenu.values`；后台 controller 每秒读取五个本插件设置，游戏更新帧不执行设置文件 I/O。只校验本插件的选项值；主文件缺失、不可读或没有制表符分隔记录时尝试 `.bak`，主文件已有记录而本插件项缺失或无效时直接使用对应默认值。其他模组的开关、滑块等值不会导致读取旧备份。读取上限为 512 KiB，拒绝非普通文件与 reparse point，并允许菜单原子替换文件。此路径对应随包 loader 的默认日志目录；不支持其他 loader 自定义的日志目录。

入站目标语言在任务内保持不变；出站目标语言与超时在拦截时快照，后续消息使用 controller 已读到的新选择；缓存按语言隔离。Lua 设置模块不读取已选值、服务凭据或启用状态；它只为菜单展示读取上述本地化注册表。公开 C ABI 仍只有提交、轮询、取消三个函数。

## 调度、队列与数据上限

初始化基线每 200ms 最多读取 4 个槽，64 槽在理想调度下约需 3.2 秒。常规扫描每秒生成一次计划并复核历史与事件环索引；索引变化时按 FIFO 检查新增消息、优先检查最新行，并在随后两个计划复查最新行。索引稳定后不持续深读最新行。

后台每个计划轮转两个物理槽号，只读取当前 UI 历史中的活动槽；事件环非空而 UI 历史为空时保留两槽稀疏回退，避免持续触发全量扫描。后台遍历 64 个槽号约需 32 秒，低帧率、读取预算或队列等待会延长此时间。首次索引、历史缩小、不连续变化或无法确认的环绕走重新同步路径，owner 改变仍建立完整基线。不以索引未变作为正文未变的依据，历史槽内的原位正文变化依靠后台复查发现。

扫描计划只保存有界的槽号，每个游戏更新步仍最多读取 4 槽、提交一个请求；较大的计划分帧处理。待处理请求上限为 32 条，消息 60 秒过期。长帧之后不补跑积压周期。翻译状态机一旦进入 adapter.apply，该步便保留扫描计划游标与 next_scan_ms，并跳过本步扫描；下一步恢复扫描。到期的常规报告也延至下一步未进入 apply 时写入，terminal 报告仍立即强制写出。

游戏更新链每帧正常转发。没有待处理请求和未完成扫描计划时，插件只检查单调时钟，在扫描、心跳检查、心跳过期或报告期限到达时运行翻译状态机；存在待处理工作时保留逐帧推进。状态报告的 steps、active_steps 统计实际状态机执行次数，不等于游戏帧数。更新包装器通过固定的返回值转发函数保留任意多返回值和末尾 nil，不逐帧创建返回值表。

受检读取复用同步调用的 FFI 输出、计数、页查询和指针解析缓冲区；每次仍重新查询页属性、复核区域与分配范围，并执行精确长度的 ReadProcessMemory。成功读取返回独立的 Lua 字符串快照，不跨帧缓存动态内存。事件环活动计数为零时，在根指针及环元数据双读一致后直接返回空槽；存在活动事件时继续完整的属性、事件和正文核验。

待处理请求轮流检查；每个请求最早每 200ms 检查一次，每个游戏更新步最多处理一个响应。两个原生 WinHTTP worker 在后台运行。原生队列最多 32 个任务，原始服务商响应上限 1,000,000 字节；成功结果缓存最多 512 项，网络请求最多每分钟 30 次。请求超时由游戏菜单控制，可选10、20或30秒；任务总期限为60秒。

聊天正文须是合法 UTF-8、无 NUL、1–1,023 字节；译文上限 16,384 字节。显示分隔符“\n译文：”占 10 个 UTF-8 字节，正文加译文的合计上限为 17,417 字节。安装包中的 Lua 源码上限为 512 KiB。失败提示从固定字典选择，不展示服务商响应正文或堆栈。

## 通知门禁与身份局限

请求前会检查正文属性、活动事件环指针和事件类型。类型不匹配的槽样本在提交请求前被过滤；slot_event_filtered 计数的是采样次数，同一可见事件可能多次采样，不能当作通知数量。没有基于“发现”“巢穴”等正文关键词的过滤规则。当前 tag 的代码路径关联不足以证明它专属于玩家消息或系统通知类别，因而不能据此保证完整分类。

事件记录没有已证实的唯一 epoch。根指针、事件槽、控件属性、正文和历史的多次核验可以减少误回写风险，但无法完全排除同槽快速复用且正文又相同的情况。后续游戏构建不在当前兼容范围内。

## 原生模块安全加载

DLL 随 Lua addon 嵌入，构建器要求 metadata 的 ABI 为 2，并核验字节长度和 SHA-256。Lua 侧将其写入 %LOCALAPPDATA%\HD2ChatTranslate\native 目录，DLL 文件名使用其 SHA-256 值；再从已核验的绝对路径使用 LoadLibraryExW，并将依赖搜索限制在 System32，按三个任务接口解析函数。路径及文件句柄拒绝 reparse point；文件大小和哈希在加载前复核。C 在启动后台初始化前使用 GetModuleHandleExW pin 保持至游戏进程结束。

Lua FFI 的 BY_HANDLE_FILE_INFORMATION 定义为 52 字节，并核对 dwVolumeSerialNumber 偏移 28、nFileSizeHigh 偏移 32、nFileIndexLow 偏移 48。原生构建限定为 Win64 PE、三个任务接口导出和 Windows 系统依赖，不接受额外 MinGW 运行库。

状态报告位于 %LOCALAPPDATA%\HD2ChatTranslate\mailbox\chat-translate-{session}.json。独立模式下请求与响应经进程内 API 传递，mailbox 只用于状态报告。首次与停止时写入报告，运行期间最多每 5 秒更新一次；通过临时文件完整写入、关闭后替换，不强制刷盘。

## Arsenal patch 格式

生成的 addon patch 使用一个 Lua type（type_count=1）和三个资源文件（file_count=3），类型值为 0xA14E8DFA2CD117E2。资源按名称 hash 排序，编号为 0、1、2，依次为 loader、聊天插件、Mod Options Menu；资源数据按 16 字节边界对齐。Bingus Shared Loader v18 和 Mod Options Menu v1.2 的 Lua resource 从固定输入中校验后原字节嵌入；stream 与 gpu_resources sidecar 均为空。

Arsenal manifest Guid 固定为 a741d044-972b-4dc5-b08e-1a68441e1d7f，patch 文件名为 9ba626afa44a3aa3.patch_0。构建时保留这两个身份值，以便导入新 ZIP 时更新同名模组。Lua resource 名为 mods/hd2chat/HD2ChatTranslate，加载器依据此标识发现 addon。

## 启动日志保留

初始化时仅清理 `%LOCALAPPDATA%\HD2ChatTranslate` 下三种已发布的 JSON 报告：`probe/chat-probe-*.json`、`observe/chat-observe-*.json` 和 `mailbox/chat-translate-hd2ct_*.json`。各类独立按最后写入时间降序保留 10 个，同时间以文件名确定顺序；即将生成本次会话报告的类别先保留 9 个。会话内同类报告使用固定文件名更新，不增加文件数量。

枚举和删除使用 UTF-16 Win32 API；`WIN32_FIND_DATAW` 大小为 592 字节，文件名偏移为 44。只处理名称完整匹配的普通文件，拒绝目录和 reparse point，不递归，不删除 `.partial`、`.tmp`、配置、通信文件或原生 DLL。枚举不完整时不删除该类文件；删除失败或清理异常会被捕获，不中断插件。清理不进入逐帧或状态报告更新路径。

## 状态报告字段

独立包报告 schema_version 为 1，mode 为 standalone，transport 为 in_process_winhttp。status 可为 target_unverified、inactive、baseline、ready、pending、applying 或 stopped；code 仅包含固定状态原因。counters 是白名单计数，不含聊天正文、URL 或密钥。常见字段包括 submitted、translations_ready、error_displays_ready、apply_confirmed、slot_event_filtered；pending_count 上限为 32，baseline_remaining 上限为 64。

C 管理的配置、启用及初始化状态不作为 Lua 报告字段。任务失败通过响应短码显示，报告保留消息、队列和布局的白名单计数。

layout.verified 表示布局签名门禁通过；reflows_confirmed、rows_positioned、positions_skipped 和 failures 分别记录已确认重排、实际定位 helper 调用、缓存匹配跳过与布局失败次数。预算不足的 deferred 不计入 failures。固定白名单的 last/max 毫秒字段覆盖 apply 的 prepare、verify、setter、verify_apply、reflow、总耗时，以及重排的 measure/position 和 read_slot、scan_plan、submit、response、report 阶段；GetTickCount64 不可用或返回异常时只跳过该样本。报告不含正文、URL、密钥或异常文本。last_failure_code 表示最近失败阶段：

| 值 | 最近失败阶段 |
| --- | --- |
| 0 | 尚无失败 |
| 1 | 上下文、manager 或地址核验 |
| 2 | 历史读取 |
| 3 | 历史索引或计数范围 |
| 4 | 行内存可写性或分配范围 |
| 5 | 行几何读取 |
| 6 | 纵向缩放范围 |
| 7 | 行高范围 |
| 8 | 目标行不在当前历史中 |
| 9 | 历史发生变化 |
| 10 | 行几何发生变化 |
| 11 | 正文设置后测量、定位或确认失败 |
| 12 | 其他捕获到的异常 |

last_history_head 和 last_history_count 大于等于 65 时统一记作 65；last_geometry_slot 范围为 0–63。几何失败时记录 last_scale_milli、last_height_milli（已有值乘 1000 四舍五入，非有限、负数或大于 1,000,000 时记 0）及对应 class：0 未测量、1 非有限、2 负数、3 零、4 正常正值、5 超出允许范围。scale 允许 0.01–16，height 允许 0–4096。
