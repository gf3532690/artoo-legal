# Agent Note: 检索请求上的效力层级与地域过滤

Status: implemented

## Problem

PRD 要求调用方能按**效力层级**（仅法律 / 含行政法规 / 全部）与**省份或城市**收窄检索，
验收项也是过滤口径（"指定仅法律层级，返回结果不包含司法解释"；"输入『物业费』+『北京市』，
返回《北京市物业管理条例》"）。而检索请求此前没有任何过滤字段，且方案当初明确决定不做
精确过滤车道。

提案
[过滤用标量字段必须在首次入库前定](2026-09-14-legal-filter-scalars-before-first-ingest.zh.md)
解决了存储那半边：`law_type` / `province` / `city` 是 Milvus 标量字段、逐 chunk 写入。
本记录交付查询那半边。

## Decision

`RetrievalTestRequest` 新增三个可选字段，可任意组合：

- `law_levels: list[str]` —— **与语料类别解耦的层级枚举**：`constitution`、`law`、
  `decision`、`administrative_regulation`、`judicial_interpretation`、
  `local_regulation`、`supervision_regulation`。应用层把层级映射成原始 `law_type`
  （`retrieval/filter.py::LEGAL_LEVEL_TYPES`），因此口径变化永远不需要重灌。
  层级名拼错会被忽略，而不是把查询变成"什么都匹配不到"。
- `province` / `city` —— **保留国家层面法规**的地域过滤：`province == ""`（法律 /
  行政法规 / 司法解释等无地域归属的文档）始终保留，所以限定地域只是筛地方性法规，不会把
  国家法律藏起来。两者同时给定时，地方性法规要同时匹配。

既有的 Milvus 预过滤 dataclass `RetrievalFilter` 新增 `law_types` / `province` / `city` 并
负责拼 `expr`；`RetrievalTestRequest.to_filter()` 负责翻译请求。两条检索路径都把它下推：
单库路径给 `VectorRetriever` / `HybridRetriever.search_with_trace` 传 `expr=`，多源路径给
`MultiKBRetriever` 传 `filters=`（它本就把全局 `expr` 与各源自己的表达式合并）。

`decision` 单列而不并入 `law`：修改、废止的决定在国家与地方两级都存在，语料只记类别，
强行归入 `law` 会把省级废止决定放进国家层级。`supervision_regulation`（监察法规）同样单列
而不并入行政法规——这也顺带回答了入库清单里那个悬而未决的归位问题。

## Alternatives considered

**直接把语料原始 `law_type` 当过滤参数。** 否决：那会把 12 个语料类别
（`修改、废止的决定`、`法规性决定`…）泄露进对外契约，任何口径调整都会变成破坏性变更。

**地域做严格过滤（丢掉没有省份的文档）。** 否决："物业费 + 北京市"会因此把《民法典》与
所有国家法律排除，与 PRD"触发地方性法规"的意图相反——它要的是触发，不是白名单。

**召回之后在 PostgreSQL 后置过滤。** 在存储那份提案里已否决，此处不变：法律占语料 1.6%，
对 `recall_k=128` 的候选池做后置过滤只剩两三条可用命中。

**把 `修改、废止的决定` 当作法律层级的一部分。** 否决：这类决定国家与地方两级都有，而语料
没有逐类别区分层级；后续应该在层级映射里按 `province` 拆分它们，前提是先定下国家/地方的
边界。

## Consequences

过滤发生在 Milvus 内部，是**预过滤**而非事后截断，召回池完全可用。三个字段可选、默认不过滤，
因此既有调用方行为不变。

层级映射从此成为需要维护的一件产物：新增语料类别（例如语料里没有的部门规章）只需加一条
映射，不需要重灌。`decision` 与 `supervision_regulation` 单列意味着"仅法律"不包含国家层面的
废止决定，除非调用方同时传 `decision`；契约里写明了这一点，PRD 的三档两种传法都能表达。

## Testing

`tests/test_retrieval_legal_filters.py` 钉住映射（law 覆盖法律与其解释、不含行政法规与地方
法规；决定与监察法规单列；未知层级被忽略）与表达式语义（省份过滤保留 `province == ""`；
引号被转义）。

在运行中的部署上验证：库里入了 5 份地方性法规后，
`law_levels=["local_regulation"] + province="江西省"` 只返回江西那份，`province="福建省"`
只返回福建的两份，`law_levels=["law"]` 返回 0 条（库里还没有国家层面文档）。
