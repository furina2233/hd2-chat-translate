# 原生聊天接入研究

核对日期：2026-10-01；本机 Steam build `25480438`。本文记录函数定位线索，不能当作已经实现的聊天接口。

## 从代码模组借鉴的方法

| 项目 | 可核验的实现方式 | 对聊天翻译的帮助与边界 |
| --- | --- | --- |
| [BetterLobbyManagement](https://github.com/CowboyBingus/BetterLobbyManagement/blob/main/src/chat.lua) | 原生聊天发送函数、RPC 签名、历史计数字段；调用前验证指令字节 | 提供聊天子系统的代码锚点；没有接收回调、历史条目布局或显示替换实现 |
| [ModOptionsMenu](https://github.com/CowboyBingus/ModOptionsMenu/blob/main/src/mod_options_menu.lua) | 给原生 Options 页添加 MODS tab；通过 `set_label` 与 `set_string_arg` 更新插件控件 | 证明原生文本 setter 存在；仍需确认聊天控件是否属于同一类型。公开配置 API 只有 toggle/choice/slider |
| [ClickableScrollbars](https://github.com/CowboyBingus/ClickableScrollbars/blob/main/src/clickable_scrollbars.lua) | 只读 UI dispatch 表与 screen stack，按 controller 类型识别界面；验证签名后调用滚动函数 | 可借鉴控件定位与构建校验；已确认的是 Armory/loadout controller，不能套用为聊天 controller |
| [ArmoryPreviewCache](https://github.com/CowboyBingus/ArmoryPreviewCache/blob/main/src/native.lua) | 读取 screen stack、thumbnail manager、controller registry | 提供原生 UI 状态枚举经验；不读取聊天文本 |
| [HUD Ballistic Trajectory](https://github.com/NeverB0re/HD2-HUD-Ballistic-Trajectory-DX11/blob/main/src/gun_calibration.lua) | 解析 PE 代码节，扫描签名，要求唯一匹配并验证指针目标/语义关系 | 可借鉴地址重定位；扫描命中本身不能证明函数用途 |
| [Driver HUD](https://github.com/FireScallion/DRIVER-HUD---HD2-Vehicle-HUD/blob/main/src/driver_hud.lua) | `World.create_screen_gui` 与 `Gui.text` 绘制 HUD；保留原 `update` | 证明独立覆盖层可行；没有更新原生聊天行的证据 |
| [Chinese IME Support](https://github.com/zyklone4096/reshade-hd2-ingameime) | ReShade 获取窗口后观察 Enter/Esc，读取剪贴板，用 `SendInput` 模拟输入 | 没有读取聊天框或历史；自有按键状态不等于游戏聊天开启状态 |

上述项目的当前源码或发布说明大多标注 build `25480438`；Driver HUD 未确认这一构建。ClickableScrollbars 的研究文档仍包含旧构建记录，使用偏移时应以匹配的源码和指令校验为准。此次搜索未找到可核验的通用聊天接收/替换 API，搜索无命中也不能证明所有模组都没有相关实现。

Chinese IME Support 的当前 README 将原输入方案标为失效；[2026-05-20 的兼容性说明](https://www.nexusmods.com/helldivers2/mods/12856) 称只保留键盘布局修复与剪贴板粘贴。检查其 `library.cpp`、`input.cpp` 没有得到原生聊天控件或函数地址。

ModOptionsMenu 的文本控件包含 label ID 和格式参数，使用 `#COUNT` 模板显示任意文本。它会长期保留字符串缓冲区，因为隐藏控件仍可能持有地址。这说明后续实现需要处理字符串所有权，不能仅把较长中文写入未知容量的原文本内存。[文本缓冲区与 setter 调用](https://github.com/CowboyBingus/ModOptionsMenu/blob/main/src/mod_options_menu.lua#L357-L385)

## 已获得的地址线索

以下地址均为 `game.dll` 相对地址（RVA），不是本进程绝对地址。2026-10-01 已在本机运行态确认六处已知签名的字节一致；函数语义与聊天显示接入仍需继续验证。

| 候选位置 | RVA / 字段偏移 | 来源与待确认内容 |
| --- | --- | --- |
| 聊天发送入口 | `0x1097560` | BetterLobbyManagement 的 `LmChatSend`；发送路径包含本机显示逻辑，需跟踪调用关系 |
| 聊天框发送路径 | `0x186025D` | 将网络 context 的 `+0xC418` 聊天对象交给发送函数 |
| 网络 context 全局指针线索 | `0x347CEF0` | 从上述公开签名的 RIP-relative 指令计算；未读取本机运行态指针 |
| 发送输入地址线索 | `RDI + 0x16D4` | 上述公开签名中的 `lea r8`；RDI 所属对象及生命周期仍未知 |
| 聊天 RPC 路径 | `0xBEB103` | `rpc_ingame_chat_message`，hash `0x9FDDB88E`；不是接收回调地址 |
| 聊天历史代码锚点 | `0x1097A7C` | 字节 `8B 87 94 95 00 00 8B 8F 90 95 00 00`，读取历史 count/first 字段 |
| 历史 first / count | `+0x9590` / `+0x9594` | 64 行历史；尚未确定每项地址、正文布局、消息身份与生命周期 |
| 原生 label setter | `0x143BF90` | ModOptionsMenu 使用；原型 `void(widget, u32 label)` |
| 原生 string argument setter | `0x143C950` | ModOptionsMenu 使用；原型 `void(widget, u32 key, const char *text)` |

后续优先从历史代码锚点恢复访问条目的步长、正文地址和调用关系，再从聊天行构建/刷新路径确认控件类型与字符串复制规则。发送函数可用于定位，不用于广播翻译结果。只有确认每行稳定标识和显示更新方式后，才连接现有文件邮箱。

独立反汇编 BetterLobbyManagement 发布的聊天框签名字节，按其 RVA 修正指令地址后得到：

```asm
mov rcx, [rip + 0x1C1CC8C]  ; 全局指针 RVA 0x347CEF0
lea r8, [rdi + 0x16D4]
add rcx, 0xC418
call 0x1097560
```

这是对公开字节的离线推导，可以帮助从聊天 UI 发送侧回溯所属对象；它不是本机运行态地址验证，也不提供入站消息接收方法。命令为 `objdump -D -b binary -m i386:x86-64 --adjust-vma=0x186025d chat_box_signature.bin`，输入仅是源码中那段签名，未导出游戏内存。

## 已尝试的离线路线

1. **资源目录与 XAML 数据绑定。** 用本机 HD2SDK 的 slim reader 检查全部 3,522 个虚拟包 TOC，发现两个含 XAML 的包：`007e093ca718ca1a`、`6cbf0e73d5a5d9f9`。去重后 264 项均成功按 16 字节头与 UTF-8 解析（175 ResourceDictionary、31 Page、57 Component、1 Grid），没有跳过或解析错误。检索 `chat`、`conversation`、`messagehistory`、`sendmessage`，唯一命中是法语购买文本 `ACHAT`，不属于聊天绑定。这只说明本次关键词检索未发现聊天 XAML，不能排除其他格式或无显式名称的原生 UI。
2. **磁盘 DLL 符号与字符串。** 检索可读 ASCII/UTF-16 的 `chat`、`conversation`、`noesis`、`textbox`，没有命中。聊天候选 RVA 超出主代码节的磁盘 raw-data 范围，无法直接从安装文件提取对应指令。需读取游戏加载后可用的代码，而不是继续对缺失的 raw bytes 做反汇编。
3. **全部基础 Lua 资源。** 同样检查 3,522 个虚拟包目录，找到 5 个含 `lua` 类型（`0xA14E8DFA2CD117E2`）的包、12 项条目，去重后 10 项，合计 30,934 字节。全部为 8 字节封装头之后的 LuaJIT BC v2；对可读常量字节检索 `text_chat`、`chat_history`、`ingame_chat_message`、`chatbox`、`sendmessage`、`chat` 及“聊天/消息/输入”均无命中。没有执行游戏字节码。这不排除数值哈希绑定、动态名称、其他类型资源或原生路由。

只读检查和提取结果存放在忽略的 `.research/`；不提交游戏原始资源。

## 运行态采集的边界

[HD2Runtime 的 `capture_snapshot`](https://github.com/SkyeShade/HD2Runtime/blob/master/api/snapshot_capture.lua) 捕获全进程的可读区域，[文档预计 6–12 GiB](https://github.com/SkyeShade/HD2Runtime/blob/master/docs/snapshots.md)。它会包含无关运行时数据，不适合直接用于这次函数研究。公开 `hd2.read` 只接受已编目的语义目标，也不是任意地址 reader。

本项目改为单独的 LuaJIT FFI 只读探针，借鉴 [Windows 只读适配器](https://github.com/SkyeShade/HD2Runtime/blob/master/runtime/windows_readonly.lua) 的页属性检查。探针仅采集指定 DLL 主代码节中的短代码窗口，不读取聊天对象、玩家信息或堆，不调用聊天发送/文本 setter，不写游戏内存。已取得首份真实运行态报告，详情见下文。

磁盘 `game.dll` SHA-256：`2e2c3b7c2500646dadd5f2b4c6e0504dbb7e7896139f64cddc0d1813c718f51e`；PE timestamp `1790161983`；SizeOfImage `74727424`。主代码节 RVA `0x1000`、virtual size `34667155`、raw size `8718336`，节标志 `0x60000020`。该节没有可用名称，不能依赖 `.text` 名称定位。

独立核验：探针资源名 `mods/hd2chat/chat_probe` 的构建器 hash 与本机 SDK 的 Murmur64 计算结果均为 `0xC509C11199F753C2`。将本机磁盘 DLL 前 64 KiB 作为测试头、其余代码读取替换为模拟数据，在本机 LuaJIT 下成功通过 PE 检查并进入扫描阶段。这验证了真实节表的解析，未加载 `game.dll` 或读取游戏进程，也未验证聊天签名。

## 首次真实运行态结果

2026-10-01 20:37:38（北京时间），Bingus v18 日志记录 `mods/hd2chat/chat_probe: loaded`。20:38:05 完成报告 `chat-probe-1790858285-02f9a860-abe000000007ECC6D20-01.json`，SHA-256 为 `e7365016a94314146ebca8aadfb6b10be34fb4a8813ce4450d0a0a1a24a8e13a`。磁盘 DLL、PE 标识与代码节范围全通过检查；完整扫描 34,667,155 字节，跳过、读取失败、不可读候选和截断均为零。20 个候选窗口共 10,240 字节，分析器校验通过；六处已知签名比较全部为 `true`。

报告和导出窗口保存在本机忽略的 `artifacts/probe-analysis/`。原始报告只含代码与扫描信息，不包含聊天正文。`function_verification` 仍为 `none`：字节匹配不能直接证明函数用途、控件类型或可写接口。

根据窗口中的明确指令，可以进一步确认以下结构关系。这里的对象基址是对应指令中的 `RDI`，尚未读取实际聊天对象。

| 指令证据 RVA | 可复核的关系 | 仍未确认的内容 |
| --- | --- | --- |
| `0x1097A7C`–`0x1097AAD` | 读取 `+0x9590` 与 `+0x9594`，以 `(first + count) & 63` 选槽；满 64 条则递增 first，否则递增 count | 消息稳定标识及对象生命周期 |
| `0x1097ABC`、`0x1097B42` | 槽位步长为 `0x228`；在对象 `+0xB90 + slot*0x228` 写入 8 字节数值 | 数值的单位和消息身份用途 |
| `0x1097BBB`–`0x1097BE4` | 目标地址为 `+0xBA0 + slot*0x228`，向 `0x20BBA88` 传入该地址、`0x201` 及 R13 | 完整复制函数、编码和容量契约；另一分支尚不完整 |
| `0x1097020`–`0x10970AF` | 按 first/count 遍历环形记录，检查 `+0xDA2`–`+0xDA5`，随后将 `+0xBA0` 地址与 `+0xB98` 值传给 `0x12F2F60` | 这些标志和被调用函数的语义，不能直接当作 UI 刷新 |
| `0x143C950`–`0x143C965` | string argument setter 把 widget `+0x110` 传给 `0x143A1B0`，返回值决定是否继续标脏 | 是否复制字符串，以及聊天行是否使用这种 widget |

后续采集优先补足发送/历史窗口的截断尾部，并取得 `0x12F2F60`、`0x20BBA88` 和 `0x143A1B0` 的短代码窗口；在确认正文布局和显示更新之前，继续保持只读。

## 补充窗口的分析结果

第二次真实采集完成于北京时间 21:07:47，报告 SHA-256 为 `2d3cd8bf3323f7cd875260c0c00abed1a3cdaa1b3e3622aa63a5d8097459b655`。30 个窗口、完整代码节读取和六处签名检查全部通过；详情见 [补充诊断记录](chat-probe.md#补充代码窗口)。

已采集的聊天框窗口确认 `0x186025D` 加载全局指针 `0x347CEF0`，在 `0x186026B` 加 `0xC418`，随后 `0x1860272` 调用 `0x1097560`。发送入口 `0x1097583` 将 RCX 保存到 R15，`0x10977EB` 恢复 RCX，`0x10977EE` 调用历史函数 `0x10979C0`；其 `0x10979D3` 执行 `mov rdi,rcx`。因此历史基址与该路径的 `chat = global_pointer + 0xC418` 一致，偏移只加一次，不能再解引用该内嵌对象。

正文地址为 `chat + 0xBA0 + slot * 0x228`。两条复制分支分别在 `0x1097BE4`、`0x1097C31` 调用 `0x20BBA88`，目标容量与最大复制参数均为 `0x201`。目标函数的字节循环符合有界窄字符复制：遇到 NUL 正常结束，容量耗尽会清空目标首字节并返回错误。由此可把记录作为最多 512 字节内容、513 字节含终止符的候选正文；实际消息是否 UTF-8 仍需固定文本核验。

`chat + 0xB90 + slot * 0x228` 是 8 字节数值，`+0xB98` 是另一个值，本轮观察器跳过后者。`+0xDA0..DA7` 作为稳定性快照，与正文末尾重叠一字节；不能把它描述为完全独立的标志结构。派发代码实际检查 `DA2 == 0`、`DA3 != 0`、`DA5 != 0`、`DA4 != 0`，随后设置 DA2，再调用 `0x12F2F60`。

`0x12F2F60` 遍历八项、步长 `0xC0` 的表并沿匹配项调用 `0x1382650`、`0x185D6E0`、`0x185F470`，条件下再调用 `0x1327F50`。这些指令尚不能证明是 UI 刷新或网络路由。下一包补采前三个调用目标及各自 `+0x200` 窗口，保持原代码扫描预算和门禁。

文本 setter 的下层 `0x143A1B0` 保存传入字符串指针到记录 `+0x10`，在 `+0x18` 存相关信息，并调用 `0xAAFF30` 处理字符串长度/散列。现有窗口没有证明字符串所有权转移或复制；不能传入短期临时缓冲区。`0x143A3B0` 属于另一数值/标志处理函数，不能当作第二个字符串入口。

下一步使用持续只读观察器验证环形正文与固定 ASCII/中文消息，再比较聊天框打开和关闭时的 screen stack/controller 元数据。公开模组给出的 registry 布局只用于有界枚举；controller kind 的实际数值与聊天行映射尚未确认。没有读写原生聊天行或连接大模型。

## 正文验证与下游记录路径

第三轮代码报告已校验并导出到 `artifacts/probe-observe-analysis/`：完整扫描、36 个窗口、无读取失败；报告 SHA-256 为 `1ade5087699ac91ad79d7cabe4c06a1a66e5f12c09a53847f68974ef222dec3c`。同时，实际观察器成功识别固定 ASCII 消息，长度为 20 字节，双读、终止符和 UTF-8 校验无错误。用户无法在现有游戏输入框输入中文，因此真实中文正文测试仍未完成。详情与诊断命令错误记录见 [观察结果](chat-probe.md#首次正文观察与诊断命令错误)。

`0x12F2F81` 从全局槽 `0x346D538` 读取对象指针到 RDI；它与前述网络 context 全局槽是不同入口。函数在八项、步长 `0xC0` 的表中匹配消息键。`0x1382650` 返回 16 字节描述数据，其已采集指令没有读取正文。

`0x12F2FE2` 设置 `RCX = RDI + 0x4F7080`；`0x12F300B` 调用 `0x185D6E0`，第六个栈参数是原正文指针。后者以对象 `+0x12D00` 的索引、步长 `0x4B4` 写入记录：`+0` 为代码，`+4` 为 16 字节描述，`+0x14` 为容量 `0x81` 的标签，`+0x98` 为另一份 16 字节数据，`+0xB4` 为容量 `0x400` 的正文。索引对 64 取模，`+0x12D04` 是上限为 64 的计数，返回新记录地址。复制末端用 `byte & 0xC0 == 0x80` 识别续字节并清除截断尾段，支持其使用 UTF-8 字节串的判断，但不能替代完整合法性验证。

随后 `0x12F3018` 设置 `RCX = RDI + 0x14498`，`0x12F301F` 将返回记录地址放到 RDX，`0x12F3022` 调用 `0x185F470`。后者把子槽地址 `manager + 0x4390 + index * 0x3D8` 放入 RCX、R8B 设为一，保持 RDX，并在 `0x185F49B` 调用 `0x1860B00`；这是接收该文本记录的下游候选。接着 `0x185F4A3` 调用 `0x185F170`，但 RDX 可能已被前一次调用改变，不能认定它也接收同一记录。

现有指令未显示最终正文绘制或刷新控件，因此不能把这些候选直接命名为聊天 UI setter。下一修订包增加 `0x1860B00`、`0x185F170` 各自入口及 `+0x200` 的四个固定窗口，继续确认记录的复制、指针保留和显示更新方式。未读取第二组记录的实际对象，也未调用这些函数。

## 控件正文属性路径

修订观察器的第四轮代码报告 `chat-probe-1790870167-02c0b258-abe000000007ECD7BC0-01.json` 完整扫描通过，共 40 个窗口，无读取失败；SHA-256 为 `563a71775a37492cbeb73ad71a851b49a243302e1dc721f36aa9da6a5e5d5afd`。导出及相邻窗口拼接反汇编位于忽略的 `artifacts/probe-display-analysis/`。函数验证字段仍为 `none`，没有运行原生调用。

`0x1860B00` 将入参 RDX 保存为记录指针 RBX，RCX 保存为控件槽 RDI，并令 RSI 为 `RDI + 0x110`。`0x1860B9D`–`0x1860BBC` 对记录 `+0x14` 的非空字符串调用 `0x1441CA0`，参数 RCX 为 RSI、EDX 为属性 hash `0x341F7711`、R8 为该字符串地址。`0x1860C2F`–`0x1860C58` 则对记录 `+0xB4` 正文调用同一接口，属性 hash 为 `0x7518C954`。这确认了原正文被送入控件属性路径；接口是否复制或长期保留指针仍未知。

函数在 `[0x3326340] + 0xAC55C` 与控件槽 `+0x220` 均非零时，还会调用 `0x13006A0` 生成容量 `0x800` 的栈内文本，处理 UTF-8 截断并调用 `0x173C360`，随后经 `[[0x3326308]+0x10]+0x630` 间接交接文本。该条件路径的用途和间接目标尚未确认，不能据此认定是最终绘制；本轮没有解析或调用该目标。

`0x185F170` 的已捕获主体只推进 64 项索引/计数，按 `+0x43A0/+0x43B0` 的浮点字段更新状态并调用 `0x1860DA0`，没有读取正文。`0x1860B00` 之后的 `0x1861010` 是另一更新候选。下一包仅加入 `0x1441CA0`、`0x13006A0`、`0x173C360`、`0x1861010` 各自入口及 `+0x200` 的八个固定窗口，用于核对字符串所有权和刷新契约，采集预算不变。

## 正文属性的指针生命周期

2026-10-02 第五轮报告完整扫描通过，共 48 个窗口、24,576 字节，读取失败及跳过为零，六处已知签名全部匹配。报告 SHA-256 为 `6c5ac206d7d97865a47118c5cb3a47c6333176383640c1fc4cc636d3a0bb52b6`，导出位于忽略的 `artifacts/probe-property-analysis/`；没有调用这些函数。

`0x1441CA0` 是包装器：`0x1441CA9` 给 RCX 加 `0x110`，`0x1441CB0` 调用 `0x143A1B0`，RDX 属性 hash 与 R8 字符串地址透传。结合上游 `RCX = widget_slot + 0x110`，最终属性 map 基址是 `widget_slot + 0x220`。下层按 key 查找条目，直接保存 R8 指针，不复制正文。因此正文属性借用事件记录 `+0xB4` 的缓冲区；临时字符串地址不可用于替换，环形记录复用也会影响指针内容。控件槽与事件槽是否同索引尚未证明。

map 的 count 是 `BYTE[map+0x158]`，条目从 `map+8` 起、步长 `0x18`：key `+0`、flag `+4`、value pointer `+8`、cached hash `+0x10`。该区间容纳 14 项，这是结构容量推导；setter 未显示 `count < 14` 的硬上限。未来只读定位必须拒绝 count 超过 14 的 map，不能把它称为游戏接口强制的上限。

另一条定位候选是从全局 `0x346D538` 的对象到 `+0x14498` manager，再到 `+0x4390 + i*0x3D8` 的 64 个控件槽，逐项查正文 key `0x7518C954`。它可避开通用 controller registry，但仍需真实运行核对属性 count、借用指针是否落在已知 64 项事件正文区，以及 `0x400` 字节内的 NUL；目前没有读回或改写这些槽。

`0x13006A0` 按属性表构造输出字符串，在 `0x13009C3` 调用 `0x1300B90`。偏移修正包顺带加入该目标及 `+0x200` 的两个固定窗口，以补足转换契约。条件路径的间接调用目标仍未知；本轮未解析其运行态指针链，不能将它命名为最终绘制。
