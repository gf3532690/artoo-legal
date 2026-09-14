"""法条过滤参数：层级枚举映射成语料 law_type、地域语义、以及 Milvus expr 拼装。

过滤是在 Milvus 侧下推的（``expr`` 一路传到子检索器），所以这里断言的是"拼出来的
表达式长什么样"，它决定了过滤的实际语义。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

from app.api.retrieval import RetrievalTestRequest
from app.retrieval.filter import RetrievalFilter, law_types_for_levels


class TestLevelMapping:
    def test_single_level_expands_to_corpus_categories(self) -> None:
        assert law_types_for_levels(["administrative_regulation"]) == ["行政法规"]

    def test_law_level_covers_law_and_its_interpretations(self) -> None:
        types = law_types_for_levels(["law"])

        assert "法律" in types
        assert "法律解释" in types
        assert "行政法规" not in types
        assert "地方法规" not in types

    def test_decisions_are_their_own_level(self) -> None:
        """修改/废止的决定在国家与地方两级都存在，语料不区分，所以不并入 law。"""
        assert law_types_for_levels(["decision"]) == ["修改、废止的决定"]

    def test_supervision_regulation_is_not_administrative(self) -> None:
        assert law_types_for_levels(["supervision_regulation"]) == ["监察法规"]

    def test_multiple_levels_are_deduplicated_in_order(self) -> None:
        types = law_types_for_levels(["constitution", "law", "constitution"])

        assert types[0] == "宪法"
        assert len(types) == len(dict.fromkeys(types))

    def test_unknown_level_is_ignored(self) -> None:
        assert law_types_for_levels(["whatever"]) == []
        assert law_types_for_levels(None) == []


class TestFilterExpr:
    def test_no_conditions_returns_none(self) -> None:
        assert RetrievalFilter().to_milvus_expr() is None

    def test_law_types_use_in_list(self) -> None:
        expr = RetrievalFilter(law_types=["法律", "行政法规"]).to_milvus_expr()

        assert expr == 'law_type in ["法律", "行政法规"]'

    def test_province_keeps_national_documents(self) -> None:
        """指定省份时只筛地方性法规；国家层面法规（province 为空串）始终保留。"""
        expr = RetrievalFilter(province="江西省").to_milvus_expr()

        assert expr == '(province == "" or province == "江西省")'

    def test_city_only_condition(self) -> None:
        expr = RetrievalFilter(city="景德镇市").to_milvus_expr()

        assert expr == '(province == "" or city == "景德镇市")'

    def test_province_and_city_must_both_match_for_local_documents(self) -> None:
        expr = RetrievalFilter(province="江西省", city="景德镇市").to_milvus_expr()

        assert expr == (
            '(province == "" or (province == "江西省" '
            'and (city == "" or city == "景德镇市")))'
        )

    def test_conditions_are_combined_with_and(self) -> None:
        expr = RetrievalFilter(
            law_types=["地方法规"], province="江西省"
        ).to_milvus_expr()

        assert expr == (
            'law_type in ["地方法规"] and (province == "" or province == "江西省")'
        )

    def test_existing_conditions_still_work(self) -> None:
        expr = RetrievalFilter(doc_ids=["d1"], file_types=["docx"]).to_milvus_expr()

        assert expr == 'doc_id in ["d1"] and file_type in ["docx"]'

    def test_quotes_are_escaped(self) -> None:
        expr = RetrievalFilter(doc_ids=['a"b']).to_milvus_expr()

        assert expr == 'doc_id in ["a\\"b"]'


class TestRequestToFilter:
    def test_no_params_means_no_filter(self) -> None:
        assert RetrievalTestRequest(query="q").to_filter() is None

    def test_levels_become_law_types(self) -> None:
        req = RetrievalTestRequest(query="q", law_levels=["local_regulation"])

        filt = req.to_filter()

        assert filt is not None
        assert filt.law_types == ["地方法规"]
        assert filt.to_milvus_expr() == 'law_type in ["地方法规"]'

    def test_region_params_are_trimmed_and_blank_is_ignored(self) -> None:
        req = RetrievalTestRequest(query="q", province="  江西省 ", city="   ")

        filt = req.to_filter()

        assert filt is not None
        assert filt.province == "江西省"
        assert filt.city is None

    def test_unknown_level_alone_yields_no_filter(self) -> None:
        """拼错层级名不应变成"过滤掉一切"的空结果。"""
        assert RetrievalTestRequest(query="q", law_levels=["typo"]).to_filter() is None
