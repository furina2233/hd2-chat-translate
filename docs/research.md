# 模型协议与实现资料

当前产品使用游戏进程内的 WinHTTP worker 请求兼容 Chat Completions 的端点，配置来源为 Windows 环境变量。安装、状态和运行边界见 [使用说明](chat-standalone.md)，构建依赖见 [手动构建指南](build.md)。

## 请求与返回

协议以 [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 的公开文档为参考：使用 `messages`、`response_format` JSON mode，读取 `choices[0].message.content`。服务商和模型由用户配置。

系统提示要求先判断正文是否中文，仅对非中文输出简体中文译文，并返回受约束的 JSON。中文结果强制保留本次原文；模型输出只作为文本处理，不执行代码。成功译文显示在原文下一行；无效响应、HTTP 错误与超时按固定字典显示简短错误，仍保留原文。消息过期或回写门禁失效时不更新聊天行，详见 [请求错误显示](chat-standalone.md#请求错误显示)。

根地址和 `/v1` 地址仅补全到 Chat Completions 路径，其他路径保持用户配置。HTTP 只允许回环服务，HTTPS 保留证书验证；禁用重定向、自动认证与 cookies。只发送聊天正文，不包含玩家账户标识，报告不包含正文、模型地址或密钥。

## 提示词与聊天用语

系统提示词位于 [`native/hd2ct_http.c`](../native/hd2ct_http.c) 的 `HD2CT_SYSTEM_PROMPT`，作为 `system` 消息发送；原始聊天正文单独作为 `user` 消息发送。修改后需要按 [手动构建指南](build.md) 重新编译原生 DLL，再打包，才能让新提示词进入安装包。

`gg`、`ggs` 与其他聊天缩写一样交由模型正常识别和翻译，不要求将其标记为中文或强制原样返回。

缩写按上下文理解，避免替换昵称或较长单词内部的字母；`btw`、`lol`、`afk`、`brb`、`idk`、`imo` 等参考 [Cambridge 的聊天缩写资料](https://www.cambridge.org/core/services/aop-file-manager/file/5bd884d86431f1de07115e65/130-Texting-abbreviations.pdf)。`xd` 作为大笑表情处理，参见 [Slang.net 的 XD 释义](https://slang.net/meaning/xd)；`omw` 表示正在赶来，`rn` 在聊天语境中可表示现在，分别参考 [OMW](https://slang.net/meaning/omw) 和 [RN](https://slang.net/meaning/rn)。完整缩写规则以源码中的提示词为准。

敌人名称采用用户指定的译名，不区分大小写，并覆盖常规复数和同类变体。完整特定名称优先，例如 `Spore Charger` 为“孢子牛”、`Charger` 默认“牛”（口语可用“牛牛”）；`Factory Strider` 为“移动工厂”、`Scout Strider` 及其变体为“小双足”、`War Strider` 为“大双足”。全部名称对应保存在同一提示词中。

## Windows 依赖

- [WinHTTP 安全说明](https://learn.microsoft.com/en-us/windows/win32/winhttp/winhttp-security-considerations)：正常证书校验及重定向的处理依据。
- [LoadLibraryExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexw)：使用已核验的绝对路径，限制 DLL 依赖搜索。
- [GetModuleHandleExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getmodulehandleexw)：后台线程启动后保留模块，避免运行中卸载。
- [BY_HANDLE_FILE_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/ns-fileapi-by_handle_file_information)：文件核验 FFI 的 52 字节布局，包含 `dwVolumeSerialNumber`。
- [cJSON v1.7.19](https://github.com/DaveGamble/cJSON/tree/v1.7.19)：JSON 解析源码与 MIT 许可随仓库和安装包保留。

## 测试边界

离线回归保留 20 项核心测试，使用模拟响应、回环 HTTP、假密钥、私有 LuaJIT 状态以及假内存/控件。测试不调用公网模型服务、不读取真实环境中的模型配置、不离线加载 `game.dll`，也不读取外部游戏进程。包格式回归使用独立 TOC parser 核对资源表、编号、对齐及 loader 资源，并验证 ZIP CRC 和许可证。

模型回归检查编译后 DLL 实际发出的请求、英文翻译及中文标记处理，并验证 `gg/ggs` 可正常提交模型并使用返回的译文，以及关键提示词规则。模拟模型响应只能验证传输与返回处理；真实模型对缩写和敌人名称的理解需要在游戏中确认。

当前产品的模型请求与响应由游戏进程内的网络线程处理；本机 `mailbox` 目录仅承载状态报告。
