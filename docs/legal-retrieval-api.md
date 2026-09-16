# 法条检索接口文档

面向集成的入参/出参参考，覆盖三个端点。平台级接口手册见
[`artoo-open-api.md`](../artoo-open-api.md)；本部署相对上游的差异见该文档第 0 节。

- 服务地址：`{BASE}`（本地 `http://localhost:8888`，测试环境 `http://10.30.1.6:8889`）
- 内容类型：请求与响应均为 `application/json`（上传类接口除外）
- 鉴权：`Authorization: Bearer <凭据>`。凭据可以是 API Key（`sk-` 开头）、登录后的 JWT，
  或第三方接入用的代理 Key（此时另需 `X-External-User-Id`，见 `artoo-open-api.md` §2.0）

| # | 端点 | 用途 |
|---|---|---|
| 1 | `POST /api/retrieval/search` | 关键词检索 / 按层级与地域筛选 / 精确检索 |
| 2 | `POST /api/retrieval/test` | 与 1 完全一致（同一实现），前端调参页保留此路径 |
| 3 | `GET /api/legal/articles/{article_id}` | 按法条 ID 取条文详情 |

> 「法条内容校验」不单独开端点：它等于用端点 1 传 `top_k=1`，见文末示例。

---

## 1. POST /api/retrieval/search

### 入参

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | string | ✅ | — | 查询文本，最少 1 字符。关键词、法条编号、正文片段都可以 |
| `kb_ids` | string[] | ✗ | — | 多知识库联合检索。**不传即只查全局法条库**（服务端自动并入） |
| `knowledge_base_id` | string | ✗ | — | 单知识库，与 `kb_ids` 二选一 |
| `session_id` | string | ✗ | — | **本部署不支持**，传了返回 `400` |
| `mode` | string | ✗ | `hybrid` | `hybrid` = 稠密+稀疏+BM25 三路 → RRF → rerank → 位阶加权 → MMR → 父块扩展；`direct` = 仅稠密向量。多源（多库）时强制按 hybrid |
| `match_mode` | string | ✗ | `semantic` | `semantic` 语义召回；`exact` 精确检索（见下）。取值非法返回 `400` |
| `top_k` | int | ✗ | `5` | 每页条数，1–100 |
| `page` | int | ✗ | `1` | 页码，从 1 开始。`page × top_k` 上限 **100**，超出返回 `400` |
| `law_levels` | string[] | ✗ | — | 限定效力层级，取值见「层级枚举」 |
| `province` | string | ✗ | — | 限定省份（如 `江西省`）。**国家层面法规始终保留** |
| `city` | string | ✗ | — | 限定城市（如 `景德镇市`）。规则同 `province` |

#### 层级枚举 `law_levels`

枚举是**层级词表**，值不是响应里 `law_type` 的原文（这样口径变化不必重建索引）：

| 枚举 | 覆盖的语料 `law_type` |
|---|---|
| `constitution` | 宪法 |
| `law` | 法律、法律解释、修正案、**法规性决定**、有关法律问题和重大问题的决定（部分） |
| `decision` | 修改、废止的决定 |
| `administrative_regulation` | 行政法规 |
| `judicial_interpretation` | 司法解释 |
| `local_regulation` | 地方法规 |
| `supervision_regulation` | 监察法规 |

两条注意：`law` 这一档**包含「法规性决定」**（想要"法律本体"需在应用层再按
`metadata.law_type == "法律"` 过滤）；**拼错的层级名会被静默忽略**（等于不过滤），
不会报错——这与 `match_mode` 写错直接 `400` 不同。

#### `match_mode = exact` 的语义

解析查询里的「法名 + 条号」（中文或阿拉伯数字、带不带书名号都认，如 `民法典第一条`、
`刑法第234条`），按法条元数据直接定位，**不走向量召回**，因此通常 0.1 秒级。

- 命中：返回 `mode="exact"`、`routes=["exact"]`、`score=1.0`
- **忽略 `law_levels` / `province` / `city`**：条号已唯一确定那一条
- 解析不出条号、或库里没有这一条：**回退 semantic 召回**，并在 `fallback_reason` 说明原因
  （不静默降级）

### 出参

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | string | 原样回显 |
| `mode` | string | 实际使用的检索模式：`direct` / `hybrid` / `exact` |
| `total` | int | **本次返回条数**，不是命中总数（向量召回没有有意义的总数） |
| `elapsed_ms` | int | 服务端耗时（毫秒） |
| `page` | int | 回显页码 |
| `page_size` | int | 等于请求的 `top_k` |
| `has_more` | bool | 本页之后是否还有候选。服务端多取一条探测得出，不是估计 |
| `match_mode` | string | 实际生效的检索模式（exact 回退时为 `semantic`） |
| `fallback_reason` | string\|null | 仅 exact 未命中回退时有值 |
| `degraded` | bool | 是否有检索源失败（多源时可能为 true） |
| `failed_source_count` | int | 失败的检索源数量（多源时；单源恒 0） |
| `trace` | object\|null | 检索链路追踪，**仅单库 `hybrid` 有值**；`direct` 与多源为 `null` |
| `results` | array | 结果列表，见下 |

`trace` 结构：`routes[]` = `{name, recalled, enabled}`（三路 + 图谱第四路），
`funnel[]` = `{stage, count}`（召回 → RRF → rerank 候选 → 输出 → MMR）。

### `results[]` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunk_id` | string | 命中的子块 ID |
| `doc_id` | string | 文档 ID；取原件用 `GET /api/documents/{doc_id}/raw` |
| `filename` | string | 文件名 |
| `source_type` | string | 本部署恒为 `knowledge_base` |
| `content` | string | **条文全文**（父块正文，已剥离索引用的 `[法名 第N条]` 前缀） |
| `child_content` | string | 命中的那一段子块正文 |
| `score` | float | 最终分。`hybrid` 为复合分；`direct` 为稠密余弦相似度；`exact` 恒为 1.0 |
| `rrf_score` | float\|null | RRF 融合分，仅 `hybrid` |
| `rerank_score` | float\|null | rerank 精排分，仅 `hybrid` |
| `routes` | string[] | 命中路由：`dense` / `sparse` / `bm25` / `exact` |
| `metadata` | object | 见下 |

### `metadata` 字段

| 字段 | 说明 |
|---|---|
| `law_name` | 法名 |
| `article_number` | 整数条号（`第六条` → `6`） |
| `article_label` | 条号原文（`第六条`） |
| `article_id` | `{doc_id}:{article_number}`，可直接用于端点 3。**无条号的文档整键缺失** |
| `chapter` | 所属章 |
| `law_type` | 语料原始效力层级（如 `法律` / `司法解释` / `地方法规`） |
| `issuing_authority` | 发布机关 |
| `publish_date` / `effective_date` | 发布 / 施行日期（`YYYY-MM-DD`） |
| `validity_status` | 效力状态原始整数（枚举见上，服务端原样下发；**检索不过滤状态**，六种取值都会返回） |
| `validity_status_label` | 上者的中文描述（`现行有效` / `已废止` / …）。字段字典外的取值只发原值、不给描述 |
| `province` / `city` | 省 / 市 |
| `source` | `global`（全局法条库）/ `personal`（个人库） |

> **缺值约定**：元数据取不到的字段**整个键缺失**，不是 `null`；客户端按"键可能不存在"处理。
> （`province` 与 `city` 是例外：国家层面法规为 `province=""`、`city` 键缺失。）
> `validity_status` 是另一个例外，而且是反向的：法条文档解析完成后它**总是有值**——源文件
> 没标状态时落 `0` 未标注（字典里 `0` 的语义就是"源库没给这份文件标状态"）。"没有这个键"
> 只出现在未解析完成的文档上，那种文档在列表里也没有徽标。

#### 效力状态 `validity_status`（官方枚举）

值由数据源字典给出，服务端**原样下发**（不做归一化），并附带中文描述
`validity_status_label`——枚举只有一份真源，调用方不必自己维护一份。

| 值 | 含义 | `validity_status_label` |
|---|---|---|
| `3` | 现行有效 | `现行有效` |
| `2` | 已修改（该版已被后续版本取代） | `已修改` |
| `0` | 未标注（源库没给这份文件标状态；主要是"修改、废止的决定"这类文件） | `未标注` |
| `4` | 尚未生效（还没到施行日期的新法） | `尚未生效` |
| `1` | 已废止 | `已废止` |
| `-1` | 已失效 | `已失效` |

**检索不按效力状态过滤**：六种取值一律返回，包括已废止与已失效。要只看现行有效、或排除某几种，
请按 `validity_status`（或直接用 `validity_status_label`）自行判断——服务端把状态发出来，就是
为了让调用方决定口径。

> 早先的默认口径是"排除 `1` 已废止与 `-1` 已失效"（配 `include_invalid` 开关与
> `filtered_invalid_count` 回显）。该口径已取消：过滤放在调用方手里的前提，是它先拿得到状态。
> 请求里的 `include_invalid` 与响应里的 `filtered_invalid_count` 一并移除，传了会被忽略。

#### 时间字段：判断新旧用哪个

同一部法的新版本、以及针对某一条的补充/修改文件，检索时**都会命中**，正文看起来也都很像。
区分它们靠 `publish_date`。

| 字段 | 是什么 | 拿来做什么 |
|---|---|---|
| `publish_date` | **这个版本的公布日期** | **判断哪一条更新** |
| `effective_date` | 这个版本开始施行的日期 | 回答"行为时法"：某个时间点该条是怎么规定的 |
| `validity_status` / `validity_status_label` | 效力状态（原值 / 中文） | 自己判断这条还算不算数——**检索不过滤状态**，已废止与已失效一样会返回 |

`publish_date` **不是**正文里那个通过日期，别自己去正文抠。两者在全语料 29,957 份上有
20,234 份不一致（68.5%），其中 20,147 份是属性日期**更晚**，例如正文 `2017-08-24`、
属性 `2024-10-31`——那是"原始通过日"与"本文件所载版本"的区别。服务端下发的是属性值，
即**版本日期**；从正文抠出来的会把 2024 年的版本显示成 2017 年。

实测（查「民法典 第一条」）：

```text
中华人民共和国民法典   第一条   公布 2020-05-28   施行 2021-01-01   现行有效
中华人民共和国民法总则 第一条   公布 2017-03-15   施行 2017-10-01   已废止
中华人民共和国民法通则 第一条   公布 2009-08-27   施行 2009-08-27   已废止
```

三条都会返回（检索不过滤状态），`validity_status_label` 直接标出后两条已废止；这里的重点是
**同为「第一条」，日期能把三个版本排出先后**。同法名的新旧版本、基础法与针对它的修改决定，同理。

覆盖度（全语料）：`publish_date` 有值 99.9%，`effective_date` 91.8%。要排序时优先用
`publish_date`——它几乎总是有值；`effective_date` 缺的时候才需要它兜底。

---

## 2. POST /api/retrieval/test

入参与出参与端点 1 **完全一致**（同一份实现），保留此路径供前端调参页调用。对外集成用
`/api/retrieval/search`。

---

## 3. GET /api/legal/articles/{article_id}

### 入参

| 参数 | 位置 | 说明 |
|---|---|---|
| `article_id` | path | `{doc_id}:{article_number}`，例如 `0cab655e-3bbc-41bf-b30c-f93753493220:6`。通常取自检索结果的 `metadata.article_id`；**也可以自己拼**：文档列表 `GET /api/knowledge-bases/{kb_id}/documents` 里的 `id` 就是 `doc_id`。按最后一个冒号切分（doc_id 是 UUID，不含冒号） |

### 出参

| 字段 | 类型 | 说明 |
|---|---|---|
| `article_id` | string | 原样回显 |
| `doc_id` / `kb_id` / `filename` | string | 所属文档与知识库 |
| `content` | string | **条文全文**（父块；超长条文被切成多个子块时，这里返回拼回的完整条文） |
| `matched_content` | string | 命中的那一段子块正文 |
| `law_name` / `article_number` / `article_label` / `chapter` | 可能为 null | 法条身份 |
| `law_type` / `province` / `city` / `issuing_authority` | 可能为 null | 效力层级与地域 |
| `publish_date` / `effective_date` | 可能为 null | 发布 / 施行日期 |
| `validity_status` | int\|null | 时效状态原始整数（不解释语义） |
| `source` | string\|null | `global` / `personal` |

**不返回**修订历史与关联司法解释：语料里没有这两类数据，返回空数组会被误读为"该法条恰好
没有"，因此宁可不给这个字段。

---

## 4. 状态码

| 状态码 | 场景 |
|---|---|
| `200` | 成功 |
| `400` | `session_id` 非空；`match_mode` 取值非法；`page × top_k > 100`；`article_id` 格式不正确 |
| `401` | 缺少或无效凭据 |
| `403` | 超级管理员默认不可读业务正文（平台内容边界） |
| `404` | 法条不存在；知识库不可读（跨租户/无权限统一表现为 404，不泄露存在性） |
| `422` | 请求体校验失败（`query` 为空、`top_k` 越界等） |

---

## 5. 调用示例

```bash
BASE=http://10.30.1.6:8889
KEY=<API Key>

# 关键词检索（不传库 → 只查全局法条库）
curl -s -X POST $BASE/api/retrieval/search -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{"query":"劳动保护"}' | python3 -m json.tool

# 层级筛选 + 地域筛选 + 分页
curl -s -X POST $BASE/api/retrieval/search -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"陶瓷","law_levels":["local_regulation"],"province":"江西省","city":"景德镇市","top_k":5,"page":1}' \
  | python3 -m json.tool

# 精确检索（法名 + 条号）
curl -s -X POST $BASE/api/retrieval/search -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{"query":"民法典第一条","match_mode":"exact"}' \
  | python3 -m json.tool

# 检索 → 取 article_id → 查详情（完整闭环）
AID=$(curl -s -X POST $BASE/api/retrieval/search -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{"query":"民法典第一条","match_mode":"exact"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['results'][0]['metadata']['article_id'])")
curl -s -H "Authorization: Bearer $KEY" "$BASE/api/legal/articles/$AID" | python3 -m json.tool

# 法条内容校验（= 检索 + top_k=1，取相似度自行判阈值）
curl -s -X POST $BASE/api/retrieval/search -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"故意伤害他人身体的，处三年以下有期徒刑、拘役或者管制","top_k":1}' \
  | python3 -m json.tool
```

---

## 6. 使用注意

**结果顺序不等于 `score` 降序。** 链路末段有 MMR 多样性重排，实测出现过 `#5 score=0.1945`
排在 `#6 score=0.2567` 之前。需要原始顺序就直接用 `results` 的下标；按 `score` 自行重排会
得到不同顺序。

**`total` 不是命中总数。** 向量召回走有上限的候选池，不存在有意义的总数；翻页只用
`has_more`。

**分数不能跨模式比较。** `direct` 的 `score` 是余弦相似度，`hybrid` 的是复合分，`exact`
恒为 1.0。

**`article_id` 的稳定性取决于 `doc_id`。** 重跑管线、`batch-retry`、`reindex_all` 都不换
`doc_id`，ID 稳定；删除文档后重新上传会换 `doc_id`，该法所有条文的 `article_id` 随之失效。

**检索延迟受远程 embedding / rerank 服务影响。** 入库高峰时（入库与检索共用同一个远程
embedding 服务）实测单次可达 20–40 秒；服务空闲时 `direct` 可到 0.1 秒级、`hybrid` 1–2 秒。
排查延迟先看这两个远程服务，而不是检索链路本身。

---

## 7. 当前未提供的能力

| 能力 | 状态 |
|---|---|
| 仅返回现行有效法条（PRD F-103） | **不实现（口径已改）**：检索返回全部状态，并附 `validity_status` 与 `validity_status_label`，由调用方自行筛掉不要的。需要"只留 `3` 现行有效"的话，用返回的取值过滤即可 |
| 列出一部法的全部条号 | 未提供。只能知道条号后自行拼 `{doc_id}:{条号}` |
| 深分页 | 窗口上限 100（`page × top_k`），更深的翻页无意义（候选池有上限） |
| 修订历史 / 关联司法解释 | 语料中没有这两类数据 |
| 会话附件作为检索源 | 本部署已移除会话链路，传 `session_id` 返回 `400` |
