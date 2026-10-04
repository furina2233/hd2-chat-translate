# 当前实现与技术边界

本文记录当前独立翻译包的实现约束。源码是具体行为的最终依据。

## 模块与兼容门禁

- [game/chat_probe.lua](../game/chat_probe.lua) 连接 LuaJIT FFI、聊天适配器、翻译状态机、布局和状态报告。
- [game/chat_probe_core.lua](../game/chat_probe_core.lua) 校验固定游戏构建及代码签名，并限制内存读取。
- [game/chat_translate_core.lua](../game/chat_translate_core.lua) 管理新消息、请求队列、响应、过期和回写状态。
- [game/chat_http_native.lua](../game/chat_http_native.lua) 校验并加载随包嵌入的原生 DLL。
- [native/hd2ct_http.c](../native/hd2ct_http.c) 使用 WinHTTP 后台线程发送请求；JSON 解析使用仓库内的 cJSON v1.7.19。
- [tools/build_package.py](../tools/build_package.py) 将固定 loader 资源、Lua addon 和原生模块合成 Arsenal 安装包。

当前兼容范围为 Steam build 25480438、游戏 EXE 1.8.46015.0。门禁同时核对 game.dll SHA-256 2e2c3b7c2500646dadd5f2b4c6e0504dbb7e7896139f64cddc0d1813c718f51e、文件长度 15,522,408、PE timestamp 1790161983、SizeOfImage 74,727,424，以及可执行节 RVA 0x1000、长度 34,667,155、标志 0x60000020 和已知指令签名。指纹或签名不匹配时不会进入聊天回写路径。

## 聊天正文与布局路径

聊天根指针位于 game.dll+0x346D538；事件环位于 root+0x4F7080，共 64 项、每项 0x4B4 字节，正文缓冲区位于项内 +0xB4，容量为 0x400 字节；控件属性表中的字符串指针指向该缓冲区。事件环项与 UI 控件槽不假定同索引。

聊天 manager 位于 root+0x14498。UI 有 64 个槽，从 manager+0x4390 开始，槽步长 0x3D8。控件属性表位于槽内 +0x220，count 在表内 +0x158，entries 从 +8 开始、每项 0x18 字节；count 最多接受 14。正文属性 key 为 0x7518C954，属性类型必须为已核验的字符串类型，正文指针还须匹配活动事件环中的有效项。当前事件类型 tag 为 0x1C12037F。

原生正文 setter wrapper 位于 game.dll+0x1441CA0，调用参数使用控件槽 +0x110、正文 key 和 UTF-8 字符串。底层字符串 setter（game.dll+0x143A1B0）借用传入指针，因此译文缓冲区需保持存活至进程结束；每进程最多保留 512 个缓冲区、合计 8 MiB。

布局回写限于同一固定构建。helper 位于 game.dll+0x18610C0，直接定位 wrapper 位于 +0x1860DA0；两处都先核对代码签名。历史 head/count 在 manager+0x13990/+0x139C0，历史槽仍从 +0x4390 开始、步长 0x3D8、最多 64 项。行高度在 +0x10，纵向缩放在 +0x20，位置缓存从 +0x3CC 开始；行间隔 float 来自 game.dll+0x23C7554。只使用已核验的位置直接分支（标志 0）。回写前后核对正文、历史、内存范围和行几何；正文属性确认后才重新测量目标行并重排当前历史。预算耗尽会延迟，不会绕过核验；布局预检失败时不会调用正文 setter，setter 调用、正文确认或重排未能确认时会禁用该会话后续布局回写。

## 配置与 HTTP/JSON 协议

原生模块在初始化时读取 Windows 持久环境变量：先读 HKCU\Environment，再读 HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment。若用户变量存在，即使值为空或无效，也不回退到系统变量；不读取进程继承值。每次游戏启动只初始化一次。

HD2CT_API_URL、HD2CT_MODEL、HD2CT_API_KEY 为必填；HD2CT_TIMEOUT_SECONDS 可选，默认为 20 秒、有效范围 1–120；HD2CT_ENABLED 可选，默认为 1，允许值为 0 或 1。模型名最多 256 字节，密钥最多 4096 字节，URL 最多 2048 字节。API key 保存在进程内供请求使用，停用或初始化失败时会清零。

只把地址根路径补全为 /chat/completions，或把精确的 /v1、/v1/ 补全为 /v1/chat/completions；其他路径保持原样。要求 HTTPS，只有 localhost、127.0.0.0/8 和 ::1 可使用 HTTP。拒绝凭据、查询、片段、反斜杠、空白和控制字符；禁用重定向、cookies 和自动认证。

请求 JSON 含 model、temperature=0、response_format.type=json_object 及 system/user 两条 messages。聊天正文单独放在 user message；提示词常量 HD2CT_SYSTEM_PROMPT 位于 [native/hd2ct_http.c](../native/hd2ct_http.c)。它要求中文原样保留、其他语言翻为简短自然的简体中文，并要求 JSON 仅含 is_chinese 和 translation。当前规则包含常见缩写、游戏术语和敌名映射，例如 Charger=牛、Spore Charger=孢子牛；不对 gg 或 ggs 加特例。

响应读取 Chat Completions 的 choices[0].message.content，并要求其为只含 is_chinese(bool) 与 translation(string) 的 JSON 对象。若 is_chinese 为 true，原文直接作为结果；否则 translation 必须是合法 UTF-8、非空且不超过 16,384 字节。

## 调度、队列与数据上限

扫描及基线初始化每次最多读取 4 个槽，每 200ms 开始一个批次；读内存的单步预算为 16 KiB。64 槽基线在理想调度下约需 3.2 秒。每步最多提交一个请求，待处理上限为 32 条，消息 60 秒过期。低帧率会延长扫描和显示等待；长帧之后不补跑积压批次。

待处理请求轮流检查；每个请求最早每 200ms 检查一次，每个游戏更新步最多处理一个响应。两个原生 WinHTTP worker 在后台运行。原生队列最多 32 个任务，原始服务商响应上限 1,000,000 字节；成功结果缓存最多 512 项，网络请求最多每分钟 30 次。超时范围由 HD2CT_TIMEOUT_SECONDS 控制，任务总期限仍为 60 秒。

聊天正文须是合法 UTF-8、无 NUL、1–1,023 字节；译文上限 16,384 字节。显示分隔符“\n译文：”占 10 个 UTF-8 字节，正文加译文的合计上限为 17,417 字节。安装包中的 Lua 源码上限为 512 KiB。失败提示从固定字典选择，不展示服务商响应正文或堆栈。

## 通知门禁与身份局限

请求前会检查正文属性、活动事件环指针和事件类型。类型不匹配的槽样本在提交请求前被过滤；slot_event_filtered 计数的是采样次数，同一可见事件可能多次采样，不能当作通知数量。没有基于“发现”“巢穴”等正文关键词的过滤规则。当前 tag 的代码路径关联不足以证明它专属于玩家消息或系统通知类别，因而不能据此保证完整分类。

事件记录没有已证实的唯一 epoch。根指针、事件槽、控件属性、正文和历史的多次核验可以减少误回写风险，但无法完全排除同槽快速复用且正文又相同的情况。后续游戏构建不在当前兼容范围内。

## 原生模块安全加载

DLL 随 Lua addon 嵌入并校验字节长度、SHA-256 和 ABI version 1。Lua 侧将其写入 %LOCALAPPDATA%\HD2ChatTranslate\native 目录，DLL 文件名使用其 SHA-256 值；再从已核验的绝对路径使用 LoadLibraryExW，并将依赖搜索限制在 System32。路径及文件句柄拒绝 reparse point；文件大小和哈希在加载前复核。后台线程启动后，原生模块使用 GetModuleHandleExW pin 保持至游戏进程结束。

Lua FFI 的 BY_HANDLE_FILE_INFORMATION 定义为 52 字节，并核对 dwVolumeSerialNumber 偏移 28、nFileSizeHigh 偏移 32、nFileIndexLow 偏移 48。原生构建限定为 Win64 PE、九个 ABI 导出和 Windows 系统依赖，不接受额外 MinGW 运行库。

状态报告位于 %LOCALAPPDATA%\HD2ChatTranslate\mailbox\chat-translate-{session}.json。独立模式下请求与响应经进程内 API 传递，mailbox 只用于状态报告。首次与停止时写入报告，运行期间最多每 5 秒更新一次；通过临时文件完整写入、关闭后替换，不强制刷盘。

## Arsenal patch 格式

生成的 addon patch 使用一个 Lua type（type_count=1）和两个资源文件（file_count=2），类型值为 0xA14E8DFA2CD117E2。资源按名称 hash 排序，编号为 0、1；资源数据按 16 字节边界对齐。Bingus Shared Loader v18 的 Lua resource 从固定输入中校验后原字节嵌入；stream 与 gpu_resources sidecar 均为空。

Arsenal manifest Guid 固定为 a741d044-972b-4dc5-b08e-1a68441e1d7f，Lua resource 名为 mods/hd2chat/chat_probe，patch 文件名为 9ba626afa44a3aa3.patch_0。构建时保留这些身份值，以便导入新 ZIP 时更新同名模组。

## 状态报告字段

独立包报告 schema_version 为 1，mode 为 standalone，transport 为 in_process_winhttp。status 可为 target_unverified、inactive、baseline、ready、pending、applying 或 stopped；code 仅包含固定状态原因。counters 是白名单计数，不含聊天正文、URL 或密钥。常见字段包括 submitted、translations_ready、error_displays_ready、apply_confirmed、slot_event_filtered；pending_count 上限为 32，baseline_remaining 上限为 64。

native_init_status 的含义：

| 值 | 含义 |
| --- | --- |
| 0 | 初始化就绪 |
| 1 | 缺少配置 |
| 2 | 配置或模块无效 |
| 3 | 翻译已关闭 |
| 4 | 后台 worker 初始化失败 |

native_last_status 为 0 表示成功，100–599 表示 HTTP 状态码；1000 为网络或其他传输错误，1001 为响应格式错误，1002 为超时，1003 为本机频率限制，1004 为退避，1005 为过期，1007 为响应过大，1008 为地址无效，1009 为内部错误。

layout.verified 表示布局签名门禁通过；reflows_confirmed、rows_positioned 和 failures 分别记录已确认重排、已确认定位与布局失败次数。预算不足的 deferred 不计入 failures。last_failure_code 表示最近失败阶段：

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
