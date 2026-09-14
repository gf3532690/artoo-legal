"""法条过滤字段必须存在于 Milvus schema 与标量索引里。

这是首次入库的 gate 之一：schema 是固定的、没有开 dynamic field，而 Milvus 没有
"只更新某个标量字段"的接口——事后补值等于把每个 chunk 重新 embedding。所以
``law_type`` / ``province`` / ``city`` 必须在建表时就带上，并由写入路径逐行填充。

这里只断言**声明层**（字段名、长度上限、标量索引、`_build_fields` 消费这些常量）：
建表结果本身依赖真实的 pymilvus 与 Milvus 服务，且本套件里 ``test_milvus_b3.py`` 会用
``sys.modules`` 注入假的 pymilvus，运行结果随导入顺序变化。真实的 schema 用
``describe_collection`` 在部署上核验（见 Agent Note 的验证记录）。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import inspect

from app.storage import milvus as milvus_module
from app.storage.milvus import LEGAL_FILTER_FIELD_LENGTHS, _SCALAR_INDEXES

_LEGAL_FILTER_FIELDS = ("law_type", "province", "city")


class TestLegalFilterFields:
    def test_legal_field_specs_are_declared(self) -> None:
        """字段名与长度上限是 schema 的 gate；不需要 pymilvus 即可断言。"""
        assert set(LEGAL_FILTER_FIELD_LENGTHS) == set(_LEGAL_FILTER_FIELDS)

    def test_schema_builder_consumes_the_declaration(self) -> None:
        """``_build_fields`` 必须从这份常量取法条字段，否则声明与建表会分叉。"""
        source = inspect.getsource(milvus_module._build_fields)

        assert "LEGAL_FILTER_FIELD_LENGTHS" in source

    def test_fields_have_scalar_indexes(self) -> None:
        assert set(_LEGAL_FILTER_FIELDS) <= set(_SCALAR_INDEXES)

    def test_index_names_are_distinct(self) -> None:
        names = list(_SCALAR_INDEXES.values())

        assert len(names) == len(set(names))

    def test_law_type_holds_the_longest_known_value(self) -> None:
        """max_length 是字节数：最长取值「有关法律问题和重大问题的决定（部分）」。"""
        longest = "有关法律问题和重大问题的决定（部分）"

        assert LEGAL_FILTER_FIELD_LENGTHS["law_type"] >= len(longest.encode("utf-8"))
        assert LEGAL_FILTER_FIELD_LENGTHS["province"] >= len("新疆维吾尔自治区".encode("utf-8"))
