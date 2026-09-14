# 法条库首次入库前置清单（gate）

> 状态：**待确认**。首次入库前需逐项打勾；未打勾的项意味着首次入库之后要付返工代价。
> 背景与决策记录见
> [`.agents/notes/proposed/architecture/2026-09-14-legal-filter-scalars-before-first-ingest.md`](../.agents/notes/proposed/architecture/2026-09-14-legal-filter-scalars-before-first-ingest.md)。

## 为什么需要这个 gate

Milvus 的 collection schema 是**固定字段列表、没有开 dynamic field**（`storage/milvus.py`
的 `_build_fields`），标量索引只覆盖 `doc_id` / `tenant_id` / `file_type` / `element_type`。
要在检索里按某个维度过滤，该字段必须在建表时就存在并写入——**事后补值等于把全部 chunk
重新 embedding**（Milvus 没有"只更新某个标量字段"的接口，`upsert` 需要完整实体含向量）。

PG 侧宽松得多：`chunk_metadata` 是 JSON 列，加键不需要迁移，值可以用脚本回填（原件仍在，
`GET /api/documents/{doc_id}/raw`），**不需要重算向量**。

所以这个 gate 只卡一件事：**首次入库时写进去什么**。至于"怎么用"——接口参数、排序权重、
分页、响应下发字段——都可以随时迭代，不进这个 gate。

---

## 一、Milvus schema（唯一"后补必然重算向量"的项，优先级最高）

- [ ] **定是否新增 `law_type` 标量字段 + 标量索引**（PRD 的"仅法律 / 含行政法规 / 全部"三档需要它）
      —— 建议存**原始 `law_type`**，三档映射放应用层：以后 PRD 的层级口径变化（甚至新增"部门规章"）不用重灌。
- [ ] **定是否新增 `province` / `city` 标量字段 + 标量索引**（对应 PRD 的地域筛选与"地方优先"）
      —— 建议两个字段都存，不要只存一个原始 `region` 串：否则"按省查"要在应用层展开成百个城市名的 `expr`。
- [ ] 写入路径同步：`pipeline.py` 写 Milvus 的 `milvus_data` 增加对应键
      （现有键：`chunk_id` / `doc_id` / `content` / `dense_vector` / `sparse_vector` /
      `parent_id` / `chunk_index` / `file_type` / `element_type` / `tenant_id`）。
- [ ] 验收：`describe_collection` 能看到新字段与索引；用 `expr` 过滤检索能命中且能排除。
- [ ] 明确**不开** `enable_dynamic_field`（它只省 schema 变更，不解决旧行没有值 → 仍要回填）。

## 二、PG `chunk_metadata` 新增字段

- [ ] `province` / `city`：从 `issuing_authority`（实测零缺失）或 `law_name` 抽取；
      省级的直接填省，市级法规的 `province` 靠映射表补。
- [ ] **城市 → 省份映射表**规格：约 340 行（地级市 / 自治州 / 地区 / 盟 → 省 / 自治区 / 直辖市）；
      决定它放代码常量还是配置表、以及谁维护。
      —— 这张表**必须在首次入库前到位**，否则 11,812 份市级法规的 `province` 会是空。
- [ ] 覆盖目标与边界：地方法规 25,285 份里省/市两级命中 24,522 份（97.0%），
      剩余 **763 份是县级**（如「三江侗族自治县」）——定它们归到所属省还是留空。
- [ ] 1 份 `law_type` 为空值的文档怎么参与层级过滤。

## 三、子块粒度默认值

- [x] **已定：`article`（一条文一子块）**，由全局法条库的引导步骤写入
      （`auth/bootstrap.py` 的 `law_child_policy: "article"`）。`LawsChunker` 自身的代码默认
      仍是 `paragraph`，保持其他调用方与核对脚本行为不变。
      依据：全量 2.88 M → 1.19 M；本次为全新部署，改回只需改这个配置值（首次入库前）。
- [ ] 待补：检索质量 A/B（`paragraph` vs `article`）——需要 Milvus + 远程 Embedding/Rerank
      环境与一个小评测集（方案里评测集是暂缓项）。属于**事后验证**，不阻塞首次入库。

## 四、版本选版（PRD 的"仅返回现行有效"依赖它）

- [ ] 确认 `validity_status` 枚举语义：`0`（1,902/1,902 的"修改、废止的决定"）与
      `3`（宪法 7/7、修正案 12/12）有强证据，`1` / `2` / `4` / `-1` **需要数据源给定义**。
- [ ] 定选版规则与人工队列处置：379 组无 `status=3`、2 组日期并列、
      1,984 份孤本 `status=0`（多为废止决定，正文本身有价值，不能仅凭状态删除）。
- [x] **已定：入库前过滤**（省一次 embedding）。落地物是核对脚本的 `--ingest-list`：
      全量跑一次即产出 `ingest_list.csv`（本次 22,036 份，淘汰 7,921 份），首次入库按它上传。
- [ ] 待确认：上一条的枚举语义；未确认前 `select_versions` 的规则（优先 `status=3`、再取最新
      `publish_date`、异常进人工队列）继续按现状执行。

## 五、执行参数（记录，便于复现）

- [ ] 语料来源形态：解压目录（文件名正常）或原始 zip（条目名是 cp437 误读，需要还原）。
- [ ] 入库方式：`POST /api/knowledge-bases/{kb_id}/documents/upload-folder` 分批，
      还是脚本直连 pipeline。
- [ ] 分批大小与并发（`Pre_Embed_Gate` 按 `kb_chunk_cap` 判，超限会拦下整批）。
- [ ] 对账基线：`app/scripts/audit_legal_metadata.py` 产出的 `files.csv` / `groups.csv`
      作为入库前快照，入库后逐份比对。

## 六、明确**不在**本 gate 内（可随时迭代）

接口请求参数（层级 / 地域）、位阶排序权重、分页、响应 `metadata` 字段下发、`article_id`、
法条详情端点——这些都不改已入库数据，随时可做。

数据缺口项按当前约定暂缓：部门规章层级、`expiry_date` / `superseded_by`、关联司法解释
（846 份司法解释里标题含《》的仅 115 份 / 13.6%，关系数据不足以支撑）。

---

## 现状快照（2026-09-14，供打勾时对照）

| 事实 | 实测值 |
|---|---|
| 语料 | 29,957 份 `.docx`，单一来源 `national_laws`，约 1.14 M 行条文 |
| `law_type` 类别数 | 12（含 1 份空值） |
| 地域可抽性（地方法规 25,285 份） | 省级 12,710 / 市级 11,812 / 县级 763 |
| 地域取值种数 | 省级 31、市级 350 |
| 同名多版本 | 6,107 组、14,026 份文档，其中 6,104 组每个版本都有 `publish_date` |
| 估算 chunk（现状粒度 / 一条文一子块） | 2.88 M / 1.19 M |
| `kb_chunk_cap` | 1,000,000（每库） |
| 入库清单（选版后） | 22,036 份 / 778,880 条文行，约 **0.82 M** chunk（`article` 粒度） |
| 地域覆盖（全量 29,957 份） | 有省份 27,579 / 只有城市 15 / 都无 2,363（国家标准类法规本就不属于省份） |
