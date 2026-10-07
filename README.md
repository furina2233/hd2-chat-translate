# HD2 Chat Translate

《绝地潜兵 2》聊天框翻译插件。支持 AI 翻译和机器翻译，目标语言为简体中文。译文不会被发送给其他玩家。

当前兼容范围为 Steam build 25480438、游戏 EXE 1.8.46015.0。游戏更新可能导致本插件失效。

> **使用风险：** 插件会注入游戏进程、执行代码并调用游戏内部函数更新聊天显示。此行为可能违反游戏或反作弊规则，并导致账号处罚或封禁。

## 安装与更新

1. 正常关闭游戏，在 HD2Arsenal 导入 HD2ChatTranslateYYYYMMDDHHMMSS.zip，更新同名模组并启用。
2. 默认优先级放在列表最底端；启用“第一个模组优先”时放在最顶端。
3. 点击部署后启动游戏。包内已合并 Bingus Shared Loader v18，无需另行导入。
4. 更新模组时关闭游戏，再导入新 ZIP、更新同名模组并重新部署。

## 配置

支持的翻译提供商：
- AI翻译：所有提供Chat Completions接口且支持JSON mode的提供商
- 机器翻译：Google、百度、有道

在 Windows 环境变量设置中填写以下配置：

| 变量 | 内容 | 必填 |  默认值|
| --- | --- | --- | --- |
| HD2CT_API_URL | 所选翻译服务的接口地址 | 是 | 无 |
| HD2CT_MODEL | AI 模型名；机器翻译时删除或留空 | AI翻译必填 | 无 |
| HD2CT_API_KEY | AI/Google 的 API Key，或百度/有道的应用密钥 | 是 | 无 |
| HD2CT_APP_ID | 百度 APP ID 或有道应用 ID | 百度/有道必填 | 无 |
| HD2CT_TIMEOUT_SECONDS | 可选，请求超时，整数 1–120 秒 | 否 | 20 |
| HD2CT_ENABLED | 可选，1 启用；0 停用 | 否 | 1 |

模型名非空时使用 AI Chat Completions 接口，支持自动补全接口地址。模型名缺失、为空时使用机器翻译，同样支持自动补全接口地址，支持Google、百度、有道三个提供商。

| 机器翻译服务 | 完整接口地址 | 凭据 |
| --- | --- | --- |
| [Google Cloud Translation Basic v2](https://docs.cloud.google.com/translate/docs/reference/rest/v2/translate) | `https://translation.googleapis.com/language/translate/v2` | HD2CT_API_KEY |
| [百度通用翻译](https://fanyi-api.baidu.com/doc_bd/21) | `https://fanyi-api.baidu.com/api/trans/vip/translate` | HD2CT_APP_ID、HD2CT_API_KEY |
| [有道文本翻译](https://ai.youdao.com/DOCSIRMA/html/trans/api/wbfy/index.html) | `https://openapi.youdao.com/api` | HD2CT_APP_ID、HD2CT_API_KEY |


使用机器翻译时，即使源语言和目标语言相同，也会显示译文行；使用AI翻译时，若源语言和目标语言相同，则不会显示译文行。

配置优先读取 Windows 用户变量，再读取系统变量。
切换到机器翻译时，确认用户和系统两处均没有非空的模型配置，或用空的用户模型值覆盖系统模型值。

原生客户端在首次有效提交聊天时后台读取一次配置，并管理翻译方式和启用状态。初始化后修改、添加或删除变量，需要重启游戏才会生效。环境变量以明文保存；请只在本机填写密钥。启用翻译后，聊天正文会发送给所配置的服务商。

## 可见提示与基础排查

常见请求失败会保留原文，并在译文位置显示以下提示：

| 情况 | 显示提示 |
| --- | --- |
| HTTP 400 或 422 | 请求参数有误 |
| HTTP 401 | API 密钥无效 |
| HTTP 403 | 无权使用此接口 |
| HTTP 404 | 接口或模型不存在 |
| 超时 | 请求超时 |
| HTTP 429 | 请求太频繁，请稍后再试 |
| 其他 HTTP 5xx | 服务暂时不可用 |
| 网络连接失败 | 网络连接失败 |
| 无效或过长的返回内容 | 返回内容无效或返回内容过长 |
| 配置缺失 | 翻译配置不完整 |
| 配置或地址无效 | 翻译配置无效或接口地址无效 |
| 机器翻译 URL 无法匹配服务 | 暂不支持此翻译服务 |
| 应用凭据或签名错误 | 翻译凭据或签名无效 |
| 翻译账户额度不足 | 翻译额度不足 |
| 服务不支持源语言 | 不支持此语言 |
| 其他失败 | 翻译服务异常，或翻译失败，请稍后重试 |

没有出现译文时，先确认模组已在 Arsenal 启用并完成部署，再检查所选服务要求的变量及用户/系统变量位置，然后重启游戏并发送新的非中文聊天。AI 模式经服务识别为中文时只保留原文，机器翻译的中文消息行为见上文；启动前已有的聊天不会补翻。AI 模式需核对模型名、接口路径和 JSON mode 支持；机器翻译需核对 API 开通状态、应用 ID、密钥与账户额度。有道签名还依赖正确的系统时间。网络提示则检查本机网络与服务商可用性。

## 本地日志

日志位于 `%LOCALAPPDATA%\HD2ChatTranslate`：`probe` 保存启动核验报告，`observe` 保存观察报告，`mailbox` 保存翻译状态报告。插件启动时按各类报告的最后修改时间清理较早文件，每类最多保留 10 个，并为本次会话的新报告预留位置。

清理仅在启动时执行，保留配置、DLL 缓存、通信文件和临时文件。被占用或无法删除的报告会略过，下次启动再次尝试；清理失败不影响翻译。

## 停用与卸载

将 HD2CT_ENABLED 设为 0 并重启游戏即可停用翻译。卸载时正常关闭游戏，在 Arsenal 禁用或移除本模组并重新部署。若其他模组依赖 Shared Loader，卸载本合并包后需保留或恢复它们各自的加载器。游戏关闭后可删除插件专用的本机 DLL 缓存，也可自行移除环境变量。

## 文档

- [当前实现与技术边界](docs/technical.md)
- [手动构建指南](docs/build.md)

## 许可证

本项目原创代码采用 GNU GPL v3.0 only（SPDX：GPL-3.0-only），完整文本见 [LICENSE](LICENSE)。第三方 cJSON 保留 MIT 许可证；合并包中的 Bingus Shared Loader v18 遵循其上游许可与来源说明，本项目不重新许可该上游内容。
