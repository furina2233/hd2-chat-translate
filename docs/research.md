# 模型协议与实现资料

当前产品使用游戏进程内的 WinHTTP worker 请求兼容 Chat Completions 的端点，配置来源为 Windows 环境变量。安装、状态和运行边界见 [使用说明](chat-standalone.md)，构建依赖见 [手动构建指南](build.md)。

## 请求与返回

协议以 [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) 的公开文档为参考：使用 `messages`、`response_format` JSON mode，读取 `choices[0].message.content`。服务商和模型由用户配置。

系统提示要求先判断正文是否中文，仅对非中文输出简体中文译文，并返回受约束的 JSON。中文结果强制保留本次原文；模型输出只作为文本处理，不执行代码。空内容、无效 JSON、不合法 UTF-8、超长响应、HTTP 错误与超时都保留原文。

根地址和 `/v1` 地址仅补全到 Chat Completions 路径，其他路径保持用户配置。HTTP 只允许回环服务，HTTPS 保留证书验证；禁用重定向、自动认证与 cookies。只发送聊天正文，不包含玩家账户标识，报告不包含正文、模型地址或密钥。

## Windows 依赖

- [WinHTTP 安全说明](https://learn.microsoft.com/en-us/windows/win32/winhttp/winhttp-security-considerations)：正常证书校验及重定向的处理依据。
- [LoadLibraryExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexw)：使用已核验的绝对路径，限制 DLL 依赖搜索。
- [GetModuleHandleExW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getmodulehandleexw)：后台线程启动后保留模块，避免运行中卸载。
- [BY_HANDLE_FILE_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/ns-fileapi-by_handle_file_information)：文件核验 FFI 的 52 字节布局，包含 `dwVolumeSerialNumber`。
- [cJSON v1.7.19](https://github.com/DaveGamble/cJSON/tree/v1.7.19)：JSON 解析源码与 MIT 许可随仓库和安装包保留。

## 测试边界

离线回归使用模拟响应、回环 HTTP、假密钥、私有 LuaJIT 状态以及假内存/控件。测试不调用公网模型服务、不读取真实环境中的模型配置、不离线加载 `game.dll`，也不读取外部游戏进程。包格式回归使用独立 TOC parser 和上游构建器生成的黄金摘要。

早期伴随服务、DPAPI 配置窗口、文件请求/响应及临时部署流程已退出产品构建链，相关源码和记录可从 Git 历史查阅。当前产品的模型请求与响应都在进程内传递；名称为 `mailbox` 的本机目录仅承载状态报告。
