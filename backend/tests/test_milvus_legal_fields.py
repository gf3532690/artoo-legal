"""法条过滤字段必须存在于 Milvus schema 与标量索引里。

这是首次入库的 gate 之一：schema 是固定的、没有开 dynamic field，而 Milvus 没有
"只更新某个标量字段"的接口——事后补值等于把每个 chunk 重新 embedding。所以
``law_type`` / ``province`` / ``city`` 必须在建表时就带上，并由写入路径逐行填充。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

from app.storage.milvus import _SCALAR_INDEXES, _build_fields

_LEGAL_FILTER_FIELDS = ("law_type", "province", "city")


class TestLegalFilterFields:
    def test_fields_exist_in_schema(self) -> None:
        names = {field.name for field in _build_fields("kb_id", 1024)}

        assert set(_LEGAL_FILTER_FIELDS) <= names

    def test_fields_have_scalar_indexes(self) -> None:
        assert set(_LEGAL_FILTER_FIELDS) <= set(_SCALAR_INDEXES)

    def test_index_names_are_distinct(self) -> None:
        names = list(_SCALAR_INDEXES.values())

        assert len(names) == len(set(names))

    def test_law_type_holds_the_longest_known_value(self) -> None:
        """max_length 是字节数：最长取值「有关法律问题和重大问题的决定（部分）」。"""
        fields = {field.name: field for field in _build_fields("kb_id", 1024)}
        longest = "有关法律问题和重大问题的决定（部分）"

        assert fields["law_type"].max_length >= len(longest.encode("utf-8"))
        assert fields["province"].max_length >= len("新疆维吾尔自治区".encode("utf-8"))

    def test_session_collection_uses_the_same_fields(self) -> None:
        """两套 collection 共用一套字段形状，会话库也不能例外。"""
        names = {field.name for field in _build_fields("session_id", 1024)}

        assert set(_LEGAL_FILTER_FIELDS) <= names
