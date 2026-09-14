"""从语料推导「城市 → 省份」映射表。

地方法规里 11,812 份只带市名（如「菏泽市人民代表大会常务委员会」），省份要看正文头部
的批准机关（「…山东省第十二届人民代表大会常务委员会…批准」）。本脚本扫语料，把这些
「市 → 省」配对收集起来，按多数票消解冲突，落成
``app/pipeline/city_province.json``（与 ``legal_region`` 同目录）供入库时兜底使用。

**这是一张推导出来的表，不是手写的行政区划表**：它只覆盖语料里出现过的城市，作用是在
正文批准机关缺失时代替那一路证据。冲突（同一城市推出一省以上）与孤例都会写进报告，不静默
取多数——同名不同省的城市必须人工确认。

用法::

    python -m app.scripts.build_city_province_map <corpus> --out app/pipeline/city_province.json
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys

from app.pipeline.legal_region import (
    PROVINCES,
    _CITY_AT_START,
    _province_at_start,
    find_approving_province,
)
from app.pipeline.loaders.docx_xml_text import paragraphs_from_bytes


def _head_text(path: str, limit: int = 12) -> str:
    """取正文前若干段拼成的头部文本；失败返回空串。"""
    try:
        with open(path, "rb") as handle:
            paragraphs = paragraphs_from_bytes(handle.read())
    except Exception:  # noqa: BLE001 — 单份失败不影响整表推导
        return ""
    return "\n".join(paragraphs[:limit])[:2000]


def collect(corpus: str, *, limit: int = 0) -> tuple[dict[str, collections.Counter], int]:
    """扫描语料，返回 ``{城市: Counter(省份)}`` 与处理的文件数。"""
    pairs: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    names = sorted(n for n in os.listdir(corpus) if n.lower().endswith(".docx"))
    processed = 0
    for name in names:
        if limit and processed >= limit:
            break
        path = os.path.join(corpus, name)
        processed += 1
        # 市级：法名不以市名开头时（如「深圳经济特区…」）从标题猜不出来，跳过。
        match = _CITY_AT_START.match(name.split("_")[0])
        if not match or _province_at_start(name):
            continue
        city = match.group(1) + "市"
        province = find_approving_province(_head_text(path))
        if province:
            pairs[city][province] += 1
    return pairs, processed


def build(pairs: dict[str, collections.Counter]) -> tuple[dict[str, str], list[str]]:
    """按多数票生成映射；冲突与孤例写进报告行。"""
    mapping: dict[str, str] = {}
    notes: list[str] = []
    for city, counter in sorted(pairs.items()):
        province, votes = counter.most_common(1)[0]
        mapping[city] = province
        if len(counter) > 1:
            notes.append(
                "冲突: %s → %s（多数票 %d，其它 %s）"
                % (city, province, votes,
                   ", ".join(f"{p}:{c}" for p, c in counter.most_common()[1:]))
            )
        elif votes == 1:
            notes.append(f"孤例: {city} → {province}（仅 1 次证据）")
    return mapping, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从语料推导城市→省份映射表")
    parser.add_argument("corpus", help="解压后的目录")
    # 放在 pipeline/ 下而不是 pipeline/data/：.gitignore 的 ``data/`` 规则会忽略任意层级的
    # data 目录，而这张表是随代码提交的静态资源，不是运行时数据。
    parser.add_argument("--out", default="app/pipeline/city_province.json")
    parser.add_argument("--report", default="", help="冲突/孤例报告输出路径（默认不写）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 份（0=全部）")
    args = parser.parse_args(argv)

    pairs, processed = collect(args.corpus, limit=args.limit)
    mapping, notes = build(pairs)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(mapping, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    if args.report:
        with open(args.report, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["kind", "detail"])
            for note in notes:
                writer.writerow([note.split(":", 1)[0], note])

    provinces = {p for c in pairs.values() for p in c}
    print(f"扫描 {processed} 份，得到 {len(mapping)} 个城市 → {len(provinces)} 个省份")
    print(f"写出 {os.path.abspath(args.out)}")
    conflicts = [n for n in notes if n.startswith("冲突")]
    singles = [n for n in notes if n.startswith("孤例")]
    print(f"冲突 {len(conflicts)} 条、孤例 {len(singles)} 条"
          + (f"，报告：{os.path.abspath(args.report)}" if args.report else ""))
    for note in conflicts[:5]:
        print("   " + note)
    unknown = [p for p in provinces if p not in PROVINCES]
    if unknown:
        print("   非预期省份名（需人工检查）:", unknown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
