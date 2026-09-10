"""全局法条库的解析（法条库部署专用）。

本部署是**单租户**部署：全局法条库全租户一个，个人库由下游传入。
检索时全局库必须**默认并入**检索范围——调用方只传个人库的 ``kb_ids``，
全局库由服务端补上，理由见 ``docs/legal-recall-implementation-plan.md`` 的 D5/D6。

同一个模块也提供结果来源判定所需的信息：命中的 chunk 属于全局库时
``metadata.source`` 为 ``"global"``，否则为 ``"personal"``。
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.storage.database import async_session

logger = logging.getLogger(__name__)

# 全局法条库的识别标记，写在 ``KnowledgeBase.config`` 里。
DEFAULT_LEGAL_KB_FLAG = "is_default_legal_kb"


def _is_default_legal_kb(config: object) -> bool:
    """``config`` 中是否带全局法条库标记。

    容忍布尔与字符串两种写法（JSON 里可能是 ``true`` 也可能是 ``"true"``），
    字符串 ``"false"`` 不算命中。
    """
    if not isinstance(config, dict):
        return False
    value = config.get(DEFAULT_LEGAL_KB_FLAG)
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


async def resolve_global_legal_kb_ids() -> list[str]:
    """返回全局法条库的 id 列表（正常 0 或 1 个）。

    任何异常都退化为空列表：解析全局库失败不应让检索整体不可用。调用方拿到
    空列表时行为与改造前一致（只查调用方指定的范围）。
    """
    try:
        from app.schema.db import KnowledgeBase

        async with async_session() as session:
            rows = await session.execute(
                select(KnowledgeBase.id, KnowledgeBase.config)
            )
            return [kb_id for kb_id, config in rows.all() if _is_default_legal_kb(config)]
    except Exception as e:  # noqa: BLE001 - 增量能力，失败应安全降级
        logger.warning("解析全局法条库失败，本次检索不并入全局库: %s", e)
        return []
