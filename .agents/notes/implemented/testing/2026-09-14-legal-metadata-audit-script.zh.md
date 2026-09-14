# Agent Note: 法条语料元数据核对脚本

Status: implemented

## Problem

本部署的验证手段是**全量核对**：决策记录要求逐份核对每个文档的法名与条号数，并
明确不设百分比验收目标。但这件事一直没有工具——347 份样本是人工看的，而上线语料
是 29,957 份。

没有工具就无法回答三个问题：

- docx 属性值与正文解析到底在哪些字段上不一致？
- 语料实际产出多少 child chunk？对照 `kb_chunk_cap`（1,000,000）是什么量级？
- 在删除任何东西之前，版本选版会淘汰什么？

## Decision

`app/scripts/audit_legal_metadata.py` 接受「解压后的目录」或「原始 zip」两种语料形态，
输出 `files.csv` / `groups.csv` / `manual_review.csv` / `summary.json`。

- `fast`（默认）：只用 `zipfile` 读 `docProps` 与 `word/document.xml`，重建段落后走
  `TextCleaner` 与法条预处理，记录法名、条号数、字段来源，以及属性值与正文解析不
  一致的字段。全量约 78 秒。
- `full`：跑真实入库链路（loader → cleaner → chunker → 大小护栏 → 元数据抽取），
  额外报告 chunk 数与「每个 chunk 是否带齐 6 个新键」。单份约 51 ms，只适合抽样。

zip 条目名在未置 UTF-8 标志位时按 cp437 误读还原（`decode_zip_name`），因此 zip 语料
与解压目录给出同样的法名。fast 模式剔除 `<w:tbl>` 整块，并把文本元素匹配写成
`<w:t>` 加可选属性——因为 `DocxLoader` 读的是 `doc.paragraphs`，它看不到表格内段落。

`select_versions` 产出的是**预览**，不是删除指令：同一法名内优先取
`validity_status == 3`，再取最新的 `publish_date`；组内没有有效版本、日期并列、
或完全没有日期，都标记进人工复核。单版本且 `validity_status == 0` 的文档标为
`solo_inactive` 并保持 `selected = true`——1,902 份「修改、废止的决定」里有 1,853 份
落在这个桶里，它们的正文就是"某法被废止"的唯一记录。

## Alternatives considered

**全部用真实 loader（一律 full 模式）。** 默认否决：单份约 51 ms，全量约 25 分钟，
而 fast 模式 78 秒。`--mode full` 保留给抽样，两者不一致时以 full 为准。

**沿用本次工作中临时写的 `w:t` 扫描。** 否决：它把每个文本 run 当成一行，且
`<w:t[^>]*>` 这个模式还会匹配 `<w:tbl>` / `<w:tc>` / `<w:tab/>`，于是 XML 标记渗进了
解析字段。发现方式正是核对脚本自己的 CSV——某些文档的 `authority_rule` 里出现了
`<w:autoSpaceDE/>` 这样的原始标签，这也是"输出逐份 CSV"最有力的论据。

**把选版放进入库链路。** 当前否决：这只是预览报告。真正的删除策略属于版本治理阶段，
并且依赖数据源至今未公布的 `validity_status` 枚举语义。

**把 `validity_status == 0` 的文档一律自动删除。** 否决：有 2,092 份是该状态，而其中
没有同法名兄弟版本的，多数是废止决定——它们的正文是"某法被废止"的唯一记录。

**只打印汇总。** 否决：核对按定义就是逐份的，报告必须是人工能扫的逐文件表。

## Consequences

语料事实从"凭印象"变成"可复现"，并且推翻了本方案原先的容量估算：2000 份 `full`
抽样测得每条条文 **2.25** 个 child chunk、每份无条文文档 **30.9** 个，据此推算全量
约 **267 万** chunk、版本选版后约 **185 万**，而上限是 100 万；347 份样本当初的估算
是 2.78 万。`docs/legal-recall-implementation-plan.md` 的 2.1 节记录了实测值与三条
出路（调大 cap、按类别拆库、或不再按「款」细分条文子块）。

同一次核对还量化了一个既有的入库阻塞——**88 份文档（0.29%）无法入库**——该问题随后已修复，
见 [DocxLoader 容错：损坏的包声明与表格正文](../bug-fix/2026-09-14-docx-loader-tolerates-damaged-and-table-bodies.zh.md)。
核对的价值在于让缺陷可度量：51 份损坏包由静态检测筛出、再用真实 `DocxLoader` 逐份复核
（无误报），37 份表格正文文档则是通过"条文数=0"这一列暴露出来的。

选版规则现在写在代码里，这是等 `validity_status` 枚举确认后要重新审视的决策。它的
输出是候选清单而不是指令：真实语料上 `manual_review.csv` 有 2,365 行（`solo_inactive`
1,984、无有效版本的组 379、日期并列 2）。

fast 模式是管线文本的近似：不走 python-docx、不做 OCR、除 `TextCleaner` 外不做
额外清洗。关于 chunk 形态的结论必须来自 full 模式。报告是可丢弃的派生物，不提交。

## Testing

`tests/test_legal_audit.py` 钉住 zip 条目名还原（含"还原结果不是合法 UTF-8 时保留
原名"这一支）、段落抽取（含标记渗漏与表格剔除两个用例）、fast 核对记录、选版规则的
每个分支，以及报告写出。

真实语料上：fast 模式跑 29,957 份返回 0 异常、0 缺失属性、1,141,002 行条文、
3,227 份无条文结构文档；full 模式跑 2000 份产出 182,589 个 chunk，且每份产出 chunk 的
文档的每个 chunk 都带齐 6 个新元数据键。
