# HD2 Chat Translate

绝地潜兵 2 本机聊天翻译插件。新聊天正文交给用户配置的大模型检测：模型判定为中文时保留原文，其他语言翻译为简体中文，随后将对应的本机聊天行更新为原文、换行和“译文：”加译文。译文不会广播给其他玩家。

例如 `We need reinforcements at A1` 翻译后显示为：

```text
We need reinforcements at A1
译文：我们需要在 A1 增援
```

请求失败时，译文位置显示按错误码选择的简短提示，例如“API 密钥无效”或“请求超时”。

当前版本通过 Windows 环境变量配置，网络请求在游戏进程内的后台线程执行。Arsenal 安装包已合并 Bingus Shared Loader v18，进入游戏即可运行。

> **使用风险：** 本插件属于注入类程序，会在游戏进程中加载并执行代码，并调用游戏内部函数修改聊天显示。此类行为可能违反游戏或反作弊规则，并可能导致账号处罚或封禁。

## 安装

1. 正常关闭游戏，在 HD2Arsenal 导入 `HD2ChatTranslateYYYYMMDDHHMMSS.zip`，更新同名模组并启用。
2. 默认优先级放在列表最底端；启用“第一个模组优先”时放在最顶端。点击部署。
3. 在 Windows 用户或系统环境变量中填写下表配置，启动游戏，等待约一分钟后发送新消息。

| 变量 | 含义 |
| --- | --- |
| `HD2CT_API_URL` | 服务商根地址、`/v1` 地址或完整 Chat Completions 接口 |
| `HD2CT_MODEL` | 账户可用的模型名 |
| `HD2CT_API_KEY` | API Key，仅在本机填写 |
| `HD2CT_TIMEOUT_SECONDS` | 可选，1–120 秒，默认 `20` |
| `HD2CT_ENABLED` | 可选，`0` 停用，默认 `1` |

根地址补全 `/chat/completions`，`/v1` 补全 `/v1/chat/completions`；完整接口及其他路径原样使用。服务商需支持 Chat Completions 和 JSON mode。配置在启动时读取，修改后重启游戏。启用后聊天正文会发给配置的服务商。

详细配置、状态码和卸载见 [使用说明](docs/chat-standalone.md)。

## 从源码构建

需要 Windows x64、Python 3.10+、MinGW-w64 GCC，以及固定版本的官方 loader ZIP。准备依赖后，在项目根目录运行：

```pwsh
python tools/build_native_http.py
python tools/build_package.py
```

输出为 `artifacts/HD2ChatTranslateYYYYMMDDHHMMSS.zip`，时间使用北京时间，同名文件不会被覆盖。依赖下载、显式工具路径、完整验证和常见失败处理见 [手动构建指南](docs/build.md)。安装和运行生成的 ZIP 不需要 Python 或编译器。

## 源码结构与验收

| 目录 | 用途 |
| --- | --- |
| `game/` | Lua 入口、游戏构建门禁、聊天控件适配器、翻译状态机及原生模块加载 |
| `native/` | WinHTTP 后台请求实现及固定版本的 cJSON 源码、许可 |
| `tools/` | 原生 DLL 构建和 Arsenal 安装包构建 |
| `tests/` | 假内存/控件、LuaJIT、原生 ABI、回环 HTTP 和安装包回归 |
| `docs/` | 使用、构建、接入依据及崩溃修复记录 |
| `artifacts/` | 被 Git 忽略的依赖、当前产物与必要核验记录 |

游戏入口中的诊断内核仍用于签名校验和适配器回归；沿用的 `chat_probe` 文件名及资源名保证已安装模组的身份连续。构建入口生成独立翻译包。早期 Python 伴随程序、临时部署工具和旧诊断发行包已移除，历史源码可从 Git 找回。

目前适配 Steam build `25480438`、EXE `1.8.46015.0` 的固定游戏指纹。独立翻译路径已完成真实中文回写，合并包的 TOC 错误已修正。用户后续反馈能在英文界面运行且系统通知没有被翻译。本次双语显示和错误提示更新仍需游戏内验收。边界与依据见 [接入研究](docs/native-chat-research.md) 和 [崩溃修复记录](docs/crash-analysis-2026-10-02.md)。

## 开源许可证

本项目原创代码采用 GNU General Public License v3.0 only（SPDX：`GPL-3.0-only`），完整文本见 [LICENSE](LICENSE)。第三方 cJSON 保留 MIT 许可证，见 [native/vendor/cjson/LICENSE](native/vendor/cjson/LICENSE)。合并包中的 Bingus Shared Loader v18 及其内容遵循上游许可和来源说明；本项目不重新许可该上游内容。
