"""法条检索接口的三项补齐：分页（F-006）、精确模式（F-002）、详情端点（F-201）。

这三项的共同点是"接口契约"而不是检索质量，所以测试都钉在**可离线验证的部分**：
查询解析、分页切片、参数校验、以及路由/字段是否真的注册进 OpenAPI。
真正的召回质量要看满库后的实测，不在单测里假装。
"""

from __future__ import annotations

import os as _os
from types import SimpleNamespace

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import pytest
from fastapi import HTTPException

from app.api.retrieval import (
    INVALID_VALIDITY_STATUSES,
    MATCH_MODE_EXACT,
    MATCH_MODE_SEMANTIC,
    _MAX_PAGINATION_WINDOW,
    RetrievalTestRequest,
    _is_invalid_legal,
    _run_retrieval,
    _slice_page_items,
    parse_article_query,
)
from app.retrieval.article_lookup import split_article_id


class TestParseArticleQuery:
    @pytest.mark.parametrize("text,expected", [
        ("民法典第一条", ("民法典", 1)),
        ("《中华人民共和国民法典》第一条", ("中华人民共和国民法典", 1)),
        ("刑法第234条", ("刑法", 234)),
        ("民法典 第一百四十六条", ("民法典", 146)),
        ("民法典第一百零一条", ("民法典", 101)),
        # 只给条号：法名留空，由调用方决定范围
        ("第234条", (None, 234)),
        ("第六十八条", (None, 68)),
    ])
    def test_parses_law_and_article(self, text: str, expected) -> None:
        assert parse_article_query(text) == expected

    @pytest.mark.parametrize("text", ["劳动合同解除的经济补偿", "", "第条", "民法典"])
    def test_returns_empty_when_no_article_number(self, text: str) -> None:
        assert parse_article_query(text) == (None, None)


class TestPagination:
    def test_window_is_page_times_top_k(self) -> None:
        assert RetrievalTestRequest(query="x", top_k=5, page=3).window() == 15

    def test_slice_walks_the_ranked_list(self) -> None:
        ranked = list(range(11))  # 调用方按 window+1 = 11 取候选
        first, more1 = _slice_page_items(ranked, RetrievalTestRequest(query="x", top_k=5, page=1))
        second, more2 = _slice_page_items(ranked, RetrievalTestRequest(query="x", top_k=5, page=2))
        third, more3 = _slice_page_items(ranked, RetrievalTestRequest(query="x", top_k=5, page=3))

        assert (first, more1) == ([0, 1, 2, 3, 4], True)
        assert (second, more2) == ([5, 6, 7, 8, 9], True)
        # 最后一页只剩 1 条，且多取的那一条证明后面没有了
        assert (third, more3) == ([10], False)

    def test_empty_page_reports_no_more(self) -> None:
        assert _slice_page_items([0, 1, 2], RetrievalTestRequest(query="x", top_k=5, page=2)) == ([], False)

    @pytest.mark.asyncio
    async def test_window_over_cap_is_rejected(self) -> None:
        """超过窗口上限要明确 400，而不是悄悄返回空页。"""
        body = RetrievalTestRequest(
            query="x",
            top_k=100,
            page=_MAX_PAGINATION_WINDOW // 100 + 2,
        )
        assert body.window() > _MAX_PAGINATION_WINDOW

        # 校验发生在解析知识库与授权之前，所以不需要任何身份/数据库。
        with pytest.raises(HTTPException) as exc:
            await _run_retrieval(body, SimpleNamespace())

        assert exc.value.status_code == 400
        assert "分页窗口" in exc.value.detail


class TestMatchModeValidation:
    @pytest.mark.asyncio
    async def test_unknown_match_mode_is_rejected(self) -> None:
        """写错枚举不能静默退化成"不过滤/不精确"——那正是 law_levels 踩过的坑。"""
        body = RetrievalTestRequest(query="民法典第一条", match_mode="exactt")

        with pytest.raises(HTTPException) as exc:
            await _run_retrieval(body, SimpleNamespace())

        assert exc.value.status_code == 400
        assert "match_mode" in exc.value.detail

    def test_default_is_semantic(self) -> None:
        assert RetrievalTestRequest(query="x").match_mode == MATCH_MODE_SEMANTIC
        assert MATCH_MODE_EXACT == "exact"


class TestEffectivenessFilter:
    """默认口径排除已废止/已失效。

    官方枚举（数据源字典，2026-09-15 确认）：3 现行有效 / 2 已修改 / 1 已废止 /
    -1 已失效 / 4 尚未生效 / 0 未标注。默认只排除「明确不具法律效力」的两个，
    因为库内这类约占 12%，不该静默混进检索结果。
    """

    def test_only_repealed_and_lapsed_are_excluded(self) -> None:
        assert INVALID_VALIDITY_STATUSES == frozenset({1, -1})

    @pytest.mark.parametrize("status,excluded", [
        (3, False),   # 现行有效
        (0, False),   # 未标注（主要是决定类文件，本身是有效文件）
        (2, False),   # 已修改（是库里能拿到的最新版本）
        (4, False),   # 尚未生效（还没生效的新法，不该被静默吞掉）
        (1, True),    # 已废止
        (-1, True),   # 已失效
    ])
    def test_status_predicate(self, status: int, excluded: bool) -> None:
        assert _is_invalid_legal({"validity_status": status}) is excluded

    def test_missing_status_is_treated_as_valid(self) -> None:
        """元数据缺失不等于失效——宁可多给一条，也不要凭空少一条。"""
        assert _is_invalid_legal({}) is False
        assert _is_invalid_legal({"validity_status": None}) is False

    def test_default_is_to_filter(self) -> None:
        assert RetrievalTestRequest(query="x").include_invalid is False
        assert RetrievalTestRequest(query="x", include_invalid=True).include_invalid is True


class TestArticleId:
    def test_splits_doc_and_article(self) -> None:
        assert split_article_id("2f1c9a1e-0000-4000-8000-000000000001:146") == (
            "2f1c9a1e-0000-4000-8000-000000000001", 146
        )

    @pytest.mark.parametrize("value", ["", "no-colon", "doc:abc", "doc:", ":12"])
    def test_rejects_malformed_ids(self, value: str) -> None:
        with pytest.raises(ValueError):
            split_article_id(value)


class TestRoutesAreRegistered:
    """路由与响应字段是接口契约的一部分，缺了就是对外承诺没兑现。"""

    def test_openapi_exposes_new_surface(self) -> None:
        from app.main import app

        schema = app.openapi()
        paths = set(schema["paths"])
        assert "/api/legal/articles/{article_id}" in paths

        request_props = schema["components"]["schemas"]["RetrievalTestRequest"]["properties"]
        assert request_props["page"]["default"] == 1
        assert request_props["match_mode"]["default"] == MATCH_MODE_SEMANTIC
        assert request_props["include_invalid"]["default"] is False

        response_props = schema["components"]["schemas"]["RetrievalTestResponse"]["properties"]
        for field in ("page", "page_size", "has_more", "match_mode", "fallback_reason",
                      "filtered_invalid_count"):
            assert field in response_props

        detail_props = schema["components"]["schemas"]["LegalArticleDetail"]["properties"]
        for field in ("article_id", "content", "matched_content", "law_name", "article_label"):
            assert field in detail_props
