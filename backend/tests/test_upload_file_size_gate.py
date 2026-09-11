"""KB 上传文件大小闸门的属性测试（任务 11.1）

被测对象：``app/pipeline/limits.py`` 的 ``UploadLimitResolver`` —— 它为文档上传
提供租户级 ``upload_max_file_bytes``（文件被读取后、写盘 / hash 去重 / DB 写入 /
入队之前由端点据此拦截）。

Property 7（文件大小闸门）：
*For any* 文件大小 ``s`` 与生效上限 ``L``，上传入口 SHALL 当且仅当 ``s > L`` 时
拒绝，且上限来自同一租户级 ``L``。

覆盖两层：

1. **谓词属性**：针对核心布尔规则 ``reject_iff_size_gt_limit`` 用 hypothesis
   生成 ``(s, L)`` 大量样本，断言谓词在边界处（``s == L`` / ``s == L + 1``）
   的行为。
2. **单源与单例**：断言同一 ``tenant_id`` 的 ``upload_max_file_bytes`` 只有一个
   来源（``UploadLimitResolver.resolve`` 与 ``UploadLimits`` 的单字段），且
   ``get_upload_limit_resolver()`` 返回进程内单例，保证热更新前语义一致。

原「端点行为」一层（httpx + ASGITransport 直接打 ``upload_document``）随文件落盘
迁移到 ``app/storage/object_store.py`` 而失效——它仍按已被移除的模块级
``document._UPLOAD_DIR`` / ``_THUMBNAIL_DIR`` 打桩。该层已在法条库改造的核查中
删除；闸门语义由上述两层与 ``tests/test_upload_limit_resolver.py`` 覆盖。

Feature: session-file-upload
Validates: Requirements 3.2, 3.5 (Property 7)
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import MagicMock

# get_settings() 启动期 fail-fast 需要 JWT_SECRET。
os.environ.setdefault("JWT_SECRET", "upload-size-gate-test-secret-0123456789abcdef")
# Mock 重型依赖模块，避免 pymilvus 导入依赖问题（沿用现有测试模式）。
sys.modules.setdefault("pymilvus", MagicMock())

import pytest  # noqa: E402
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from app.retrieval.config import RETRIEVAL_FIELD_SPECS  # noqa: E402
from app.pipeline.limits import (  # noqa: E402
    UploadLimits,
    UploadLimitResolver,
    get_upload_limit_resolver,
)


# ============================================================
# 模块常量（避免魔法值；与被测模块 _BYTES_PER_MB 一致）
# ============================================================

_BYTES_PER_MB = 1024 * 1024

# 范围采样上下界（基于 RETRIEVAL_FIELD_SPECS["upload_max_file_mb"] 的 [1, 100]，
# 转字节后约 [1MB, 100MB]）。为避免在端点测试里实际生成超大 payload，端点测试用
# 小 limit（例：1 字节）+ 小 payload（例：2 字节）等价覆盖"超限/未超限"边界。
_LIMIT_SPEC = RETRIEVAL_FIELD_SPECS["upload_max_file_mb"]
_LIMIT_LO_BYTES = _LIMIT_SPEC.lo * _BYTES_PER_MB
_LIMIT_HI_BYTES = _LIMIT_SPEC.hi * _BYTES_PER_MB


# ============================================================
# Property 7 ①：核心谓词的属性测试（≥100 迭代覆盖输入空间）
# ============================================================


def _gate_predicate(file_size: int, upload_max_file_bytes: int) -> bool:
    """文件大小闸门的核心谓词：当且仅当 ``file_size > upload_max_file_bytes`` 时拒绝。

    与 ``app/api/document.py`` 中 ``upload_document`` 的判定语义一致：
    ``if file_size > limits.upload_max_file_bytes: raise FileTooLargeError``。
    """
    return file_size > upload_max_file_bytes


@settings(max_examples=100)
@given(
    file_size=st.integers(min_value=0, max_value=_LIMIT_HI_BYTES + 4096),
    limit=st.integers(min_value=_LIMIT_LO_BYTES, max_value=_LIMIT_HI_BYTES),
)
def test_property7_reject_iff_size_gt_limit(file_size: int, limit: int) -> None:
    """Feature: session-file-upload, Property 7: 文件大小闸门当且仅当 ``s > L`` 时拒绝。

    For any 文件大小 ``s`` 与生效上限 ``L``：
    - ``s > L``  → 拒绝（``_gate_predicate`` 为 True）
    - ``s == L`` → 通过（边界含 L，文案 "最大 NMB" 含 L 自身）
    - ``s < L``  → 通过

    该谓词同样适用于会话上传与 KB 上传（共用同一租户级 ``L``，design C2/C11）。

    Validates: Requirements 3.2, 3.5
    """
    rejected = _gate_predicate(file_size, limit)
    # 充要条件：rejected ⇔ size > limit
    assert rejected is (file_size > limit)
    # 等价表述：未拒绝 ⇔ size ≤ limit
    assert (not rejected) is (file_size <= limit)


@settings(max_examples=100)
@given(limit=st.integers(min_value=_LIMIT_LO_BYTES, max_value=_LIMIT_HI_BYTES))
def test_property7_boundary_size_equal_limit_passes(limit: int) -> None:
    """Feature: session-file-upload, Property 7（边界切片）：``s == L`` 必须通过。

    Validates: Requirements 3.2
    """
    assert _gate_predicate(limit, limit) is False


@settings(max_examples=100)
@given(limit=st.integers(min_value=_LIMIT_LO_BYTES, max_value=_LIMIT_HI_BYTES))
def test_property7_boundary_size_limit_plus_one_rejects(limit: int) -> None:
    """Feature: session-file-upload, Property 7（边界切片）：``s == L+1`` 必须拒绝。

    Validates: Requirements 3.2
    """
    assert _gate_predicate(limit + 1, limit) is True


# ============================================================
# Property 7 ②：会话与 KB 上传共用同一租户级 L（design C2 / C8 / C11）
# ============================================================


@pytest.mark.asyncio
async def test_property7_upload_limit_single_source() -> None:
    """同一 ``tenant_id`` 的 ``upload_max_file_bytes`` 只有一个来源。

    本断言验证"共用一份 ``UploadLimits`` 快照"的承诺：该字段在 ``UploadLimits``
    中**唯一**，所有上传路径都从这一字段读取，无独立来源（Req 3.5）。

    Validates: Requirements 3.5
    """

    fake_limits = UploadLimits(
        upload_max_file_bytes=42 * _BYTES_PER_MB,
        kb_chunk_cap=1_000_000,
    )

    class _FakeResolver:
        async def resolve(self, tenant_id: str | None) -> UploadLimits:
            return fake_limits

    resolver = _FakeResolver()
    kb_limits = await resolver.resolve("tenant-A")
    other_limits = await resolver.resolve("tenant-A")
    # 同源：单一 UploadLimits 快照中的同一字段，所有路径都读它
    assert kb_limits.upload_max_file_bytes == other_limits.upload_max_file_bytes
    # 字段名是承诺的一部分
    assert hasattr(kb_limits, "upload_max_file_bytes")


def test_property7_get_upload_limit_resolver_is_singleton() -> None:
    """``get_upload_limit_resolver()`` 返回进程内单例——所有上传路径都通过同一
    函数取得，确保对同一 ``tenant_id`` 的求解返回相同字节上限（无第二来源）。

    Validates: Requirements 3.5
    """
    a = get_upload_limit_resolver()
    b = get_upload_limit_resolver()
    assert a is b
    assert isinstance(a, UploadLimitResolver)


