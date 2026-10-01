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

加载后保持游戏运行，让更新回调持续执行。扫描约 34.7 MiB 主代码节需要数千帧；帧率、可读页情况与候选量会影响耗时。仅启动游戏不会执行尚未部署的 addon。当前这套加载与采集步骤尚未在真实游戏内验证。

输出位置为 `%LOCALAPPDATA%/HD2ChatTranslate/probe`。探针先写临时文件，完成后重命名成 JSON；只有完整 JSON 才用于分析。报告包含构建核验、各候选 RVA/字节/页保护/SHA-256、已知签名比较、扫描与跳过计数，以及候选截断情况。报告不包含聊天正文。

`scan_complete` 只表示扫描结束。应同时检查实际读取量、跳过量，以及已知签名的 `true` / `false` / `unreadable` 比较结果。哈希不匹配、头信息不匹配或不可读状态均不能作为函数发现结果。候选代码经过离线反汇编后，还需跟踪消息条目布局、聊天行构建和字符串所有权，才能实现原位显示替换。

## 验证与移除

自动测试使用模拟内存和 LuaJIT 核心，不读取真实游戏进程、不加载 `game.dll`、不发送聊天。它们验证页边界、断层、读取失败、采集上限和打包格式；不能替代游戏内加载测试。

运行 `python -m unittest discover -s tests -v`。Lua 测试需要 64 位 LuaJIT DLL；可用 `HD2_LUAJIT_DLL` 指定游戏 `bin/lua51.dll` 的实际路径。测试默认尝试本机已知安装路径，找不到时会明确显示跳过；跳过不能算作 Lua 核心验证通过。

2026-10-01 本机独立验收：24/24 通过、零跳过（16 项伴随服务、8 项研究工具）；测试汇总耗时 10.086 秒。`compileall`、两个工具的 `--help` 和 Git 空白检查均通过。ZIP 完整性检查通过。另用实际入口 FFI 声明确认 Windows 内存区域结构大小 48 字节、RegionSize 偏移 24，并用自有缓冲区验证指针算术及 BCrypt 的 `abc` SHA-256。未执行游戏探针或读取真实游戏进程。按用户选择，本轮交付源码与诊断包，游戏内核验留待后续。

通过模组管理器禁用并重新部署诊断 addon 可移除游戏端探针；仅在菜单中取消勾选但不重新部署，可能仍保留旧 patch。研究输出和本地构建产物可自行删除。译文伴随程序的配置和邮箱是独立的。
