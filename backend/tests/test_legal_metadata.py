"""法条入库结构化（Phase 1）单测。

覆盖设计文档 D10～D14 的每条规则。fixture 文本取自真实语料
（监管目录 `法律法规/法律/`），其中《刑法修正案》19991225 一次覆盖三个难点：
条目跨行、条目内嵌「（一）」子项、首个条目之前有引语。
"""

from __future__ import annotations

# 导入 app.pipeline.* 会连带触发 app.config.get_settings()，缺少 JWT_SECRET 时
# fail-fast；新克隆没有 backend/.env，因此这里在导入前补一份仅测试用的默认值。
import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

from app.pipeline.chunkers.laws import LawsChunker
from app.pipeline.legal_metadata import (
    LegalMetadataExtractor,
    analyze_legal_document,
    build_content_prefix,
    clean_section_path,
    extract_article_number,
    extract_referenced_articles,
    parse_legal_header,
    strip_toc,
)
from app.pipeline.legal_terms import chinese_to_int


# ── fixture：有目录的法规（《民法典》开头片段） ─────────────────────────────

MINFADIAN_WITH_TOC = """中华人民共和国民法典
（2020年5月28日第十三届全国人民代表大会第三次会议通过）
目　　录
第一编　总　　则
第一章　基本规定
第二章　自然人
第九章　诉讼时效
第一编　总　　则
第一章　基本规定
第一条　为了保护民事主体的合法权益，调整民事关系，维护社会和经济秩序，适应中国特色社会主义发展要求，弘扬社会主义核心价值观，根据宪法，制定本法。
第二条　民法调整平等主体的自然人、法人和非法人组织之间的人身关系和财产关系。
"""

# ── fixture：标题跨行的解释（两个标题行 + 日期行） ──────────────────────────

MULTILINE_TITLE = """全国人民代表大会常务委员会关于
《中华人民共和国国籍法》在香港特别行政区实施的几个问题的解释
（1996年5月15日第八届全国人民代表大会常务委员会第十九次会议通过）
根据《中华人民共和国香港特别行政区基本法》第十八条和附件三的规定，《中华人民共和国国籍法》自1997年7月1日起在香港特别行政区实施。
一、凡具有中国血统的香港居民，本人出生在中国领土（含香港）者，都是中国公民。
"""

# ── fixture：无条文结构、条目跨行、含嵌套项（《刑法修正案》19991225） ────────

XINGFA_AMENDMENT = """中华人民共和国刑法修正案
(1999年12月25日第九届全国人民代表大会常务委员会第十三次会议通过)
为了惩治破坏社会主义市场经济秩序的犯罪，对刑法作如下补充修改：
一、第一百六十二条后增加一条，作为第一百六十二条之一：“隐匿或者故意销毁依法应当保存的会计凭证，情节严重的，处五年以下有期徒刑或者拘役。
“单位犯前款罪的，对单位判处罚金。”
二、将刑法第一百六十八条修改为：“国有公司、企业的工作人员，由于严重不负责任或者滥用职权，造成国有公司、企业破产或者严重损失，处三年以下有期徒刑或者拘役。
三、将刑法第一百八十二条修改为：“有下列情形之一，操纵证券、期货交易价格，情节严重的：
（一）单独或者合谋，集中资金优势、持股或者持仓优势联合或者连续买卖的；
（二）与他人串通，以事先约定的时间、价格和方式相互进行证券、期货交易的；
四、本修正案自公布之日起施行。
"""


class TestChineseToInt:
    def test_simple_units(self) -> None:
        assert chinese_to_int("一") == 1
        assert chinese_to_int("十") == 10
        assert chinese_to_int("十五") == 15
        assert chinese_to_int("二十四") == 24

    def test_hundreds_and_thousands(self) -> None:
        assert chinese_to_int("百") == 100
        assert chinese_to_int("一百四十六") == 146
        assert chinese_to_int("三百八十四") == 384
        assert chinese_to_int("一千零一") == 1001

    def test_wan(self) -> None:
        assert chinese_to_int("一万") == 10000
        # 覆盖到万位——方案要求的转换范围上限
        assert chinese_to_int("一万二千三百四十五") == 12345

    def test_arabic_passthrough(self) -> None:
        assert chinese_to_int("146") == 146

    def test_unsupported_returns_none(self) -> None:
        assert chinese_to_int("") is None
        assert chinese_to_int("壹佰") is None
        assert chinese_to_int("第x条") is None


class TestStripToc:
    def test_strips_when_marker_present(self) -> None:
        out, stripped = strip_toc(MINFADIAN_WITH_TOC)
        assert stripped is True
        assert "第九章　诉讼时效" not in out  # 目录行已消失
        assert "第一条　为了保护民事主体" in out  # 正文保留
        assert out.startswith("中华人民共和国民法典")  # 标题保留

    def test_no_marker_returns_text_unchanged(self) -> None:
        text = "中华人民共和国国籍法\n（1980年9月10日…通过）\n第一条　正文。"
        out, stripped = strip_toc(text)
        assert stripped is False
        assert out == text

    def test_article_less_document_is_never_touched(self) -> None:
        """关键安全性质：无条文结构文档即便出现「目录」字样也不剥离。"""
        text = "某某决定\n（2000年1月1日…通过）\n一、正文第一项。\n二、正文第二项。"
        out, stripped = strip_toc(text)
        assert stripped is False
        assert out == text


class TestParseLegalHeader:
    def test_single_line_title(self) -> None:
        header = parse_legal_header(MINFADIAN_WITH_TOC)
        assert header.law_name == "中华人民共和国民法典"
        assert header.publish_date == "2020-05-28"
        assert "第十三届全国人民代表大会" in (header.issuing_authority or "")

    def test_multiline_title_is_joined(self) -> None:
        """34/345 份文档标题跨行；取首行会得到无意义的法名。"""
        header = parse_legal_header(MULTILINE_TITLE)
        assert header.law_name is not None
        assert header.law_name.startswith("全国人民代表大会常务委员会关于")
        assert "在香港特别行政区实施的几个问题的解释" in header.law_name
        assert header.publish_date == "1996-05-15"

    def test_halfwidth_parentheses_accepted(self) -> None:
        """语料 345 份中仅 1 份用半角括号，必须同时接受。"""
        header = parse_legal_header(XINGFA_AMENDMENT)
        assert header.publish_date == "1999-12-25"
        assert header.law_name == "中华人民共和国刑法修正案"
        assert header.has_article_structure is False


class TestArticleExtraction:
    def test_extracts_number_and_label(self) -> None:
        assert extract_article_number("第一百四十六条　具备下列条件…") == (
            146,
            "第一百四十六条",
        )

    def test_returns_none_for_item_style_text(self) -> None:
        assert extract_article_number("一、第一百六十二条后增加一条…") == (None, None)

    def test_referenced_articles_are_collected(self) -> None:
        refs = extract_referenced_articles(
            "一、第一百六十二条后增加一条，作为第一百六十二条之一。"
        )
        assert "第一百六十二条" in refs
        assert "第一百六十二条之一" not in refs  # 只认「第X条」形态


class TestSectionPathCleaning:
    def test_keeps_only_structural_levels(self) -> None:
        raw = ["第一编　总　　则", "第一分编　通则", "第一章　基本规定", "（一）某项内容"]
        assert clean_section_path(raw) == [
            "第一编　总　　则",
            "第一分编　通则",
            "第一章　基本规定",
        ]

    def test_fenbian_is_kept(self) -> None:
        """《民法典》层级含「分编」；清洗规则漏掉它会误删这一级。"""
        assert clean_section_path(["第一分编　通则"]) == ["第一分编　通则"]

    def test_empty_input(self) -> None:
        assert clean_section_path(None) == []
        assert clean_section_path([]) == []


class TestLawsChunkerFallback:
    def test_article_document_uses_article_boundaries(self) -> None:
        # 必须先用 analyze_legal_document 剥掉目录再切——这正是 pipeline 的顺序。
        analysis = analyze_legal_document(MINFADIAN_WITH_TOC)
        result = LawsChunker().chunk(analysis.text)
        # 三个父块 = [标题+日期（第一条之前的引语段）, 第一条, 第二条]。
        # 标题段自成父块是 _split_by_pattern 的既有行为，保留即可。
        assert len(result.parent_chunks) == 3
        assert all("第九章　诉讼时效" not in p for p in result.parent_chunks)
        assert result.parent_chunks[0].strip() == (
            "中华人民共和国民法典\n（2020年5月28日第十三届全国人民代表大会第三次会议通过）"
        )
        joined = "".join(result.parent_chunks)
        assert "第一条　为了保护民事主体" in joined

    def test_toc_region_becomes_its_own_parent_if_not_stripped_first(self) -> None:
        """反向验证：不先剥目录，目录区会自成一个父块——所以剥离必须前置。"""
        result = LawsChunker().chunk(MINFADIAN_WITH_TOC)
        assert len(result.parent_chunks) == 3
        assert "第九章　诉讼时效" in result.parent_chunks[0]

    def test_item_style_document_uses_item_boundaries(self) -> None:
        """无条文结构时退化为「一、」边界，条目不再被从中间切断。"""
        result = LawsChunker().chunk(XINGFA_AMENDMENT)
        parents = [p for p in result.parent_chunks if p.strip()]
        item_parents = [p for p in parents if p.lstrip().startswith("一、")]
        assert item_parents, "应至少切出一个以「一、」开头的父块"
        # 条目三与它内部的「（一）（二）」应落在同一个父块内（未被从中间切断）
        third = [p for p in parents if p.lstrip().startswith("三、")]
        assert third and "（一）单独或者合谋" in third[0]
        assert "（二）与他人串通" in third[0]

    def test_conditional_fallback_is_not_applied_to_normal_statutes(self) -> None:
        """普通法律里的「一、」是条文内部的项，不能被提升为父块边界。"""
        text = (
            "中华人民共和国国籍法\n"
            "（1980年9月10日第五届全国人民代表大会第三次会议通过）\n"
            "第七条　外国人或无国籍人，愿意遵守中国宪法和法律，并具有下列条件之一的：\n"
            "一、中国人的近亲属；\n"
            "二、定居在中国的；\n"
            "第八条　申请加入中国国籍获得批准的，即取得中国国籍。\n"
        )
        result = LawsChunker().chunk(text)
        seventh = [p for p in result.parent_chunks if p.lstrip().startswith("第七条")]
        assert seventh, "第七条应作为一个父块"
        assert "一、中国人的近亲属" in seventh[0]
        assert "二、定居在中国的" in seventh[0]


class TestAnalyzeAndExtract:
    def test_end_to_end_metadata_for_article_document(self) -> None:
        analysis = analyze_legal_document(MINFADIAN_WITH_TOC)
        assert analysis.header.toc_stripped is True
        assert analysis.header.law_name == "中华人民共和国民法典"

        chunker_result = LawsChunker().chunk(analysis.text)
        child_to_parent = {}
        for p_idx, children in chunker_result.parent_child_map.items():
            for c_idx in children:
                child_to_parent[c_idx] = p_idx
        section_paths = [[] for _ in chunker_result.child_chunks]
        meta = LegalMetadataExtractor().extract(
            child_chunks=chunker_result.child_chunks,
            parent_chunks=chunker_result.parent_chunks,
            parent_child_map=chunker_result.parent_child_map,
            section_paths=section_paths,
            analysis=analysis,
        )
        assert len(meta) == len(chunker_result.child_chunks)
        numbers = {m["article_number"] for m in meta}
        assert 1 in numbers and 2 in numbers
        assert all(m["law_name"] == "中华人民共和国民法典" for m in meta)
        assert all(m["extraction_method"] == "rule" for m in meta)

    def test_article_less_document_keeps_null_number(self) -> None:
        analysis = analyze_legal_document(XINGFA_AMENDMENT)
        chunker_result = LawsChunker().chunk(analysis.text)
        meta = LegalMetadataExtractor().extract(
            child_chunks=chunker_result.child_chunks,
            parent_chunks=chunker_result.parent_chunks,
            parent_child_map=chunker_result.parent_child_map,
            section_paths=[[] for _ in chunker_result.child_chunks],
            analysis=analysis,
        )
        assert all(m["article_number"] is None for m in meta)
        assert all(m["law_name"] == "中华人民共和国刑法修正案" for m in meta)
        # 无条号是预期结果，不应因此拉低置信度
        assert all(m["confidence"] >= 0.8 for m in meta)


class TestContentPrefix:
    def test_builds_law_name_and_arabic_article(self) -> None:
        prefix = build_content_prefix(
            {"law_name": "中华人民共和国民法典", "article_number": 146}
        )
        assert prefix == "[中华人民共和国民法典 第146条]"

    def test_falls_back_to_filename(self) -> None:
        assert build_content_prefix({}, "中华人民共和国民法典_20200528") == (
            "[中华人民共和国民法典_20200528]"
        )

    def test_includes_referenced_articles_when_no_article_number(self) -> None:
        prefix = build_content_prefix(
            {
                "law_name": "中华人民共和国刑法修正案",
                "article_number": None,
                "referenced_articles": ["第一百六十二条"],
            }
        )
        assert prefix == "[中华人民共和国刑法修正案 第一百六十二条]"
