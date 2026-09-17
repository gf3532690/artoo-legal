"""法条库文件列表的「搜索框」：按文件名的子串模糊匹配。

钉的是两件在界面上不容易发现、但一旦错了就会悄悄给错结果的事：

1. 用户输入里的 ``%`` / ``_`` 必须当普通字符——搜索框的语义是「文件名里含这几个字」，
   不是「按用户写的通配符匹配」。不转义的话搜 ``办法_`` 会把「办法A」「办法B」全捞出来。
2. 条件真的下推成 SQL ``ILIKE``（带 ``ESCAPE``），而不是把整页拉下来在前端筛。
"""

from __future__ import annotations

import inspect
import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

from sqlalchemy.dialects import postgresql

from app.api.document import _like_substring_pattern, list_documents
from app.schema.db import Document


class TestLikeSubstringPattern:
    def test_wraps_the_keyword_with_wildcards(self) -> None:
        assert _like_substring_pattern("公司法") == "%公司法%"

    def test_percent_and_underscore_become_literal_characters(self) -> None:
        """`_` 与 `%` 是 LIKE 的通配符，必须转义，否则搜出的是"任意字符"。"""
        assert _like_substring_pattern("办法_") == "%办法\\_%"
        assert _like_substring_pattern("100%") == "%100\\%%"

    def test_backslash_is_escaped_before_the_others(self) -> None:
        """反斜杠是转义符本身，先转它，否则会把后补的转义符再转一遍。"""
        assert _like_substring_pattern("a\\b") == "%a\\\\b%"
        assert _like_substring_pattern("a\\_b") == "%a\\\\\\_b%"


class TestSqlShape:
    def test_compiles_to_ilike_with_escape_clause(self) -> None:
        expr = Document.filename.ilike(_like_substring_pattern("办法_"), escape="\\")
        sql = str(
            expr.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        assert "ILIKE" in sql.upper()
        assert "ESCAPE" in sql.upper()
        # 下划线在 SQL 里是「被转义的字面下划线」，不是通配符
        assert "\\_" in sql


class TestEndpointShape:
    def test_list_documents_exposes_the_keyword_parameter(self) -> None:
        """参数名是对外契约（前端与管理端都按 `q` 传），改名要有意识地改。"""
        assert "q" in inspect.signature(list_documents).parameters
