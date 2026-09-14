"""法条语料元数据全量核对。

三个用途：

1. **逐份核对抽取结果**（法名 / 条号数 / 字段来源）——方案决策 22 的自动化版本。
2. **输出 docx 属性与正文解析的冲突清单**——阶段一、二把来源换成内嵌属性后的验证。
3. **输出同法名多版本的选版预览与人工复核队列**——阶段三选版流程的输入。

两种模式：

- ``fast``（默认）：只用 ``zipfile`` 解 ``docProps`` 与 ``word/document.xml``，
  按 ``<w:p>`` 切段落后走 ``TextCleaner`` 与法条预处理。全量 3 万份约一分钟。
  表格内段落按文档顺序一并计入——与 ``DocxLoader`` 现在的行为一致（它也会读表格）。
  已知差异：正则切段不等价于 python-docx 的段落模型，个别文档条数会差 1。
- ``full``：跑真实入库链路（loader → cleaner → chunker → 大小护栏 → 元数据抽取），
  额外校验 ``chunk_metadata`` 键是否齐全、条号是否正确落到子块。慢，配合
  ``--limit`` 抽样使用。

语料既可以是解压后的目录，也可以是原始 zip。两者文件名形态不同：zip 由 macOS
打包、未置 UTF-8 标志位，条目名是「UTF-8 字节被 cp437 解码」的结果，必须还原
（见 :func:`decode_zip_name`）；解压后的目录名是正确的。

输出（``--out`` 目录）：``files.csv`` 逐份清单、``groups.csv`` 分组选版结果、
``manual_review.csv`` 人工复核队列、``summary.json`` 汇总。报告是可丢弃的派生物，
不要提交进仓库。

用法::

    python -m app.scripts.audit_legal_metadata <corpus> --out legal_audit_out
    python -m app.scripts.audit_legal_metadata <corpus> --mode full --limit 500
    python -m app.scripts.audit_legal_metadata <corpus> --mode full --sample 2000
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import os
import random
import re
import statistics
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from typing import Callable, Iterator

from app.pipeline.docx_meta import DocxProps, extract_docx_props, extract_docx_props_from_bytes
from app.pipeline.legal_metadata import analyze_legal_document, parse_legal_header
from app.pipeline.legal_terms import ARTICLE_LINE_PATTERN
from app.pipeline.loaders.docx_xml_text import paragraphs_from_bytes
_DOCX_SUFFIX = ".docx"
_MACOSX_PREFIX = "__MACOSX"

# 选版复核原因
REVIEW_NO_STATUS_3 = "no_status_3"          # 组内没有任何 validity_status == 3 的版本
REVIEW_TIED_DATE = "tied_publish_date"      # 最新的 publish_date 有并列
REVIEW_NO_DATE = "missing_publish_date"     # 组内所有候选都没有 publish_date
REVIEW_SOLO_INACTIVE = "solo_inactive"      # 单版本且 validity_status == 0（孤本废止类）


def decode_zip_name(name: str, flag_bits: int) -> str:
    """还原 zip 条目名。

    压缩包若置了 UTF-8 标志位（``0x800``）或名字是纯 ASCII，原样返回；否则按
    「UTF-8 字节被 cp437 解码」还原。还原失败（结果不是合法 UTF-8）时保留原名，
    宁可留下乱码也不改成另一个错名字。
    """
    if flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def iter_corpus(
    corpus: str, *, sample: int = 0, seed: int = 0
) -> Iterator[tuple[str, Callable[[], bytes]]]:
    """遍历语料，产出 ``(文件名, 读取字节的回调)``。

    目录按文件名排序；zip 按条目顺序、跳过 ``__MACOSX`` 与目录项。
    ``sample`` > 0 时按固定 ``seed`` 做可复现的随机抽样（再按文件名排序输出），
    用于「full 模式跑全量太慢、又不想被文件名顺序带偏」的场景。
    """
    entries = list(_iter_all(corpus))
    if sample and 0 < sample < len(entries):
        entries = sorted(
            random.Random(seed).sample(entries, sample), key=lambda item: item[0]
        )
    yield from entries


def _iter_all(corpus: str) -> Iterator[tuple[str, Callable[[], bytes]]]:
    if os.path.isdir(corpus):
        names = sorted(n for n in os.listdir(corpus) if n.lower().endswith(_DOCX_SUFFIX))
        for name in names:
            path = os.path.join(corpus, name)
            yield name, (lambda p=path: open(p, "rb").read())
        return

    archive = zipfile.ZipFile(corpus)
    for info in archive.infolist():
        if info.is_dir() or info.filename.startswith(_MACOSX_PREFIX):
            continue
        if not info.filename.lower().endswith(_DOCX_SUFFIX):
            continue
        name = decode_zip_name(info.filename.split("/")[-1], info.flag_bits)
        yield name, (lambda n=info.filename: archive.read(n))


def normalize_law_name(law_name: str | None) -> str:
    """分组键的归一化：去掉一切空白（含全角空格）。"""
    if not law_name:
        return ""
    return re.sub(r"\s+", "", law_name.replace("\u3000", " "))


@dataclass
class DocRecord:
    """一份文档的核对结果（阶段一/二字段 + 阶段三选版标注）。"""

    filename: str
    law_name: str | None = None
    law_name_rule: str | None = None
    authority: str | None = None
    authority_rule: str | None = None
    publish_date: str | None = None
    publish_date_rule: str | None = None
    effective_date: str | None = None
    validity_status: int | None = None
    law_type: str | None = None
    external_id: str | None = None
    source_code: str | None = None
    meta_source: str = "rule"
    props_origin: str = ""
    # 地域（由 legal_region 解析；PRD 的层级/地域过滤与本次入库清单都要用）
    province: str | None = None
    city: str | None = None
    diff_fields: str = ""
    article_count: int = 0
    char_count: int = 0
    error: str = ""
    # full 模式补充
    chunks: int = 0
    # 父块数 = 「一条文一子块」策略下的子块数（用于比较切分粒度，见 2.1 节的容量表）
    parents: int = 0
    long_parents: int = 0
    long_parent_split_total: int = 0
    article_chunks: int = 0
    keys_ok: bool = False
    # 选版
    group_key: str = ""
    group_size: int = 1
    selected: bool = True
    review_reason: str = ""


def _diff_fields(rule_header, merged_header) -> str:
    diffs = [
        name
        for name, rule_value, merged_value in (
            ("law_name", rule_header.law_name, merged_header.law_name),
            ("issuing_authority", rule_header.issuing_authority, merged_header.issuing_authority),
            ("publish_date", rule_header.publish_date, merged_header.publish_date),
        )
        if rule_value != merged_value
    ]
    return ",".join(diffs)


def analyze_bytes(data: bytes, props: DocxProps | None) -> tuple[DocRecord, object]:
    """fast 模式：只用 zipfile 解文本，跑清洗与法条预处理。"""
    content = "\n\n".join(paragraphs_from_bytes(data))
    try:
        from app.pipeline.cleaner import TextCleaner, strip_data_uris

        content = strip_data_uris(
            TextCleaner().clean(content=content, page_texts=None, page_blocks=None)
        )
    except Exception:  # noqa: BLE001 — 清洗失败就用原始文本，核对不因此中断
        pass
    return _record_from_text(content, props)


def _record_from_text(content: str, props: DocxProps | None) -> tuple[DocRecord, object]:
    rule_header = parse_legal_header(content)
    analysis = analyze_legal_document(content, props=props)
    header = analysis.header
    record = DocRecord(
        filename="",
        law_name=header.law_name,
        law_name_rule=rule_header.law_name,
        authority=header.issuing_authority,
        authority_rule=rule_header.issuing_authority,
        publish_date=header.publish_date,
        publish_date_rule=rule_header.publish_date,
        effective_date=header.effective_date,
        validity_status=header.validity_status,
        law_type=header.law_type,
        external_id=header.external_id,
        source_code=header.source_code,
        meta_source=header.meta_source,
        props_origin=(props.origin if props else ""),
        province=header.province,
        city=header.city,
        diff_fields=_diff_fields(rule_header, header),
        article_count=sum(1 for _ in ARTICLE_LINE_PATTERN.finditer(analysis.text)),
        char_count=len(analysis.text),
    )
    return record, analysis


def audit_fast(filename: str, data: bytes) -> DocRecord:
    try:
        props = extract_docx_props_from_bytes(data)
        record, _ = analyze_bytes(data, props)
    except Exception as exc:  # noqa: BLE001 — 单份文件失败不中断全量核对
        record = DocRecord(filename=filename, error=f"{type(exc).__name__}: {exc}")
        return record
    record.filename = filename
    return record


def audit_full(filename: str, path: str) -> DocRecord:
    """full 模式：真实入库链路（不含 embed / 写索引）。"""
    from dataclasses import asdict as _asdict

    from app.pipeline.chunkers.laws import LawsChunker
    from app.pipeline.chunker import DEFAULT_CHILD_SIZE, enforce_size_limits
    from app.pipeline.cleaner import TextCleaner, strip_data_uris
    from app.pipeline.legal_metadata import LegalMetadataExtractor
    from app.pipeline.loaders.docx_loader import DocxLoader
    from app.pipeline.metadata import MetadataExtractor

    try:
        props = extract_docx_props(path)
        load_result = DocxLoader().load(path)
        content = strip_data_uris(
            TextCleaner().clean(
                content=load_result.content, page_texts=None, page_blocks=None
            )
        )
        record, analysis = _record_from_text(content, props)
        chunk_result = enforce_size_limits(
            LawsChunker().chunk(analysis.text, load_result.metadata)
        )
        metadata_list = MetadataExtractor().extract(
            child_chunks=chunk_result.child_chunks,
            parent_chunks=chunk_result.parent_chunks,
            parent_child_map=chunk_result.parent_child_map,
            doc_metadata=load_result.metadata,
            page_texts=None,
        )
        legal = LegalMetadataExtractor().extract(
            child_chunks=chunk_result.child_chunks,
            parent_chunks=chunk_result.parent_chunks,
            parent_child_map=chunk_result.parent_child_map,
            section_paths=[m.section_path for m in metadata_list],
            analysis=analysis,
        )
        need = {
            "effective_date", "validity_status", "law_type",
            "external_id", "source_code", "meta_source",
        }
        record.chunks = len(legal)
        # 「一条文一子块」的对照口径：父块本身若不超 child_size 就是一个子块，超了仍会被
        # 护栏按 child_size 再切，所以要把这部分单独算出来，否则会低估。
        parents = chunk_result.parent_chunks
        long_parents = [p for p in parents if len(p) > DEFAULT_CHILD_SIZE]
        record.parents = len(parents)
        record.long_parents = len(long_parents)
        record.long_parent_split_total = sum(
            math.ceil(len(p) / DEFAULT_CHILD_SIZE) for p in long_parents
        )
        record.article_chunks = sum(1 for m in legal if m.get("article_number") is not None)
        record.keys_ok = bool(legal) and all(need <= set(_asdict(metadata_list[i]) | m)
                                             for i, m in enumerate(legal))
    except Exception as exc:  # noqa: BLE001
        record = DocRecord(filename=filename, error=f"{type(exc).__name__}: {exc}")
    record.filename = filename
    return record


def select_versions(records: list[DocRecord]) -> None:
    """就地标注选版结果（阶段三预览，不删除任何数据）。

    规则：组内优先取 ``validity_status == 3``，再按 ``publish_date`` 取最新；
    并列 / 无 ``3`` / 无日期都进人工复核队列。单版本且 ``validity_status == 0``
    的文档（多为废止决定）标为 ``solo_inactive``——它们的正文本身就是有用信息，
    不能仅凭该状态自动删除。
    """
    groups: dict[str, list[DocRecord]] = {}
    for index, record in enumerate(records):
        key = normalize_law_name(record.law_name) or f"\x00{record.filename}\x00{index}"
        record.group_key = key
        groups.setdefault(key, []).append(record)

    for key, members in groups.items():
        for member in members:
            member.group_size = len(members)
        if len(members) == 1:
            only = members[0]
            only.selected = True
            if only.validity_status == 0:
                only.review_reason = REVIEW_SOLO_INACTIVE
            continue

        active = [m for m in members if m.validity_status == 3]
        pool = active or members
        reason = "" if active else REVIEW_NO_STATUS_3
        best_date = max((m.publish_date or "") for m in pool)
        top = [m for m in pool if (m.publish_date or "") == best_date]
        if not best_date:
            reason = REVIEW_NO_DATE
        elif len(top) > 1 and not reason:
            reason = REVIEW_TIED_DATE
        winner = top[0]
        for member in members:
            member.selected = member is winner
        winner.review_reason = reason


def summarize(records: list[DocRecord], *, corpus: str, mode: str) -> dict:
    ok = [r for r in records if not r.error]
    articles = [r.article_count for r in ok]
    kept = [r for r in ok if r.selected]
    dropped = [r for r in ok if not r.selected and r.group_size > 1]
    review = [r for r in ok if r.review_reason]
    return {
        "corpus": corpus,
        "mode": mode,
        "documents": len(records),
        "errors": len(records) - len(ok),
        "props_missing": sum(1 for r in ok if not r.props_origin),
        "meta_source": _counts(r.meta_source for r in ok),
        "law_type": _counts(r.law_type for r in ok),
        "validity_status": _counts(r.validity_status for r in ok),
        "effective_date_source": {
            "props": sum(1 for r in ok if r.effective_date),
            "none": sum(1 for r in ok if not r.effective_date),
        },
        "region": {
            "with_province": sum(1 for r in ok if r.province),
            "city_only": sum(1 for r in ok if r.city and not r.province),
            "none": sum(1 for r in ok if not r.province and not r.city),
            "top_provinces": _counts(r.province for r in ok if r.province),
        },
        "field_differs_from_body_parse": {
            name: sum(1 for r in ok if name in r.diff_fields.split(","))
            for name in ("law_name", "issuing_authority", "publish_date")
        },
        "articles": {
            "total": sum(articles),
            "documents_without_article": sum(1 for a in articles if a == 0),
            "median_per_document": statistics.median(articles) if articles else 0,
            "p90_per_document": _percentile(articles, 0.9),
        },
        "version_selection": {
            "groups": len({r.group_key for r in ok}),
            "multi_version_groups": len(
                {r.group_key for r in ok if r.group_size > 1}
            ),
            "documents_kept": len(kept),
            "documents_dropped": len(dropped),
            "articles_kept": sum(r.article_count for r in kept),
            "articles_dropped": sum(r.article_count for r in dropped),
            "review_reasons": _counts(r.review_reason for r in review),
        },
        "full_mode": {
            "chunks": sum(r.chunks for r in ok),
            "article_chunks": sum(r.article_chunks for r in ok),
            "documents_with_complete_keys": sum(1 for r in ok if r.keys_ok),
            # 对照口径：若「一条文一子块」（父块即子块），子块总数会是多少。
            "one_child_per_parent": sum(
                r.parents - r.long_parents + r.long_parent_split_total for r in ok
            ),
        },
    }


def _counts(values) -> dict:
    counted: dict = {}
    for value in values:
        key = "None" if value is None else str(value)
        counted[key] = counted.get(key, 0) + 1
    return dict(sorted(counted.items(), key=lambda kv: -kv[1]))


def _percentile(values: list[int], q: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


def write_reports(records: list[DocRecord], summary: dict, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    with open(
        os.path.join(out_dir, "files.csv"), "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(DocRecord.__dataclass_fields__))
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))

    groups: dict[str, list[DocRecord]] = {}
    for record in records:
        groups.setdefault(record.group_key, []).append(record)
    with open(
        os.path.join(out_dir, "groups.csv"), "w", encoding="utf-8-sig", newline=""
    ) as handle:
        fields = [
            "group_key", "group_size", "selected_filename", "selected_publish_date",
            "selected_effective_date", "selected_validity_status", "review_reason",
            "dropped_filenames",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for key, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            winner = next((m for m in members if m.selected), members[0])
            writer.writerow({
                "group_key": key,
                "group_size": len(members),
                "selected_filename": winner.filename,
                "selected_publish_date": winner.publish_date,
                "selected_effective_date": winner.effective_date,
                "selected_validity_status": winner.validity_status,
                "review_reason": winner.review_reason,
                "dropped_filenames": " | ".join(
                    m.filename for m in members if not m.selected
                ),
            })

    with open(
        os.path.join(out_dir, "manual_review.csv"), "w", encoding="utf-8-sig", newline=""
    ) as handle:
        fields = ["reason", "filename", "law_name", "law_type", "publish_date",
                  "effective_date", "validity_status", "group_size", "external_id"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            if record.review_reason:
                writer.writerow({"reason": record.review_reason, **{
                    key: getattr(record, key) for key in fields[1:]
                }})


def write_ingest_list(records: list[DocRecord], path: str) -> int:
    """写出「入库清单」：版本选版后保留的文档，含地域与条文数。

    这是容量方案的落地物——首次入库按这份清单上传，被淘汰的旧版本根本不进库，
    因此省下一次 embedding（全量 vs 选版后差约 31% 条文行）。人工复核队列不在这里，
    它们在 ``manual_review.csv``。
    """
    fields = [
        "filename", "law_name", "law_type", "province", "city",
        "publish_date", "effective_date", "validity_status", "external_id",
        "article_count", "group_size", "review_reason",
    ]
    kept = [r for r in records if r.selected and not r.error]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in kept:
            writer.writerow({key: getattr(record, key) for key in fields})
    return len(kept)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="法条语料元数据全量核对")
    parser.add_argument("corpus", help="解压后的目录或原始 zip")
    parser.add_argument("--mode", choices=("fast", "full"), default="fast")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--limit", type=int, default=0, help="只处理前 N 份（0=全部）")
    scope.add_argument(
        "--sample", type=int, default=0, help="随机抽 N 份处理（配合 --seed 可复现）"
    )
    parser.add_argument("--seed", type=int, default=0, help="抽样种子（默认 0）")
    parser.add_argument("--out", default="legal_audit_out", help="报告输出目录")
    parser.add_argument(
        "--ingest-list", default="",
        help="写出入库清单 CSV（版本选版后保留的文档）；留空则只出报告",
    )
    args = parser.parse_args(argv)

    records: list[DocRecord] = []
    temp_dir = tempfile.mkdtemp(prefix="legal_audit_") if args.mode == "full" else None
    try:
        for index, (filename, read_bytes) in enumerate(
            iter_corpus(args.corpus, sample=args.sample, seed=args.seed)
        ):
            if args.limit and index >= args.limit:
                break
            if args.mode == "fast":
                records.append(audit_fast(filename, read_bytes()))
            else:
                path = os.path.join(temp_dir or ".", filename)
                with open(path, "wb") as handle:
                    handle.write(read_bytes())
                records.append(audit_full(filename, path))
    finally:
        if temp_dir:
            for name in os.listdir(temp_dir):
                try:
                    os.remove(os.path.join(temp_dir, name))
                except OSError:
                    pass
            os.rmdir(temp_dir)

    select_versions(records)
    summary = summarize(records, corpus=args.corpus, mode=args.mode)
    summary["scope"] = {
        "limit": args.limit, "sample": args.sample, "seed": args.seed,
    }
    write_reports(records, summary, args.out)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n报告目录: {os.path.abspath(args.out)}")
    if args.ingest_list:
        kept = write_ingest_list(records, args.ingest_list)
        print(f"入库清单: {os.path.abspath(args.ingest_list)}（{kept} 份）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
