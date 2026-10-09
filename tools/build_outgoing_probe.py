"""构建临时只读发送代码窗口诊断包。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import zipfile

import build_package as builder


OUTGOING_PROBE_FLAG = b"local OUTGOING_PROBE_ENABLED = false --[[HD2_CHAT_OUTGOING_PROBE_ENABLED]]"
DISABLED_FLAGS = (
    builder.OBSERVE_FLAG,
    builder.DISPLAY_TEST_FLAG,
    builder.TRANSLATE_FLAG,
    builder.STANDALONE_FLAG,
)
PROBE_DESCRIPTION = (
    "临时只读发送代码窗口与输入生命周期诊断包。仅在游戏构建指纹门禁通过后读取八个固定代码窗口，"
    "并在代码探针完成后短暂采样输入长度及数值状态；不记录聊天正文、不修改游戏内存、"
    "不调用候选函数、不翻译或发送网络请求。候选函数及其 ABI 均未验证。"
    "此包会暂时替换同 GUID 模组，采集后请重新导入正常版本。"
)
LIFECYCLE_RETURN = b"    return one_probe_step, outgoing_pump_step"
INITIALIZE_CALL = b"local setup_ok, probe_step, outgoing_pump_step = pcall(initialize_probe)"
RETENTION_CALL = (
    b"startup_report_retention.run, ffi, kernel, OBSERVE_ENABLED, TRANSLATE_ENABLED"
)


def _lifecycle_adapter_source(newline: bytes) -> bytes:
    """把进程内读取限制在本探针需要的固定数值字段。"""
    source = r'''    local outgoing_probe_root = nil
    local outgoing_probe_generation = 0
    local function outgoing_probe_array()
        return setmetatable({}, {__json_array = true})
    end
    local function outgoing_probe_read(address, length)
        if not address then return nil end
        local bytes = observer_read(address, length)
        if type(bytes) ~= "string" or #bytes ~= length then return nil end
        return bytes
    end
    local function outgoing_probe_vtable(manager, offset, failures)
        local slots = outgoing_probe_array()
        local address = observer_add(manager, offset)
        if not address then return slots, failures + 1 end
        local table_address, pointer_reason = observer_read_pointer(address)
        if not table_address then
            if pointer_reason ~= "null_pointer" then failures = failures + 1 end
            return slots, failures
        end
        if table_address < module_base_number
            or table_address >= module_base_number + core.SOURCE.size_of_image then
            return slots, failures
        end
        local bytes = outgoing_probe_read(table_address, 16 * 8)
        if not bytes then return slots, failures + 1 end
        local code_start = module_base_number + core.SECTION.rva
        local code_end = code_start + core.SECTION.size
        for slot = 0, 15 do
            local function_address = observer_u64(bytes, slot * 8)
            if function_address == nil then
                failures = failures + 1
            elseif function_address >= code_start and function_address < code_end then
                slots[#slots + 1] = {
                    slot = slot,
                    rva = function_address - module_base_number,
                }
            end
        end
        return slots, failures
    end
    local outgoing_probe_adapter = {
        is_ready = function()
            return state.done == true and state.status == "outgoing_probe_complete"
        end,
        now_ms = function() return tonumber(kernel.GetTickCount64()) end,
        prepare_report = function()
            prepare_observer_paths()
            return true
        end,
        encode_json = core.encode_json,
        write_report = observer_write_report,
        sample = function()
            observer_read_budget = 0
            local failures = 0
            local snapshot = {
                generation = outgoing_probe_generation,
                root_changed = 0,
                submit_flag = -1,
                body_length = -1,
                input_flags = -1,
                history_head = -1,
                history_count = -1,
                manager_slots = outgoing_probe_array(),
                input_slots = outgoing_probe_array(),
            }
            local root_global = observer_add(module_base_number, 0x346D538)
            local root_first = root_global and observer_read_pointer(root_global)
            if not root_first then
                failures = failures + 1
            else
                local manager = observer_add(root_first, 0x14498)
                if not manager then
                    failures = failures + 1
                else
                    local function read_field(offset, length)
                        local address = observer_add(manager, offset)
                        if not address then
                            failures = failures + 1
                            return nil
                        end
                        local bytes = outgoing_probe_read(address, length)
                        if not bytes then failures = failures + 1 end
                        return bytes
                    end
                    local submit = read_field(0x1E0F, 1)
                    if submit then snapshot.submit_flag = submit:byte(1) end
                    local body = read_field(0x16D4, 804)
                    if body then
                        local terminator = body:find("\0", 1, true)
                        if terminator then snapshot.body_length = terminator - 1 end
                        if not terminator then snapshot.body_length = -2 end
                        body = nil
                    end
                    local flags = read_field(0x139B8, 5)
                    if flags then
                        local packed = 0
                        for index = 1, 5 do
                            packed = packed + flags:byte(index) * (256 ^ (index - 1))
                        end
                        snapshot.input_flags = packed
                    end
                    local history_head = read_field(0x13990, 4)
                    local history_count = read_field(0x139C0, 4)
                    if history_head then snapshot.history_head = observer_u32(history_head, 0) or -1 end
                    if history_count then snapshot.history_count = observer_u32(history_count, 0) or -1 end
                    snapshot.manager_slots, failures = outgoing_probe_vtable(manager, 0, failures)
                    snapshot.input_slots, failures = outgoing_probe_vtable(manager, 0x1398, failures)
                end
                local root_last = observer_read_pointer(root_global)
                if root_last ~= root_first then
                    failures = 1
                    snapshot.submit_flag = -1
                    snapshot.body_length = -1
                    snapshot.input_flags = -1
                    snapshot.history_head = -1
                    snapshot.history_count = -1
                    snapshot.manager_slots = outgoing_probe_array()
                    snapshot.input_slots = outgoing_probe_array()
                else
                    if outgoing_probe_root ~= nil and root_first ~= outgoing_probe_root then
                        outgoing_probe_generation = outgoing_probe_generation + 1
                        snapshot.root_changed = 1
                    end
                    outgoing_probe_root = root_first
                    snapshot.generation = outgoing_probe_generation
                end
            end
            snapshot.read_failures = failures
            snapshot.read_bytes = observer_read_budget
            return snapshot
        end,
    }
    local outgoing_lifecycle_ok, outgoing_lifecycle = pcall(
        outgoing_probe_module.new, outgoing_probe_adapter)
    if not outgoing_lifecycle_ok then outgoing_lifecycle = nil end
'''
    return source.replace("\n", newline.decode("ascii")).encode("utf-8")


def _diagnostic_entry() -> bytes:
    """生成只含扫描核心的入口，并只启用有界代码窗口探针。"""
    entry_path = builder.ROOT / "game" / "chat_probe.lua"
    core_path = builder.ROOT / "game" / "chat_probe_core.lua"
    module_path = builder.ROOT / "game" / "chat_outgoing_probe.lua"
    entry = builder.entry_source(entry_path.read_bytes(), core_path.read_bytes())
    module_source = module_path.read_bytes()
    if module_source.startswith((b"\xef\xbb\xbf", b"\x1b")) or b"\0" in module_source:
        raise ValueError("发送生命周期探针必须是无 BOM、无字节码标记的 Lua 文本")
    module_source.decode("utf-8")
    if len(module_source) > builder.MAX_SOURCE_BYTES:
        raise ValueError("发送生命周期探针超过构建大小上限")
    if entry.count(OUTGOING_PROBE_FLAG) != 1:
        raise ValueError("入口必须恰好包含一个默认关闭的发送代码窗口探针开关")
    for flag in DISABLED_FLAGS:
        if entry.count(flag) != 1:
            raise ValueError("入口必须恰好包含一个默认关闭的观察、显示或翻译开关")
    enabled = OUTGOING_PROBE_FLAG.replace(b"= false", b"= true")
    if enabled == OUTGOING_PROBE_FLAG:
        raise ValueError("发送代码窗口探针开关格式无效")
    entry = entry.replace(OUTGOING_PROBE_FLAG, enabled, 1)
    if any(flag.replace(b"= false", b"= true") in entry for flag in DISABLED_FLAGS):
        raise ValueError("诊断入口意外启用了观察、显示或翻译功能")
    if entry.count(RETENTION_CALL) != 1:
        raise ValueError("诊断入口的报告保留调用标记必须恰好出现一次")
    entry = entry.replace(
        RETENTION_CALL,
        b"startup_report_retention.run, ffi, kernel, true, TRANSLATE_ENABLED",
        1,
    )
    newline = b"\r\n" if b"\r\n" in entry else b"\n"
    lifecycle_return_line = LIFECYCLE_RETURN + newline
    if entry.count(lifecycle_return_line) != 1 or entry.count(INITIALIZE_CALL) != 1:
        raise ValueError("诊断入口的生命周期注入标记必须各出现一次")
    module_block = (
        b"    local outgoing_probe_module = (function()" + newline
        + module_source.rstrip(b"\r\n").replace(b"\r\n", b"\n").replace(b"\n", newline)
        + newline + b"    end)()" + newline
    )
    entry = entry.replace(
        lifecycle_return_line,
        module_block + _lifecycle_adapter_source(newline)
        + b"    return one_probe_step, outgoing_pump_step, outgoing_lifecycle" + newline,
        1,
    )
    entry = entry.replace(
        INITIALIZE_CALL,
        b"local setup_ok, probe_step, outgoing_pump_step, outgoing_lifecycle = pcall(initialize_probe)",
        1,
    )
    entry += (
        b"\nif setup_ok and type(outgoing_lifecycle) == \"table\" "
        b"and type(outgoing_lifecycle.wrap_update) == \"function\" then\n"
        b"    local lifecycle_wrap_ok, lifecycle_wrapped = pcall("
        b"outgoing_lifecycle.wrap_update, _G.update)\n"
        b"    if lifecycle_wrap_ok and type(lifecycle_wrapped) == \"function\" then\n"
        b"        _G.update = lifecycle_wrapped\n"
        b"    end\n"
        b"end\n"
    )
    if len(entry) > builder.MAX_SOURCE_BYTES:
        raise ValueError("嵌入生命周期探针后的 Lua 源文件超过构建大小上限")
    return entry


def build_artifact(
    output: Path | str | None = None,
    *,
    loader_zip: Path | str | None = None,
    menu_zip: Path | str | None = None,
    now: datetime | None = None,
) -> Path:
    """用固定上游资源生成同 GUID 的临时诊断 ZIP，不覆盖已有文件。"""
    output_path = builder._select_output_path(output, now=now)
    if output is None and os.path.lexists(output_path):
        raise ValueError(f"默认诊断包已存在，拒绝覆盖：{output_path.name}")
    input_paths = (
        builder.ROOT / "game" / "chat_probe.lua",
        builder.ROOT / "game" / "chat_probe_core.lua",
        builder.ROOT / "game" / "chat_outgoing_probe.lua",
        builder.PROJECT_LICENSE,
        builder.STANDALONE_LICENSE,
        builder.MOD_OPTIONS_MENU_LICENSE,
        builder.MOD_OPTIONS_MENU_SOURCE,
        builder.SHARED_LOADER_ZIP if loader_zip is None else Path(loader_zip),
        builder.MOD_OPTIONS_MENU_ZIP if menu_zip is None else Path(menu_zip),
    )
    if any(builder._same_path(output_path, path) for path in input_paths):
        raise ValueError("输出不能覆盖诊断包构建输入")

    files = builder.addon_files(
        _diagnostic_entry(),
        loader_zip=loader_zip,
        menu_zip=menu_zip,
    )
    if len(files) != 11:
        raise ValueError("诊断包资源清单与固定 11 个成员不匹配")
    try:
        manifest = json.loads(files["manifest.json"].decode("utf-8"))
    except (KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("诊断包 manifest 无法安全读取") from error
    if not isinstance(manifest, dict) or manifest.get("Guid") != builder.ADDON_GUID:
        raise ValueError("诊断包必须保留现有模组 GUID")
    manifest["Name"] = "HD2 Chat Outgoing Code Probe"
    manifest["Description"] = PROBE_DESCRIPTION
    for option in manifest.get("Options", []):
        if isinstance(option, dict):
            option["Name"] = "HD2 Chat Outgoing Code Probe"
            option["Description"] = PROBE_DESCRIPTION
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "x", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, content)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        help="显式指定 ZIP 路径；CLI 文件名须为 HD2ChatTranslateYYYYMMDDHHMMSS.zip。",
    )
    parser.add_argument("--loader-zip", type=Path, help="固定 Bingus Shared Loader v18 来源 ZIP。")
    parser.add_argument("--menu-zip", type=Path, help="固定 ModOptionsMenu v1.2 来源 ZIP。")
    args = parser.parse_args()
    if args.output is not None and not builder.is_delivery_filename(args.output.name):
        parser.error("CLI 诊断文件名必须为 HD2ChatTranslateYYYYMMDDHHMMSS.zip，时间使用北京时间")
    try:
        result = build_artifact(args.output, loader_zip=args.loader_zip, menu_zip=args.menu_zip)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"已构建临时只读发送代码窗口诊断 ZIP：{result}")


if __name__ == "__main__":
    main()
