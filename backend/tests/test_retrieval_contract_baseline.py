"""检索接口契约基线（法条库改造 Phase 0）。

锁定 ``POST /api/retrieval/search`` 上**改造前后都必须保持**的契约不变量，让
「默认带上全局法条库 / 结果补法条字段 / top_k 默认值调整」这些改动只能在既定
边界内发生：

1. 请求侧检索范围的归并语义（``kb_ids`` 优先、去重、保持顺序、单选退化）；
2. 响应信封的字段集合；
3. 单条结果承载元数据的 ``metadata`` 槽位。

刻意**不**断言 ``top_k`` 的默认值——本改造会把它从 10 调到 5，断言它会让基线在
改造后失效。这里只断言字段存在与其取值范围。

这些断言不依赖 Milvus / PostgreSQL / 外部模型服务，可在无服务环境下运行。
"""

from __future__ import annotations

# 导入 `app.api.retrieval` 会连带触发 `app.config.get_settings()`，而它在缺少
# JWT_SECRET 时 fail-fast。上游的 `backend/.env` 是本地文件（.gitignore 忽略），
# 新克隆里没有，因此这里在导入任何 app 模块之前补一组仅用于测试的默认值，
# 让本文件不依赖本机环境即可运行。
import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

from app.api.retrieval import (
    RetrievalResultItem,
    RetrievalTestRequest,
    RetrievalTestResponse,
)


class TestResolveKbIds:
    """检索范围归并：kb_ids 优先、去重、保持顺序、单选退化。"""

    def test_kb_ids_take_priority_over_single_field(self) -> None:
        req = RetrievalTestRequest(
            query="q", kb_ids=["a", "b"], knowledge_base_id="c"
        )
        assert req.resolve_kb_ids() == ["a", "b"]

    def test_single_field_degrades_to_one_element(self) -> None:
        req = RetrievalTestRequest(query="q", knowledge_base_id="a")
        assert req.resolve_kb_ids() == ["a"]

    def test_duplicates_removed_and_order_preserved(self) -> None:
        req = RetrievalTestRequest(query="q", kb_ids=["b", "a", "b", "c", "a"])
        assert req.resolve_kb_ids() == ["b", "a", "c"]

    def test_empty_when_no_scope_given(self) -> None:
        """归并本身不注入任何库；注入全局法条库发生在端点层。"""
        req = RetrievalTestRequest(query="q")
        assert req.resolve_kb_ids() == []


class TestRequestFieldContract:
    """请求字段仍然存在，且约束不变。"""

    def test_top_k_field_exists_with_documented_range(self) -> None:
        field = RetrievalTestRequest.model_fields["top_k"]
        assert field.annotation is int
        # ge=1 / le=100：取值范围是契约的一部分，默认值不是。
        constraints = [getattr(m, "ge", None) for m in field.metadata]
        assert 1 in constraints

    def test_mode_field_defaults_to_hybrid(self) -> None:
        assert RetrievalTestRequest(query="q").mode == "hybrid"


class TestResponseEnvelope:
    """响应信封的字段集合是稳定契约。

    本次新增 5 个字段是**有意的契约扩展**（PRD F-006 分页 / F-002 精确模式）：
    ``page`` / ``page_size`` / ``has_more`` 描述分页位置，``match_mode`` 回显实际生效的模式
    （exact 没命中会退回 semantic），``fallback_reason`` 说明回退原因。都带默认值，
    因此对不传新参数的既有调用方是向后兼容的。
    """

    EXPECTED_KEYS = {
        "query",
        "mode",
        "total",
        "elapsed_ms",
        "results",
        "trace",
        "degraded",
        "failed_source_count",
        "page",
        "page_size",
        "has_more",
        "match_mode",
        "fallback_reason",
    }

    def test_envelope_keys_are_exactly_the_documented_set(self) -> None:
        assert set(RetrievalTestResponse.model_fields) == self.EXPECTED_KEYS


class TestResultItemMetadataSlot:
    """法条字段走已有的 metadata 槽位，不新增顶层字段。"""

    def test_metadata_defaults_to_empty_dict(self) -> None:
        item = RetrievalResultItem(
            chunk_id="ck-1", doc_id="doc-1", content="正文", score=0.5
        )
        assert item.metadata == {}
        assert isinstance(item.metadata, dict)

    def test_metadata_accepts_legal_fields(self) -> None:
        item = RetrievalResultItem(
            chunk_id="ck-1",
            doc_id="doc-1",
            content="第一百四十六条　具备下列条件的民事法律行为有效：…",
            score=0.95,
            metadata={
                "law_name": "中华人民共和国民法典",
                "article_number": 146,
                "article_label": "第一百四十六条",
                "chapter": "第三编　合同 / 第一分编　通则",
                "source": "global",
            },
        )
        assert item.metadata["article_number"] == 146
        assert item.metadata["source"] == "global"

    def test_result_item_top_level_fields_are_unchanged(self) -> None:
        assert set(RetrievalResultItem.model_fields) == {
            "chunk_id",
            "doc_id",
            "filename",
            "source_type",
            "content",
            "child_content",
            "score",
            "rrf_score",
            "rerank_score",
            "routes",
            "metadata",
        }
