# HD2 Chat Translate

绝地潜兵 2 本机聊天翻译插件。新聊天正文交给用户配置的大模型检测语言；中文保留原文，其他语言翻译为简体中文，并替换对应的本机聊天行。译文不会发送给其他玩家。

**当前状态：完整插件已部署并通过本机真实游戏验证。新聊天经用户配置的模型翻译后，原聊天行成功替换为中文，原生属性读回与用户画面确认一致。** 66 项离线测试通过。游戏接口来自实际代码采集和控件读回，适配目前核验的构建，详情见 [运行与兼容性说明](docs/chat-translate.md)。

## 配置与启动

需要 Windows 和 Python 3.10 或更新版本，伴随程序仅使用 Python 标准库。在项目目录运行：

```pwsh
pwsh -File .\run.ps1
```

也可以执行 `python -m hd2_translate`。填写服务商基础地址或完整 Chat Completions URL、模型名称、API Key 和超时，点击“测试连接”。测试只发送固定示例，不要求启用翻译。测试通过后，勾选“启用翻译服务”并点击“开始”，然后启动游戏。

根基础地址会补全 `/chat/completions`，`/v1` 基础地址会补全 `/v1/chat/completions`，其他自定义路径请填写完整请求地址。例如 `https://api.deepseek.com/` 会补全为 `https://api.deepseek.com/chat/completions`。服务商需要支持 Chat Completions 和 JSON mode；模型名使用账户可用的实际名称。窗口会给出 HTTP 状态码和对应建议。

本机服务允许 HTTP，例如 `http://localhost:11434/v1/chat/completions`；Ollama 可填写 API Key `ollama`，模型名使用本机已安装名称，参见 [Ollama 文档](https://docs.ollama.com/api/openai-compatibility)。无界面运行方式为 `python -m hd2_translate --headless`，使用已保存的配置。

默认地址、模型和密钥为空，翻译关闭。密钥由当前 Windows 用户的 DPAPI 加密，保存在 `%LOCALAPPDATA%/HD2ChatTranslate/config.json`，不进入游戏 patch 或项目。启用后聊天正文会发送至配置的服务商，只提交正文，不提交玩家账户信息。停止会阻止新请求和游戏回写；已经发送的网络请求仍可能处理至返回或超时。

## 游戏安装与回滚

安装包为 `artifacts/HD2ChatTranslate.zip`，需要 Bingus Shared Loader v18。现有本机部署使用独立 patch 槽和带指纹的收据，保留原 Arsenal patch 0–20；来源包与 `.local` 收据需保留至回滚完成。安装与移除前正常退出游戏。

```pwsh
pwsh -NoProfile -File tools/deploy_probe.ps1 -Translate -DryRun
pwsh -NoProfile -File tools/deploy_probe.ps1 -Translate
```

回滚本次部署：

```pwsh
pwsh -NoProfile -File tools/deploy_probe.ps1 -Translate -Rollback
```

脚本只接受已核验的游戏 DLL、来源 ZIP 和独立槽位；不会覆盖已有文件。旧诊断部署需按对应模式先回滚，见 [诊断包说明](docs/chat-probe.md)。

## 行为与验证

启用时先建立已有消息的基线，随后只处理新消息或变化的正文。读取有每步预算，最多 32 条待处理请求，60 秒后过期；失败、停用或原消息已经变化时保留原文。写入前重新核对活动事件、正文与唯一控件属性，并再次读取服务心跳。原生控件借用字符串指针，译文缓冲区保留至游戏结束；每次会话上限 512 个、总量 8 MiB。达到上限保留原文。

事件记录没有已证实的唯一 epoch，当前绑定检查不能证明快速复用同槽且正文再次相同时仍为最初的消息。游戏更新后指纹门禁不通过时不会调用 setter。完整边界与本机邮箱协议见 [插件说明](docs/chat-translate.md)。

```pwsh
python -m unittest discover -s tests -v
```

66 项测试通过，零失败、零跳过，覆盖伴随服务、配置与密钥保护、连接测试、UTF-8、队列及心跳、原子邮箱、控件生命周期复核、原生 setter 模拟和 ZIP 构建。适配器使用本机私有 LuaJIT、假内核与内存缓冲区，不读取外部游戏进程或调用真实 setter。HTTP 回归使用模拟响应和本机端点；真实提供商连接另以固定示例核验。

## 研究资料

- [Wiki、HD2SDK 与模型协议核对](docs/research.md)：公开资源工具不提供聊天替换 API，配置窗口采用外部伴随程序。
- [其他代码模组与原生聊天路径研究](docs/native-chat-research.md)：发送、事件环、控件属性与字符串生命周期的证据。
- [诊断包与真实固定中文显示记录](docs/chat-probe.md)：只读采集、控件定位及原生替换验证。

游戏 Lua 通过本机文件邮箱与伴随程序通信，网络请求由后台 worker 处理。研究阶段的默认探针与只读观察模式保留，不会执行普通聊天翻译。
