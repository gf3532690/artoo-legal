# Agent Note: 精排阶段的效力位阶加权

Status: implemented

## Problem

PRD 的验收项写得很明确：未指定层级时，法律层级的结果要排在司法解释之前。而检索此前完全按
相关度排序——RRF 融合、精排，再走既有的复合评分（`composite_rerank_weight` /
`_base_weight` / `_source_weight`，混合精排分、RRF 分与位置先验），其中没有任何位阶信息。

有两个机制性障碍要先解决：

- **精排服务自己按 `top_k` 截断**，所以排在第 `k+1` 名的法律无论后面怎么加分都进不来；
- `law_type` 不在 Milvus 的查询投影里，排序阶段拿不到位阶信号。

## Decision

`retrieval/legal_level.py` 统一承载层级词汇：过滤用的层级枚举
（`LEGAL_LEVEL_TYPES`，从 filter 模块移入，让过滤与排序共用一处定义）加上
`LEGAL_LEVEL_TIERS`——一个 `[0,1]` 的位阶阶梯：宪法 `1.0`、法律 / 法律解释 / 修正案
`0.9`、法规性决定与重大决定 `0.85`、行政法规 `0.8`、监察法规 `0.75`、司法解释 `0.7`、
地方法规 `0.6`。「修改、废止的决定」两级都有，因此无省份时 `0.85`、带省份时 `0.6`。

`HybridRetriever._rerank` 对每个分数乘 `(1 + w × 位阶分)`，`w` 是新增的检索配置
`legal_level_weight`（默认 `0.1`，范围 `0..1`，`0` 关闭）。当 `w > 0` 时向精排服务多要
候选（`max(top_k × 2, top_k + 5)`），重排后自己截断到 `top_k`，这样加权才真的能把低排名的
高层级结果提上来；`w == 0` 时取候选数与所有代码路径与改造前逐字节一致。

`law_type` 与 `province` 加入 `_OUTPUT_FIELDS`（只影响查询投影，不改 schema），并加入三个
子检索器构造的 metadata。

## Alternatives considered

**只在 `_apply_composite_scoring` 里加权。** 否决：那发生在精排已经截断到 `top_k` 之后，
只能对幸存者重新排序。PRD 的验收场景需要的是"提上来"，不只是"重排"。

**在排序代码里直接用语料 `law_type`、把阶梯也写在那里。** 否决：那样过滤与排序各持一份
口径；共享的 `legal_level.py` 让口径只有一份。

**默认给一个很大的权重（例如 `0.5`）。** 否决：PRD 要的是"综合排序"，不是位阶主导。
`0.1` 时法律与司法解释之间约两个百分点的差距——足以打破近似平手，又不足以把不相关的法律
顶到明显更好的司法解释前面。需要更强层级优先的部署可以调大。

**只在法条语料上扩大候选池。** 否决且无必要：扩容对非法条语料本就是空操作——加权需要
`law_type`，它们没有，阶梯自然不生效（扩容只要 `w > 0` 就会发生，对精排服务无额外成本：
它对所有候选都打分，只是返回条数不同）。

## Consequences

对法条部署这是**默认行为变更**：同一查询的结果顺序可能与上游 Artoo 不同。已记录在
`artoo-open-api.md` 第 0 节与方案的检索默认值表里。

非法条知识库不受影响（没有 `law_type` → 没有位阶 → 不调整），因此共用检索链路的 Artoo 是
安全的。

新增一个检索配置项不是改一个文件就够：本次需要改动配置规格、`RetrievalConfig` 字段、
系统配置 API 的 `RetrievalConfigSection` 与 `RetrievalConfigUpdate`、`RetrievalConfigRow`
列与 `ALTER TABLE` 条目，以及 `tests/test_retrieval_config.py` 里的字段集合断言。因此这个
权重可以通过既有配置 API 按部署调整（`legal_level_weight`，`0` 关闭）。

阶梯是需要维护的产物：新增语料类别要加一条阶梯；而「修改、废止的决定」的国家/地方之分
仍依赖 `province`，因为语料只记了类别。

## Testing

`tests/test_legal_level_rank.py` 用假精排服务钉住行为：阶梯顺序、国家/地方废止决定的位阶、
`w=0` 保持纯相关度顺序与原始取候选数、近似平手由位阶决出、明显更好的相关度仍然胜出、
扩大候选池后低排名的法律被提上来、以及没有 `law_type` 的结果不受影响。

`tests/test_milvus_legal_fields.py` 改为断言 schema 的**声明层**（`LEGAL_FILTER_FIELD_LENGTHS`、
标量索引集合、以及 `_build_fields` 确实消费该声明），不再构造 `FieldSchema` 对象——因为本
套件里有一个测试模块通过 `sys.modules` 注入假的 pymilvus，运行时断言会随导入顺序变化。
真实 schema 已在运行中的 Milvus 上用 `describe_collection` 核验。
