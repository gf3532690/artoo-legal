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
from app.pipeline.chunker import enforce_size_limits
from app.pipeline.docx_meta import DocxProps
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

# ── fixture：含施行条款的法规（附则在末尾，正文中段引用其它法律的施行日） ──────

STATUTE_WITH_EFFECTIVE_DATE = """中华人民共和国民法典
（2020年5月28日第十三届全国人民代表大会第三次会议通过）
第一条　为了保护民事主体的合法权益，制定本法。
第九十九条　本法施行前发生的民事纠纷，依照当时的法律处理；原《合同法》自1999年10月1日起施行的规定不再适用。
第一百条　本法自2021年1月1日起施行。
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


class TestDocxPropsOverride:
    """docx 内嵌属性是文档级字段的权威来源，正文解析只作逐字段兜底。"""

    def test_props_override_rule_values(self) -> None:
        props = DocxProps(
            title="中华人民共和国民法典",
            authority="全国人民代表大会",
            publish_date="2020-05-28",
        )

        header = parse_legal_header(MINFADIAN_WITH_TOC, props=props)

        assert header.law_name == "中华人民共和国民法典"
        assert header.issuing_authority == "全国人民代表大会"
        assert header.publish_date == "2020-05-28"
        assert header.meta_source == "docx-props"

    def test_partial_props_keep_rule_fallback(self) -> None:
        """属性只给法名时，机关与日期仍走正文解析。"""
        props = DocxProps(title="权威法名")

        header = parse_legal_header(MINFADIAN_WITH_TOC, props=props)

        assert header.law_name == "权威法名"
        assert header.publish_date == "2020-05-28"
        assert "第十三届全国人民代表大会" in (header.issuing_authority or "")
        assert header.meta_source == "rule+docx"

    def test_absent_props_leave_rule_result_untouched(self) -> None:
        """props 为 None 时必须与改造前逐字段一致（净增，不是替换）。"""
        without = parse_legal_header(MINFADIAN_WITH_TOC)
        empty = parse_legal_header(
            MINFADIAN_WITH_TOC, props=DocxProps(origin="custom")
        )

        assert without.law_name == empty.law_name == "中华人民共和国民法典"
        assert without.publish_date == empty.publish_date == "2020-05-28"
        assert without.issuing_authority == empty.issuing_authority
        assert without.meta_source == empty.meta_source == "rule"

    def test_props_do_not_change_article_structure_detection(self) -> None:
        """``has_article_structure`` 来自正文，属性不参与判定。"""
        props = DocxProps(title="中华人民共和国刑法修正案")

        header = parse_legal_header(XINGFA_AMENDMENT, props=props)

        assert header.law_name == "中华人民共和国刑法修正案"
        assert header.has_article_structure is False

    def test_analyze_legal_document_forwards_props(self) -> None:
        """目录剥离与属性覆盖在同一次预处理里生效。"""
        props = DocxProps(
            title="中华人民共和国民法典",
            authority="全国人民代表大会",
            publish_date="2020-05-28",
        )

        analysis = analyze_legal_document(MINFADIAN_WITH_TOC, props=props)

        assert analysis.header.toc_stripped is True
        assert analysis.header.meta_source == "docx-props"
        assert "目　　录" not in analysis.text
        assert analysis.header.law_name == "中华人民共和国民法典"


class TestVersionAndProvenanceFields:
    """版本与溯源字段（effective_date / validity_status / law_type / …）。

    这四个字段正文里没有对应信息，只来自 docx 内嵌属性，因此没有兜底路径：
    属性缺失时必须留空，不能猜。``effective_date`` 是唯一有正文兜底的例外。
    """

    def test_props_populate_version_fields(self) -> None:
        props = DocxProps(
            title="中华人民共和国民法典",
            effective_date="2021-01-01",
            validity_status=3,
            law_type="法律",
            external_id="ff8081817e00906b017e055ae8c00e16",
            source_code="national_laws",
        )

        header = parse_legal_header(MINFADIAN_WITH_TOC, props=props)

        assert header.effective_date == "2021-01-01"
        assert header.validity_status == 3
        assert header.law_type == "法律"
        assert header.external_id == "ff8081817e00906b017e055ae8c00e16"
        assert header.source_code == "national_laws"

    def test_version_fields_are_none_without_props(self) -> None:
        """属性缺失时留空——这四个字段没有正文兜底。"""
        header = parse_legal_header(MINFADIAN_WITH_TOC)

        assert header.validity_status is None
        assert header.law_type is None
        assert header.external_id is None
        assert header.source_code is None

    def test_effective_date_falls_back_to_body_clause(self) -> None:
        """属性没有施行日期时，回退到正文的「自…起施行」，并取最后一个匹配。"""
        header = parse_legal_header(STATUTE_WITH_EFFECTIVE_DATE)

        assert header.effective_date == "2021-01-01"

    def test_props_effective_date_wins_over_body_clause(self) -> None:
        props = DocxProps(effective_date="2021-02-01")

        header = parse_legal_header(STATUTE_WITH_EFFECTIVE_DATE, props=props)

        assert header.effective_date == "2021-02-01"

    def test_effective_date_absent_everywhere_stays_none(self) -> None:
        header = parse_legal_header(MINFADIAN_WITH_TOC)

        assert header.effective_date is None

    def test_extractor_emits_version_and_provenance_keys(self) -> None:
        """字段要真的落到每个 child chunk 的元数据字典里。"""
        props = DocxProps(
            title="中华人民共和国民法典",
            authority="全国人民代表大会",
            publish_date="2020-05-28",
            effective_date="2021-01-01",
            validity_status=3,
            law_type="法律",
            external_id="ext-1",
            source_code="national_laws",
        )
        analysis = analyze_legal_document(MINFADIAN_WITH_TOC, props=props)
        chunker_result = LawsChunker().chunk(analysis.text)

        meta = LegalMetadataExtractor().extract(
            child_chunks=chunker_result.child_chunks,
            parent_chunks=chunker_result.parent_chunks,
            parent_child_map=chunker_result.parent_child_map,
            section_paths=[[] for _ in chunker_result.child_chunks],
            analysis=analysis,
        )

        assert meta
        for entry in meta:
            assert entry["effective_date"] == "2021-01-01"
            assert entry["validity_status"] == 3
            assert entry["law_type"] == "法律"
            assert entry["external_id"] == "ext-1"
            assert entry["source_code"] == "national_laws"
            assert entry["meta_source"] == "docx-props"

        # 属性把三个身份字段填满后，条文块的置信度到顶（标题块无条号，少 0.2）。
        with_article = [e for e in meta if e["article_number"] is not None]
        assert with_article
        assert all(e["confidence"] == 1.0 for e in with_article)

    def test_extractor_emits_null_keys_without_props(self) -> None:
        """没有属性时键仍在，只是值为 None——字段字典是稳定形状。"""
        analysis = analyze_legal_document(MINFADIAN_WITH_TOC)
        chunker_result = LawsChunker().chunk(analysis.text)

        meta = LegalMetadataExtractor().extract(
            child_chunks=chunker_result.child_chunks,
            parent_chunks=chunker_result.parent_chunks,
            parent_child_map=chunker_result.parent_child_map,
            section_paths=[[] for _ in chunker_result.child_chunks],
            analysis=analysis,
        )

        assert meta
        for entry in meta:
            assert entry["validity_status"] is None
            assert entry["law_type"] is None
            assert entry["external_id"] is None
            assert entry["source_code"] is None
            assert entry["meta_source"] == "rule"

    def test_region_fields_are_resolved_for_local_regulations(self) -> None:
        """地方性法规要能落到省/市——PRD 的地域筛选依赖它。"""
        text = (
            "菏泽市煤炭清洁生产使用监督管理条例\n"
            "（2016年12月23日菏泽市第十八届人民代表大会常务委员会第三十七次会议通过  "
            "2017年1月18日山东省第十二届人民代表大会常务委员会第二十五次会议批准）\n"
            "第一条　为了加强煤炭清洁生产使用监督管理，制定本条例。\n"
        )

        analysis = analyze_legal_document(text)
        meta = LegalMetadataExtractor().extract(
            child_chunks=["第一条　为了加强煤炭清洁生产使用监督管理，制定本条例。"],
            parent_chunks=["第一条　为了加强煤炭清洁生产使用监督管理，制定本条例。"],
            parent_child_map={0: [0]},
            section_paths=[[]],
            analysis=analysis,
        )

        assert analysis.header.province == "山东省"
        assert analysis.header.city == "菏泽市"
        assert meta[0]["province"] == "山东省"
        assert meta[0]["city"] == "菏泽市"

    def test_national_law_has_no_region(self) -> None:
        analysis = analyze_legal_document(MINFADIAN_WITH_TOC)

        assert analysis.header.province is None
        assert analysis.header.city is None


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

    # 一条含两款的条文 + 一条单款条文。真实链路里 loader 用 "\n\n" 拼段落，
    # 所以「同一父块内的多个段落」就长这样。
    TWO_ARTICLES = (
        "第一条　为了保护民事主体的合法权益，制定本法。\n\n"
        "本条第二款内容，与第一款同属第一条。\n\n"
        "第二条　民法调整平等主体的自然人之间的人身关系。"
    )

    def test_default_policy_splits_by_paragraph(self) -> None:
        """默认策略＝改造前行为：款成为独立子块。"""
        result = LawsChunker().chunk(self.TWO_ARTICLES)

        assert result.child_chunks == [
            "第一条　为了保护民事主体的合法权益，制定本法。",
            "本条第二款内容，与第一款同属第一条。",
            "第二条　民法调整平等主体的自然人之间的人身关系。",
        ]

    def test_article_policy_emits_one_child_per_article(self) -> None:
        """article 策略：一条文一个子块，体量约为按款切的 41%。"""
        result = LawsChunker(child_policy="article").chunk(self.TWO_ARTICLES)

        assert result.child_chunks == [
            "第一条　为了保护民事主体的合法权益，制定本法。\n\n本条第二款内容，与第一款同属第一条。",
            "第二条　民法调整平等主体的自然人之间的人身关系。",
        ]
        assert len(result.parent_chunks) == len(result.child_chunks)

    def test_unknown_policy_falls_back_to_paragraph(self) -> None:
        """KB config 写错值不能把入库打挂，也不能静默换成别的切分器。"""
        chunker = LawsChunker(child_policy="whatever")

        assert chunker.child_policy == "paragraph"
        assert len(chunker.chunk(self.TWO_ARTICLES).child_chunks) == 3

    def test_long_article_is_still_split_by_the_size_guard(self) -> None:
        """article 策略不等于"绝不切"：超长条文仍由护栏按句子边界再切。"""
        long_article = "第一条　" + "。".join(
            f"第{index}款内容要足够长以超过子块上限" for index in range(30)
        ) + "。"

        result = LawsChunker(child_policy="article").chunk(long_article)
        assert len(result.child_chunks) == 1

        guarded = enforce_size_limits(result, child_size=200)

        assert len(guarded.child_chunks) > 1
        assert all(len(child) <= 200 for child in guarded.child_chunks)
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
