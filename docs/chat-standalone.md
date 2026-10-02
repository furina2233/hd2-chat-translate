# 安装、配置与运行

插件在游戏进程内通过两个 WinHTTP 后台线程请求大模型。游戏先显示原文，成功后更新对应的本机聊天行；中文、请求失败或消息已经变化时保留原文。

## Arsenal 安装与更新

正常关闭游戏，导入 `HD2ChatTranslateYYYYMMDDHHMMSS.zip`，更新同名模组，保持启用并部署。包内包含一个合并 patch，装有 Bingus Shared Loader v18 的原始启动资源和聊天 addon。默认优先级放列表最底端；“第一个模组优先”时放最顶端。旧聊天翻译 addon 不应同时启用。

Arsenal 的 [导入说明](https://docs.rsnl.gg/mod-management/adding-mods) 与 [manifest 说明](https://docs.rsnl.gg/mod-builder/manifest) 说明了 patch 和 `Include` 文件夹的选择方式；包内单个选项只包含 `Addon`。这里的 loader 已放入同一 patch，安装时无需另行导入。

## 环境变量

在 Windows 的“环境变量”设置中配置：

| 变量 | 内容 | 默认 |
| --- | --- | --- |
| `HD2CT_API_URL` | 服务商地址或完整 Chat Completions 请求地址 | 必填 |
| `HD2CT_MODEL` | 账户可用的模型名 | 必填 |
| `HD2CT_API_KEY` | API Key，仅在本机填写 | 必填 |
| `HD2CT_TIMEOUT_SECONDS` | 请求超时，整数 1–120 秒 | `20` |
| `HD2CT_ENABLED` | `0` 禁用翻译，`1` 启用 | `1` |

同名配置优先取用户变量，再取系统变量，最后取进程继承的变量。插件只读取这五个名称，在游戏启动时直接读取持久配置；修改后重启游戏即可。

环境变量以明文保存。密钥不进入安装包、聊天或状态报告。启用后仅把聊天正文发给配置的服务商，不提交玩家账户信息。缺少或无效配置时保留原文。

## URL 补全

| 配置值 | 实际请求地址 |
| --- | --- |
| `https://api.example.com` 或结尾 `/` | `https://api.example.com/chat/completions` |
| `https://api.example.com/v1` 或结尾 `/v1/` | `https://api.example.com/v1/chat/completions` |
| 完整接口或其他自定义路径 | 原样使用 |

只补全根地址和 `/v1`，不纠正拼写。使用正常验证证书的 HTTPS；仅本机回环地址允许 HTTP。地址不接受用户名、密码、查询参数或片段，服务商需支持 Chat Completions 和 JSON mode。重定向被禁用。

## 状态与故障排查

启动后等待约一分钟，发送 `We need reinforcements at A1`。报告位于 `%LOCALAPPDATA%/HD2ChatTranslate/mailbox/chat-translate-<session>.json`；此目录用于状态报告，翻译请求与响应通过进程内 API 传递。检查文件时间是否属于本次游戏会话。

报告应显示 `mode: standalone`、`transport: in_process_winhttp`。`native_init_status`：

| 数值 | 含义 |
| --- | --- |
| `0` | 初始化就绪 |
| `1` | 缺少配置 |
| `2` | 配置或模块无效 |
| `3` | 翻译已关闭 |
| `4` | 后台线程初始化失败 |

`native_last_status` 成功时为 `0`；HTTP 失败使用状态码，如 `401`、`429`；`1000` 为网络错误，`1001` 为响应格式无效，`1002` 为超时。初始化成功却不翻译时，查看是否有提交、译文就绪和回写确认计数；只有旧报告时先检查 Arsenal 是否完成部署及模组优先级。

## 运行边界

启动时核验游戏 DLL 指纹、PE 布局和原生函数签名，通过后才加载网络模块并启用 setter。网络 DLL 从 addon 提取至 `%LOCALAPPDATA%/HD2ChatTranslate/native/<SHA256>.dll`，核验长度、哈希、文件布局并限制依赖搜索到系统目录；线程启动后保留模块至游戏进程结束。

启用后先建立现有消息基线，只处理新消息或变化的正文。每步最多采样四槽、提交一个请求、处理一个响应，共享 16 KiB 读取预算。正文必须是合法 UTF-8、无 NUL 且最多 1023 字节；译文最多 16384 字节。待处理上限 32 条，60 秒过期，网络请求每分钟最多 30 次，并使用有限成功缓存与失败退避。

回写前重新检查根、活动事件、正文指针、唯一正文属性与原文，双读稳定后调用 setter。属性借用字符串指针，译文缓冲区保留到进程结束；每会话最多 512 个、合计 8 MiB，达到上限保留原文。每个响应最多调用一次 setter。

事件记录没有已证实的唯一 epoch，无法完全排除同槽快速复用且正文再次相同的情况。游戏更新后指纹门禁不通过时停止回写。支持的构建和接入依据见 [接入研究](native-chat-research.md)。

## 停用与卸载

将 `HD2CT_ENABLED` 设为 `0` 后重启游戏可以停用翻译。正常关闭游戏后，在 Arsenal 禁用或移除本模组并重新部署。若其他模组依赖 Shared Loader，卸载本合并包后需保留或恢复它们的独立加载器。插件专用 DLL 缓存可在游戏关闭后删除，环境变量可自行移除。

从源码生成包见 [手动构建指南](build.md)。本机已知崩溃及修复的验收状态见 [修复记录](crash-analysis-2026-10-02.md)。
