# Agent Note: 法条子块粒度是知识库级选择

Status: proposed

## Problem

本部署要把 29,957 份文档入进一个 `kb_chunk_cap` 为 1,000,000 的知识库。用核对脚本实测
（`--mode full --sample 2000 --seed 11`，按「有条文 / 无条文」分层后外推全量）：

| 切分策略 | 估算 child chunk | 稠密向量占用 |
|---|---|---|
| 条文按「款」切（现状） | **2.88 M** | 11.8 GB |
| 条文按「款」切 + 版本选版 | 1.99 M | 8.2 GB |
| 一条文一子块 | **1.19 M** | 4.9 GB |
| 一条文一子块 + 版本选版 | **0.82 M** | 3.4 GB |

只有最后一行能进上限；而本次是全新部署、没有存量数据，所以改粒度不需要重灌——只要在首次
入库前定下来。体量高度集中在一类：地方法规 25,285 份占文档数 84%，却占 2.88 M 中的
2.58 M（89.6%）。

## Decision

`LawsChunker` 接受 `child_policy`（KB config 键 `law_child_policy`）：

- `paragraph` —— 父块内按段落切，款成为独立检索单元。这是改造前行为，**保持为默认值**。
- `article` —— 一条文一个子块；超过 `child_chunk_size`（450 字）的仍由
  `enforce_size_limits` 按句子边界再切，长条文因此保留细粒度。这是预期的目标形态：
  `article` + 版本选版 = 0.82 M，进上限并留约 18% 余量。

开关已实现并有单测。**部署层面**的默认值是 `article`：法条库的引导步骤在创建全局库时写入
`law_child_policy: "article"`（`auth/bootstrap.py`）。`LawsChunker` 自身的代码默认仍是
`paragraph`，因此其他调用方与核对脚本行为不变。

子块才是 embedding / BM25 / rerank 真正打分的单元，粒度变粗会稀释"只命中某一款"的长条文。
这个质量取舍用今天的证据无法判定——方案把评测集列为暂缓项，而开发检出既没有 Milvus 也没有
远程 Embedding / Rerank（只有 `.env.example`，19530 没有监听）。因此这个选择建立在容量与
"全新部署"之上：库还是空的，首次入库前改回来成本为零；本记录保持 `proposed`，等 A/B 作为
事后验证补上。

A/B 步骤（有环境之后）：把样本库入库两次（2,184 份国家层面法规就够，且在两种策略下都进
上限），一次一种策略；建一个小评测集（法条查询 + 期望法名与条号）；用
`app/scripts/evaluate_retrieval.py` 跑两次对比命中率，再决定是否翻默认值。

## Alternatives considered

**把全局库拆成 3–4 个知识库。** 保持 `paragraph` 粒度、不需要改 chunker，而且
`kb_chunk_cap` 本来就是每库口径。当前否决，因为代价落在检索契约与运维上：每次检索都会走
多源路径（`mode` 恒 hybrid、`trace` 恒 null）；`_compute_source_top_k` 在源数 ≤4 时每源
返 `top_k * 3`，于是 rerank 候选池从 30 涨到 60（`top_k=5`）；任一分片失败即降级；分片键
本身是新决策（地方法规一家占 89.6%，按类别拆极不均衡）；上传、备份、重建都变成 N 份。

**调大 `kb_chunk_cap`。** 零代码、零契约改动，且保留最细粒度。否决：2.88 M 向量约 11.8 GB
稠密数据，还没算稀疏向量、HNSW 结构与 288 万行 PostgreSQL；而且 cap 是安全阀不是容量目标，
调大等于拆掉保护而底层成本不变。

**分级上线——先国家层面法规，地方法规后上。** 2,184 份国家层面法规即使按 `paragraph` 也只有
0.219 M，是零风险的上线方式。只把它作为**备选**否决：它推迟而不是回答问题；若上线前跑不了
A/B，它就是推荐方案。

**只靠版本选版，不改粒度。** 版本选版是正交的、无论怎样都该做（约占条文行 31%），但单独
用它仍有 1.99 M——还是上限的两倍。

## Consequences

chunker 多了一条要维护的分支；`law_child_policy` 配错时回退到 `paragraph` 并打 warning，
既不会打断入库，也不会静默换成另一个切分器。

部署现在落在全量约 1.19 M chunk、配合版本选版约 0.82 M（由
`audit_legal_metadata.py --ingest-list` 产出的入库清单），进 `kb_chunk_cap` 并留约 18% 余量。
对其他调用方，本次改动不改变任何既有行为：代码默认路径与改造前逐字节等价。

## Testing

`tests/test_legal_metadata.py::TestLawsChunkerFallback` 覆盖：同一份"两条文"样本在两种策略下的
子块数（3 对 2）、非法取值回退到 `paragraph`、以及 `article` 策略下超长条文仍会被
`enforce_size_limits` 切开。
