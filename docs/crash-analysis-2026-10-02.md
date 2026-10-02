# 独立版启动崩溃调查（2026-10-02）

首次独立版真实游戏验证发生启动崩溃。停止失败版本后，保留失败安装包和本机数值证据，临时恢复此前验证过的伴随程序版 addon 与 loader，再部署修订独立版完成游戏内核验。没有更改原 Arsenal patch 0–20 或用户的模型配置。

## 已取得的证据

Shared Loader 的加载记录确认聊天 addon 已加载。插件报告显示 `native_init_status: 0`、`heartbeat_active: true`，但只执行了一步已有消息基线，`submitted` 和 `apply_attempts` 均为零。尚未发出模型请求或调用聊天 setter。

15:02、15:04 与 15:07 的游戏转储重复记录读访问异常 `0xC0000005`：地址为 `helldivers2.exe+0x67A279`，被读取地址为 `0xFFFFFFFFFFFFFFFF`。这些转储的模块表中存在与构建 SHA-256 相符的网络 DLL，异常线程的有限栈地址扫描包含稳定的 LuaJIT 地址候选。候选扫描不是可靠展开的调用栈，故障地址也不在网络 DLL 内。

另有 15:06 的不同异常，在 `helldivers2.exe+0x5D7384` 写空地址时网络 DLL 尚未加载；该次另产生 GPU device-hung 记录。不能把这一次与重复的 Lua/宿主异常直接合并为同一原因。

宿主 EXE 对应的故障代码和异常展开表没有可用磁盘映射。可验证为执行页的游戏转储没有保存故障窗口字节，其他次级转储缺少可验证的页面信息。因此本次没有确定故障指令或唯一崩溃原因，也没有读取环境变量、密钥或任意聊天内存。

## 明确发现的缺陷与修复

独立加载器使用的 Lua FFI `HD2Probe_BY_HANDLE_FILE_INFORMATION` 遗漏 `dwVolumeSerialNumber`，结构大小为 48 字节。Windows 的 `GetFileInformationByHandle` 写入完整的 52 字节结构，因此调用会越界写入 4 字节。字段顺序依据 [Microsoft 的结构定义](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/ns-fileapi-by_handle_file_information) 和开发机 Windows SDK 头文件核对。

原模拟测试复用了错误的结构，且模拟 API 只写入第一个属性字段，未覆盖真实 API 的完整写入。这解释了离线测试通过仍存在内存破坏的原因；此缺陷与转储中的 Lua/宿主异常相符。修订版后续完成一次真实翻译，支持该修复有效，但不能由单次运行确定此前所有崩溃的唯一原因。

修复补齐缺失字段，并在原生加载器调用文件信息 API 前核对 52 字节大小及关键字段偏移。布局不符时停止原生加载。回归将模拟完整结构写入，并用真实 Windows API 读取测试临时文件，检查结构后的保护字节保持不变；旧 48 字节布局应在调用 API 前被拒绝。

## 验收记录

修订包 SHA-256 为 `5cbe8bc56ae50d73148a7f814cc8d17b6940efa566c10f2045edd736628f4948`。全套 101 项测试通过，零失败、零跳过，27.448 秒；新增真实 Windows API 保护字节检查通过，旧布局被拒绝且 API 调用数与 DLL 加载数均为零。内嵌 DLL 指纹、ZIP CRC、完整 LuaJIT 语法和源码上限核验通过，构建信息见 [独立版说明](chat-standalone.md)。

15:39 完成修订版本机部署：addon 为 patch 21，loader 为 patch 22，六个文件均与来源 ZIP 和收据摘要匹配；原 patch 0–20 的 21 个主文件摘要未变。临时目录内实际安装、重复安装、实际回滚，以及本机回滚 dry-run 均通过。失败包已保留在本机忽略目录供比较。

后续真实游戏验证中，用户确认“原聊天行已变成中文”。16:22:29 的修订版新会话报告显示 `mode: standalone`、`transport: in_process_winhttp`，初始化及最后状态为 `0`，执行 6,308 个更新步，提交 1 次、收到译文 1 次、确认回写 1 次，翻译、适配器与回写错误均为 `0`。安全报告 SHA-256 为 `0e74c582224ccdf3ecfe4a455d884bce3b9e221dd732a4e78da48b0eee7c9b41`。这次运行已越过此前仅执行一个基线步的失败位置并完成翻译；长期稳定性和其他异常类型尚未验收。

验收时再次核对来源包、六个安装文件和游戏 DLL 的摘要，全部匹配。只读枚举 Arrowhead 游戏转储目录及 Windows 的 Helldivers 2 转储文件，共 153 份，创建时间晚于本次 15:39 部署的为零；未读取转储内容。核验时游戏和本项目伴随程序进程均已退出，未据此推断游戏退出原因。

## 17 时合并包的加载阶段崩溃

17:10、17:15、17:18 的三份新转储均为读访问冲突 `0xC0000005`，故障位于 `helldivers2.exe+0x5F2EB0`，目标地址为当时的 `RDI+0x80`。网络 helper DLL 均未加载；有限栈候选没有 Lua 地址，但候选扫描不等于可靠展开的调用栈，不能据此证明 addon 是否执行。加载器日志和翻译报告均停在此前会话，与 15 时已进入网络 DLL/Lua 路径的异常不同。安全数值证据位于本机忽略目录 `artifacts/arsenal-repair/crash-evidence.json`，没有读取聊天、配置、密钥或任意内存字符串。

查阅 [Bingus 官方 archive 构建器](https://github.com/CowboyBingus/BingusSharedLoader/blob/main/scripts/archive.py) 并比对原有多资源 patch 后，确认本项目合并器存在两处格式错误：头部偏移 4 是类型数、偏移 8 是文件数，本项目却写成相反顺序；每条 file row 的末 DWORD 应为递增编号，第二项却也写成 `0`。这些是直接的格式不兼容证据，支持加载阶段崩溃的原因判断，尚不能从转储确定唯一故障原因。

已修正为一个类型、两个文件，按资源 hash 排序，编号 `0,1`。使用上游纯函数对实际两个原始资源生成参考包，本项目修订主 patch 与参考结果完全一致，SHA-256 为 `4cb51d198a387746afbace921dc6f9763e7a587748ea16e97c252defd92097cf`。仅偏移 4、8、260 的三个字节改变，原资源、网络 DLL 和配置读取逻辑均未变化；独立 reader 及上游双资源黄金值回归覆盖之前漏检的结构差异。

修订安装包为 `HD2ChatTranslate20261002173335.zip`，SHA-256 `5d8064b0a4ebb3d27cf4e131b4b1f5cac91485a3a1f2e04de3b23340ef11092a`，112 项测试通过、零失败、零跳过，28.628 秒。实际 ZIP 的 CRC、LuaJIT 语法和官方构造器逐字节比对通过。新的实际 Arsenal 更新、稳定启动和聊天翻译仍待用户核验；失败安装包保持原样供比较。
