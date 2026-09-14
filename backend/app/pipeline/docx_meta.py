"""从 docx 内嵌属性读取文档级权威元数据。

链路位置：``DocumentPipeline.process_to_vectors`` 里 Load 之后、法条文档级预处理
（``legal_metadata.analyze_legal_document``）之前。

为什么不用 python-docx：``DocxLoader`` 已经在用 python-docx 取正文，但它只暴露
``core_properties``（对应 ``docProps/core.xml`` 的 5 个字段），没有自定义属性 API
——而法条语料的业务字段全在 ``docProps/custom.xml``（实测 20 个）。因此这里直接用
标准库解包 OOXML，只读 ``docProps`` 两个部件，不解析 ``word/document.xml``。

三条设计约束（都来自真实语料实测）：

- **任何异常都返回 ``None``**：非 zip、缺部件、XML 损坏一律降级到正文解析。
  入库链路绝不能让「元数据读不到」变成「文档处理失败」。
- **不猜值**：日期不是 ``YYYY-MM-DD`` 就不采信；``validity_status`` 只做整数转换，
  不推断枚举含义（数据源的枚举定义尚未确认）。
- **前向兼容**：未知键收进 ``extra``，上游加字段不需要改代码。

读取层一次读出 8 个字段，消费侧分期接入：``title`` / ``authority`` /
``publish_date`` 供 ``legal_metadata`` 覆盖正文解析结果，其余 5 个字段随
``chunk_metadata`` 的字段字典变更一并接入。
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_CORE_PART = "docProps/core.xml"
_CUSTOM_PART = "docProps/custom.xml"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ``core.xml`` 的 ``<dc:description>`` 里按约定镜像了日期摘要，形如
# 「publish_date=2021-11-23; effective_date=2022-01-01」。
_DESCRIPTION_FIELD = re.compile(
    r"(publish_date|effective_date)\s*=\s*(\d{4}-\d{2}-\d{2})"
)

# 已知业务字段。不在此集合内的键收进 ``DocxProps.extra``。
_KNOWN_FIELDS = frozenset(
    {
        "title",
        "authority",
        "publish_date",
        "effective_date",
        "validity_status",
        "law_type",
        "external_id",
        "source_code",
    }
)


@dataclass(frozen=True)
class DocxProps:
    """docx 内嵌的文档级元数据；取不到的字段为 ``None``，不猜。"""

    title: str | None = None
    authority: str | None = None
    publish_date: str | None = None
    effective_date: str | None = None
    validity_status: int | None = None
    law_type: str | None = None
    external_id: str | None = None
    source_code: str | None = None
    # 实际读到的部件：custom / core / custom+core。仅供排查。
    origin: str = ""
    # 未知键（含 core.xml 里的非镜像字段的归一化结果）。
    extra: dict[str, str] = field(default_factory=dict)


def extract_docx_props(file_path: str) -> DocxProps | None:
    """读 docx 内嵌属性；读不到返回 ``None``。

    只解包 ``docProps/core.xml`` 与 ``docProps/custom.xml``。文件不存在、不是 zip、
    两个部件都缺、XML 损坏等任何异常都返回 ``None``，由调用方回退到正文解析。

    ``custom.xml`` 是权威来源，``core.xml`` 只是镜像（本语料实测两者零漂移）；
    逐字段以后者兜底，覆盖「只有 core.xml」的第三方文档。

    Args:
        file_path: docx 文件路径。

    Returns:
        :class:`DocxProps`；无任何可用字段时返回 ``None``。
    """
    try:
        with open(file_path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        logger.debug("读取 docx 文件失败，降级到正文解析: %s（%s）", file_path, exc)
        return None
    return extract_docx_props_from_bytes(data)


def extract_docx_props_from_bytes(data: bytes) -> DocxProps | None:
    """与 :func:`extract_docx_props` 同语义，输入是 docx 的字节内容。

    供批量工具直接从压缩包读条目时复用，免去为每份文件落一次临时文件。
    任何解析异常同样返回 ``None``。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            has_core = _CORE_PART in names
            has_custom = _CUSTOM_PART in names
            if not has_core and not has_custom:
                return None
            core_raw = _safe_read(_read_core, archive) if has_core else {}
            custom_raw = _safe_read(_read_custom, archive) if has_custom else {}
    except Exception as exc:  # noqa: BLE001 — 读取失败必须降级，不能中断入库
        logger.debug("解析 docx 内嵌属性失败，降级到正文解析: %s", exc)
        return None

    merged = _mirror_from_core(core_raw)
    merged.update(custom_raw)  # custom.xml 是权威，覆盖 core.xml 镜像
    if not merged:
        return None

    known = {k: v for k, v in merged.items() if k in _KNOWN_FIELDS}
    extra = {k: v for k, v in merged.items() if k not in _KNOWN_FIELDS}

    origin_parts: list[str] = []
    if custom_raw:
        origin_parts.append("custom")
    if core_raw:
        origin_parts.append("core")

    return DocxProps(
        title=_as_text(known.get("title")),
        authority=_as_text(known.get("authority")),
        publish_date=_as_iso_date(known.get("publish_date")),
        effective_date=_as_iso_date(known.get("effective_date")),
        validity_status=_as_int(known.get("validity_status")),
        law_type=_as_text(known.get("law_type")),
        external_id=_as_text(known.get("external_id")),
        source_code=_as_text(known.get("source_code")),
        origin="+".join(origin_parts),
        extra=extra,
    )


def _safe_read(reader, archive: zipfile.ZipFile) -> dict[str, str]:
    """解析单个部件；失败只丢该部件，另一个照常使用。"""
    try:
        return reader(archive)
    except Exception as exc:  # noqa: BLE001 — 部件损坏不应连带丢弃另一个部件
        logger.debug("docx 部件解析失败，已忽略该部件: %s", exc)
        return {}


def _read_custom(archive: zipfile.ZipFile) -> dict[str, str]:
    """读 ``docProps/custom.xml``，返回 ``属性名 -> 文本值``（空值键不收录）。

    值元素取 ``<property>`` 的任意子元素，不硬编码 ``vt:lpwstr``——同一部件里
    还可能出现 ``vt:filetime`` / ``vt:i4`` / ``vt:bool``，只认 ``lpwstr`` 会让
    这些字段静默变空。
    """
    root = ET.fromstring(archive.read(_CUSTOM_PART))
    values: dict[str, str] = {}
    for prop in root:
        if not prop.tag.endswith("property"):
            continue
        name = (prop.get("name") or "").strip()
        if not name:
            continue
        for child in prop:
            text = (child.text or "").strip()
            if text:
                values[name] = text
                break
    return values


def _read_core(archive: zipfile.ZipFile) -> dict[str, str]:
    """读 ``docProps/core.xml``，返回 ``本地名 -> 文本值``（空值键不收录）。"""
    root = ET.fromstring(archive.read(_CORE_PART))
    values: dict[str, str] = {}
    for element in root:
        text = (element.text or "").strip()
        if text:
            values[element.tag.split("}")[-1]] = text
    return values


def _mirror_from_core(core: dict[str, str]) -> dict[str, str]:
    """把 ``core.xml`` 的镜像字段映射成业务键名。"""
    mirrored: dict[str, str] = {}
    if core.get("title"):
        mirrored["title"] = core["title"]
    if core.get("creator"):
        mirrored["authority"] = core["creator"]
    if core.get("subject"):
        mirrored["law_type"] = core["subject"]
    for key, value in _DESCRIPTION_FIELD.findall(core.get("description", "")):
        mirrored[key] = value
    return mirrored


def _as_text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _as_iso_date(value: str | None) -> str | None:
    """只采信 ``YYYY-MM-DD``；其它形态留空，不猜。"""
    text = (value or "").strip()
    return text if _ISO_DATE.match(text) else None


def _as_int(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None
