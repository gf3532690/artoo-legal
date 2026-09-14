# Agent Note: 过滤用标量字段必须在首次入库前定

Status: proposed

## Problem

法条检索 PRD 要求按**效力层级**（仅法律 / 含行政法规 / 全部）与**省份或城市**过滤结果，并要求
法律层级排在司法解释之前。而实施方案的 D1 定的正相反：法条字段只落 PostgreSQL 的
`chunk_metadata`，不动 Milvus schema。

那个决策是在"检索只做语义、没有任何过滤"的前提下做的，现在撞上了一个由代码决定的成本不对称：

- Milvus collection 是**固定字段列表、且没有开 dynamic field**
  （`storage/milvus.py::_build_fields`，标量索引只覆盖 `doc_id` / `tenant_id` /
  `file_type` / `element_type`）。过滤键必须存在于 schema 且逐行写入。
- 事后回填过滤值不是小迁移：Milvus 没有"只更新某个标量字段"的操作，`upsert` 需要完整实体
  （含稠密与稀疏向量）。因此补值等于**把每个 chunk 重新 embedding**。
- PostgreSQL 相对便宜：`chunk_metadata` 是 JSON 列，加键无需迁移，值可以用脚本对已入库文档
  回填（原件仍在，`GET /api/documents/{doc_id}/raw`）。

本次是全新部署，所以 schema 决策今天的成本是零，之后是极大。

## Decision

凡是在入库时写入的东西，一律在**首次入库前**定，gate 记录在
`docs/legal-first-ingest-checklist.md`。具体包括：

- 决定是否在首个 schema 里加入 `law_type`、`province`、`city` 三个标量字段（含标量索引），
  并由 `pipeline.py::milvus_data` 写入。
- `law_type` 存**原始值**；PRD 的三档在应用层翻译成 `law_type` 的成员集合，这样以后层级
  口径变化都不需要重灌。
- `province` 与 `city` 存两个归一化字段，而不是一个原始 `region` 串——否则"按省查"要在
  Milvus `expr` 里展开成上百个城市名。
- **不要**用 `enable_dynamic_field` 当保险：它省掉 schema 变更，但省不掉回填，旧行照样
  没有值、过滤照样漏。

接口层的事情明确不进 gate，因为它们不改已入库数据：层级与地域的请求参数、位阶排序权重、
分页、响应 `metadata` 下发、`article_id`、法条详情端点，都可以之后再加。

> 请求参数那半边随后已经落地：见
> [检索请求上的效力层级与地域过滤](../../implemented/architecture/2026-09-14-legal-filter-params.zh.md)。
> 过滤现在就是对本文档新增的那几个标量字段做 Milvus 预过滤。本文档仍保持 `proposed`，因为
> "字段集合"对每个新部署而言仍是一份提案——一个没带这些字段建起来的 collection 之后无法接受
> 它们。

## Alternatives considered

**在 Milvus 召回之后用 PostgreSQL 过滤。** 不需要改 schema、不需要回填，而且 `law_type`
已经在 `chunk_metadata` 里。按选择性否决：法律只有 477 / 29,957 份（1.6%），对
`recall_k=128` 的候选池过滤后大约只剩两条可用命中。后置过滤只适用于弱选择性条件。

**完全推迟过滤、保住 D1。** 否决：PRD 的两条验收（「仅法律」不返回司法解释；「物业费」+
「北京市」返回《北京市物业管理条例》）没有过滤车道就无法达成；而 7,921 份重复版本让
"不过滤"从"不够精确"变成"结果就是错的"。

**先开 dynamic field 当作未来保险。** 否决：它省掉 schema 变更这一步，却省不掉值回填——
已存在的行在动态 JSON 里没有值，过滤会静默漏掉。它带来的好处，现在就把字段定下来同样有。

**等看到检索质量之后再定 schema。** 否决：质量验证发生在首次入库之后，那时 schema 已冻结，
过滤字段的代价就是全量重新 embedding。

## Consequences

首次入库被 gate 卡住的是清单而不是代码完整性。若加入过滤字段，`_build_fields`、
`_SCALAR_INDEXES` 与 Milvus 写入路径都要改——今天是小改动，但属于"有数据之后改起来不再便宜"
的那一类。

加入 `province` 会让"城市 → 省份"映射表（约 340 行）成为入库期依赖：没有它，11,812 份市级
法规的 `province` 就是空的。763 份县级法规（自治县等）需要一个明确的归属决策。

按当前约定，PRD 里依赖我们没有的数据的项仍然不做：部门规章（语料中没有）、
`expiry_date` / `superseded_by`（没有）、关联司法解释（846 份司法解释里只有 115 份标题点名
了具体法律）。

## Testing

schema 那半边的验证是结构性的而非单测级的：`ensure_collection` 之后，
`describe_collection` 必须列出新字段及其标量索引，且带 `expr` 的检索能包含与排除预期 chunk。
清单的"现状快照"一节固定了这些决策所依据的语料数字（`law_type` 12 类、省级 31 种、
市级 350 种、2.88 M / 1.19 M chunk 估算），全部由 `app/scripts/audit_legal_metadata.py` 产出。
