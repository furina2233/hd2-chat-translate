# 聊天代码研究探针

这是定位聊天函数用的诊断 addon，尚不翻译或替换游戏聊天。它不需要大模型配置，也不会调用伴随程序。代码与研究来源见 [原生聊天接入研究](native-chat-research.md)。

## 构建与离线分析

在项目目录执行，使用 Python 3.10+ 标准库：

```pwsh
python tools/build_chat_probe.py
```

默认生成 `artifacts/HD2ChatProbe.zip`。`--output` 可指定其他 ZIP 路径。源文件是 `game/chat_probe.lua` 与 `game/chat_probe_core.lua`；构建时将核心嵌入单一 Lua resource，并加入加载器需要的 addon 声明。

取得游戏生成的 JSON 后执行：

```pwsh
python tools/analyze_chat_probe.py "$env:LOCALAPPDATA/HD2ChatTranslate/probe/<报告文件>.json" --export-dir artifacts/probe-analysis
```

把 `<报告文件>` 换成实际文件名。分析器校验结构、构建信息、窗口边界和字节 SHA-256，再汇总候选；`--export-dir` 可选，指定时导出 `.bin`。导出目录建议放在已忽略的 `artifacts/` 中。

使用 objdump 分析时，`--adjust-vma` 必须填该窗口在 JSON 中的 `window_rva`，不是命中点 `rva`。例如首个发送函数候选通常是 `rva=0x1097560`、`window_rva=0x10974E0`：

```pwsh
objdump -D -b binary -m i386:x86-64 --adjust-vma=0x10974e0 artifacts/probe-analysis/candidate-000-rva-01097560.bin
```

objdump 是可选的本机反汇编工具，不是本项目生产依赖。实际窗口可能在指令中间起始，需要依据完整签名确定指令边界；仅靠从窗口首字节线性反汇编不能证明函数边界。

## 采集范围

探针运行在游戏的 LuaJIT 环境，通过当前进程句柄读取 `game.dll`。先验证磁盘 SHA-256、PE timestamp、SizeOfImage 和主代码节布局；只有匹配本次记录的 build 才扫描。更新游戏后，校验不符就停止，不能直接修改哈希或 RVA 来跳过检查。

扫描限定主代码节；不扫描其他模块、堆、聊天对象或玩家信息，不读取 API Key，不调用游戏函数，不改进程内存或页保护。只允许属于该 DLL 的已提交、可读可执行映像页；每段读取前复核属性，并要求完整读取。遇到不可读页或断层清除匹配缓存，避免跨洞拼接字节。

每帧代码读取预算为 16 KiB；初始化另需读取最多 64 KiB 映像头和约 15 MiB 磁盘 DLL 进行指纹核验。每种模式最多保留 32 个命中，总候选不超过 128 个，每个代码窗口不超过 512 字节，总候选字节预算不超过 128 KiB。原有 `update` 会继续执行，探针完成或出错后停止自身工作。

搜索内容为聊天历史精确签名，以及 `0x9590`、`0x9594`、`0xC418` 的四字节小端引用。另采集研究文档中的发送/历史代码和两个文本 setter 附近的窗口。立即数字节可能恰好出现在其他指令或常量中，命中不能证明函数用途。

## 游戏内核验

诊断包依赖 [Bingus Shared Loader v15+ / API 1](https://github.com/CowboyBingus/BingusSharedLoader)。生成 ZIP 后，通过现有模组管理器导入并启用诊断 addon 与兼容加载器，再部署；无需替换 `game.dll`。本项目的构建工具只生成文件，不安装、部署或启动游戏。

加载后保持游戏运行，让更新回调持续执行。扫描 34,667,155 字节主代码节需要数千帧；帧率、可读页情况与候选量会影响耗时。仅启动游戏不会执行尚未部署的 addon。本机已完成首次游戏内加载与完整采集，结果见 [运行态记录](native-chat-research.md#首次真实运行态结果)。

输出位置为 `%LOCALAPPDATA%/HD2ChatTranslate/probe`。探针先写临时文件，完成后重命名成 JSON；只有完整 JSON 才用于分析。报告包含构建核验、各候选 RVA/字节/页保护/SHA-256、已知签名比较、扫描与跳过计数，以及候选截断情况。报告不包含聊天正文。

`scan_complete` 只表示扫描结束。应同时检查实际读取量、跳过量，以及已知签名的 `true` / `false` / `unreadable` 比较结果。哈希不匹配、头信息不匹配或不可读状态均不能作为函数发现结果。候选代码经过离线反汇编后，还需跟踪消息条目布局、聊天行构建和字符串所有权，才能实现原位显示替换。

## 验证与移除

自动测试使用模拟内存和 LuaJIT 核心，不读取真实游戏进程、不加载 `game.dll`、不发送聊天。它们验证页边界、断层、读取失败、采集上限和打包格式；不能替代游戏内加载测试。

运行 `python -m unittest discover -s tests -v`。Lua 测试需要 64 位 LuaJIT DLL；可用 `HD2_LUAJIT_DLL` 指定游戏 `bin/lua51.dll` 的实际路径。测试默认尝试本机已知安装路径，找不到时会明确显示跳过；跳过不能算作 Lua 核心验证通过。

2026-10-01 本机独立验收：24/24 通过、零跳过（16 项伴随服务、8 项研究工具）；测试汇总耗时 10.086 秒。`compileall`、两个工具的 `--help` 和 Git 空白检查均通过。ZIP 完整性检查通过。另用实际入口 FFI 声明确认 Windows 内存区域结构大小 48 字节、RegionSize 偏移 24，并用自有缓冲区验证指针算术及 BCrypt 的 `abc` SHA-256。未执行游戏探针或读取真实游戏进程。按用户选择，本轮交付源码与诊断包，游戏内核验留待后续。

通过模组管理器禁用并重新部署诊断 addon 可移除游戏端探针；仅在菜单中取消勾选但不重新部署，可能仍保留旧 patch。研究输出和本地构建产物可自行删除。译文伴随程序的配置和邮箱是独立的。

## 本机首次加载检查

2026-10-01 用户启动游戏后，确认进程已运行，但基础 package 的 patch 0–20 中没有探针资源 `0xC509C11199F753C2`，也没有加载器拥有的 Wwise resource `0x7251FDD9BB62480A`。Arsenal 0.36.2 的模组库及部署配置中没有 Bingus Shared Loader，加载器日志目录也不存在。仅启动当前配置的游戏不会运行诊断 addon。

已准备官方 [Bingus Shared Loader v18](https://github.com/CowboyBingus/BingusSharedLoader/releases/tag/v18)，支持本机构建 `25480438`。下载 ZIP 的 SHA-256 与 GitHub release digest 一致：`53af5698aeacfb27b98dfa00054923d11dc854e1e67b4af14798877812a93ba6`。加载器启动后应生成 `%LOCALAPPDATA%/CowboyBingus/Helldivers2/Logs/BingusSharedLoader.log`。

常规安装可在游戏正常退出后，向 Arsenal 导入加载器和诊断 ZIP，启用两者，将加载器置于列表末尾（本机 `setTopPriority=false`），再部署并重新启动。当前游戏已加载的资源不会因新增 patch 自动重新初始化。[加载器安装说明](https://github.com/CowboyBingus/BingusSharedLoader/blob/main/INSTALL.txt)

### 临时诊断部署

`tools/deploy_probe.ps1` 可在本机向现有 patch 后追加探针和加载器。它固定核验本次游戏 DLL、两个来源 ZIP 的 SHA-256，并记录六个文件的路径与摘要；拒绝覆盖现有文件，重复执行只验证已有部署。默认游戏目录为 `E:/SteamLibrary/steamapps/common/Helldivers 2`，可用 `-GameDirectory` 显式指定。

```pwsh
pwsh -NoProfile -File tools/deploy_probe.ps1 -DryRun
# 正常退出游戏后执行部署。
pwsh -NoProfile -File tools/deploy_probe.ps1
# 正常退出游戏后执行回滚。
pwsh -NoProfile -File tools/deploy_probe.ps1 -Rollback
```

收据保存在项目 `.local/chat-probe-deployment.json`。回滚前会整组检查路径和摘要，只移除收据记录的六个文件；文件缺失或被改动时停止。保留来源 ZIP 和收据以便回滚。

这次追加未登记到 Arsenal。再次使用 Arsenal 部署、清理或切换配置前，应先正常退出游戏并执行上述回滚，避免管理器改写诊断槽位。部署完成只说明文件就位；加载器日志和游戏生成的探针 JSON 才能证明运行情况。

2026-10-01 用户正常退出游戏并授权继续部署后，已追加探针 `patch_21` 与加载器 `patch_22` 及各自两个空 sidecar，共六个文件。六个摘要与来源 ZIP 匹配；部署前后的原有 21 个主 patch 摘要全部相同。真实目录重复部署预演和回滚预演通过，未删除诊断文件。临时目录验证了部署、幂等、篡改收据/不同目录拒绝及实际回滚；缺 EXE 和错误 DLL SHA 会被拒绝。重启后加载器日志确认探针加载，完整扫描报告于北京时间 20:38:05 生成，分析器校验通过。

### 补充代码窗口

首份报告的发送及历史代码窗口在后续分支处截断。补充版增加十个固定 512 字节窗口，接续 `0x1097760`、`0x1097960`、`0x1097CA0`、`0x1097EA0`，以及三个直接调用目标 `0x12F2F60`、`0x20BBA88`、`0x143A1B0` 和各自 `+0x200` 续段。六处已知签名及构建门禁不变；不读取堆、聊天正文，不调用原生函数或写游戏内存。

```pwsh
python tools/build_chat_probe.py --output artifacts/HD2ChatProbeFollowup.zip
# 正常退出游戏后，先回滚仍在使用的首版，再部署补充版。
pwsh -NoProfile -File tools/deploy_probe.ps1 -Rollback
pwsh -NoProfile -File tools/deploy_probe.ps1 -Followup -DryRun
pwsh -NoProfile -File tools/deploy_probe.ps1 -Followup
# 补充版运行完毕后的移除命令。
pwsh -NoProfile -File tools/deploy_probe.ps1 -Followup -Rollback
```

补充 ZIP SHA-256 为 `7e562cda0307d5db679e9d3a8c57073044c25247c03581ecf29f35c7459f2334`，主 patch 条目 44,448 字节。原 `HD2ChatProbe.zip` 已保留；构建器会拒绝覆盖当前部署收据引用的来源 ZIP，以保留回滚依据。补充版已部署并完成真实游戏采集。

补充版独立验收：24/24 测试通过、零跳过（10.078 秒）；三份 ZIP CRC 与固定指纹通过。隔离 TEMP 副本验证补充版部署、幂等及实际回滚，正好新增/移除六文件；由于真实游戏仍运行，副本中的进程守卫使用不操作进程的测试桩，生产守卫保持原样。真实目录只读检查确认首版六文件匹配，回滚预演通过，补充模式遇到首版收据会拒绝覆盖。未改真实收据或已部署文件。

2026-10-01 用户再次正常退出游戏后，确认进程关闭，按首版收据实际回滚六个文件，再部署补充探针到 `patch_21`、加载器到 `patch_22`。新收据指向补充 ZIP；六个文件摘要通过，探针主 patch SHA-256 为 `e0cba668afa2017cf4f0a887f4092f83f31de0c8ba8d85baeca79bd11fa51a17`。部署前后原有 `patch_0`–`patch_20` 的 21 个主文件摘要全部相同，补充模式重复部署预演与回滚预演通过。后续移除应使用 `-Followup -Rollback`，首版扫描报告已保留。

本次重启后，加载器日志于北京时间 21:07:20 确认探针加载；21:07:47 生成完整报告 `chat-probe-1790860067-02eb8f00-abe000000007ECE2FE0-01.json`，SHA-256 为 `2d3cd8bf3323f7cd875260c0c00abed1a3cdaa1b3e3622aa63a5d8097459b655`。分析器校验通过：扫描 34,667,155 字节，跳过/读取失败/候选不可读/截断均为零；30 个窗口共 15,360 字节，十个补充窗口全部在列，六处已知签名仍全匹配。副本及二进制窗口保存在忽略的 `artifacts/probe-followup-analysis/`；尚未读取或替换聊天正文。

### 持续只读观察器

```pwsh
python tools/build_chat_probe.py --observe
# 正常退出游戏后，移除现有补充探针，再追加观察器。
pwsh -NoProfile -File tools/deploy_probe.ps1 -Followup -Rollback
pwsh -NoProfile -File tools/deploy_probe.ps1 -Observe
# 观察器移除命令。
pwsh -NoProfile -File tools/deploy_probe.ps1 -Observe -Rollback
```

观察器输出为 `artifacts/HD2ChatObserve.zip`，仅该构建启用 `game/chat_observe_core.lua`。普通探针构建保持观察器关闭。先完成同样的构建核验和代码扫描，只有六处已知签名全部匹配才观察历史对象。新扫描另加入六个固定窗口，跟踪派发函数的下游调用；各项读取预算和候选上限不变。

本机观察器包 19,737 字节，SHA-256 为 `e27eb79ac770a6064604ce5c4ec0826bd9e6043a7631d4e6d6482e4aad7b55e2`；主 patch 89,824 字节，SHA-256 为 `4163f473ddfda15fe469fd80d99e7e0a31c9d6a76fd749c2f309772006fa0c22`。两次独立构建摘要一致，ZIP CRC 通过。隔离 TEMP 目录中的部署、幂等和实际回滚通过，正好新增/移除六文件，原有测试 patch 摘要不变；生产进程守卫原样运行，未用测试桩替换。

这是诊断工具，尚未执行翻译或原文字替换。它读取固定全局槽指向的聊天环形对象，每步最多八项、每 500 ms 开始一轮，最多运行 30 分钟。正文窗口为 513 字节，严格检查 NUL 和 UTF-8；两次条目快照及整轮元数据必须一致，否则丢弃整轮。读取 timestamp 与正文头形成临时 identity，不读取 `+0xB98` 的值或跟随玩家指针。周期之间重复观察同一消息，因此匹配次数是成功观察次数，不代表唯一消息数。

每步所有对象读取（包括失败尝试）合计不超过 16 KiB，单次不超过 4 KiB。逐页复核 `VirtualQuery`，只接受已提交的私有数据页或属于当前 `game.dll` 的映像数据页，保护必须为 READONLY/READWRITE/WRITECOPY；拒绝可执行、guard、noaccess 页和其他模块。读取仅使用游戏内的当前进程句柄，不从外部连接游戏进程，不写内存、不调用游戏函数。

报告保存在 `%LOCALAPPDATA%/HD2ChatTranslate/observe/chat-observe-<会话>.json`，每五秒以及测试消息/标签变化时原子更新同一文件。最多 256 KiB，路径通过 Windows 宽字符 API 处理。报告只含计数、字节长度汇总、两条固定消息的是否命中，以及最多 32 份 UI 数字元数据快照；不输出普通聊天正文、正文散列、身份字节、绝对地址或 API Key，也不联网。

游戏启动、扫描完成后，在聊天框手动发送：

```text
HD2CT_PROBE_ASCII_01
HD2CT_PROBE_中文_02
```

报告的 `seen_ascii`、`seen_cjk` 用于确认正文定位和 UTF-8。可以在聊天框关闭、打开、发送后分别向固定文件 `observe/request.txt` 写入 `chat_closed`、`chat_open`、`chat_sent`，请求一份带标签的 UI 快照。最多读取 65 字节，只接受上述固定 ASCII 标签或 `startup`，不会把内容当代码、路径或聊天消息。该请求文件消费后删除；UI 深度最多五、controller 最多 64，只保存 kind、匿名 object ID、当前 DLL 内的 vtable RVA 及最多八个代码节内函数 RVA。它们用于比较 UI 状态，不能直接认定哪个对象是聊天行。

2026-10-01 观察器独立验收：35/35 测试通过、零跳过（10.208 秒）。新增 11 项覆盖核心、构建及从生产入口抽取的适配器动态模拟；模拟内核只访问测试 VM 自己分配的缓冲区。`compileall`、Git 空白检查与 PowerShell 语法解析通过。Windows 宽字符报告的实际写入、真实历史布局及 UI 行为仍需游戏运行核验。

验收后确认游戏关闭，实际回滚补充版的六个文件，再把观察器部署到 `patch_21`、加载器部署到 `patch_22`。新收据指向观察器 ZIP，六个文件摘要均通过；原有 `patch_0`–`patch_20` 的 21 个主文件摘要全部相同。观察模式的重复部署与回滚预演通过。该版的移除模式为 `-Observe -Rollback`；部署完成时尚未取得游戏内报告。

### 首次正文观察与诊断命令错误

2026-10-01 用户启动游戏，Bingus 日志确认 addon 加载。23:33:18 生成第三轮代码报告 `chat-probe-1790868798-0335da88-abe000000007ECED3B0-01.json`，SHA-256 为 `1ade5087699ac91ad79d7cabe4c06a1a66e5f12c09a53847f68974ef222dec3c`；新增派发窗口已取得。

用户发送固定 ASCII 消息后，观察报告 `seen_ascii=true`，正文长度为 20 字节，历史读取、双读、元数据、NUL 与 UTF-8 校验均无错误。留存快照 `artifacts/observer-runtime/ascii-confirmed.json` 的 SHA-256 为 `5499fdd3babf9afe372f239815108d21a9ddc9504916f87831a1a671edf2f2f2`；其中 396 轮完成，350 次重复观察命中。中文样本因游戏输入框限制无法输入，故不能声称已验证真实中文正文编码。

随后写入固定 `startup` 请求时，诊断命令处理抛出错误，观察器按设计停止，`stop_reason=adapter_error`；快照尝试次数仍为一，说明本次请求未进入 UI 采样。停止报告副本 `artifacts/observer-runtime/command-stopped.json` SHA-256 为 `d6e2d4eb363ee3fe0a38f8562c1f59114badfe75860a197bef89c1d1302ad528`。原先离线测试遗漏了该命令路径，本轮补测并修复。

UI 启动快照也未成功，但无异常。公开模组的 root、stack、dispatch 表布局与适配器一致；公开实现先筛 kind，只解引用匹配项，没有证明每种 controller 的首字都必然是 vtable。观察器原先要求每项都有可读 vtable，过于严格。修订版保留整表二读一致性，仅跳过非法/null controller 行，并把 vtable 探测作为可选字段；不扩大读取范围或放宽页保护。[公开的 controller 定位实现](https://github.com/CowboyBingus/ClickableScrollbars/blob/main/src/clickable_scrollbars.lua#L2799-L2917)

旧命令模式已在私有 LuaJIT state 精确复现 `malformed pattern (missing ']')`。修复为最多 64 字节的逐字节检查，再匹配白名单；读取失败保留请求，删除失败不返回标签。全量 35 项测试通过、零跳过（10.291 秒），新增情形纳入已有适配器动态测试：四种标签及 LF/CRLF、NUL/高字节/内嵌换行、64/65 字节边界、读取/删除失败、250 ms 轮询，以及混合有效/null/坏 vptr 的 UI 行。不读取真实游戏或真实请求文件。

修订包为 `artifacts/HD2ChatObserveFix.zip`，20,086 字节，SHA-256 `0adb6ab1743a50713f2032e16de1fd945daa3294b94039832367b4a83d2114be`；主 patch 90,528 字节、SHA-256 `d4138aaffb88830582574bf19529334aad6f8946fb642cea14e3dbe631a9021f`，ZIP CRC 通过。它另加入下游 `0x1860B00`、`0x185F170` 与各自 `+0x200` 的四个代码窗口。准备阶段保留旧观察器 ZIP 与已部署文件，当时真实游戏仍在运行。固定来源指纹与隔离目录部署预演通过。

```pwsh
python tools/build_chat_probe.py --observe --output artifacts/HD2ChatObserveFix.zip
# 正常退出游戏后，回滚旧观察器，再部署修订包。
pwsh -NoProfile -File tools/deploy_probe.ps1 -Observe -Rollback
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveFix
# 修订包运行完毕后的移除命令。
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveFix -Rollback
```

已部署包不能被构建工具覆盖，直到正常回滚完成。安装修订包需要重启以重新加载 addon；不向运行中游戏热替换文件。下一轮可先只发送 ASCII 测试消息，再比较聊天框关闭/打开时的 UI 快照，中文样本暂记未验证。

用户正常退出游戏后，本轮确认进程关闭。先完成修订模式在隔离 TEMP 目录中的实际部署、幂等和回滚，再在真实目录按旧收据回滚六个文件，部署修订观察器至 `patch_21`、加载器至 `patch_22`。新收据指向 `HD2ChatObserveFix.zip`；六文件摘要全部通过，原有 `patch_0`–`patch_20` 的 21 个主文件摘要全部不变。真实目录的重复部署预演及回滚预演通过，未执行新包回滚。**当前安装的是修订观察器，移除请使用 `-ObserveFix -Rollback`。** 尚待重启后的修订版报告验证。

### 修订版运行结果

修订版重启后成功捕获 ASCII 测试消息，`seen_ascii=true`，正文为 20 字节；读取、双读、元数据、UTF-8 和终止符计数均无错误。`startup` 与用户确认输入框打开后的 `chat_open` 请求均被消费，观察器持续运行，`adapter_errors=0`，原诊断命令异常已修复。留存 `artifacts/observer-fix-runtime/command-and-open.json` SHA-256 为 `12c58eae98e69b2b2887f5f61dcfe43eac175e9a73375103f5a9033c9e2d5aa5`；该样本完成 1,334 轮，累计 1,274 次重复 ASCII 命中。

但三次 UI 采样均失败，报告没有具体阶段，不能确定根指针、页属性、布局范围或复核漂移哪一项触发拒绝。下一版将添加最多 32 条固定阶段/原因枚举诊断，保留现有读取范围、页保护和预算；不再仅凭失败计数猜测修复。中文正文仍未验证，也未连接大模型或替换原聊天行。

第四轮代码扫描同时完成，报告 `chat-probe-1790870167-02c0b258-abe000000007ECD7BC0-01.json` SHA-256 为 `563a71775a37492cbeb73ad71a851b49a243302e1dc721f36aa9da6a5e5d5afd`。完整代码节扫描、40 个窗口和六处签名比较通过，具体控件正文属性路径见 [研究记录](native-chat-research.md#控件正文属性路径)。

### 有限阶段诊断包

`ui_snapshot` 失败时返回固定阶段/原因枚举，core 只保存白名单字段到 `ui_diagnostics`：version、可信 label、at_ms、stage、reason、read_size（0–4096）与 budget_used（0–16384）。最多 32 条，超限使用饱和 dropped 计数；未知枚举、非法数字和额外字段均净化，不记录绝对地址、任意错误文本或聊天正文。原有快照次数/失败统计不变。

阶段包括根槽、stack、dispatch、count、rows、解码与各处二次复核；原因区分空指针、范围、页查询、提交/保护/归属拒绝、预算、完整读取失败和元数据变化。reader 只增加第二返回值原因，旧正文读取仍使用第一个返回值；所有内存与时间限制保持不变。

独立验收：`python -m unittest discover -s tests -v` 全部 36 项通过、零跳过（10.255 秒）；观察器专项 12 项通过。测试覆盖第二返回值、诊断净化/32 项上限/饱和，以及 fake-kernel 的 owner/dispatch 空指针、保护/归属拒绝、depth=6/count=65、五处复核变动、VirtualQuery/RPM/短读与预算失败。`compileall`、Git 空白检查与 PowerShell 语法解析通过。

包为 `artifacts/HD2ChatObserveDiag.zip`，21,549 字节，SHA-256 为 `834d4b6bf8cf9a5956d61c6a989f25ad43495179b3e3d4e6fbf748f365a4a178`。主 patch 98,544 字节，SHA-256 为 `c7225d45b2df028f74a147b849254b128389a10fb77596b932eab3978dd14c0f`。ZIP CRC 和源码/封装精确比较通过；固定来源的隔离目录部署预演通过，混用版本模式被拒绝。另加入四个函数各两段、共八个固定代码窗口，详见研究记录。准备阶段保留了正在使用的修订包及其回滚来源。

```pwsh
python tools/build_chat_probe.py --observe --output artifacts/HD2ChatObserveDiag.zip
# 正常退出游戏后，回滚当前修订包，再部署阶段诊断包。
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveFix -Rollback
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveDiag
# 阶段诊断包的移除命令。
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveDiag -Rollback
```

准备完成后检测到游戏进程已经关闭，按既有诊断部署授权继续。本轮先通过阶段诊断模式在隔离 TEMP 目录的实际部署、幂等和回滚，再按真实旧收据回滚修订包，部署阶段诊断 addon 至 `patch_21`、加载器至 `patch_22`。新收据指向 `HD2ChatObserveDiag.zip`；六文件摘要全部通过，部署前后原有 21 个主 patch 摘要不变。真实目录重复部署与回滚预演通过。**当前安装的是阶段诊断观察器，移除模式为 `-ObserveDiag -Rollback`。** 尚待启动后的新报告。

### 阶段诊断结果与偏移修正

2026-10-02 用户再次启动游戏。阶段诊断观察器持续运行，启动与重复 `startup` 采样均在 `dispatch_count/value_out_of_range` 处停止，读取预算为 44 字节、当前读取为 4 字节，adapter 异常为零。留存 `artifacts/observer-diag-runtime/startup-repeated.json` 的 SHA-256 为 `22c43b566c3a83b4c384c316a88bc4efe0c5fce3312511b587ee12c62814c425`。本轮尚未发送新测试消息，不能把此报告的未命中当作正文读取失效。

核对公开原文后发现探针的进制错误：[ClickableScrollbars](https://github.com/CowboyBingus/ClickableScrollbars/blob/main/src/clickable_scrollbars.lua#L2820-L2825) 与 [ArmoryPreviewCache](https://github.com/CowboyBingus/ArmoryPreviewCache/blob/main/src/native.lua#L917-L930) 都使用十进制 `dispatch + 5740`、`dispatch + 5744`，即 `+0x166C/+0x1670`。此前适配器错误写成 `+0x5740/+0x5744`，读的是另一位置；其越界结果不能用于判断真实 registry 数量。stack 的 `+0x429C` 与深度的十进制 `20` 核对一致。

修正只更改两个字段偏移，保留最多 64 项、原页保护、二读校验与每步 16 KiB 预算。超限诊断另可输出已有读取值 `observed_count`，仅允许有效的 `dispatch_count/value_out_of_range` 情形及 u32 整数；不增加内存读取或保存地址/正文。测试会在旧错误地址安置干扰值，验证适配器实际使用正确字段。新的来源 ZIP 独立保存，不覆盖当前部署收据引用的阶段诊断包。

偏移修正版 `artifacts/HD2ChatObserveOffset.zip` 为 21,791 字节，SHA-256 为 `746d1dd4403a5b10d225593504396a2aa01680a843c0f1a7ed6746ff175d7f9e`；主 patch 为 99,328 字节，SHA-256 为 `4fd24a8a82b9bbb08b0d92fc1eb035cc23d33af0ce2d7b303dcdf325e1450ec8`。ZIP CRC 通过；另增加两段固定格式化窗口。最终 36 项测试全部通过、零跳过（10.801 秒），PowerShell 语法、compileall 与空白检查通过。

```pwsh
python tools/build_chat_probe.py --observe --output artifacts/HD2ChatObserveOffset.zip
# 正常退出游戏后，回滚阶段诊断包，再部署偏移修正版。
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveDiag -Rollback
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveOffset
# 偏移修正版的移除命令。
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveOffset -Rollback
```

用户确认正常退出后，本轮确认游戏进程关闭。新模式在隔离 TEMP 目录完成实际部署、幂等与回滚，六个新增文件全部移除，基线文件集合保留。随后回滚真实目录的阶段诊断包，部署偏移修正版至 `patch_21`、加载器至 `patch_22`。六文件摘要通过、原有 21 个主 patch 摘要全部不变；真实目录重复部署与回滚预演通过。**当前安装偏移修正版，移除模式为 `-ObserveOffset -Rollback`。** 修正后的真实 UI 快照尚未验证，翻译与原聊天行替换也尚未接入。

### 偏移修正版的真实核验

2026-10-02 重启后，启动、用户确认关闭输入框后的 `chat_closed`、确认打开后的 `chat_open` 三份快照全部成功，失败与 adapter 异常均为零。三种状态都是 screen depth 为零，controller kind 为 `288/290/220/67`，同会话匿名 ID 也相同；可选 vtable 未取得。由此确认偏移修正恢复了有界枚举，但仅凭这些快照不能认定聊天 controller。用户可关闭输入框，后续沿已确认的具体正文属性表继续定位。

本轮再次成功识别固定 ASCII 测试消息，长度为 20 字节，无正文读取、双读、终止符或 UTF-8 错误。保存的 `artifacts/observer-offset-runtime/chat-open.json` SHA-256 为 `2540ede60d27e1a908738e55d106fef71d4c32a5d9ddfd358951b531ee428363`。中文样本仍未验证。

第六轮代码报告包含 50 个窗口、25,600 字节，完整代码节扫描无跳过或读取失败，六处签名匹配。报告保存为 `artifacts/probe-format-analysis/runtime-report.json`，SHA-256 为 `d86277068cd418ebd8062b4965af1c9da3756c0e3861f646cc2219f3daab4a9a`。所有函数仍是静态研究候选，没有执行原生函数或翻译替换。

### 控件正文属性的有限定位

新观察器在历史中首次识别任一固定测试消息后，利用历史采样空闲的 step 扫描具体控件槽，每步最多一个、总计最多 128 个非 deferred 槽次，槽游标对 64 回绕。剩余预算少于 4096 字节时不读、不推进；仍共享每步 16 KiB、单次最多 4096 字节和原页保护门禁。

读取 `0x346D538` 根对象下的事件环与控件 manager；属性表只接受 count 不大于 14，查找唯一正文 key，重复 key 拒绝。值指针必须精确等于某条活动事件记录的 `+0xB4`，事件 code 必须匹配聊天路径，正文读取限于 `0x400` 字节。根指针、环 cursor/count、属性 count/entries、event code 和正文全部二读一致；复核根变化立即拒绝，不继续追随新根。正文只有遇到 NUL 且精确等于固定测试消息时才记录匹配。

报告增加 `widget_probe`，只含固定状态、饱和状态计数、最多 128 次尝试与最多 64 条净化匹配。匹配仅含控件槽号、事件槽号、根匿名 ID 和 ASCII/中文布尔标志，不输出正文、其他属性 key、地址或任意错误文本。两轮匹配可以重复，不代表唯一消息。此诊断只用于验证槽位对应，不证明异步译文回写的身份或最终刷新契约。

独立包 `artifacts/HD2ChatObserveWidget.zip` 为 24,442 字节，SHA-256 为 `a488ed3ec7030a27eb9bf765606bb9474c82df25bbe3f5455664b3787ca04410`；主 patch 为 113,776 字节，SHA-256 为 `6b0d5de5da467474fec812ac3018627676dce3495edb2b1d9577b3994be7cf88`。包内源码精确比对和 ZIP CRC 通过；独立模式在隔离 TEMP 目录完成实际部署、幂等与回滚，基线文件集合与摘要不变，版本模式混用被拒绝。准备期间保留了已安装偏移修正版的来源和收据。

```pwsh
python tools/build_chat_probe.py --observe --output artifacts/HD2ChatObserveWidget.zip
# 正常退出游戏后回滚偏移修正版，再部署属性定位包。
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveOffset -Rollback
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveWidget
# 属性定位包的移除命令。
pwsh -NoProfile -File tools/deploy_probe.ps1 -ObserveWidget -Rollback
```

最终回归全部 38 项通过、零跳过（10.485 秒）；新增两个持久化测试在私有 LuaJIT 的 fake-kernel 与 pure core 中验证真实生产片段，覆盖 14/15 项边界、精确指针、活动环槽、根/元数据/属性/正文漂移、缺 NUL、预算 defer、每步一个槽、128 上限、64 条匹配上限及输出净化。未读取真实游戏或请求文件。PowerShell 语法、compileall 与空白检查通过。

验收期间检测到游戏已退出，确认进程为零后按既有诊断部署授权继续：回滚偏移修正版的六个文件，安装控件定位版至 `patch_21`、加载器至 `patch_22`。六文件摘要全部通过，原有 21 个主 patch 摘要全部不变；真实目录重复部署与回滚预演通过。**当前安装控件定位观察器，回滚模式为 `-ObserveWidget -Rollback`。** 新版控件槽匹配尚待游戏内验证；它没有连接大模型或调用文本 setter。
