# Agent Note: 法条结果元数据下发

Status: implemented

## Problem

字段字典那份记录当时决定：版本、溯源与地域字段只留在 PostgreSQL 的
`chunk_metadata` 里，不下发到线上——检索只返回 `law_name` / `article_number` /
`article_label` / `chapter` 加 `source`（`api/retrieval.py::_LEGAL_KEYS`）。在"检索只做
语义、没有消费方"的前提下这是对的。

《法条检索基础API》PRD 改变了要求：结果必须带**效力层级**（`law_type`）供调用方筛选与
展示，必须带发布/施行日期与时效状态供判断自己看的是哪一版，必须带地域以便展示地方性法规
并与请求的省市核对。这些数据本来就逐 chunk 存在，缺的只是"露出来"。

## Decision

`api/retrieval.py` 的 `_LEGAL_KEYS` 从 4 个扩到 11 个：`law_name`、`article_number`、
`article_label`、`chapter`、`law_type`、`issuing_authority`、`publish_date`、
`effective_date`、`validity_status`、`province`、`city`。`_build_result_items` 另外派生
`article_id`＝`"{doc_id}:{article_number}"`（有条号时），给调用方一个稳定的"某一条"句柄，
不需要新增入库字段；无条文结构的文档（修正案 / 决定）**不给** `article_id`，而不是给一个
假 ID。

响应模型不变：所有字段仍走 `RetrievalResultItem` 既有的 `metadata` 字典，信封字段不动。
既有约定也保持不变——值为空时**整键缺失**而非 `null`，客户端需按可空处理。

契约文档 `artoo-open-api.md` 第 0 节已记录扩展后的字段清单；
`docs/legal-recall-implementation-plan.md` §6.1 把"不下发"改成"下发"并交叉链接到本记录。

## Alternatives considered

**保持不下发，让 PRD 的消费方自己读数据库。** 否决：消费方是搜索前端与上游 LLM 应用，
两者都没有数据库访问权；PRD 的验收项（结果展示效力层级、地方性法规与请求地域可核对）说的
就是 API 响应。

**把字段加在响应顶层而不是 `metadata` 里。** 否决：响应模型与信封是对外契约，而 `metadata`
正是上一次变更专门为法条字段预留的槽位。

**先下发字段、等详情接口再做 `article_id`。** 否决：`article_id` 可派生、无存储成本，而且
一旦结果开始携带版本与地域信息，调用方立刻就需要一个稳定的"某一条"句柄。

**缺失值统一发 `null` 以保持键集合稳定。** 否决：这与既有水合约定（`chapter`、
`article_label` 等所有可选字段）冲突，也会逼客户端去区分"该字段不适用"与"该行早于这个字段"。

## Consequences

响应体积变大：每条结果最多可带 11 个 metadata 键（原为 4 个）。值都来自已经在一次批量查询
里水合好的 `chunk_metadata`，所以代价在载荷大小而不在查询次数。

下游必须容忍缺键——契约现在明确写了这一点。任何原先假设 metadata 里"法条键恰好 4 个"的
消费方（例如有严格 schema 的类型化客户端）需要更新。

`law_type` 下发的是语料原始类别（12 个取值，含 `修改、废止的决定`、`法规性决定`），不是
归一化后的层级：把它们映射到 PRD 的三档属于过滤侧的职责，放在应用层做才能在口径变化时
不用重灌。`validity_status` 同样下发原始整数，以后也还是原始整数；改变的是枚举不再是
未公开的——它后来从数据源自己的字典里拿到了：`3` 现行有效 / `2` 已修改 / `1` 已废止 /
`-1` 已失效 / `4` 尚未生效 / `0` 未标注，并且
[检索默认排除已废止与已失效的法条](../feature/2026-09-15-legal-exclude-repealed-by-default.zh.md)
已经开始解释其中两个取值。对消费方的建议形状不变：读原始整数，但定义请读那份 note，
而不是靠猜。

## Testing

`tests/test_retrieval_legal_metadata.py` 用假会话顶替两次水合查询，钉住下发口径：PRD 字段被
透传、`article_id` 由 `doc_id` + 条号派生、无条号文档不给 `article_id`、地方性法规的地域字段
能出来、缺失值整键缺失。`tests/test_retrieval_contract_baseline.py` 继续通过，说明响应信封
与请求语义未变。
