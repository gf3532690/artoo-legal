# Agent Note: chunk_metadata 中的法条版本与溯源字段

Status: implemented

## Problem

字段字典此前只承载文档**身份**：`law_name` / `issuing_authority` /
`publish_date` / `article_number` / `chapter`。而"这个 chunk 属于哪一版法条""文档
来自哪里"这类信息，任何消费方都拿不到。爬取语料（29,957 份 `.docx`）实测：

- 6,107 个法名存在 2–6 个版本，多出 7,919 份文档，而落库的元数据里没有任何东西
  能区分它们——同一法条的新旧两版可被同等召回，调用方无从辨别。
- 2,092 份文档 `validity_status = 0`、17,235 份 `= 3`，7,419 份正文头部没有
  `effective_date`，这些信息一条都没有进存储。
- 入库核对事后无法区分"权威值"与"启发式值"。

配套记录
[法条文档级元数据改用 docx 内嵌属性](../feature/2026-09-14-docx-embedded-legal-metadata.zh.md)
已经能从 `docProps` 读出 8 个字段，但当时只消费 3 个——因为其余 5 个是新的持久化键。

## Decision

`LegalDocumentHeader` 新增 `effective_date` / `validity_status` / `law_type` /
`external_id` / `source_code`，`LegalMetadataExtractor.extract` 为每个 child chunk
多写 6 个键：上述 5 个加 `meta_source`。

- `effective_date`：属性优先，其次正文兜底——匹配「自…起施行」并取**最后一个**
  匹配，因为施行条款按体例在正文末尾的附则。
- `validity_status`：原样存整数。当时数据源没有给出枚举定义，因此这里不做任何解释、
  也不据此做任何筛选。该定义后来从数据源自己的字典里拿到了，记录在
  [检索默认排除已废止与已失效的法条](../feature/2026-09-15-legal-exclude-repealed-by-default.zh.md)；
  入库仍然逐字存该值。
- `law_type` / `external_id` / `source_code`：只来自属性，正文里没有对应信息。
- `meta_source`（`rule` / `docx-props` / `rule+docx`）与既有排查字段 `has_toc`
  并列落库，让全量核对能区分权威值与启发式值。

这些键通过既有的 `Chunk.chunk_metadata` JSON 列进入 PostgreSQL——**无需迁移**——
且不碰 Milvus，与 D1 一致。对外**不新增任何字段**：`api/retrieval.py::_LEGAL_KEYS`
仍然只取 `law_name` / `article_number` / `article_label` / `chapter`。

## Alternatives considered

**等入库侧选版流程落地再一起接这 5 个字段。** 否决：落库本身零成本（JSON 列、
无迁移），而版本排序的键必须先存下来，选版流程才能对它做事；先存后用也让该流程
可以对真实数据做验证。

**用正文解析出的 `publish_date` 当版本键。** 否决：300 份抽样里正文日期与属性日期
有 196 份不同，因为正文括注的第一个日期是**法条文本的通过日**，而属性标识的是**该
文件所载的版本**。

**现在就把 `validity_status` 解释成 `legal_status` 枚举。** 否决：只有两个取值有
证据——`修改、废止的决定` 全是 `0`（1,902/1,902）、`宪法` 与 `修正案` 全是 `3`
（19/19）。存原值可以保留后路，又不必断言数据源从未定义过的含义。**该前提后来被取代**：
完整的六值枚举现在有据可查，检索也解释了其中两个"已不具法律效力"的取值，记录在
[检索默认排除已废止与已失效的法条](../feature/2026-09-15-legal-exclude-repealed-by-default.zh.md)。
此处记录的决定——存原值、不在入库时归一化——依然成立。

**把这批新字段下发到检索响应。** 本次否决：没有任何过滤或排序用到它们，而每个响应
字段都是对外契约。响应形状保持不变。

**只从文档头部扫施行条款。** 否决：该条款在末尾的附则里，只扫头部恰好会漏掉最
需要它的那些文档。全篇扫描取最后一个匹配既更简单也更准。

**`meta_source` 只打日志、不落库。** 否决：全量核对是本部署的验证手段，它需要在
入库之后仍能逐份判断来源；`has_toc` 已是"仅排查用"字段落库的先例。

## Consequences

每个 child chunk 多 6 个键；由于文档级字段是**反规范化到每个子块**的（`law_name`
原本就如此），同一份文档的各个 chunk 会重复这些值。JSON 列能吸收这个增长、不需要
迁移，但存储与水合成本随字段数上升。

版本治理从"不可能"变成"可能，但尚未实现"：在入库侧选版流程落地之前，同一法条的
新旧版本仍可被同等召回。该流程必须遵守一条已经能从数据里看到的约束——2,031 份
文档的 `validity_status = 0` 且没有同法名的其它版本，其中多数是
`修改、废止的决定`，它们的正文本身就是"某法被废止"的记录，信息量很高，**不能仅凭
这个状态删除**。

`effective_date` 对约 22% 的文档仍为空（属性缺失且找不到施行条款），这是有意留空
而不是猜一个值。`validity_status` 原样存储，本 note 的代码路径不解释它：需要"是否现行
有效"的消费方应当去
[检索默认排除已废止与已失效的法条](../feature/2026-09-15-legal-exclude-repealed-by-default.zh.md)
读枚举，而不是从整数里推断含义。

`.doc` 输入会丢掉全部 6 个字段，因为 LibreOffice 转换会丢掉 `docProps`；当前语料
100% 是 `.docx`。

属性 `title` 是来源系统登记表里的**规范名**，并不总是文档首行印刷标题的逐字拷贝：
最小的 200 份里有 9 份不同，例如登记表形态去掉了会议届次前缀
（「海西蒙古族藏族自治州第十四届人民代表大会第六次会议关于废止…」），而正文保留。
对"按法名给版本分组"来说这正是想要的形态，但消费方不能把 `law_name` 当作文档的
印刷标题。

有两类失败模式从日志搬进了数据库，这正是 `meta_source` 的意义：源元数据里错误的
`title` 现在会静默覆盖正确的正文解析（属性优先是有意为之，但它不再不可见），
以及 `rule+docx` 标记出属性不全的文档。

## Testing

`tests/test_legal_metadata.py::TestVersionAndProvenanceFields` 钉住新行为：属性填满
5 个字段；没有属性时值全为 null 而键仍然存在（字段形状稳定）；`effective_date`
回退到正文条款并取最后一个匹配；属性值优先于该条款；`LegalMetadataExtractor`
把 6 个键都写进 per-chunk 字典，且在身份字段齐备时条文块的 `confidence` 到 1.0。

另抽爬取语料 300 份验证形状与来源：每一份文档的每个 chunk 都带齐 6 个新键；属性对
300 份全部提供了 `validity_status` / `law_type` / `external_id` / `source_code`；
`effective_date` 有 220 份来自属性、53 份来自施行条款兜底、27 份为空。
