# 从源代码手动构建

所有命令在项目根目录的 PowerShell 7（`pwsh`）中运行。构建过程不启动游戏、不读取模型配置、不请求真实模型服务，也不部署游戏文件。

## 1. 准备工具与依赖

需要 Windows x64、64 位 Python 3.10+、MinGW-w64 的 x86_64 GCC 和同套 Binutils `objdump`。Python 脚本只使用标准库，无需 `pip install`。可从 [MinGW-w64 官方工具链目录](https://www.mingw-w64.org/downloads/) 选择带 GCC 的 Windows x64 工具链，将其 `bin` 加入当前终端 PATH；也可在下一步用完整路径指定工具。

```pwsh
python --version
python -c 'import struct; print(struct.calcsize("P") * 8)'
gcc --version
gcc -dumpmachine
objdump --version
```

Python 位数应为 `64`，GCC 目标应为 `x86_64-w64-mingw32`。Clang/MSVC 和 Linux ELF 编译器不属于本构建流程。

从 [Bingus Shared Loader v18 发布页](https://github.com/CowboyBingus/BingusSharedLoader/releases/tag/v18) 下载 **`Bingus-Shared-Loader-v18.zip`**，放到 `artifacts/`。不要使用源码 ZIP，也不要解压后重新打包。该输入未提交到 Git，干净克隆需要自行准备：

```pwsh
New-Item -ItemType Directory -Path artifacts -Force | Out-Null
# 尚未准备此文件时执行下载；已存在则保留本机文件。
if (-not (Test-Path -LiteralPath artifacts/Bingus-Shared-Loader-v18.zip)) {
    Invoke-WebRequest -Uri 'https://github.com/CowboyBingus/BingusSharedLoader/releases/download/v18/Bingus-Shared-Loader-v18.zip' -OutFile artifacts/Bingus-Shared-Loader-v18.zip
}
(Get-FileHash -LiteralPath artifacts/Bingus-Shared-Loader-v18.zip -Algorithm SHA256).Hash
```

预期 SHA-256 为 `53af5698aeacfb27b98dfa00054923d11dc854e1e67b4af14798877812a93ba6`，与[官方发布资产摘要](https://github.com/CowboyBingus/BingusSharedLoader/releases/expanded_assets/v18)一致。构建器同时核验内部启动 patch 和 Lua 资源的摘要；不匹配时会拒绝构建，不应绕过校验。

`native/vendor/cjson/` 已包含固定的 [cJSON v1.7.19](https://github.com/DaveGamble/cJSON/tree/v1.7.19) 源码与 MIT 许可。无需另行下载、安装 HD2SDK 或提取游戏资源。

## 2. 编译原生网络模块

```pwsh
python tools/build_native_http.py
if ($LASTEXITCODE -ne 0) { throw '原生模块构建失败' }
```

默认从 PATH 查找 `gcc`，优先使用它同目录、同前缀的 `objdump`。未加入 PATH 时使用实际工具路径，例如：

```pwsh
python tools/build_native_http.py --cc 'C:\tools\mingw64\bin\gcc.exe' --objdump 'C:\tools\mingw64\bin\objdump.exe'
```

生成 `artifacts/native/hd2ct_http.dll` 和 `artifacts/native/hd2ct_http.meta.json`。脚本校验 Win64 PE 格式、九个 ABI 导出、WinHTTP/注册表及 Windows CRT 系统依赖，记录长度、SHA-256 与编译器版本。不接受额外的 MinGW 动态运行库依赖。

可用 `--output` 和 `--meta` 指定中间产物路径；两者必须成对交给下一步，不可混用旧 DLL 与新 metadata。更换编译器时二进制摘要可能改变，包构建器会嵌入本次实际模块及其摘要。

## 3. 生成 Arsenal 安装包

```pwsh
python tools/build_package.py
if ($LASTEXITCODE -ne 0) { throw '安装包构建失败' }
```

默认读取上一步的 DLL/meta、`game/` 源码与固定 loader ZIP，输出 `artifacts/HD2ChatTranslateYYYYMMDDHHMMSS.zip`。命名时间为北京时间，CLI 的 `--output` 也必须使用该格式；已有文件不会被覆盖，同秒重复构建需等下一秒。

使用其他依赖路径时可以显式传入：

```pwsh
python tools/build_package.py --loader-zip 'C:\dependencies\Bingus-Shared-Loader-v18.zip' --native-dll 'C:\build\hd2ct_http.dll' --native-meta 'C:\build\hd2ct_http.meta.json'
```

安装包包含 `manifest.json`、`Addon/` 的一个合并 patch 及两个空 sidecar、`LICENSES/` 的 cJSON 许可与上游原始说明、元数据、来源记录。patch 中一个 Lua 类型、两个资源，按 hash 排序，编号为 `0,1`。原始 loader 资源保持字节不变，聊天资源内嵌本次 DLL；模型地址、模型名和密钥不进入包。

构建保留既有 Guid 与资源名，导入 Arsenal 时更新同名模组。不要手改 archive 计数、编号、声明或 manifest 身份。包名中的时间不改变模组身份。

## 4. 验证

HTTP 测试自动在临时目录编译 DLL，使用本机回环服务器与假密钥。需保证 GCC/objdump 在 PATH；Lua 回归还需要兼容的 Win64 LuaJIT 2.x `lua51.dll`。可使用已安装游戏 `bin/` 下的 `lua51.dll`，或自行准备的兼容 DLL，先显式设置本机路径：

```pwsh
$env:HD2_LUAJIT_DLL = 'C:\dependencies\LuaJIT\lua51.dll'
```

也可将该 DLL 所在目录加入 PATH。测试在私有 LuaJIT 状态中运行本项目源码和假内存/控件，不调用真实游戏 setter。缺少或不兼容的 LuaJIT 会导致相关用例报错或跳过；完整验收要求零失败、零跳过。LuaJIT 仅用于开发验证，不需要额外装到游戏目录。不要离线加载或执行 `game.dll`。

```pwsh
python -m compileall -q tools tests
python -X utf8 -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '回归测试失败' }
git diff --check
```

选择本次构建脚本输出的确切 ZIP 路径，检查 CRC 和摘要：

```pwsh
$taskPackage = 'artifacts/HD2ChatTranslateYYYYMMDDHHMMSS.zip' # 替换为本次实际文件名
python -m zipfile -t $taskPackage
Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256
```

离线测试与包格式验证不能代替实际游戏启动。游戏关闭时在 Arsenal 导入更新并部署，启动后检查新会话报告，再验证英文聊天行替换为中文。流程见 [使用说明](chat-standalone.md)。

## 常见构建失败

| 提示 | 处理 |
| --- | --- |
| 找不到构建工具 | 将 MinGW `bin` 加入 PATH，或传入 `--cc` / `--objdump` |
| 不是 Win64 PE DLL | 检查 GCC 目标架构，使用 x86_64 MinGW-w64 |
| DLL 有额外动态依赖 | 使用不引入额外运行库的工具链；核对实际导入，不要移除校验 |
| DLL 与 metadata 不匹配 | 重新执行原生构建，使用同次生成的两个文件 |
| loader 摘要或条目不匹配 | 重新获取固定 v18 发布资产，保留原 ZIP |
| 文件名不符合要求或已存在 | 使用新的 `HD2ChatTranslate年月日时分秒.zip` 名称 |
| Lua 回归报错或被跳过 | 指定兼容 Win64 LuaJIT DLL 后重跑 |

`artifacts/`、Python 缓存和本机研究目录均被 Git 忽略。提交源码时不要提交生成的 DLL、ZIP、上游安装包、游戏资源或本机配置。
