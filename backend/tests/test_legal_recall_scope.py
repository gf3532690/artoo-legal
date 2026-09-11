"""法条召回范围与响应组装（Phase 2）单测。

覆盖两处纯逻辑：

- ``legal_scope._is_default_legal_kb``：全局法条库的标记判定（容忍布尔与字符串）；
- ``legal_metadata.strip_content_prefix``：响应组装时剥离索引前缀，避免
  ``[法名 第N条]`` / ``[文件名]`` 污染对外返回的法条正文。

不依赖 Milvus / PostgreSQL。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

from app.pipeline.legal_metadata import strip_content_prefix
from app.retrieval.legal_scope import _is_default_legal_kb
from app.api.knowledge_base import (
    _ensure_default_legal_kb_chunker_unchanged,
    _ensure_not_default_legal_kb,
)


class TestDefaultLegalKbFlag:
    def test_true_boolean(self) -> None:
        assert _is_default_legal_kb({"is_default_legal_kb": True}) is True

    def test_true_string(self) -> None:
        assert _is_default_legal_kb({"is_default_legal_kb": "true"}) is True

    def test_false_variants(self) -> None:
        assert _is_default_legal_kb({"is_default_legal_kb": False}) is False
        assert _is_default_legal_kb({"is_default_legal_kb": "false"}) is False

    def test_missing_or_wrong_type(self) -> None:
        assert _is_default_legal_kb({}) is False
        assert _is_default_legal_kb(None) is False
        assert _is_default_legal_kb("is_default_legal_kb") is False
        assert _is_default_legal_kb({"is_personal_legal_kb": True}) is False


class TestStripContentPrefix:
    def test_strips_legal_prefix(self) -> None:
        text = "[中华人民共和国民法典 第146条] 第一百四十六条　具备下列条件的…"
        assert strip_content_prefix(text) == (
            "第一百四十六条　具备下列条件的…"
        )

    def test_strips_filename_prefix(self) -> None:
        text = "[中华人民共和国民法典_20200528] 第一百四十六条　具备下列条件的…"
        assert strip_content_prefix(text) == "第一百四十六条　具备下列条件的…"

    def test_leaves_unprefixed_text_alone(self) -> None:
        text = "第一百四十六条　具备下列条件的民事法律行为有效：…"
        assert strip_content_prefix(text) == text

    def test_does_not_touch_brackets_in_the_middle(self) -> None:
        text = "第一条　本法所称[合同]是民事主体之间…"
        assert strip_content_prefix(text) == text

    def test_empty_input(self) -> None:
        assert strip_content_prefix("") == ""


class _FakeKb:
    """仅承载 config 的知识库替身：保护闸门只读这一个属性。"""

    def __init__(self, config: object) -> None:
        self.config = config


class TestDefaultLegalKbProtection:
    """全局法条库的保护闸门（方案 Phase 3「保护规则」）。"""

    def test_delete_is_rejected(self) -> None:
        kb = _FakeKb({"chunker_type": "laws", "is_default_legal_kb": True})
        try:
            _ensure_not_default_legal_kb(kb, "删除")
        except Exception as e:  # PermissionDeniedError
            assert "全局法条库" in str(e)
            return
        raise AssertionError("删除全局法条库应当被拒绝")

    def test_delete_is_allowed_for_ordinary_kb(self) -> None:
        # 普通知识库不受影响
        _ensure_not_default_legal_kb(_FakeKb({"chunker_type": "laws"}), "删除")
        _ensure_not_default_legal_kb(_FakeKb(None), "删除")

    def test_chunker_type_change_is_rejected(self) -> None:
        kb = _FakeKb({"chunker_type": "laws", "is_default_legal_kb": True})
        try:
            _ensure_default_legal_kb_chunker_unchanged(kb, {"chunker_type": "naive"})
        except Exception as e:
            assert "chunker_type" in str(e)
            return
        raise AssertionError("修改全局法条库的 chunker_type 应当被拒绝")

    def test_omitting_chunker_type_is_also_a_change(self) -> None:
        kb = _FakeKb({"chunker_type": "laws", "is_default_legal_kb": True})
        try:
            _ensure_default_legal_kb_chunker_unchanged(kb, {"other": 1})
        except Exception:
            return
        raise AssertionError("丢掉 chunker_type 也应视为修改")

    def test_keeping_chunker_type_and_other_kb_are_allowed(self) -> None:
        kb = _FakeKb({"chunker_type": "laws", "is_default_legal_kb": True})
        _ensure_default_legal_kb_chunker_unchanged(kb, {"chunker_type": "laws"})
        _ensure_default_legal_kb_chunker_unchanged(
            _FakeKb({"chunker_type": "naive"}), {"chunker_type": "naive"}
        )
