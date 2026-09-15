"""回填 ``documents.validity_status``（法条文档级效力状态）。

为什么需要它
------------
效力状态原本只落在 ``chunks.metadata`` 里——文档级字段被冗余到每个子块。文件列表要按它
做等值过滤，就必须落到 ``documents`` 表上并建索引，否则每次列表都得去扫 chunks 的 JSON
列，那就不叫「快速过滤」了。

这一列由 ``app/startup._auto_migrate_legal_document_columns`` 在进程启动时补出来，但
**不回填**：回填要扫全表，不适合放在每次启动都会跑的路径上。本脚本就是那次显式回填。

它**不重跑抽取、不重算向量**，只是把已经躺在 chunks 里的值搬到 documents 上，因此对
正在跑的全量入库没有破坏性（新入库的文档由管道自己写这一列）。

用法::

    # 1) 先体检：打印将影响的文档数，不做任何修改
    python -m scripts.backfill_document_validity --dry-run

    # 2) 执行（交互确认）
    python -m scripts.backfill_document_validity

    # 3) 自动化执行（跳过确认；**没有 TTY 时必须用这个**，否则 input() 会 EOF）
    python -m scripts.backfill_document_validity --yes

    # 4) 只处理某个库
    python -m scripts.backfill_document_validity --kb-id <kb_id> --yes

幂等：只补 ``status='completed'`` 且 ``validity_status IS NULL`` 的文档，重复执行不会覆盖
已写好的值。跑完再跑一次 ``--dry-run`` 应当报 0。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.pipeline.legal_metadata import VALIDITY_STATUS_LABELS
from app.storage.database import async_session

# 只认纯整数。元数据理论上只有整数，但真出现 'null' / 空串这类值时 ``::int`` 会让整条
# UPDATE 失败——宁可少补一条，也不要整个脚本炸掉。
_NUMERIC = r"^-?[0-9]+$"


def _kb_clause(kb_id: str | None) -> str:
    return " AND d.kb_id = :kb_id" if kb_id else ""


def _params(kb_id: str | None) -> dict[str, str]:
    return {"kb_id": kb_id} if kb_id else {}


async def _pending_count(session, kb_id: str | None) -> int:
    """待回填的文档数：已完成、且效力状态还是空的。"""
    sql = (
        "SELECT count(*) FROM documents d "
        "WHERE d.status = 'completed' AND d.validity_status IS NULL"
        + _kb_clause(kb_id)
    )
    return int(await session.scalar(text(sql), _params(kb_id)) or 0)


async def _recoverable_count(session, kb_id: str | None) -> int:
    """上面那些文档里，chunks 里确实存着一个可取值的文档数。"""
    sql = (
        "SELECT count(DISTINCT c.doc_id) FROM chunks c "
        "JOIN documents d ON d.id = c.doc_id "
        "WHERE d.status = 'completed' AND d.validity_status IS NULL "
        f"AND c.metadata ->> 'validity_status' ~ '{_NUMERIC}'"
        + _kb_clause(kb_id)
    )
    return int(await session.scalar(text(sql), _params(kb_id)) or 0)


async def _distribution(session, kb_id: str | None) -> list[tuple[int | None, int]]:
    sql = (
        "SELECT d.validity_status AS vs, count(*) AS n FROM documents d "
        "WHERE 1 = 1"
        + _kb_clause(kb_id)
        + " GROUP BY d.validity_status ORDER BY n DESC, vs NULLS LAST"
    )
    rows = await session.execute(text(sql), _params(kb_id))
    return [(row.vs, row.n) for row in rows]


def _print_distribution(rows: list[tuple[int | None, int]]) -> None:
    for value, count in rows:
        label = (
            "（空：未解析完成 / 已失败 / 非法条文档）"
            if value is None
            else VALIDITY_STATUS_LABELS.get(value, "未知取值")
        )
        shown = "null" if value is None else str(value)
        print(f"    {shown:>5}  {label:<38} {count:>7}")


async def _backfill(session, kb_id: str | None) -> int:
    """把每个文档**最小 chunk_index** 那份子块上的效力状态搬到 documents 上。"""
    sql = f"""
        UPDATE documents d
           SET validity_status = s.vs
          FROM (
                SELECT DISTINCT ON (c.doc_id)
                       c.doc_id AS doc_id,
                       (c.metadata ->> 'validity_status')::int AS vs
                  FROM chunks c
                  JOIN documents dd ON dd.id = c.doc_id
                 WHERE dd.status = 'completed'
                   AND dd.validity_status IS NULL
                   AND c.metadata ->> 'validity_status' ~ '{_NUMERIC}'
                 ORDER BY c.doc_id, c.chunk_index NULLS LAST
               ) s
         WHERE d.id = s.doc_id
        """
    if kb_id:
        sql += " AND d.kb_id = :kb_id"
    result = await session.execute(text(sql), _params(kb_id))
    return int(result.rowcount or 0)


async def _run(args: argparse.Namespace) -> int:
    kb_id = args.kb_id

    async with async_session() as session:
        pending = await _pending_count(session, kb_id)
        recoverable = await _recoverable_count(session, kb_id)
        before = await _distribution(session, kb_id)

        print("=" * 72)
        print(f"目标范围: {'全部知识库' if not kb_id else kb_id}")
        print(f"已完成但效力状态为空的文档: {pending}")
        print(f"  其中能从 chunks 取到值的: {recoverable}")
        print(f"  取不到值的（保持为空）:   {pending - recoverable}")
        print("当前 documents.validity_status 分布:")
        _print_distribution(before)
        print("=" * 72)

        if pending == 0:
            print("没有需要回填的文档，退出。")
            return 0

        if args.dry_run:
            print("--dry-run：只体检，未做任何修改。")
            return 0

        if not args.yes and not sys.stdin.isatty():
            # 之前踩过这个坑：重定向/管道里 input() 直接 EOF 抛栈，看起来像脚本坏了。
            print("没有交互终端，不能确认。确认要回填请加 --yes。")
            return 2
        if not args.yes:
            answer = input(f"确认回填上述 {recoverable} 份文档？输入 yes 继续，其它取消: ")
            if answer.strip().lower() != "yes":
                print("已取消。")
                return 1

        updated = await _backfill(session, kb_id)
        await session.commit()
        print(f"回填完成: {updated} 份文档写入 validity_status。")

        after = await _distribution(session, kb_id)
        print("回填后 documents.validity_status 分布:")
        _print_distribution(after)
        remaining = await _pending_count(session, kb_id)
        recoverable_left = await _recoverable_count(session, kb_id)
        print(f"仍为空的已完成文档: {remaining}")
        print(f"  其中还能从 chunks 取到值的: {recoverable_left}（正常应为 0）")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="回填 documents.validity_status（不重跑抽取、不重算向量）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只体检并打印计划，不做修改")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认（自动化 / 无 TTY 用）")
    parser.add_argument("--kb-id", default=None, help="只处理指定知识库")
    return await _run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
