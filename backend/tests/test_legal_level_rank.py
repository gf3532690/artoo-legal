"""效力位阶加权：位阶阶梯与「同等相关度下高层级优先」的排序行为。

PRD 的验收是"未指定层级时，法律层级结果排在司法解释之前"。这里用假 reranker 顶替外部
精排服务，直接检验加权与"多取候选再收敛"的机制。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

from types import SimpleNamespace

import pytest

from app.retrieval.base import RetrievalResult
from app.retrieval.config import RetrievalConfig
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.legal_level import LEGAL_LEVEL_TIERS, level_tier


class _StubReranker:
    """按预设分数降序返回 ``top_k`` 个 ``(索引, 分数)``。"""

    def __init__(self, scores: list[float]):
        self._scores = scores
        self.requested_top_k: int | None = None

    async def rerank(self, query: str, documents: list[str], top_k: int = 10):
        self.requested_top_k = top_k
        pairs = sorted(enumerate(self._scores), key=lambda p: p[1], reverse=True)
        return pairs[:top_k]


def _result(index: int, law_type: str | None, province: str | None = None) -> RetrievalResult:
    metadata = {"_rrf_score": 0.01}
    if law_type is not None:
        metadata["law_type"] = law_type
    if province is not None:
        metadata["province"] = province
    return RetrievalResult(
        chunk_id=f"ck-{index}",
        content=f"第{index}条　这是一段足够长的法条正文内容，用于避开结构碎片惩罚。",
        score=0.0,
        doc_id=f"doc-{index}",
        metadata=metadata,
    )


async def _rerank(results, scores, *, top_k, weight):
    reranker = _StubReranker(scores)
    fake_self = SimpleNamespace(
        reranker=reranker,
        _is_structural_fragment=HybridRetriever._is_structural_fragment,
    )
    config = RetrievalConfig(legal_level_weight=weight)
    out = await HybridRetriever._rerank(
        fake_self, "q", results, top_k, config, apply_filter=False
    )
    return out, reranker


class TestTierLadder:
    def test_law_ranks_above_judicial_interpretation(self) -> None:
        assert LEGAL_LEVEL_TIERS["法律"] > LEGAL_LEVEL_TIERS["司法解释"]
        assert LEGAL_LEVEL_TIERS["宪法"] == 1.0
        assert LEGAL_LEVEL_TIERS["地方法规"] < LEGAL_LEVEL_TIERS["司法解释"]

    def test_national_repeal_decision_ranks_above_local_one(self) -> None:
        """「修改、废止的决定」国家与地方两级都有，用 province 区分位阶。"""
        assert level_tier("修改、废止的决定", "") == LEGAL_LEVEL_TIERS["修改、废止的决定"]
        assert level_tier("修改、废止的决定", "江西省") == 0.6

    def test_missing_law_type_has_no_tier(self) -> None:
        """非法条语料（Artoo 的知识库）没有 law_type，天然不加权。"""
        assert level_tier(None) is None
        assert level_tier("") is None
        assert level_tier("未知类别") is None


class TestRerankBoost:
    @pytest.mark.asyncio
    async def test_weight_zero_keeps_pure_relevance_order(self) -> None:
        results = [_result(0, "法律"), _result(1, "司法解释")]
        out, reranker = await _rerank(results, [0.50, 0.80], top_k=2, weight=0.0)

        assert [r.chunk_id for r in out] == ["ck-1", "ck-0"]   # 司法解释分高 → 在前
        assert reranker.requested_top_k == 2                    # 不额外取候选

    @pytest.mark.asyncio
    async def test_boost_promotes_higher_tier_when_close(self) -> None:
        """分数接近时，法律层级被提到司法解释之前。"""
        results = [_result(0, "法律"), _result(1, "司法解释")]
        out, _ = await _rerank(results, [0.79, 0.80], top_k=2, weight=0.1)

        # 法律: 0.79*(1+0.09)=0.8611 ；司法解释: 0.80*(1+0.07)=0.856
        assert [r.chunk_id for r in out] == ["ck-0", "ck-1"]

    @pytest.mark.asyncio
    async def test_boost_does_not_override_clearly_better_relevance(self) -> None:
        """位阶只是"综合排序"里的偏好，不能盖过明显的相关度差距。"""
        results = [_result(0, "法律"), _result(1, "司法解释")]
        out, _ = await _rerank(results, [0.30, 0.90], top_k=2, weight=0.1)

        assert [r.chunk_id for r in out] == ["ck-1", "ck-0"]

    @pytest.mark.asyncio
    async def test_fetch_is_widened_so_lower_ranked_law_can_enter(self) -> None:
        """只取 top_k 时排第 k+1 的法律永远进不来——加权开启后要多取候选。"""
        results = [_result(0, "司法解释"), _result(1, "司法解释"), _result(2, "法律")]
        out, reranker = await _rerank(results, [0.90, 0.89, 0.88], top_k=2, weight=1.0)

        assert reranker.requested_top_k > 2
        assert [r.chunk_id for r in out] == ["ck-2", "ck-0"]
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_results_without_law_type_are_untouched(self) -> None:
        results = [_result(0, None), _result(1, None)]
        out, _ = await _rerank(results, [0.40, 0.90], top_k=2, weight=0.5)

        assert [r.chunk_id for r in out] == ["ck-1", "ck-0"]
