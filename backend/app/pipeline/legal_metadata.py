"""法条入库的文档级预处理与 chunk 级元数据抽取。

链路位置（顺序很重要）：

1. **切分之前**——``analyze_legal_document``：目录剥离 + 法名解析。
   目录区一旦被切成 chunk 就再也剥不掉了，所以必须前置。
2. **切分与护栏之后**——``LegalMetadataExtractor.extract``：逐 chunk 抽条号、
   清洗章节路径。放在护栏之后是因为 ``enforce_size_limits`` 会再切超长父块，
   在它之前抽条号会让被拆出的父块丢失归属。
3. **写索引时**——``build_content_prefix``：给 Milvus 的 ``content`` 生成
   ``[法名 第N条]`` 前缀，让「民法典第146条」能在词法层命中「第一百四十六条」。

设计依据见 ``docs/legal-recall-implementation-plan.md`` 的 D10 / D11 / D12 / D13 / D14。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.pipeline.legal_terms import (
    ARTICLE_LINE_PATTERN,
    ARTICLE_REFERENCE_PATTERN,
    chinese_to_int,
)

# 独立成行的「目录」标记。全角空格（U+3000）常见于中文文档的排版。
_TOC_LINE = re.compile(r"^[^\S\n]*目[\s\u3000]*录[^\S\n]*$", re.MULTILINE)

# 日期行：「（2020年5月28日…通过）」。全角与半角括号都要接受——
# 语料 345 份里 344 份用全角、1 份（刑法修正案 19991225）用半角。
_DATE_IN_PARENS = re.compile(
    r"[（(]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)

# 章节路径清洗：只保留「第X编 / 第X分编 / 第X章 / 第X节」。
# 必须含「分编」——《民法典》的层级是「编 → 分编 → 章 → 节」。
_KEEP_CHAPTER = re.compile(
    r"^第[零〇一二三四五六七八九十百千万\d]+(?:分?编|章|节)"
)

# 发布机关尾部的动词，用于从括注里剥出机关名。
_TRAILING_VERB = re.compile(r"(通过|公布|修订|修正|施行|废止)$")


@dataclass
class LegalDocumentHeader:
    """文档级头信息。字段可空——解析不出就留空，不猜。"""

    law_name: str | None = None
    issuing_authority: str | None = None
    publish_date: str | None = None  # ISO: YYYY-MM-DD
    toc_stripped: bool = False
    has_article_structure: bool = True


def strip_toc(text: str) -> tuple[str, bool]:
    """剥离目录区；返回 ``(处理后文本, 是否剥离过)``。

    **标记驱动，绝不按位置猜。** 只有检测到独立成行的「目录」标记时才动手，
    剥离范围是该行到第一个行首 ``第X条`` 之前。没有标记就原样返回。

    为什么必须这样：语料里有 37 份文档（修正案 / 决定 / 规定）**完全没有**
    ``第X条`` 结构，正文从第一行就开始。若按「丢弃第一条之前的全部内容」
    这种朴素规则处理，它们会被整篇删空。而「有目录标记」与「无 ``第X条``」
    的交集为空，因此本规则可证明不会误伤它们。
    """
    if not text:
        return text, False

    marker = _TOC_LINE.search(text)
    if marker is None:
        return text, False

    first_article = ARTICLE_LINE_PATTERN.search(text, marker.end())
    if first_article is None:
        # 有目录标记却没有条文结构——防御性放弃剥离，宁可多留噪声也不删正文。
        return text, False

    head = text[: marker.start()].rstrip()
    tail = text[first_article.start():].lstrip()
    stripped = f"{head}\n{tail}" if head else tail
    return stripped, True


def parse_legal_header(text: str) -> LegalDocumentHeader:
    """解析文档头：法名 + 发布机关 + 发布日期。

    **法名 = 首行到「日期行」之前的全部行拼接**，不是取首行。语料 345 份里
    有 34 份（9.9%）标题跨两到三行，例如：

        全国人民代表大会常务委员会关于
        《中华人民共和国国籍法》在香港特别行政区实施的几个问题的解释

    取首行会得到「全国人民代表大会常务委员会关于」——一个看起来正常、
    不会报错、却毫无意义的法名。

    日期行是天然且普遍存在的边界：345/345 份文档都能解析出
    「YYYY年M月D日」，所以这条规则对所有文件都有定义。
    """
    header = LegalDocumentHeader()
    if not text:
        return header

    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    header.has_article_structure = ARTICLE_LINE_PATTERN.search(text) is not None

    title_parts: list[str] = []
    date_line: str | None = None
    for line in lines[:12]:
        match = _DATE_IN_PARENS.search(line)
        if match:
            date_line = line
            year, month, day = match.group(1), match.group(2), match.group(3)
            header.publish_date = f"{year}-{int(month):02d}-{int(day):02d}"
            break
        # 遇到正文起始标记就停止收集标题，避免把正文首行当标题。
        if ARTICLE_LINE_PATTERN.match(line) or re.match(
            r"^[零〇一二三四五六七八九十]+、", line
        ):
            break
        title_parts.append(line)

    if date_line is not None:
        inner = date_line.strip().strip("（）()").strip()
        authority = _DATE_IN_PARENS.sub("", inner, count=1).strip()
        authority = _TRAILING_VERB.sub("", authority).strip()
        # 去掉日期后残留的前导分隔符。
        authority = authority.strip("　 、,，")
        header.issuing_authority = authority or None

    law_name = "".join(title_parts).strip()
    header.law_name = law_name or (lines[0] if lines else None)
    return header


@dataclass
class LegalDocumentAnalysis:
    """文档级预处理的产出：清洗后的文本 + 头信息。"""

    text: str
    header: LegalDocumentHeader


def analyze_legal_document(text: str) -> LegalDocumentAnalysis:
    """切分前的一次性预处理：目录剥离 → 头部解析。"""
    stripped, toc_stripped = strip_toc(text)
    header = parse_legal_header(stripped)
    header.toc_stripped = toc_stripped
    return LegalDocumentAnalysis(text=stripped, header=header)


def clean_section_path(section_path: list[str] | None) -> list[str]:
    """只保留「第X编 / 第X分编 / 第X章 / 第X节」，丢弃其余元素。

    上游 ``MetadataExtractor`` 把行首「（一）…」也当作三级标题，唯一的护栏是
    「标题长度 ≤ 40 字」；而语料里 11,063 行此类行中有 8,950 行（81%）短到能
    通过该护栏。不过滤的话，条文里的「项」内容会混进章节字段。

    不改 ``metadata.py``——那是上游的公共行为，改动面大且会影响 Artoo 那边。
    """
    if not section_path:
        return []
    return [p for p in section_path if p and _KEEP_CHAPTER.match(p.strip())]


def extract_article_number(parent_text: str) -> tuple[int | None, str | None]:
    """从父块首部提取条号；返回 ``(整数条号, 原文条号)``。"""
    if not parent_text:
        return None, None
    match = ARTICLE_LINE_PATTERN.match(parent_text.strip())
    if not match:
        return None, None
    raw = match.group(1)
    label = f"第{raw}条"
    return chinese_to_int(raw), label


def extract_referenced_articles(text: str) -> list[str]:
    """收集文本中出现的「第X条」引用（去重、保持出现顺序）。

    用于 D11 的增强：修正案 / 决定类文档的条目正文几乎都会点名它改的是哪一条，
    把这些条号写进 BM25 前缀，查「刑法第一百六十二条」时就能召回改过该条的
    修正案。
    """
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in ARTICLE_REFERENCE_PATTERN.finditer(text):
        token = m.group(0)
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def build_content_prefix(legal: dict | None, fallback_title: str = "") -> str:
    """构造写入 Milvus ``content`` 的前缀：``[法名 第N条]``。

    ``N`` 用**阿拉伯数字**，这样「民法典第146条」能在 BM25 上命中正文写作
    「第一百四十六条」的 chunk。取不到法名时回退为旧行为 ``[文件名]``。
    """
    legal = legal or {}
    law_name = (legal.get("law_name") or "").strip()
    article_number = legal.get("article_number")

    parts: list[str] = []
    if law_name:
        parts.append(law_name)
    if isinstance(article_number, int):
        parts.append(f"第{article_number}条")
    if not parts and fallback_title:
        parts.append(fallback_title)
    if not parts:
        return ""

    # 无条号但有条文引用时一并带上，覆盖修正案 / 决定类文档。
    if not isinstance(article_number, int):
        for ref in (legal.get("referenced_articles") or [])[:5]:
            parts.append(ref)

    return "[" + " ".join(parts) + "]"


class LegalMetadataExtractor:
    """逐 child chunk 产出一份法条元数据字典。

    与 ``MetadataExtractor`` 平行而非替代：后者产出通用字段（章节路径、页码、
    元素类型），本类在其产出之上叠加法条字段。两者都返回与 ``child_chunks``
    等长的列表，索引一一对应。
    """

    def extract(
        self,
        *,
        child_chunks: list[str],
        parent_chunks: list[str],
        parent_child_map: dict[int, list[int]],
        section_paths: list[list[str]] | None = None,
        analysis: LegalDocumentAnalysis | None = None,
    ) -> list[dict]:
        """产出与 ``child_chunks`` 等长的法条元数据列表。"""
        header = analysis.header if analysis is not None else LegalDocumentHeader()
        has_articles = header.has_article_structure

        child_to_parent: dict[int, int] = {}
        for parent_idx, children in (parent_child_map or {}).items():
            for child_idx in children:
                child_to_parent[child_idx] = parent_idx

        per_parent: dict[int, tuple[int | None, str | None, list[str]]] = {}
        for parent_idx, parent_text in enumerate(parent_chunks or []):
            number, label = extract_article_number(parent_text)
            refs = [] if number is not None else extract_referenced_articles(parent_text)
            per_parent[parent_idx] = (number, label, refs)

        paths = section_paths or []
        results: list[dict] = []
        for child_idx in range(len(child_chunks or [])):
            parent_idx = child_to_parent.get(child_idx)
            number, label, refs = per_parent.get(parent_idx, (None, None, []))
            path = paths[child_idx] if child_idx < len(paths) else []

            results.append(
                {
                    "law_name": header.law_name,
                    "article_number": number,
                    "article_label": label,
                    "referenced_articles": refs,
                    "chapter": " / ".join(clean_section_path(path)) or None,
                    "issuing_authority": header.issuing_authority,
                    "publish_date": header.publish_date,
                    "has_toc": header.toc_stripped,
                    "extraction_method": "rule",
                    "confidence": self._confidence(
                        header, number, has_articles
                    ),
                }
            )
        return results

    @staticmethod
    def _confidence(
        header: LegalDocumentHeader,
        article_number: int | None,
        has_articles: bool,
    ) -> float:
        """规则置信度：字段越全越高。

        无条文结构的文档（``has_articles=False``）条号为空是**预期**的，
        因此不因此扣分。
        """
        score = 0.0
        if header.law_name:
            score += 0.5
        if header.publish_date:
            score += 0.2
        if header.issuing_authority:
            score += 0.1
        if article_number is not None or not has_articles:
            score += 0.2
        return round(score, 2)
