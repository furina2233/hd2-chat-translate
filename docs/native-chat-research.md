# 接入依据

资源型 HD2SDK 用于 archive、网格、材质等模组开发，没有公开的运行时聊天替换 API。当前插件通过 Bingus Shared Loader 加载 Lua addon，用 LuaJIT FFI 校验固定游戏构建并接入原生聊天控件，网络请求由自带 WinHTTP DLL 完成。

## 公开实现与格式来源

| 来源 | 本项目采用的依据 |
| --- | --- |
| [Helldivers Wiki：Broken Mods](https://helldivers.wiki.gg/wiki/Broken_Mods) | 更新后需要重新核验游戏与模组兼容性 |
| [HD2 Modding Wiki](https://boxofbiscuits97.github.io/HD2-Modding-Wiki/dev/overview.html) / [HD2SDK Community Edition](https://github.com/Boxofbiscuits97/HD2SDK-CommunityEdition) | 资源处理工具与 patch 格式背景 |
| [Bingus Shared Loader 技术文档](https://github.com/CowboyBingus/BingusSharedLoader/blob/main/docs/TECHNICAL.md) | API 1、Lua addon discovery、原始启动资源 |
| [Bingus archive.py](https://github.com/CowboyBingus/BingusSharedLoader/blob/main/scripts/archive.py) | 72 字节头、类型表、文件表、16 字节对齐及资源编号 |
| [BetterLobbyManagement 聊天代码](https://github.com/CowboyBingus/BetterLobbyManagement/blob/main/src/chat.lua) | 原生发送侧与聊天对象定位线索；不广播译文 |
| [ModOptionsMenu](https://github.com/CowboyBingus/ModOptionsMenu/blob/main/src/mod_options_menu.lua) | 原生属性 setter、字符串指针保留、既有 update 的包装方式 |
| [HD2Runtime 只读适配器](https://github.com/SkyeShade/HD2Runtime/blob/master/runtime/windows_readonly.lua) | 页属性、范围和预算检查的参考 |

截至 2026-10-01 的研究，HD2Runtime 公开事件没有可用的聊天接收/替换接口，其设置类型也没有任意密码输入。本项目最终以 Windows 环境变量配置大模型，接口与协议参考见 [协议依据](research.md)。

## 已核验的游戏构建与正文路径

适配 Steam build `25480438`、EXE `1.8.46015.0`，磁盘 `game.dll` SHA-256 为 `2e2c3b7c2500646dadd5f2b4c6e0504dbb7e7896139f64cddc0d1813c718f51e`，PE timestamp `1790161983`、SizeOfImage `74727424`。启动时检查这些标识、代码节范围和已知签名；不匹配时停止回写。

已采集的短代码窗口及固定文本观察确认：

- 全局槽 `game.dll+0x346D538` 指向根对象；事件环从 `root+0x4F7080` 开始，64 项、步长 `0x4B4`，正文在每项 `+0xB4`，容量 `0x400`。
- 聊天 manager 为 `root+0x14498`，64 个控件槽从 manager `+0x4390` 开始，步长 `0x3D8`。不假定控件槽与事件槽始终同索引。
- 控件正文 key 为 `0x7518C954`。属性 map 在控件槽 `+0x220`；count 位于 map `+0x158`，entries 从 `+8` 开始，步长 `0x18`，有界读取最多 14 项。
- 正文 wrapper 为 `game.dll+0x1441CA0`，参数为控件槽 `+0x110`、正文 key 和持久 UTF-8 字符串。下层 `0x143A1B0` 借用指针，不复制正文，因此译文不能使用短期缓冲区。

原生 setter 会更新目标属性的类型标记、缓存 hash 和正文指针。回读允许这三个字段按契约变化，其余属性仍需稳定；失败时不会重复调用 setter。

上述 RVA 只对匹配构建有效。扫描字节相同本身不能证明函数语义，实际接入还依赖固定消息、控件读回和用户画面验证。代码中的 `chat_probe` 资源身份保持不变，诊断/扫描内核保留供签名门禁及回归；早期采集脚本和发行包已清理。

## 验收与限制

2026-10-02 固定中文试验确认原聊天行显示“聊天翻译测试成功”；随后独立版在 addon 与 loader 分槽部署时完成进程内模型请求，用户确认原聊天行变成中文，新会话报告记录提交、译文就绪和回写确认各一次，相关错误为零。合并包后续的 TOC 错误已修正；修订合并包的游戏启动仍待确认，见 [崩溃修复记录](crash-analysis-2026-10-02.md)。

事件记录没有已证实的唯一 epoch，双读与绑定检查不能完全排除同槽快速复用且正文又相同。缓冲区保活、预算、队列、过期和回写前复核见 [使用说明](chat-standalone.md)。长时间游玩、多玩家连续消息、其他服务商和后续游戏构建未完成全面验收。
