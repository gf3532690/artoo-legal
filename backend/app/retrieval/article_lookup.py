"""按「条号（＋法名）」精确取法条 —— 精确检索与法条详情共用一份实现。

为什么单独一个模块：法条元数据（``law_name`` / ``article_number`` …）只挂在**子块**上，
条文的完整正文在**父块**（``parent_id`` 指向它，父块自身没有 metadata，见 pipeline 的写库
顺序）。``/api/retrieval`` 的 exact 模式与 ``/api/legal/articles/{article_id}`` 必须用同一套
取法，否则同一条文在两个接口里可能给出不同的正文——这正是这一版在别处反复踩到的
「同一概念两套实现」问题。
"""

from __future__ import annotations

from sqlalchemy import select

from app.storage.database import async_session


def split_article_id(article_id: str) -> tuple[str, int]:
    """把 ``{doc_id}:{article_number}`` 拆成 ``(doc_id, article_number)``。

    Raises:
        ValueError: 格式不对（doc_id 是 UUID，不含冒号，所以按最后一个冒号切分）。
    """
    doc_id, _, raw = (article_id or "").rpartition(":")
    if not doc_id or not raw.strip().isdigit():
        raise ValueError(f"article_id 格式应为 {{doc_id}}:{{article_number}}，收到 {article_id!r}")
    return doc_id, int(raw)


async def load_article_rows(
    *,
    kb_ids: list[str] | None = None,
    doc_id: str | None = None,
    article_number: int,
    law_name: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """按「条号（＋法名）」取法条，每条带上父块正文。

    至少要给 ``doc_id`` 或 ``kb_ids`` 之一来确定范围。

    法名匹配分两级：先全等（``中华人民共和国民法典``），没有再用「以查询串结尾」
    （``民法典`` → ``中华人民共和国民法典``）。两级都空才算查不到——调用方据此决定是回退语义
    召回还是返回 404。

    Returns:
        ``[{chunk_id, doc_id, kb_id, child_content, article_content, metadata}]``；
        ``article_content`` 是父块正文（条文全文），``child_content`` 是命中的子块。
    """
    from app.schema.db import Chunk

    async def _query(where_law) -> list:
        stmt = select(
            Chunk.id, Chunk.doc_id, Chunk.kb_id, Chunk.parent_id,
            Chunk.content, Chunk.chunk_metadata,
        ).where(Chunk.chunk_metadata["article_number"].as_string() == str(article_number))
        if doc_id:
            stmt = stmt.where(Chunk.doc_id == doc_id)
        elif kb_ids:
            stmt = stmt.where(Chunk.kb_id.in_(kb_ids))
        if where_law is not None:
            stmt = stmt.where(where_law)
        stmt = stmt.order_by(Chunk.chunk_index).limit(limit)
        async with async_session() as session:
            return list((await session.execute(stmt)).all())

    if law_name:
        law_col = Chunk.chunk_metadata["law_name"].as_string()
        rows = await _query(law_col == law_name)
        if not rows:
            rows = await _query(law_col.like(f"%{law_name}"))
    else:
        rows = await _query(None)
    if not rows:
        return []

    # 父块正文：一次批量取回，避免逐条查询。
    parent_ids = list({row.parent_id for row in rows if row.parent_id})
    parents: dict[str, str] = {}
    if parent_ids:
        async with async_session() as session:
            result = await session.execute(
                select(Chunk.id, Chunk.content).where(Chunk.id.in_(parent_ids))
            )
            parents = {row.id: row.content for row in result}

    out: list[dict] = []
    for row in rows:
        out.append({
            "chunk_id": row.id,
            "doc_id": row.doc_id,
            "kb_id": row.kb_id,
            "child_content": row.content,
            "article_content": parents.get(row.parent_id or "", row.content),
            "metadata": row.chunk_metadata if isinstance(row.chunk_metadata, dict) else {},
        })
    return out
