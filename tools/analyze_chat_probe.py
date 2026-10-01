"""校验并汇总只读聊天探针 manifest，可选导出候选字节供 objdump 使用。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

EXPECTED_SHA256 = "2e2c3b7c2500646dadd5f2b4c6e0504dbb7e7896139f64cddc0d1813c718f51e"
EXPECTED_DISK_SIZE = 15522408
SECTION_RVA = 4096
SECTION_SIZE = 34667155
SECTION_END = SECTION_RVA + SECTION_SIZE
MAX_MANIFEST_BYTES = 512 * 1024
MAX_CANDIDATES = 128
MAX_CANDIDATE_BYTES = 512
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """输入 manifest 结构或候选数据不可信。"""


def _integer(value: object, low: int, high: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ManifestError(f"{field} 数值越界或类型错误")
    return value


def _is_hex(text: object, *, maximum: int, field: str) -> str:
    if not isinstance(text, str) or len(text) > maximum or len(text) % 2:
        raise ManifestError(f"{field} 长度或类型错误")
    if text and not re.fullmatch(r"[0-9a-fA-F]+", text):
        raise ManifestError(f"{field} 不是十六进制")
    return text


def validate_manifest(value: object) -> dict:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        raise ManifestError("manifest 根对象或 schema_version 无效")
    status = value.get("status")
    if not isinstance(status, str) or status not in {
        "running",
        "scan_complete",
        "disk_hash_unreadable",
        "disk_size_mismatch",
        "hash_mismatch",
        "headers_unreadable",
        "invalid_pe",
        "pe_mismatch",
        "section_mismatch",
        "probe_error",
    }:
        raise ManifestError("status 无效")

    source = value.get("source_build")
    if not isinstance(source, dict):
        raise ManifestError("source_build 缺失")
    if source.get("module") != "game.dll":
        raise ManifestError("source_build.module 无效")
    if source.get("game_dll_sha256_expected") != EXPECTED_SHA256:
        raise ManifestError("manifest 目标构建 SHA-256 不匹配")
    observed_sha = source.get("game_dll_sha256_observed")
    if observed_sha is not None and (not isinstance(observed_sha, str) or not SHA_PATTERN.fullmatch(observed_sha)):
        raise ManifestError("observed SHA-256 格式错误")
    if status == "scan_complete" and observed_sha != EXPECTED_SHA256:
        raise ManifestError("完成扫描的构建 SHA-256 不匹配")
    if source.get("game_dll_size_expected") != EXPECTED_DISK_SIZE:
        raise ManifestError("manifest 目标构建大小不匹配")
    observed_size = source.get("game_dll_size_observed")
    if observed_size is not None:
        _integer(observed_size, 0, EXPECTED_DISK_SIZE + 1, "game_dll_size_observed")
    if status == "scan_complete" and observed_size != EXPECTED_DISK_SIZE:
        raise ManifestError("完成扫描的 game.dll 文件大小不匹配")
    for field, expected in (
        ("pe_timestamp_expected", 1790161983),
        ("size_of_image_expected", 74727424),
        ("code_section_rva", SECTION_RVA),
        ("code_section_size", SECTION_SIZE),
        ("code_section_flags", 0x60000020),
    ):
        if source.get(field) != expected:
            raise ManifestError(f"source_build.{field} 不匹配")
    if source.get("pe_timestamp_observed") is not None:
        _integer(source["pe_timestamp_observed"], 0, 0xFFFFFFFF, "pe_timestamp_observed")
    if source.get("size_of_image_observed") is not None:
        _integer(source["size_of_image_observed"], 1, 0xFFFFFFFF, "size_of_image_observed")
    if status == "scan_complete" and (
        source.get("pe_timestamp_observed") != 1790161983
        or source.get("size_of_image_observed") != 74727424
    ):
        raise ManifestError("完成扫描的 PE 标识不匹配")

    scan = value.get("scan")
    if not isinstance(scan, dict) or scan.get("section_rva") != SECTION_RVA or scan.get("section_size") != SECTION_SIZE:
        raise ManifestError("scan section bounds 不匹配")
    if (
        scan.get("max_code_read_bytes_per_step") != 16 * 1024
        or scan.get("disk_hash_and_pe_header_read_outside_code_budget") is not True
    ):
        raise ManifestError("scan 读取预算声明无效")
    for counter in (
        "scanned_bytes",
        "skipped_bytes",
        "skipped_regions",
        "read_failures",
        "unreadable_candidates",
        "candidate_bytes",
    ):
        _integer(scan.get(counter), 0, SECTION_SIZE * 2, f"scan.{counter}")
    if scan["candidate_bytes"] > 128 * 1024:
        raise ManifestError("candidate_bytes 超过上限")
    pattern_names = {
        "history_signature",
        "imm_le32_9590",
        "imm_le32_9594",
        "imm_le32_c418",
    }
    matches = scan.get("pattern_matches")
    if not isinstance(matches, dict) or set(matches) != pattern_names:
        raise ManifestError("pattern_matches 结构无效")
    for name, count in matches.items():
        _integer(count, 0, SECTION_SIZE, f"pattern_matches.{name}")
    truncation = scan.get("truncation")
    if not isinstance(truncation, dict) or set(truncation) != {"patterns", "total"}:
        raise ManifestError("truncation 结构无效")
    truncation_patterns = truncation["patterns"]
    if not isinstance(truncation_patterns, dict) or set(truncation_patterns) != pattern_names:
        raise ManifestError("truncation.patterns 结构无效")
    for name, count in truncation_patterns.items():
        _integer(count, 0, SECTION_SIZE, f"truncation.patterns.{name}")
    _integer(truncation["total"], 0, SECTION_SIZE, "truncation.total")

    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise ManifestError("candidates 必须是最多 128 项的数组")
    candidate_byte_sum = 0
    for index, candidate in enumerate(candidates):
        label = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            raise ManifestError(f"{label} 不是对象")
        rva = _integer(candidate.get("rva"), SECTION_RVA, SECTION_END - 1, f"{label}.rva")
        window_rva = _integer(candidate.get("window_rva"), SECTION_RVA, SECTION_END - 1, f"{label}.window_rva")
        length = _integer(candidate.get("byte_length"), 1, MAX_CANDIDATE_BYTES, f"{label}.byte_length")
        if rva < window_rva or rva >= window_rva + length or window_rva + length > SECTION_END:
            raise ManifestError(f"{label} 候选窗口越过代码节边界")
        raw_hex = _is_hex(candidate.get("bytes_hex"), maximum=MAX_CANDIDATE_BYTES * 2, field=f"{label}.bytes_hex")
        raw = bytes.fromhex(raw_hex)
        if len(raw) != length:
            raise ManifestError(f"{label} byte_length 与 bytes_hex 不一致")
        candidate_byte_sum += length
        digest = candidate.get("sha256")
        if not isinstance(digest, str) or not SHA_PATTERN.fullmatch(digest):
            raise ManifestError(f"{label}.sha256 格式错误")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ManifestError(f"{label}.sha256 校验失败")
        reason = candidate.get("reason")
        if not isinstance(reason, str) or len(reason) > 2048:
            raise ManifestError(f"{label}.reason 类型或长度错误")
        protection = candidate.get("page_protection")
        _integer(protection, 0x20, 0x80, f"{label}.page_protection")
        if protection not in {0x20, 0x40, 0x80}:
            raise ManifestError(f"{label}.page_protection 不是可读执行页")
        protections = candidate.get("page_protections")
        if not isinstance(protections, list) or not protections or len(protections) > 16:
            raise ManifestError(f"{label}.page_protections 数组无效")
        for page_index, page_protection in enumerate(protections):
            _integer(page_protection, 0x20, 0x80, f"{label}.page_protections[{page_index}]")
            if page_protection not in {0x20, 0x40, 0x80}:
                raise ManifestError(f"{label}.page_protections[{page_index}] 不是可读执行页")
    if scan["candidate_bytes"] != candidate_byte_sum:
        raise ManifestError("scan.candidate_bytes 与候选窗口字节总数不一致")

    known = value.get("known_signatures")
    if not isinstance(known, list) or len(known) > 16:
        raise ManifestError("known_signatures 数组无效")
    for index, signature in enumerate(known):
        label = f"known_signatures[{index}]"
        comparison = signature.get("comparison") if isinstance(signature, dict) else None
        if not isinstance(signature, dict) or not isinstance(comparison, str) or comparison not in {"true", "false", "unreadable"}:
            raise ManifestError(f"{label} 结构无效")
        _integer(signature.get("rva"), SECTION_RVA, SECTION_END - 1, f"{label}.rva")
        if not isinstance(signature.get("label"), str) or len(signature["label"]) > 128:
            raise ManifestError(f"{label}.label 无效")
        expected = _is_hex(signature.get("expected_hex"), maximum=256, field=f"{label}.expected_hex")
        observed = _is_hex(signature.get("observed_hex"), maximum=256, field=f"{label}.observed_hex")
        if not expected:
            raise ManifestError(f"{label}.expected_hex 不能为空")
        if signature["comparison"] == "true" and expected.lower() != observed.lower():
            raise ManifestError(f"{label} true 比较结果与字节不一致")
        if signature["comparison"] == "false" and (
            len(expected) != len(observed) or expected.lower() == observed.lower()
        ):
            raise ManifestError(f"{label} false 比较结果与字节不一致")
        if signature["comparison"] == "unreadable" and observed:
            raise ManifestError(f"{label} unreadable 结果不能包含读到的字节")
    if value.get("function_verification") != "none; RVAs and byte matches are research candidates only":
        raise ManifestError("manifest 不得声称已验证函数")
    return value


def load_manifest(path: Path | str) -> dict:
    manifest_path = Path(path)
    with manifest_path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("manifest 文件为空或超过 512 KiB")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ManifestError("manifest 不是有效 UTF-8 JSON") from exc
    return validate_manifest(value)


def export_candidates(manifest: dict, output_dir: Path | str) -> list[Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    exported = []
    for index, candidate in enumerate(manifest["candidates"]):
        filename = f"candidate-{index:03d}-rva-{candidate['rva']:08x}.bin"
        target = destination / filename
        target.write_bytes(bytes.fromhex(candidate["bytes_hex"]))
        exported.append(target)
    return exported


def summarize(manifest: dict) -> str:
    scan = manifest["scan"]
    observed = manifest["source_build"].get("game_dll_sha256_observed") or "unavailable"
    lines = [
        f"Status: {manifest['status']}",
        f"game.dll SHA-256: {observed}",
        f"Code bytes scanned: {scan['scanned_bytes']:,} / {SECTION_SIZE:,}",
        f"Bytes skipped: {scan['skipped_bytes']:,}; read failures: {scan['read_failures']}",
        f"Candidates: {len(manifest['candidates'])}; candidate bytes: {scan['candidate_bytes']:,}",
        "Function verification: none; every RVA and byte sequence remains a research candidate.",
    ]
    if manifest["candidates"]:
        lines.append("Candidate RVAs: " + ", ".join(f"0x{item['rva']:x}" for item in manifest["candidates"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--export-dir", type=Path, help="将校验后的候选窗口导出为 .bin")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        print(summarize(manifest))
        if args.export_dir is not None:
            exported = export_candidates(manifest, args.export_dir)
            print(f"Exported {len(exported)} candidate window(s) to {args.export_dir}")
        return 0
    except (OSError, ManifestError) as exc:
        print(f"Invalid manifest: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
