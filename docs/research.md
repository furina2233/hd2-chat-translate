# 文档核对记录

核对日期：2026-10-01。实现目标为本机聊天显示翻译；不广播译文给其他玩家。

## 资源 SDK 与运行时

- [Helldivers Wiki：Broken Mods](https://helldivers.wiki.gg/wiki/Broken_Mods)：游戏没有官方模组兼容性保证，更新可能使模组失效。
- [HD2 Modding Wiki：Getting Started](https://boxofbiscuits97.github.io/HD2-Modding-Wiki/dev/overview.html)：HD2SDK 是 Blender 模组开发工具。
- [HD2SDK Community Edition](https://github.com/Boxofbiscuits97/HD2SDK-CommunityEdition)：公开功能处理 archive 中的纹理、网格、材质；不能将它当作运行时聊天事件 API。
- [HD2 Modding Wiki：Mod Managers](https://boxofbiscuits97.github.io/HD2-Modding-Wiki/user/mod%20manager/overview.html)：模组管理器负责放置 patch 文件，不提供运行时插件接口。
- [HD2Runtime](https://github.com/SkyeShade/HD2Runtime)：使用 Bingus Shared Loader 的语义运行时；公开功能主要覆盖装备和战斗数据，需另行核对聊天显示接入。
- [HD2Runtime Events](https://github.com/SkyeShade/HD2Runtime/blob/master/docs/events.md)：0.28.1 公开事件目录没有聊天事件；公开事件为 post 事件，改写 payload 不改变游戏。不能用虚构的 `hd2.on_chat` 实现需求。
- [HD2Runtime Options](https://github.com/SkyeShade/HD2Runtime/blob/master/docs/options.md)：公开设置类型为 toggle、choice、slider，没有任意文本或密码输入框。因此模型地址、模型名和 API Key 使用外部配置窗口。
- [Bingus Shared Loader：TECHNICAL](https://github.com/CowboyBingus/BingusSharedLoader/blob/main/docs/TECHNICAL.md)：API 1 支持独立 Lua resource 的 addon discovery，v15 及以后无需修改加载器注册表。
- [Vanilla Plus Megapack](https://github.com/CowboyBingus/VanillaPlusMegapack)：公开的 UI 模组、Mod Options Menu 和 Better Lobby Management 提供可核对的游戏脚本接入案例。

本机 Steam manifest 读到 buildid `25480438`。此记录不代表完成游戏内验证。

## 大模型协议

- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)：HTTP JSON `messages` 请求，`choices[].message.content` 返回内容。
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)：JSON 模式需要请求 `response_format` 并在提示词中明确 JSON 格式；空内容、截断仍需处理。

插件使用兼容 Chat Completions 的完整端点，由用户填写地址和模型名。每条新聊天原文作为 user 消息提交；系统提示要求先判断是否中文，仅对非中文输出简体中文译文。模型返回结果不作为代码执行。

配置入口也接受根基础地址与 `/v1` 基础地址，并分别补全为 `/chat/completions`、`/v1/chat/completions`；其他自定义路径保持不变。2026-10-02 核对 [DeepSeek 当前模型及请求示例](https://api-docs.deepseek.com/) 后，确认 `deepseek-flash` 有效。用用户已保存的本机密钥，仅向完整端点发送固定连接测试示例，HTTP 200、译文正常、耗时 1.031 秒；未提交游戏正文，未输出或记录密钥。基础根地址上的直接 POST 是此前连接失败的原因。连接测试现在独立于正式翻译启用状态，HTTP 错误只显示状态码和安全建议。

## 本机资源核验

本机 EXE 版本为 `1.8.46015.0`。游戏使用 slim bundles；通过现成 SDK 的 `utils/slim.py` 只读重建基础 package `9ba626afa44a3aa3` 到内存，得到 19 种资源类型、4,051 项资源，其中 Lua 资源类型 `0xa14e8dfa2cd117e2` 有 7 项。检查这些 LuaJIT 字节码的可读常量，没有得到聊天显示或消息接收 API 的证据。这不排除其它 package 或原生代码存在相关逻辑。

本机已部署 addon 使用 `sr.World.create_screen_gui` 与 `sr.Gui.text/destroy_text` 绘制独立覆盖层。这只能证明覆盖层能力，不能证明原聊天行能被替换。

[BetterLobbyManagement 的聊天源码](https://github.com/CowboyBingus/BetterLobbyManagement/blob/main/src/chat.lua) 提供文字聊天对象和原生发送路径的证据；[技术说明](https://github.com/CowboyBingus/BetterLobbyManagement/blob/main/docs/TECHNICAL.md) 提及 64 行历史计数。该源码没有提供接收回调、消息条目布局或显示文字的更新函数。发送译文到会话不是本项目要求的本机原位替换。

进一步核对本机 `game.dll`：哈希与该社区模组记录的构建一致；文档中的发送函数地址位于 PE 代码节的虚拟范围内，但超出对应节的磁盘 raw-data 范围，因此不能从当前安装文件反汇编得到相关指令。原始字节扫描没有找到历史字段引用，不能据此推断运行时代码没有相关逻辑。静态核验尚无法证实消息条目布局、字符串所有权、容量或 UI 更新函数；这些内容需要运行中调试。中文 UTF-8 译文可能比原文更长，未知容量的原位字节写入不能作为可靠实现。

## 游戏接入边界

2026-10-02 固定中文试验已证实正文控件属性与原生更新接口，完整桥接现沿同一路径实现：游戏 Lua 写入文件邮箱，伴随程序执行网络请求；游戏定时检查响应。原文先正常显示；成功后仅更新对应消息的本机显示文字。请求失败或消息已过期时保留原文。API Key 留在伴随程序配置中，不进入游戏 archive。完整模式的真实游戏到模型联调仍待验证，详见 [插件说明](chat-translate.md)。

文件邮箱目录：`%LOCALAPPDATA%/HD2ChatTranslate/mailbox`。

- `<token>.req`：UTF-8 原始消息。
- `<token>.res`：`OK\n<显示文本>` 或 `ERR\n<简短错误>`。
- 写入先使用临时文件，再重命名到正式文件，避免读取半份数据。
- 游戏端保存 token 与消息对象的对应关系；伴随程序不接收玩家账户标识。

伴随程序发布启用心跳 `bridge.flag`：持有邮箱实例锁且翻译启用时，原子发布至多 64 字节的 ASCII `HD2CT1 <uptime_ms>\n`，Windows 使用 `GetTickCount64`。禁用/停止只移除本服务代际最近发布的标记，不能删除其他实例或新代际的内容。游戏桥接只接受同一启动周期内三秒以内的心跳，并在原生回写紧前从磁盘重新验证。固定中文显示试验不使用邮箱或模型。

聊天 setter 已由代码采集、控件读回与用户画面共同确认；没有使用虚构聊天接口。其他代码模组与全包资源的结果、函数定位和生命周期限制见 [原生聊天接入研究](native-chat-research.md)。默认诊断探针与只读观察模式继续保留，普通聊天处理只在显式翻译模式且服务启用时运行。

## 伴随程序验收

`python -m unittest discover -s tests -v`：16 项通过。HTTP 验证全部使用回环地址的测试服务器和假密钥；DPAPI 在本机 Windows 当前用户下完成密文保存/读取验证。`python -m compileall -q hd2_translate tests` 通过。

独立 smoke 验证了配置切换后丢弃在途旧结果、停止/重启不重复请求、固定样例测试每次实际访问端点、DPAPI round-trip、GUI 主线程接收反馈、测试按钮复位和关闭窗口。GUI 使用被替换的配置读取与保存函数及临时目录，没有读取真实用户密钥。默认 `690×500` 与最小 `620×450` 窗口的控件尺寸核对通过。

上述首轮伴随程序验收没有调用真实模型服务、启动游戏或修改游戏安装。后续临时诊断部署完成真实代码采集与固定中文原位显示，另以固定连接示例验证用户配置的真实模型端点；完整聊天桥接的离线验收记录见 [插件说明](chat-translate.md)。停止只能取消待发请求和返回结果，已发送请求仍可能被服务商处理。
