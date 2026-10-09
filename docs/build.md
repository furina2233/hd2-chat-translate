# 从源代码手动构建

所有命令在项目根目录的 PowerShell 7（pwsh）中运行。构建过程不启动游戏、不读取模型配置、不请求真实模型服务，也不部署游戏文件。

## 准备工具与依赖

需要 Windows x64、64 位 Python 3.10+、MinGW-w64 的 x86_64 GCC 和同套 Binutils objdump。Python 脚本只使用标准库，无需安装 pip 依赖。可从 [MinGW-w64 工具链目录](https://www.mingw-w64.org/downloads/) 选择 Windows x64 工具链，将 bin 加入当前终端 PATH；也可在构建时指定工具完整路径。

~~~pwsh
python --version
python -c 'import struct; print(struct.calcsize("P") * 8)'
gcc --version
gcc -dumpmachine
objdump --version
~~~

Python 位数应为 64，GCC 目标应为 x86_64-w64-mingw32。Clang/MSVC 和 Linux ELF 编译器不属于本构建流程。

从 [Bingus Shared Loader v18 发布页](https://github.com/CowboyBingus/BingusSharedLoader/releases/tag/v18) 下载 Bingus-Shared-Loader-v18.zip，放到 artifacts/。不要使用源码 ZIP，也不要解压后重新打包。干净克隆需要自行准备这个未提交的输入：

~~~pwsh
New-Item -ItemType Directory -Path artifacts -Force | Out-Null
# 尚未准备此文件时执行下载；已存在则保留本机文件。
if (-not (Test-Path -LiteralPath artifacts/Bingus-Shared-Loader-v18.zip)) {
    Invoke-WebRequest -Uri 'https://github.com/CowboyBingus/BingusSharedLoader/releases/download/v18/Bingus-Shared-Loader-v18.zip' -OutFile artifacts/Bingus-Shared-Loader-v18.zip
}
(Get-FileHash -LiteralPath artifacts/Bingus-Shared-Loader-v18.zip -Algorithm SHA256).Hash
~~~

预期 SHA-256 为 53af5698aeacfb27b98dfa00054923d11dc854e1e67b4af14798877812a93ba6，与[官方发布资产摘要](https://github.com/CowboyBingus/BingusSharedLoader/releases/expanded_assets/v18)一致。构建器也会核验内部启动 patch 和 Lua 资源摘要，不匹配时拒绝构建。

目标语言菜单使用 [Mod Options Menu v1.2](https://github.com/CowboyBingus/ModOptionsMenu/releases/tag/v1.2) 的官方发布包。同样需保持原 ZIP，将 `Mod-Options-Menu-v1.2.zip` 放到 `artifacts/`：

~~~pwsh
if (-not (Test-Path -LiteralPath artifacts/Mod-Options-Menu-v1.2.zip)) {
    Invoke-WebRequest -Uri 'https://github.com/CowboyBingus/ModOptionsMenu/releases/download/v1.2/Mod-Options-Menu-v1.2.zip' -OutFile artifacts/Mod-Options-Menu-v1.2.zip
}
(Get-FileHash -LiteralPath artifacts/Mod-Options-Menu-v1.2.zip -Algorithm SHA256).Hash
~~~

预期 SHA-256 为 `a977d84e7f8fda62c587b5b5af2012aab3fa792e31945a9c8d5f458bfd453e13`。构建器校验 ZIP、patch 和 Lua 资源，只原样合入菜单资源，不运行上游脚本。

native/vendor/cjson/ 已包含固定的 [cJSON v1.7.19](https://github.com/DaveGamble/cJSON/tree/v1.7.19) 源码与 MIT 许可。无需另行下载、安装 HD2SDK 或提取游戏资源。

## 编译原生网络模块

构建脚本按 `tools/build_native_http.py` 中的 `NATIVE_SOURCES` 清单编译 `native/` 的客户端基础模块与 `native/adapter/` 的翻译适配器，并与固定版本的 cJSON 一起链接为 `hd2ct_http.dll`。源码模块划分和命名规则见[技术说明](technical.md#原生客户端模块)；添加模块时需同步更新源码清单。

~~~pwsh
python tools/build_native_http.py
if ($LASTEXITCODE -ne 0) { throw '原生模块构建失败' }
~~~

默认从 PATH 查找 gcc，并优先使用它同目录、同前缀的 objdump。未加入 PATH 时传入实际路径：

~~~pwsh
python tools/build_native_http.py --cc 'C:\tools\mingw64\bin\gcc.exe' --objdump 'C:\tools\mingw64\bin\objdump.exe'
~~~

生成 artifacts/native/hd2ct_http.dll 和 artifacts/native/hd2ct_http.meta.json。脚本从 `resources/target_languages.json` 自动生成同目录的 `target_languages.generated.h`，无需自行准备或提交该生成文件。脚本校验 Win64 PE 格式、三个任务接口导出及 WinHTTP、注册表、BCrypt 和 Windows CRT 系统依赖，并记录 ABI 2、长度、SHA-256、目标语言目录摘要与编译器版本。机器翻译的签名和随机数使用 Windows 自带 BCrypt，构建脚本会链接对应系统库。不接受额外的 MinGW 动态运行库依赖。

可用 --output 和 --meta 指定中间产物路径；两者必须成对交给下一步，不可混用旧 DLL 与新 metadata。更换编译器时二进制摘要可能改变，包构建器会嵌入本次实际模块及其摘要。

## 使用 CLion / CMake 构建

在 CLion 中打开项目根目录，并配置 Windows x64 MinGW 工具链：C 编译器选择 MinGW-w64 GCC，objdump 使用同一工具链的 Binutils。在 CMake 配置的 CMake options 中通过 `-DPython3_EXECUTABLE=解释器路径` 指定 Python 3.10+。CMake 配置只需要 Python 标准库，不会下载或安装依赖；不支持 MSVC、Clang、32 位或非 Windows 工具链。

在项目根目录执行以下命令；将路径替换为本机实际路径。`cmake`、`ninja` 可使用 CLion 自带版本，GCC、objdump 和 Python 使用已配置的工具；若 Ninja 不在 PATH 中，再添加 `-DCMAKE_MAKE_PROGRAM='<CLion 中的 ninja.exe 路径>'`：

~~~pwsh
cmake -S . -B build/cmake -G Ninja -DCMAKE_C_COMPILER='E:\mingw64\bin\gcc.exe' -DPython3_EXECUTABLE='C:\Program Files\Python310\python.exe'
cmake --build build/cmake
~~~

CMake 配置时会在构建目录生成 `target_languages.generated.h` 供 CLion 索引；语言目录或生成器变化后，构建会更新该头文件。输出 DLL 和 metadata 位于 `build/cmake/native/hd2ct_http.dll` 与 `build/cmake/native/hd2ct_http.meta.json`。DLL 使用与命令行构建相同的 MinGW 编译选项、Windows 系统链接库和严格导入/导出核验。

该目标面向安装包构建；Debug 配置也使用 `-Os`、`-g0`，并剥离调试信息，以控制嵌入 DLL 后的 Lua 入口大小。

CLion 的构建目标选择 `hd2ct_http`，它会依次构建 DLL 并核验、生成 metadata。命令行可构建全部目标，或只构建同名聚合目标：

~~~pwsh
cmake --build build/cmake --target hd2ct_http
~~~

打包时将同一构建目录中的 DLL 与 metadata 成对传入，保留原有 ZIP 命名与上游资产校验：

~~~pwsh
python tools/build_package.py --native-dll build/cmake/native/hd2ct_http.dll --native-meta build/cmake/native/hd2ct_http.meta.json
~~~

## 生成 Arsenal 安装包

~~~pwsh
python tools/build_package.py
if ($LASTEXITCODE -ne 0) { throw '安装包构建失败' }
~~~

默认读取上一步的 DLL/meta、game/ 源码、共享目标语言目录、`resources/menu_locales.json` 与两个固定上游 ZIP，输出 artifacts/HD2ChatTranslateYYYYMMDDHHMMSS.zip。打包器校验全部15种菜单locale和每种语言的标题、说明、目标语言名称及超时格式，再把文本快照嵌入 Lua 设置模块；游戏运行时不读取该JSON文件。命名时间为北京时间；--output 也必须使用此格式，已有文件不会覆盖，同秒重复构建需等下一秒。修改目标语言目录后必须重新构建 DLL；安装包构建器会拒绝目录摘要不匹配的旧模块。

使用其他依赖路径时可显式传入：

~~~pwsh
python tools/build_package.py --loader-zip 'C:\dependencies\Bingus-Shared-Loader-v18.zip' --menu-zip 'C:\dependencies\Mod-Options-Menu-v1.2.zip' --native-dll 'C:\build\hd2ct_http.dll' --native-meta 'C:\build\hd2ct_http.meta.json'
~~~

包中包含 manifest.json、项目 GPLv3 原文、一个合并 patch、空 stream/gpu_resources sidecar、cJSON、loader 和菜单框架的许可及来源文件。合并 patch 包含 loader、聊天翻译与菜单三个 Lua 资源，只需导入一个 ZIP。模型地址、模型名和密钥不会进入包。包保留既有 Guid 与资源名，导入 Arsenal 时更新同名模组。

## 离线验证

当前核心套件预算为 20 项，完整验收要求零失败、零跳过：

| 覆盖范围 | 用例数 |
| --- | ---: |
| 翻译、中文保持、正文校验、错误提示与通知过滤 | 5 |
| 控件回写与多行布局 | 3 |
| 1 秒扫描计划、200ms 响应轮询与状态报告 | 3 |
| 持久配置、AI/机器翻译适配器与后台 HTTP 请求 | 4 |
| Arsenal 包、原生模块加载与文件信息结构 | 4 |
| 启动签名扫描与内存读取门禁 | 1 |
| 合计 | 20 |

HTTP 测试会在临时目录编译 DLL，并使用本机回环服务器和假密钥。需保证 GCC/objdump 在 PATH。Lua 回归还需要兼容的 Win64 LuaJIT 2.x lua51.dll；可使用已安装游戏 bin/ 下的 DLL，或自行准备兼容 DLL 并显式指定路径：

~~~pwsh
$env:HD2_LUAJIT_DLL = 'C:\dependencies\LuaJIT\lua51.dll'
~~~

也可将 DLL 所在目录加入 PATH。测试使用私有 LuaJIT 状态、本项目源码和假内存/控件，不调用真实游戏 setter。缺少或不兼容的 LuaJIT 会使相关用例失败或跳过；完整验收要求零失败、零跳过。LuaJIT 仅用于开发验证，无需安装到游戏目录。不要离线加载或执行 game.dll。

~~~pwsh
python -m compileall -q tools tests
python -X utf8 -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '回归测试失败' }
git diff --check
~~~

从构建输出中选择确切 ZIP 路径，检查 CRC 与 SHA-256：

~~~pwsh
$taskPackage = 'artifacts/HD2ChatTranslateYYYYMMDDHHMMSS.zip' # 替换为本次实际文件名
python -m zipfile -t $taskPackage
Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256
~~~

离线测试和 ZIP 核验不能代替实际游戏验收。关闭游戏后在 Arsenal 导入并部署，启动后发送新英文聊天，确认原文及“译文：”行显示。连续发送至少三条长短不同的英文消息，确认较早消息已为增加的行数留出空间且没有重叠；再检查收到新消息以及打开、关闭输入框后的布局。在 ESC → MODS 中改变目标语言并应用，发送新消息核对目标语言，再重启核对保存值。环境变量配置见[安装、配置与使用](../README.md#安装与更新)，菜单操作见[游戏内设置](settings.md)。

性能回归以假单调时钟检查不同帧率下扫描、响应处理和报告次数，不等同于真实游戏 FPS。游戏内性能对比应在相同场景、画质设置和帧率上限下进行，并等待启动核验结束。

## 常见构建失败

| 提示 | 处理 |
| --- | --- |
| 找不到构建工具 | 将 MinGW bin 加入 PATH，或传入 --cc / --objdump |
| 不是 Win64 PE DLL | 检查 GCC 目标架构并使用 x86_64 MinGW-w64 |
| DLL 有额外动态依赖 | 使用不引入额外运行库的工具链并核对导入，不要移除校验 |
| DLL 与 metadata 不匹配 | 重新执行原生构建，使用同次生成的两个文件 |
| loader 摘要或条目不匹配 | 重新获取固定 v18 发布资产并保留原 ZIP |
| 菜单 ZIP 摘要或条目不匹配 | 重新获取固定 Mod Options Menu v1.2 原始发布资产 |
| 目标语言目录与 metadata 不匹配 | 用当前目录重新构建 DLL 与 metadata，再打包 |
| 文件名不符合要求或已存在 | 使用新的 HD2ChatTranslate年月日时分秒.zip 名称 |
| Lua 回归报错或被跳过 | 指定兼容 Win64 LuaJIT DLL 后重跑 |

artifacts/ 和 Python 缓存被 Git 忽略。提交源码时不要提交生成的 DLL、ZIP、上游安装包、游戏资源或本机配置。当前兼容范围、内存与加载边界见[技术说明](technical.md)。
