"""法条详情接口（PRD《法条检索基础API》表 3 的 F-201）。

按检索结果里下发的 ``article_id``（= ``{doc_id}:{article_number}``）取法条完整信息：条文全文、
所属法名、条号、效力层级、地域、发布/施行日期、时效状态。

**本接口刻意不返回修订历史与关联司法解释**：PRD 表 4 把这两项写在「法条详情查询」的备注里，
但语料里没有这两类数据（数据缺口三项之一）。不返回空数组，是因为空数组会让调用方把
"本来就没有这个能力"读成"这条法条恰好没有修订历史"——两者完全不同。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_authenticated
from app.auth.identity import IdentityContext
from app.auth.kb_scope import authorize_content_read
from app.pipeline.legal_metadata import VALIDITY_STATUS_LABELS
from app.retrieval.article_lookup import load_article_rows, split_article_id
from app.retrieval.legal_scope import resolve_global_legal_kb_ids
from app.storage.database import async_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/legal", tags=["Legal"])


class LegalArticleDetail(BaseModel):
    """单条法条的完整信息。"""

    article_id: str = Field(description="原样回显请求里的法条 ID")
    doc_id: str
    kb_id: str
    filename: str
    content: str = Field(description="条文完整正文（父块；超长条文会由分块拼回）")
    matched_content: str = Field(description="命中的那一段子块正文")
    law_name: str | None = None
    article_number: int | None = None
    article_label: str | None = Field(default=None, description="如「第一百四十六条」")
    chapter: str | None = None
    law_type: str | None = Field(default=None, description="语料原始效力层级取值")
    province: str | None = None
    city: str | None = None
    issuing_authority: str | None = None
    publish_date: str | None = None
    effective_date: str | None = None
    validity_status: int | None = None
    validity_status_label: str | None = Field(
        default=None, description="效力状态的中文描述，如「现行有效」；字典外的取值不给"
    )
    source: str | None = Field(default=None, description="global / personal；无法判定时不返回")


class ValidityStatusOption(BaseModel):
    """效力状态枚举的一项：落库与过滤用的原值 + 展示名。"""

    value: int = Field(description="落库与过滤用的原始整数")
    label: str = Field(description="展示名，如「现行有效」")


@router.get("/validity-statuses", response_model=list[ValidityStatusOption])
async def list_validity_statuses(
    identity: IdentityContext = Depends(require_authenticated()),
) -> list[ValidityStatusOption]:
    """法条效力状态枚举（数据源字典口径）。

    给前端建筛选项、并把列表结果里的整数渲染成中文标签用，省得枚举在两种语言里各抄一份
    ——这个枚举已经被读反过一次（``0`` 是未标注，``-1`` 才是已失效）。

    顺序是「现行有效 → 已修改 → 尚未生效 → 未标注 → 已废止 → 已失效」，即从最可能想要
    的排到默认可被排除的，而不是按数值大小。
    """
    del identity  # 只用于鉴权
    preferred = [3, 2, 4, 0, 1, -1]
    values = [v for v in preferred if v in VALIDITY_STATUS_LABELS]
    # 常量表里新增了取值而这里忘了排序时，仍然下发，不静默丢掉
    values += [v for v in VALIDITY_STATUS_LABELS if v not in values]
    return [ValidityStatusOption(value=v, label=VALIDITY_STATUS_LABELS[v]) for v in values]


@router.get("/articles/{article_id}", response_model=LegalArticleDetail)
async def get_legal_article(
    article_id: str,
    identity: IdentityContext = Depends(require_authenticated()),
) -> LegalArticleDetail:
    """按 ``article_id`` 取法条详情。读授权与检索走同一道闸门。"""
    try:
        doc_id, article_number = split_article_id(article_id)
    except ValueError as exc:
        # 400 而不是 404：这是调用方拼错了 ID 格式，不是"这条法条不存在"。
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with async_session() as session:
        from app.schema.db import Document

        doc = await session.get(Document, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="法条不存在")
        kb_id = doc.kb_id
        filename = doc.filename

    # 先授权再取正文：不可读的库对外表现为 404（存在性不泄露），与检索口径一致。
    await authorize_content_read(identity, [kb_id])

    rows = await load_article_rows(doc_id=doc_id, article_number=article_number)
    if not rows:
        raise HTTPException(status_code=404, detail="法条不存在")

    row = rows[0]
    meta = row["metadata"]
    global_kb_ids = await resolve_global_legal_kb_ids()
    return LegalArticleDetail(
        article_id=article_id,
        doc_id=row["doc_id"],
        kb_id=row["kb_id"],
        filename=filename,
        content=row["article_content"],
        matched_content=row["child_content"],
        law_name=meta.get("law_name"),
        article_number=meta.get("article_number", article_number),
        article_label=meta.get("article_label"),
        chapter=meta.get("chapter"),
        law_type=meta.get("law_type"),
        province=meta.get("province"),
        city=meta.get("city"),
        issuing_authority=meta.get("issuing_authority"),
        publish_date=meta.get("publish_date"),
        effective_date=meta.get("effective_date"),
        validity_status=meta.get("validity_status"),
        validity_status_label=VALIDITY_STATUS_LABELS.get(meta.get("validity_status")),
        source=("global" if row["kb_id"] in set(global_kb_ids) else "personal")
        if global_kb_ids else None,
    )
