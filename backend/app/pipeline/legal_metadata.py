"""法条入库的文档级预处理与 chunk 级元数据抽取。

链路位置（顺序很重要）：

1. **切分之前**——``analyze_legal_document``：目录剥离 + 法名解析。
  目录区一旦被切成 chunk 就再也剥不掉了，所以必须前置。
  文档级字段（法名 / 发布机关 / 发布日期）优先取 docx 内嵌属性
  （``pipeline/docx_meta.py``）——那是权威来源；正文头部解析只作逐字段兜底。
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

from app.pipeline.docx_meta import DocxProps
from app.pipeline.legal_region import resolve_region
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

# 施行日期：「本法自2021年1月1日起施行。」。取**最后一个**匹配——施行条款按立法
# 体例位于附则、即正文末尾；正文中段引用其它法律的施行日不构成本条文的生效日。
_EFFECTIVE_DATE = re.compile(
    r"自\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起\s*施行"
)

# 章节路径清洗：只保留「第X编 / 第X分编 / 第X章 / 第X节」。
# 必须含「分编」——《民法典》的层级是「编 → 分编 → 章 → 节」。
_KEEP_CHAPTER = re.compile(
    r"^第[零〇一二三四五六七八九十百千万\d]+(?:分?编|章|节)"
)

# 发布机关尾部的动词，用于从括注里剥出机关名。
_TRAILING_VERB = re.compile(r"(通过|公布|修订|修正|施行|废止)$")

# 效力状态：数据源字典的官方枚举（2026-09-15 从其字典确认）。落库与下发一律是原始整数，
# 标签只用于展示——文件列表的筛选与徽标、以及 ``GET /api/legal/validity-statuses``。
#
# 两个取值容易被读反，记在这里免得再错一次：``0`` 是**未标注**而不是「已废止」（它主要落在
# 「修改、废止的决定」这类文件上，源库本就不给这类文件标状态），``-1`` 才是**已失效**。
VALIDITY_STATUS_LABELS: dict[int, str] = {
    3: "现行有效",
    2: "已修改",
    1: "已废止",
    -1: "已失效",
    4: "尚未生效",
    0: "未标注",
}


def document_validity_status(legal_metadata: list[dict] | None) -> int | None:
    """从 per-chunk 法条元数据里取**文档级**效力状态。

    同一份文档的每个子块带的都是同一个文档级值（``validity_status`` 只来自 docx
    内嵌属性），因此按子块顺序取第一个非空即可。取不到就返回 ``None``——"读不到"
    与"这条法条失效了"是两回事，也与"未标注（``0``）"是两回事，不能混为一谈。
    """
    for meta in legal_metadata or ():
        if not isinstance(meta, dict):
            continue
        value = meta.get("validity_status")
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


@dataclass
class LegalDocumentHeader:
    """文档级头信息。字段可空——解析不出就留空，不猜。"""

    law_name: str | None = None
    issuing_authority: str | None = None
    publish_date: str | None = None  # ISO: YYYY-MM-DD
    toc_stripped: bool = False
    has_article_structure: bool = True
    # 文档级字段的来源：rule（正文解析）/ docx-props（内嵌属性）/ rule+docx（混合）。
    # 口径是三个身份字段（law_name / issuing_authority / publish_date）的命中情况；
    # 其余字段是否来自属性，直接看其值是否为空即可。
    meta_source: str = "rule"
    # ── 版本与溯源字段：只来自 docx 内嵌属性，正文里没有对应信息。──
    effective_date: str | None = None  # ISO: YYYY-MM-DD
    validity_status: int | None = None  # 原样保留；含义见 VALIDITY_STATUS_LABELS
    law_type: str | None = None
    external_id: str | None = None
    source_code: str | None = None
    # 地域（PRD 要求按省份/城市筛选地方性法规）。来源见 ``legal_region``：
    # 自带省名 → 正文批准机关 → 城市→省份映射表；推不出就留空。
    province: str | None = None
    city: str | None = None


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


def parse_legal_header(
    text: str, props: DocxProps | None = None
) -> LegalDocumentHeader:
    """解析文档头：法名 + 发布机关 + 发布日期。

    ``props`` 是 docx 内嵌属性，即文档级字段的**权威来源**；正文解析是启发式，
    只作逐字段兜底。``props`` 为 ``None`` 时行为与改造前完全一致。

    优先级与形态说明：

    - ``law_name``：``props.title`` → 正文「首行到日期行之前拼接」。
    - ``issuing_authority``：``props.authority`` → 正文括注剥动词。两个来源都是
      纯机关名（属性侧不含「通过 / 公布」等动词），形态一致可直接覆盖。
    - ``publish_date``：``props.publish_date`` → 正文括注。两者都是 ISO 形态。
    """
    header = _parse_header_by_rule(text)
    if props is None:
        return header
    return _apply_docx_props(header, props)


def _apply_docx_props(
    header: LegalDocumentHeader, props: DocxProps
) -> LegalDocumentHeader:
    """用 docx 内嵌属性逐字段覆盖正文解析结果。

    取不到的字段保持正文解析的值，因此这是净增：属性缺失、损坏或文件不是 docx
    时全链回退，不需要开关。``meta_source`` 记录实际命中情况供排查。
    """
    from_props: list[str] = []
    if props.title:
        header.law_name = props.title
        from_props.append("law_name")
    if props.authority:
        header.issuing_authority = props.authority
        from_props.append("issuing_authority")
    if props.publish_date:
        header.publish_date = props.publish_date
        from_props.append("publish_date")
    # 版本与溯源字段没有正文兜底（正文里不存在这些信息），有则覆盖、无则留空。
    if props.effective_date:
        header.effective_date = props.effective_date
    if props.validity_status is not None:
        header.validity_status = props.validity_status
    if props.law_type:
        header.law_type = props.law_type
    if props.external_id:
        header.external_id = props.external_id
    if props.source_code:
        header.source_code = props.source_code

    if len(from_props) == 3:
        header.meta_source = "docx-props"
    elif from_props:
        header.meta_source = "rule+docx"
    # 三个字段一个都没命中时保持 "rule"：没有可标记的来源。
    return header


def _parse_header_by_rule(text: str) -> LegalDocumentHeader:
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

    header.effective_date = _parse_effective_date(text)

    law_name = "".join(title_parts).strip()
    header.law_name = law_name or (lines[0] if lines else None)
    return header


def _parse_effective_date(text: str) -> str | None:
    """从正文抽施行日期，抽不到返回 ``None``。

    只在 docx 属性没有 ``effective_date`` 时才用得上（实测属性覆盖 75.2%，正文
    兜底再加约 2.7 个百分点），所以宁可少抽也不猜：只认「自…起施行」这种完整表述，
    并取最后一个匹配（附则在正文末尾）。
    """
    if not text:
        return None
    matches = _EFFECTIVE_DATE.findall(text)
    if not matches:
        return None
    year, month, day = matches[-1]
    return f"{year}-{int(month):02d}-{int(day):02d}"


@dataclass
class LegalDocumentAnalysis:
    """文档级预处理的产出：清洗后的文本 + 头信息。"""

    text: str
    header: LegalDocumentHeader


def analyze_legal_document(
    text: str, props: DocxProps | None = None
) -> LegalDocumentAnalysis:
    """切分前的一次性预处理：目录剥离 → 头部解析（docx 属性优先）。"""
    stripped, toc_stripped = strip_toc(text)
    header = parse_legal_header(stripped, props=props)
    header.toc_stripped = toc_stripped
    header.province, header.city = resolve_region(
        law_name=header.law_name,
        issuing_authority=header.issuing_authority,
        head_text=stripped[:2000],
    )
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


# 响应组装时剥离 content 前缀：``[法名 第N条]`` / ``[文件名]``。
# 限定 1~200 字符且不含换行，避免误伤以方括号开头的正文。
_CONTENT_PREFIX = re.compile(r"^\[[^\]\n]{1,200}\]\s*")


def strip_content_prefix(text: str) -> str:
    """移除 ``build_content_prefix`` 写入的前缀，恢复纯法条文本。

    ``content`` 是 Milvus 的索引字段，会原样出现在结果里：``direct`` 模式下是
    ``content`` 本身，``hybrid`` 模式下是 ``child_content``（``content`` 被父块
    内容替换，父块来自 PG、不带前缀）。前缀只服务于词法匹配，不应污染对外返回。
    """
    if not text:
        return text
    return _CONTENT_PREFIX.sub("", text, count=1)


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
                    # 版本与溯源字段：来自 docx 内嵌属性，正文里没有对应信息，
                    # 因此没有兜底路径，属性缺失时就是 None。
                    "effective_date": header.effective_date,
                    "validity_status": header.validity_status,
                    "law_type": header.law_type,
                    "external_id": header.external_id,
                    "source_code": header.source_code,
                    "province": header.province,
                    "city": header.city,
                    # 三个身份字段的来源（rule / docx-props / rule+docx）：与 has_toc
                    # 同属排查字段，全量核对时靠它区分"权威值"与"启发式值"。
                    "meta_source": header.meta_source,
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
