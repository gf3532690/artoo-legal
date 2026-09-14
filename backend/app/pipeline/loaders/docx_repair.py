"""docx 包级别的自愈：修掉不自洽的声明，让 python-docx 能打开。

为什么需要它：实测语料（29,957 份）里有 51 份 python-docx 直接抛错，而它们的正文
完全完好，问题出在包的声明层——

- 44 份：``_rels/.rels`` 声明了 ``userCustomization/customUI.xml``，但该部件不在包里
  （全部集中于江西省的来源）。
- 7 份：主部件内容类型是 macro-enabled（``.docm`` 改名成 ``.docx``），python-docx
  在 ``Document()`` 入口就按内容类型拒绝。

两处都只影响包的元数据，正文 ``word/document.xml`` 不受影响。修复在**内存副本**上
进行，不触碰原文件；返回修复说明供日志与排障使用。
"""

from __future__ import annotations

import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile

_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

# python-docx 在 Document() 里要求主部件正好是这个内容类型。
_MACRO_MAIN_TYPE = "application/vnd.ms-word.document.macroEnabled.main+xml"
_WML_MAIN_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)

# 这些 scheme 是外部链接（法律数据库的 javascript: 跳转等），不指向包内部部件。
_EXTERNAL_SCHEMES = ("http:", "https:", "mailto:", "file:", "javascript:", "tbs:", "trsbro:")

ET.register_namespace("", _REL_NS)
ET.register_namespace("", _CT_NS)


def repair_docx_bytes(data: bytes) -> tuple[bytes, list[str]]:
    """修掉悬空的关联与内容类型声明；返回 ``(字节, 修复说明)``。

    无需修复时原样返回输入字节与空列表。任何解析异常都向上抛（调用方决定是否降级），
    因为"修不了"与"不用修"是两种不同的结论。
    """
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        names = source.namelist()
        parts = set(names)
        repairs: list[str] = []
        rewritten: list[tuple[str, bytes]] = []
        for name in names:
            blob = source.read(name)
            if name.endswith(".rels"):
                blob, notes = _repair_relationships(name, blob, parts)
            elif name == "[Content_Types].xml":
                blob, notes = _repair_content_types(blob, parts)
            else:
                notes = []
            repairs.extend(notes)
            rewritten.append((name, blob))

    if not repairs:
        return data, []

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for name, blob in rewritten:
            target.writestr(name, blob)
    return buffer.getvalue(), repairs


def _repair_relationships(
    name: str, blob: bytes, parts: set[str]
) -> tuple[bytes, list[str]]:
    """删除指向不存在部件的内部关联（外部链接一律保留）。"""
    root = ET.fromstring(blob)
    # ``word/_rels/document.xml.rels`` 的相对基准是 ``word/``；``_rels/.rels`` 是包根。
    base = posixpath.dirname(posixpath.dirname(name))
    removed: list[str] = []
    for relationship in list(root):
        if relationship.get("TargetMode") == "External":
            continue
        target = (relationship.get("Target") or "").strip()
        if not target or target.startswith(_EXTERNAL_SCHEMES):
            continue
        resolved = posixpath.normpath(posixpath.join(base, target.lstrip("/")))
        if resolved not in parts:
            root.remove(relationship)
            removed.append(target)
    if not removed:
        return blob, []
    return (
        ET.tostring(root, xml_declaration=True, encoding="UTF-8"),
        [f"{name}: 移除指向缺失部件的关联 {target}" for target in removed],
    )


def _repair_content_types(blob: bytes, parts: set[str]) -> tuple[bytes, list[str]]:
    """删除指向不存在部件的内容类型声明，并把 macro-enabled 主部件改回标准类型。"""
    root = ET.fromstring(blob)
    notes: list[str] = []
    for override in list(root):
        if not override.tag.endswith("Override"):
            continue
        part_name = (override.get("PartName") or "").lstrip("/")
        if part_name and part_name not in parts:
            root.remove(override)
            notes.append(f"[Content_Types].xml: 移除缺失部件的声明 {part_name}")
        elif override.get("ContentType") == _MACRO_MAIN_TYPE:
            override.set("ContentType", _WML_MAIN_TYPE)
            notes.append("[Content_Types].xml: 主部件内容类型 macro-enabled → 标准 docx")
    if not notes:
        return blob, []
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8"), notes
