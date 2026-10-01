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

验收后确认游戏关闭，实际回滚补充版的六个文件，再把观察器部署到 `patch_21`、加载器部署到 `patch_22`。新收据指向观察器 ZIP，六个文件摘要均通过；原有 `patch_0`–`patch_20` 的 21 个主文件摘要全部相同。观察模式的重复部署与回滚预演通过。当前安装的是观察器，移除请使用 `-Observe -Rollback`；尚未取得这版游戏内报告。
