"""法条库文件列表的效力状态：下发、展示、以及**服务端**过滤。

这里钉的是"过滤真的下推到 SQL 了"这件事，而不是检索质量。列表页的过滤如果退化成
"把当前页拉下来在前端筛"，会有两个症状：分页数与筛选后的条数对不上、翻页翻着翻着
出现本该被筛掉的文档。这两点都不容易在界面上被发现，所以在 SQL 层钉住。

对照量级（22,037 文档 / 110 万 chunk 的等价数据）：过滤走 documents 上的列与索引是
24.7ms 与 147 buffers；改成读时去 chunks 的 JSON 列里派生是 3,958ms 与 9,173 buffers。
"""

from __future__ import annotations

import os as _os
from types import SimpleNamespace

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import pytest

from app.api.retrieval import INVALID_VALIDITY_STATUSES
from app.pipeline.legal_metadata import (
    VALIDITY_STATUS_LABELS,
    document_validity_status,
)


class TestValidityEnum:
    def test_covers_the_documented_six_values(self) -> None:
        assert VALIDITY_STATUS_LABELS == {
            3: "现行有效",
            2: "已修改",
            1: "已废止",
            -1: "已失效",
            4: "尚未生效",
            0: "未标注",
        }

    def test_two_values_that_were_once_read_backwards_stay_correct(self) -> None:
        """`0` 是未标注、`-1` 才是已失效。这两个曾经被读反，钉住免得再反一次。"""
        assert VALIDITY_STATUS_LABELS[0] == "未标注"
        assert VALIDITY_STATUS_LABELS[-1] == "已失效"

    def test_retrieval_only_excludes_documented_values(self) -> None:
        """检索默认排除的那两个值必须在枚举表里——否则排除的是个"不存在的状态"。"""
        assert INVALID_VALIDITY_STATUSES == frozenset({1, -1})
        assert INVALID_VALIDITY_STATUSES <= set(VALIDITY_STATUS_LABELS)


class TestDocumentValidityStatus:
    """把 per-chunk 元数据收敛成**文档级**取值。"""

    def test_takes_the_first_value_available(self) -> None:
        # 同一份文档每个子块带的都是同一个文档级值，取第一个非空即可
        assert document_validity_status([None, {}, {"validity_status": 1}]) == 1
        assert document_validity_status([{"validity_status": 3}, {"validity_status": 3}]) == 3

    def test_zero_is_a_value_not_a_missing_one(self) -> None:
        """`0` = 未标注，是**合法取值**。写成 `if value:` 就会把它当成"没有值"丢掉，
        那样这 2,031 份文档在列表里会显示成"未解析完成"。"""
        assert document_validity_status([{"validity_status": 0}]) == 0

    def test_absent_everywhere_stays_none(self) -> None:
        assert document_validity_status([]) is None
        assert document_validity_status(None) is None
        assert document_validity_status([{}, {"validity_status": None}]) is None

    def test_junk_is_skipped_rather_than_raising(self) -> None:
        assert document_validity_status([{"validity_status": "坏值"}]) is None

    def test_non_dict_entries_are_tolerated(self) -> None:
        assert document_validity_status([None, "x", {"validity_status": -1}]) == -1


# ============================================================
# 列表接口：过滤下推到 SQL
# ============================================================


def _sql_of(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _RecordingSession:
    """只记录收到的 SQL 并返回假行，不连数据库。"""

    def __init__(self, docs, chunk_rows=()):
        self._docs = docs
        self._chunk_rows = list(chunk_rows)
        self.statements: list[str] = []

    async def scalar(self, statement):
        self.statements.append(_sql_of(statement))
        return len(self._docs)

    async def execute(self, statement):
        sql = _sql_of(statement)
        self.statements.append(sql)
        # 水合法名那条查的是 chunks，其余（取文档列表）查的是 documents
        return _FakeResult(self._chunk_rows if "FROM chunks" in sql else self._docs)


def _doc(doc_id: str, validity_status: int | None):
    return SimpleNamespace(
        id=doc_id,
        kb_id="kb1",
        filename=f"{doc_id}.docx",
        file_type="docx",
        file_size=1,
        status="completed",
        error_message=None,
        chunk_count=3,
        progress=100,
        progress_message=None,
        source_url=None,
        created_at=None,
        validity_status=validity_status,
    )


async def _call_list(monkeypatch, session, **kwargs):
    from app.api import document as doc_api

    async def _fake_authorize(db, identity, kb_id, access):
        return SimpleNamespace(id=kb_id, config={})

    async def _fake_write_allowed(db, identity, kb):
        return True

    monkeypatch.setattr(doc_api, "_authorize_kb_access", _fake_authorize)
    monkeypatch.setattr(doc_api, "_kb_write_allowed", _fake_write_allowed)

    return await doc_api.list_documents(
        kb_id="kb1", identity=SimpleNamespace(), db=session, **kwargs
    )


class TestListDocumentsFilter:
    @pytest.mark.asyncio
    async def test_no_filter_means_no_predicate(self, monkeypatch) -> None:
        session = _RecordingSession([_doc("d1", 3), _doc("d2", 1)])
        page = await _call_list(monkeypatch, session)

        # 注意断言的是**谓词**而不是列名：`select(Document)` 本来就会把这一列选出来
        assert not any("documents.validity_status IN" in sql for sql in session.statements)
        assert page.total == 2
        assert [item.validity_status for item in page.items] == [3, 1]

    @pytest.mark.asyncio
    async def test_filter_becomes_an_in_predicate_on_documents(self, monkeypatch) -> None:
        session = _RecordingSession([_doc("d2", 1)])
        page = await _call_list(monkeypatch, session, validity_status=[1, -1])

        list_sql = next(sql for sql in session.statements if "FROM documents" in sql)
        assert "documents.validity_status IN (1, -1)" in list_sql
        # 过滤是服务端做的：不该出现"把 chunks 拉出来在前端筛"的痕迹
        assert not any(
            "FROM chunks" in sql and "validity_status" in sql for sql in session.statements
        )
        assert [item.validity_status for item in page.items] == [1]

    @pytest.mark.asyncio
    async def test_zero_filter_is_honoured(self, monkeypatch) -> None:
        """`0`（未标注）是合法取值，不能因为"看着像空"就被当成没传过滤条件。"""
        session = _RecordingSession([_doc("d3", 0)])
        await _call_list(monkeypatch, session, validity_status=[0])

        list_sql = next(sql for sql in session.statements if "FROM documents" in sql)
        assert "documents.validity_status IN (0)" in list_sql

    @pytest.mark.asyncio
    async def test_single_value_filter(self, monkeypatch) -> None:
        session = _RecordingSession([_doc("d1", 3)])
        await _call_list(monkeypatch, session, validity_status=[3])

        list_sql = next(sql for sql in session.statements if "FROM documents" in sql)
        assert "documents.validity_status IN (3)" in list_sql


class TestContractIsExposed:
    """接口契约：多了个查询参数、多了个响应字段，就得在 OpenAPI 里看得见。"""

    def test_document_response_carries_validity_status(self) -> None:
        from app.main import app

        props = app.openapi()["components"]["schemas"]["DocumentResponse"]["properties"]
        assert "validity_status" in props

    def test_list_documents_declares_the_query_param(self) -> None:
        from app.main import app

        params = app.openapi()["paths"][
            "/api/knowledge-bases/{kb_id}/documents"
        ]["get"]["parameters"]
        by_name = {p["name"]: p for p in params}
        assert "validity_status" in by_name
        schema = by_name["validity_status"]["schema"]
        # 可重复传，因而是一维整数数组；可空字段在 OpenAPI 里是 anyOf[T, null]
        array_schema = next(
            s for s in schema.get("anyOf", [schema]) if s.get("type") == "array"
        )
        assert array_schema["items"]["type"] == "integer"

    def test_enum_endpoint_is_registered(self) -> None:
        from app.main import app

        schema = app.openapi()
        assert "/api/legal/validity-statuses" in schema["paths"]
        option = schema["components"]["schemas"]["ValidityStatusOption"]["properties"]
        assert set(option) == {"value", "label"}
