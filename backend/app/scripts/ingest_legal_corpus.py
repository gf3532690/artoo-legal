"""按入库清单批量上传法条语料。

清单由 ``audit_legal_metadata.py --ingest-list`` 产出（版本选版后保留的文档），本脚本
只负责上传与对账，不做选版判断——这样"入库什么"与"怎么入库"分开，两边各自可复现。

关键性质：

- **幂等/可断点续跑**：已提交过的文件名记在 ``--state`` 里，重跑自动跳过；服务端本身
  也按 ``file_hash`` 去重（重复上传返回 ``status="duplicate"``），两层保险。
- **预检**：文件是否存在、扩展名与体积是否被服务端接受、文件名能否通过服务端校验
  （直接复用 ``app.api.validators``，避免规则漂移）。``--dry-run`` 只跑预检。
- **分批**：默认每批 20 份。上传接口是逐文件建 Document 行并入队，批太大只会让队列更长，
  不会更快；配合 ``--limit`` 可以先小批试跑。
- **对账**：``--verify`` 拉取该库的文档列表，按文件名与清单比对，报告缺失与失败。

用法::

    # 预检
    python -m app.scripts.ingest_legal_corpus --kb-id <kb> --list ingest_list.csv \
        --corpus <corpus_dir> --dry-run
    # 试跑 50 份
    python -m app.scripts.ingest_legal_corpus --kb-id <kb> --list ingest_list.csv \
        --corpus <corpus_dir> --api-key sk-xxx --limit 50
    # 对账
    python -m app.scripts.ingest_legal_corpus --kb-id <kb> --list ingest_list.csv --verify
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

import httpx

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def read_ingest_list(path: str) -> list[str]:
    """读入库清单，返回文件名列表（保持清单顺序）。

    兼容两种形态：``audit_legal_metadata.py --ingest-list`` 产出的带表头 CSV，以及一行
    一个文件名的纯文本。
    """
    with open(path, encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        if "filename" in sample.splitlines()[0]:
            names = [row["filename"].strip() for row in csv.DictReader(handle)]
        else:
            names = [line.strip() for line in handle]
    return [name for name in names if name]


def preflight(
    names: list[str], corpus: str, *, max_file_mb: float
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """预检清单；返回 ``(可用文件, [(文件名, 原因码, 说明)])``。

    文件名规则复用服务端的 ``validate_filename``：上传接口会对不合规的名字直接 skip，
    在这里先发现比事后从 2 万行结果里找要省事。
    """
    from app.api.validators import NameValidationError, validate_filename

    usable: list[str] = []
    problems: list[tuple[str, str, str]] = []
    limit_bytes = int(max_file_mb * 1024 * 1024)
    for name in names:
        # 先判名字：名字不合规时服务端会直接 skip，这比"文件不存在"更可操作。
        try:
            validate_filename(name)
        except NameValidationError as exc:
            problems.append((name, "bad_name", exc.message))
            continue
        if not name.lower().endswith(".docx"):
            problems.append((name, "extension", "非 .docx，法条元数据会缺失"))
            continue
        path = os.path.join(corpus, name)
        if not os.path.isfile(path):
            problems.append((name, "missing", "语料目录里找不到该文件"))
            continue
        size = os.path.getsize(path)
        if size > limit_bytes:
            problems.append((name, "too_large", f"{size / 1024 / 1024:.1f} MB 超过上限"))
            continue
        usable.append(name)
    return usable, problems


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def upload_batch(
    client: httpx.Client,
    *,
    base_url: str,
    kb_id: str,
    corpus: str,
    names: list[str],
) -> list[dict]:
    """上传一批文件，返回 ``[{filename, status, message, doc_id}]``。"""
    files = []
    for name in names:
        with open(os.path.join(corpus, name), "rb") as handle:
            files.append(("files", (name, handle.read(), _DOCX_MIME)))
    response = client.post(
        f"{base_url.rstrip('/')}/api/knowledge-bases/{kb_id}/documents/upload-folder",
        files=files,
        data={"paths": json.dumps(names, ensure_ascii=False)},
    )
    response.raise_for_status()
    payload = response.json()
    by_name = {}
    for item in payload.get("results", []):
        by_name[item.get("filename")] = item
    rows: list[dict] = []
    for name in names:
        item = by_name.get(name, {})
        rows.append({
            "filename": name,
            "status": item.get("status", "unknown"),
            "message": item.get("message") or "",
            "doc_id": item.get("doc_id") or "",
        })
    return rows


def verify(base_url: str, kb_id: str, api_key: str, names: list[str], *, timeout: float) -> dict:
    """拉文档列表与清单比对，返回缺失/失败清单。"""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    known: dict[str, str] = {}
    page = 1
    with httpx.Client(timeout=timeout, headers=headers) as client:
        while True:
            response = client.get(
                f"{base_url.rstrip('/')}/api/knowledge-bases/{kb_id}/documents",
                params={"page": page, "page_size": 100},
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items", [])
            for item in items:
                known[item.get("filename", "")] = item.get("status", "")
            if not items or len(known) >= int(payload.get("total", 0)):
                break
            page += 1
    missing = [name for name in names if name not in known]
    failed = [name for name in names if known.get(name) == "failed"]
    return {
        "documents_in_kb": len(known),
        "expected": len(names),
        "missing": missing,
        "failed": failed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按入库清单批量上传法条语料")
    parser.add_argument("--list", required=True, help="入库清单 CSV（或一行一个文件名）")
    parser.add_argument("--corpus", default="", help="语料目录（上传与预检必需）")
    parser.add_argument("--kb-id", default="", help="目标知识库 ID（上传与对账必需）")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default=os.environ.get("ARK_API_KEY", ""))
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份（0=全部）")
    parser.add_argument("--retries", type=int, default=2, help="单批失败重试次数")
    parser.add_argument("--max-file-mb", type=float, default=10.0, help="与服务端上限一致")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--state", default="", help="已提交文件名记录（断点续跑）")
    parser.add_argument("--report", default="", help="逐文件结果 CSV")
    parser.add_argument("--dry-run", action="store_true", help="只做预检，不发请求")
    parser.add_argument("--verify", action="store_true", help="只对账，不上传")
    args = parser.parse_args(argv)

    names = read_ingest_list(args.list)
    if args.limit:
        names = names[:args.limit]

    if args.verify:
        if not args.kb_id:
            parser.error("--verify 需要 --kb-id")
        result = verify(args.base_url, args.kb_id, args.api_key, names, timeout=args.timeout)
        print(json.dumps({k: (v if not isinstance(v, list) else v[:20]) for k, v in result.items()},
                         ensure_ascii=False, indent=2))
        print(f"缺失 {len(result['missing'])} 份、失败 {len(result['failed'])} 份")
        return 0

    if not args.corpus:
        parser.error("上传需要 --corpus")
    usable, problems = preflight(names, args.corpus, max_file_mb=args.max_file_mb)
    print(f"清单 {len(names)} 份：可用 {len(usable)}，预检有问题 {len(problems)}")
    for name, reason, detail in problems[:10]:
        print(f"   [{reason}] {name} — {detail}")
    if args.dry_run:
        return 0

    if not args.kb_id:
        parser.error("上传需要 --kb-id")
    submitted: set[str] = set()
    if args.state and os.path.isfile(args.state):
        with open(args.state, encoding="utf-8") as handle:
            submitted = set(json.load(handle).get("submitted", []))
    pending = [name for name in usable if name not in submitted]
    print(f"已提交 {len(submitted)} 份，本次待上传 {len(pending)} 份，批次 {args.batch_size}")

    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    rows: list[dict] = []
    with httpx.Client(timeout=args.timeout, headers=headers) as client:
        for index, batch in enumerate(_batches(pending, args.batch_size), start=1):
            for attempt in range(args.retries + 1):
                try:
                    rows.extend(upload_batch(
                        client, base_url=args.base_url, kb_id=args.kb_id,
                        corpus=args.corpus, names=batch,
                    ))
                    submitted.update(batch)
                    break
                except Exception as exc:  # noqa: BLE001 — 网络/服务端错误都要记进报告
                    if attempt >= args.retries:
                        rows.extend({
                            "filename": name, "status": "error",
                            "message": f"{type(exc).__name__}: {exc}", "doc_id": "",
                        } for name in batch)
                    else:
                        time.sleep(min(2 ** attempt, 10))
            if index % 10 == 0 or index == len(_batches(pending, args.batch_size)):
                ok = sum(1 for r in rows if r["status"] in ("uploaded", "duplicate"))
                print(f"   批次 {index}：已成功 {ok}/{len(rows)}")
            if args.state:
                with open(args.state, "w", encoding="utf-8") as handle:
                    json.dump({"submitted": sorted(submitted)}, handle, ensure_ascii=False)

    if args.report:
        with open(args.report, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["filename", "status", "message", "doc_id"]
            )
            writer.writeheader()
            writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("结果:", counts)
    if problems:
        print(f"预检跳过 {len(problems)} 份（见上方原因）")
    if args.report:
        print("逐文件结果:", os.path.abspath(args.report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
