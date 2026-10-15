# 从源代码手动构建

所有命令在项目根目录的 PowerShell 7（pwsh）中运行。构建不启动游戏、不读取翻译凭据、不请求真实翻译服务，也不部署游戏文件。

## 工具与输入

需要 Windows x64、64位 Python 3.10+、MinGW-w64 的 x86_64 GCC 和同套 Binutils objdump。脚本只使用 Python 标准库，无需安装 pip 依赖。工具可加入 PATH，或在命令中指定完整路径。

~~~pwsh
python --version
python -c 'import struct; print(struct.calcsize("P") * 8)'
gcc --version
gcc -dumpmachine
objdump --version
~~~

Python 位数应为64，GCC 目标应为 x86_64-w64-mingw32。不支持 MSVC、Clang、32位或非 Windows 工具链。`native/vendor/cjson/` 包含固定 cJSON v1.7.19 源码与 MIT 许可，无需另行下载。

根目录 [VERSION](../VERSION) 保存安装包版本号，例如 `2.4`。版本不得包含路径、空白或控制字符；文件缺失、为空或格式无效时打包失败。

构建不需要前置模组 ZIP 或免费服务私有配置。玩家使用插件时仍需单独安装 Bingus Shared Loader v18 和兼容 v1.2 接口的 Mod Options Menu。

## 一键构建

~~~pwsh
python tools/quick_build_all.py
if ($LASTEXITCODE -ne 0) { throw '一键构建失败' }
~~~

也可指定工具及输出目录：

~~~pwsh
python tools/quick_build_all.py `
    --cc 'E:\mingw64\bin\gcc.exe' `
    --objdump 'E:\mingw64\bin\objdump.exe' `
    --output-dir 'C:\release'
~~~

脚本依次构建原生 DLL、metadata 和一个不含前置模组的 Arsenal ZIP。默认输出到 `artifacts/`，名称为 `HD2ChatTranslate_版本号_YYYYMMDDHHMMSS.zip`。版本来自根目录 VERSION，时间自动使用北京时间，例如 `HD2ChatTranslate_2.4_20261015180000.zip`。同名文件不会覆盖。

## 分步构建

~~~pwsh
python tools/build_native_http.py --cc 'E:\mingw64\bin\gcc.exe' --objdump 'E:\mingw64\bin\objdump.exe'
if ($LASTEXITCODE -ne 0) { throw '原生模块构建失败' }
python tools/build_package.py --output-dir 'C:\release'
if ($LASTEXITCODE -ne 0) { throw '安装包构建失败' }
~~~

原生构建生成 `artifacts/native/hd2ct_http.dll`、`hd2ct_http.meta.json`，以及从目标语言目录生成的 `target_languages.generated.h`。源码清单在 `tools/build_native_http.py` 统一维护，新增模块时须同步清单。

构建器校验 Win64 PE、ABI 4 的三个任务接口导出及 Windows 系统依赖，并记录 DLL 长度、SHA-256、目标语言目录摘要和编译器版本。签名及随机数使用 Windows BCrypt，不接受额外 MinGW 动态运行库。

原生构建可用 `--output` 和 `--meta` 指定中间产物路径；DLL 与 metadata 必须来自同一次构建。打包其他构建目录的模块时成对传入：

~~~pwsh
python tools/build_package.py --native-dll 'C:\build\hd2ct_http.dll' --native-meta 'C:\build\hd2ct_http.meta.json'
~~~

打包器读取 game/ 源码、VERSION、目标语言目录、本地化目录和原生模块，校验全部15种菜单语言，生成只含聊天插件资源的 patch，并附项目 GPLv3 与 cJSON 许可。Guid、manifest Name、patch 和聊天资源名保持稳定，可更新已有安装。

打包仅通过 `--output-dir` 指定目录，文件名中的版本和时间由程序生成。修改目标语言目录后须重建 DLL；目录或 DLL 摘要不匹配时拒绝打包。翻译地址、模型名和密钥不作为构建配置读取。

## CLion / CMake

在 CLion 中打开项目根目录，选择 Windows x64 MinGW 工具链。可通过 `-DPython3_EXECUTABLE=解释器路径` 指定 Python 3.10+。

~~~pwsh
cmake -S . -B build/cmake -G Ninja -DCMAKE_C_COMPILER='E:\mingw64\bin\gcc.exe' -DPython3_EXECUTABLE='C:\Program Files\Python310\python.exe'
cmake --build build/cmake --target hd2ct_http
~~~

`cmake`、`ninja` 可使用 CLion 自带版本；Ninja 不在 PATH 时可传 `-DCMAKE_MAKE_PROGRAM=完整路径`。配置时生成语言头文件供索引，源码清单与命令行构建器共用，不下载依赖或读取私有服务配置。

输出位于 `build/cmake/native/`。目标 `hd2ct_http` 构建 DLL 并校验、生成 metadata；打包时将这两个文件成对传给 `tools/build_package.py`。Debug 也使用 `-Os`、`-g0` 和剥离调试信息，以控制嵌入资源大小。

## 验证

HTTP 测试使用回环服务器、假密钥和临时 DLL。Lua 测试使用私有 LuaJIT 状态和假内存，不调用游戏 setter；需要兼容的 Win64 LuaJIT 2.x DLL，可使用游戏 bin/ 下的 `lua51.dll`：

~~~pwsh
$env:HD2_LUAJIT_DLL = 'E:\SteamLibrary\steamapps\common\Helldivers 2\bin\lua51.dll'
python -m compileall -q tools tests
python -X utf8 -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '回归测试失败' }
git diff --check
~~~

缺少或不兼容的 LuaJIT 会使测试失败或跳过，完整验收要求零失败、零跳过。不要离线加载或执行 game.dll。检查构建日志中的确切 ZIP：

~~~pwsh
$taskPackage = 'artifacts/HD2ChatTranslate_2.4_20261015180000.zip'
python -m zipfile -t $taskPackage
Get-FileHash -LiteralPath $taskPackage -Algorithm SHA256
~~~

离线验证不能代替实机验收。确认前置模组已安装，关闭游戏后导入部署；进入游戏发送英文聊天，核对原文、译文及多行布局，再切换目标语言并 APPLY。性能对比须使用相同场景、画质和帧率上限，并等待启动核验结束。

## 常见构建失败

| 提示 | 处理 |
| --- | --- |
| 找不到工具或架构不符 | 配置 x64 MinGW-w64 与 Python，或传工具完整路径 |
| DLL 有额外动态依赖 | 更换合适工具链，不要绕过导入校验 |
| DLL、目录与 metadata 不匹配 | 重建并使用同次生成的文件 |
| VERSION 缺失或无效 | 在根目录填写有效版本号 |
| 输出已存在或与输入冲突 | 更换输出目录，或等待下一个构建时间 |
| Lua 测试失败或跳过 | 指定兼容 Win64 LuaJIT DLL 后重跑 |

artifacts/、构建目录及 Python 缓存由 Git 忽略，不提交 DLL、ZIP 或本机配置。实现细节见[技术说明](technical.md)。
