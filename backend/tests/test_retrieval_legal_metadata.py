"""结果水合下发法条产品字段（PRD 的"结果包含效力层级"等）。

`_build_result_items` 会查两次库（文档文件名、chunk 元数据），这里用假会话顶替，
断言的是**下发口径**而不是数据库行为：

- 已存在的法条字段照旧下发；
- PRD 要的效力层级 / 机关 / 日期 / 时效 / 地域也下发；
- 条号存在时给出派生的 `article_id`，无条号的文档不给。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import pytest

from app.api import retrieval as retrieval_api
from app.retrieval.base import RetrievalResult


class _Row:
    """够用的行对象：代码只按属性名取值。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _statement):
        # 两次查询：先文档（id/filename），后 chunk（id/kb_id/chunk_metadata）。
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _patch_sessions(monkeypatch, *, documents, chunks) -> None:
    queue = [documents, chunks]
    monkeypatch.setattr(
        retrieval_api, "async_session", lambda: _FakeSession(queue.pop(0))
    )


def _result(chunk_id="ck-1", doc_id="doc-1", content="第一百四十六条　具备下列条件…"):
    return RetrievalResult(
        chunk_id=chunk_id, content=content, score=0.9, doc_id=doc_id,
        metadata={}, child_content=content,
    )


_LEGAL_METADATA = {
    "law_name": "中华人民共和国民法典",
    "article_number": 146,
    "article_label": "第一百四十六条",
    "chapter": "第三编　合同 / 第一分编　通则",
    "law_type": "法律",
    "issuing_authority": "全国人民代表大会",
    "publish_date": "2020-05-28",
    "effective_date": "2021-01-01",
    "validity_status": 3,
    "province": None,
    "city": None,
}


@pytest.mark.asyncio
async def test_result_carries_prd_fields_and_derived_article_id(monkeypatch) -> None:
    _patch_sessions(
        monkeypatch,
        documents=[_Row(id="doc-1", filename="中华人民共和国民法典.docx")],
        chunks=[_Row(id="ck-1", kb_id="kb-global", chunk_metadata=dict(_LEGAL_METADATA))],
    )

    items, _ = await retrieval_api._build_result_items(
            [_result()], global_kb_ids=["kb-global"]
        )

    assert len(items) == 1
    metadata = items[0].metadata
    assert metadata["law_name"] == "中华人民共和国民法典"
    assert metadata["article_number"] == 146
    assert metadata["law_type"] == "法律"
    assert metadata["issuing_authority"] == "全国人民代表大会"
    assert metadata["publish_date"] == "2020-05-28"
    assert metadata["effective_date"] == "2021-01-01"
    assert metadata["validity_status"] == 3
    assert metadata["article_id"] == "doc-1:146"
    assert metadata["source"] == "global"


@pytest.mark.asyncio
async def test_region_fields_are_exposed_for_local_regulations(monkeypatch) -> None:
    _patch_sessions(
        monkeypatch,
        documents=[_Row(id="doc-9", filename="“景德镇制”陶瓷保护条例.docx")],
        chunks=[_Row(id="ck-9", kb_id="kb-global", chunk_metadata={
            "law_name": "“景德镇制”陶瓷保护条例",
            "article_number": 1,
            "article_label": "第一条",
            "law_type": "地方法规",
            "province": "江西省",
            "city": "景德镇市",
        })],
    )

    items, _ = await retrieval_api._build_result_items([_result(chunk_id="ck-9", doc_id="doc-9")])

    assert items[0].metadata["province"] == "江西省"
    assert items[0].metadata["city"] == "景德镇市"
    assert items[0].metadata["article_id"] == "doc-9:1"


@pytest.mark.asyncio
async def test_article_less_document_gets_no_article_id(monkeypatch) -> None:
    _patch_sessions(
        monkeypatch,
        documents=[_Row(id="doc-2", filename="某修正案.docx")],
        chunks=[_Row(id="ck-2", kb_id="kb-global", chunk_metadata={
            "law_name": "中华人民共和国刑法修正案（十一）",
            "article_number": None,
            "law_type": "修正案",
        })],
    )

    items, _ = await retrieval_api._build_result_items([_result(chunk_id="ck-2", doc_id="doc-2")])

    assert "article_id" not in items[0].metadata
    assert items[0].metadata["law_type"] == "修正案"


@pytest.mark.asyncio
async def test_empty_metadata_values_are_not_emitted(monkeypatch) -> None:
    """取不到的字段不写空键（既有约定，客户端要容忍缺键）。"""
    _patch_sessions(
        monkeypatch,
        documents=[_Row(id="doc-3", filename="x.docx")],
        chunks=[_Row(id="ck-3", kb_id="kb-1", chunk_metadata={"law_name": "某条例"})],
    )

    items, _ = await retrieval_api._build_result_items([_result(chunk_id="ck-3", doc_id="doc-3")])

    metadata = items[0].metadata
    assert metadata == {"law_name": "某条例"}
