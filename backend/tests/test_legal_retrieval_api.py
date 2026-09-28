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
    MATCH_MODE_EXACT,
    MATCH_MODE_SEMANTIC,
    RetrievalTestResponse,
    _MAX_PAGINATION_WINDOW,
    RetrievalTestRequest,
    _is_invalid_legal,
    _run_retrieval,
    _slice_page_items,
    _validity_status_label,
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


class TestValidityStatusFiltering:
    """默认口径排除已废止/已失效；``include_invalid`` 放开，状态与标签照常下发。"""

    def test_request_defaults_to_excluding_invalid(self) -> None:
        assert RetrievalTestRequest.model_fields["include_invalid"].default is False

    def test_response_reports_filtered_count(self) -> None:
        """被过滤掉几条必须有个交代，否则"为什么只返回 3 条"是调用方最容易困惑的地方。"""
        assert RetrievalTestResponse.model_fields["filtered_invalid_count"].default == 0

    @pytest.mark.parametrize("status", [1, -1])
    def test_repealed_and_lapsed_are_dropped(self, status: int) -> None:
        assert _is_invalid_legal({"validity_status": status}) is True

    @pytest.mark.parametrize("status", [3, 2, 0, 4])
    def test_other_statuses_are_kept(self, status: int) -> None:
        """现行有效/已修改/未标注/尚未生效都不该被静默吞掉。"""
        assert _is_invalid_legal({"validity_status": status}) is False

    def test_missing_status_is_treated_as_valid(self) -> None:
        """读不到状态不等于失效：宁可多给一条，也不要凭空少一条。"""
        assert _is_invalid_legal({}) is False
        assert _is_invalid_legal({"validity_status": None}) is False

    @pytest.mark.parametrize("status,label", [
        (3, "现行有效"),
        (2, "已修改"),
        (1, "已废止"),
        (-1, "已失效"),
        (4, "尚未生效"),
        (0, "未标注"),
    ])
    def test_label_covers_the_dictionary(self, status: int, label: str) -> None:
        assert _validity_status_label(status) == label

    def test_unknown_and_missing_values_get_no_label(self) -> None:
        """字典外的取值只发原值、不编描述；缺值同样不发——编一个标签比缺一个更糟。"""
        assert _validity_status_label(99) is None
        assert _validity_status_label(None) is None


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
        # 效力状态默认过滤：请求里有放开开关，响应里有"被过滤掉几条"的回执
        assert request_props["include_invalid"]["default"] is False

        response_props = schema["components"]["schemas"]["RetrievalTestResponse"]["properties"]
        for field in (
            "page",
            "page_size",
            "has_more",
            "match_mode",
            "fallback_reason",
            "filtered_invalid_count",
        ):
            assert field in response_props

        detail_props = schema["components"]["schemas"]["LegalArticleDetail"]["properties"]
        for field in ("article_id", "content", "matched_content", "law_name", "article_label"):
            assert field in detail_props
