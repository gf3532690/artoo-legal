# 法条召回服务 · 落地方案

> 状态：**已实施 + 已核查**（Phase 0～5 全部完成并提交；2026-09-11 完成一轮逐项核查与补齐，见 §15）
> 代码基线：`develop @ 3c184f5`
> 本文为唯一有效版本，替换此前各版讨论稿

---

## 前提（读前必读）

1. **Fork 仓库改造，不切新分支。** 将 `9ilfoyl3/artoo` fork 成独立仓库后，直接在 fork 里改造。上游仍在活跃演进，因此共享文件的改动要尽量小，并定期从 upstream 同步。
2. **两个应用完全独立部署。** Artoo 与法条库各自独立部署、互不干涉。Artoo 保留其全部原有能力；法条库是独立的精简应用。
3. **全新部署，不带存量数据。** 没有存量迁移、没有租户回填，数据存储可按目标结构直接建。
4. **定位收敛为法条召回。** 不做运行时 LLM 生成问答，不做引用图谱。

这四条前提决定了后面几乎所有取舍：删什么留什么、要不要写迁移脚本、能不能自由改结构、UI 标识怎么改。

---

## 0. 目标

把 Artoo 改造成一个**法条召回服务**：入库阶段把法条结构化（法名、条号），运行阶段只做检索，返回**能定位到具体法条**的结果。

检索以**语义检索为主**：用查询内容在法条正文上做语义匹配，命中后返回对应的法名与条号。调用方也可以直接输入「法条名 + 条号」来查，法条名不要求精确。

### 0.1 相对 Artoo 的全部差异

因为检索接口就是同一个 `/api/retrieval/search`，这里把全部差异一次列清——**其余部分完全相同，不做任何改动**。

| 维度 | Artoo | 法条库 |
|------|-------|--------|
| 检索算法 | Dense + Sparse + BM25 → RRF → Rerank → MMR → 父块扩展 | **完全相同**（同一套代码） |
| 检索接口 | `/api/retrieval/search` | **同一个接口** |
| 检索目标 | 调用方传什么查什么 | 调用方传的 + **自动带全局法条库** |
| 结果元数据 | `parent_id` / `chunk_index` / `element_type` 等 | 追加 `law_name` / `article_number` / `source` |
| `top_k` 默认值 | 10 | 5 |
| 切分器 | 自动路由到 `naive` | 显式 `chunker_type=laws` |
| 入库元数据 | 章节路径 `section_path`、页码、元素类型 | 追加 法名 / 条号 / 款项；**剥离目录** |
| Milvus `content` 前缀 | `[文件名] 正文` | `[法名 第N条] 正文`（供 BM25 匹配阿拉伯数字条号） |
| 无条文结构文档 | 整篇一个父块、按行切子块 | 按「一、」切父块（D11） |
| 会话附件 | 有 | 删除 |
| Chat / Agent / MCP / Skills | 有 | 删除 |
| 租户模型 | 多租户 + 外部用户租户 | **单租户** |
| 知识图谱 | 可选（`GRAPH_ENABLE`） | 不做 |
| 时效性 | 无 | 无（暂缓） |
| 评测 | harness + 5 条样本 | 复用 harness，不建评测集 |
| UI | 完整产品面 | 品牌与术语改为「法条库」，菜单精简 |

**结论：差异几乎全在「入库时把内容变成什么形态」这一侧，检索算法侧只有三处最小增量**（自动带全局库、结果补法条字段、`top_k` 默认值）。这也解释了为什么 §8.3「必须新写」的清单只有 6 项，且全部落在入库路径上。

**由此推出两条工程含义**：

1. Artoo 侧对检索算法的任何改进（rerank 参数、召回配置等）都**免费**惠及法条库——前提是 fork 持续从 upstream 同步（见 D8）。
2. 反过来，如果法条库的召回质量不达预期，**可调的杠杆在语料与入库，不在算法**：改分块参数、改元数据抽取、改目录剥离规则，而不是去改 RRF 或 rerank。

---

## 1. 范围边界

### 做

- **改造现有检索接口**：`POST /api/retrieval/search` 默认带上全局法条库，与调用方传入的个人库 `kb_ids` 一起检索。**不新增检索端点。**
- 法条结构化入库：法名、条号抽取（含中文数字 → 阿拉伯数字）
- 目录剥离：入库时移除「目录」区，避免导航文本变成可检索片段
- 条号数字形态归一：让「民法典第146条」能命中正文写作「第一百四十六条」的内容
- 删除非召回链路（Chat / Agent / MCP / Skills / 会话附件）
- UI 标识与术语改为「法条库」

### 不做

- 运行时 LLM 生成与多轮对话
- 引用图谱（原 P3，本期整体砍掉）
- 精确过滤车道与 `legal_filters`
- 批量检索端点
- 法条专属的增删改接口（内容维护全部复用现有知识库 / 文档接口）
- 运行时 LLM 抽取（法名与条号均为确定性提取）

### 暂缓（设计保留，本期不做）

- **法条时效性**（`legal_status` / `effective_date` / `expiry_date` / `superseded_by`）：本期不落字段、不参与过滤。过渡期建议入库时仍把正文里能解析到的施行日期写入 PG `chunk_metadata`，将来启用不必重新解析。
- **评测集**：已确认本期不做。影响是"改造是否有效"暂时无法用数据证明，验证只能依赖单测 / 集成 / 端到端与人工抽查。将来补建时优先从真实检索日志抽样。
- **法律修订的自动替换**：已确认本期由维护者在界面上**人工删除旧文档 + 上传新版**，不做按 `law_name` 自动替换。

---

## 2. 语料事实（实测）

对样本语料（`法律法规/法律/`）逐文件统计所得，是所有分块与解析决策的依据。

| 项 | 实测值 |
|---|---|
| 文件数 | 347（345 `.docx` + 2 `.doc`）；345 个 `.docx` 已全量解析，2 个 `.doc` 需 LibreOffice 转换后才能读 |
| 命名约定 | `<法名>_<YYYYMMDD>`，345/345 `.docx` 全部符合（2 个 `.doc` 同规则未验证）——**但不作为权威来源** |
| 同名多版本 | 0 个（去掉日期后无重名） |
| 总文本量 | 2,791,399 字 |
| 文档长度 | 中位数 6,517 字，P90 14,600，最长《民法典》108,593 字 |
| 行首 `第X条` 总数 | **22,610**（平均每条 **123 字**） |
| 行首 `一、` 条目总数 | 413 |
| 含独立「目录」行的文件 | **264 / 346（76.3%）**；目录区合计 **2,988 行**，平均每文件 11.3 行 |
| 完全无行首 `第X条` 的文件 | **37 个** `.docx`（另有 2 个 `.doc` 疑似同类，需转换后确认），合计 917 行 |
| 头部日期行 | **345/345 都能解析出「YYYY年M月D日」**（无例外）；括号风格 344 个用全角「（」，1 个用半角「(」 |
| 行首「（一）」层级 | 283/345 文件含该层级（条文内部的**项**） |
| 上述两类的交集 | **0** |
| 容量上限 | `kb_chunk_cap` 默认 1,000,000 |
| 预估 child chunk 总量 | 约 27,800（含目录区），占上限约 2.8% |

**三条由数据直接得出的结论：**

1. **分块参数保持默认。** 平均每条 123 字，远低于 `child_chunk_size`（450）。绝大多数条文就是「一个父块 + 一个子块」，`overlap` 几乎不触发。`parent_chunk_size` 无需调大。
2. **容量不是问题。** 约 2.78 万 chunk 对 100 万的上限，占 2.8%。
3. **语料不是单一形态。** 存在两类非标准结构，见 D10 与 D11。

---

## 3. 现状基线（已逐项核对源码）

| 能力 | 现状 | 位置 |
|------|------|------|
| 对外检索接口 | `POST /api/retrieval/search`（对外推荐）与 `/test`（前端），同一底层实现 | `api/retrieval.py` |
| 请求模型 | `query` / `knowledge_base_id` / `kb_ids` / `session_id` / `mode` / `top_k=10` | `api/retrieval.py::RetrievalTestRequest` |
| 响应信封 | `query / mode / total / elapsed_ms / results / trace / degraded / failed_source_count` | `api/retrieval.py::RetrievalTestResponse` |
| 单条结果模型 | 含 `metadata: dict`（已在下发） | `api/retrieval.py::RetrievalResultItem` |
| 结果水合 | 已有「收集 doc_ids → 一次批量查 → 拼装」的批量水合模式 | `api/retrieval.py::_build_result_items` |
| 多源检索 | 各源同权（`priority=1.0`）、统一 rerank、支持每源独立 `expr` | `retrieval/multi_kb.py::MultiKBRetriever` |
| 检索器装配 | Dense + Sparse + BM25（+ 可选图谱第四路） | `retrieval/factory.py::build_hybrid_retriever` |
| BM25 内容增强 | 写入 Milvus 的 `content` 带 `[文件名]` 前缀 | `pipeline/pipeline.py` ≈L683 |
| 切分器路由 | `csv/xlsx → table`，**其它一律 `naive`**；`laws` 仅在 KB config 显式指定时生效 | `pipeline/chunker_router.py` |
| 分块参数 | 仅 `naive` 接收 `parent_size` 等参数 | `pipeline/pipeline.py::_select_chunker` |
| 大小护栏 | 对**所有** chunker 统一生效，按租户配置取上限 | `pipeline/pipeline.py` → `chunker.py::enforce_size_limits` |
| 法条切分器 | `LawsChunker` 按「第X条」与判决结构切父块，无 size 控制 | `pipeline/chunkers/laws.py` |
| 元数据抽取 | `ChunkMetadata`（含 **`section_path`** 章节路径） | `pipeline/metadata.py` |
| 富化器 | `Enricher` 是**文本** pass-through（收 `list[str]`） | `pipeline/enricher.py` |
| Chunk 元数据落库 | `chunk_metadata` JSON 列，写入用 `asdict(meta)` | `schema/db.py`、`pipeline/pipeline.py` |
| 存储拓扑 | 所有知识库共用一张物理表（按维度分表），`kb_id` 为 Partition Key | `storage/milvus.py` |
| .doc 支持 | `DocLoader` 经 LibreOffice 转 `.docx`；Docker 镜像已装 | `pipeline/loaders/doc_loader.py`、`backend/Dockerfile` |
| 建库逻辑 | 盖章 `tenant_id` + `owner_user_id`，`require_member()` | `api/knowledge_base.py` |
| 文档维护接口 | `POST /api/knowledge-bases/{kb_id}/documents/upload` 等全套 | `api/document.py` |
| 前端能力开关 | 公开端点下发 `graph_enabled`，前端据此决定入口显隐 | `api/system.py::/frontend-config` |
| 前端超管路由白名单 | `SUPER_ADMIN_ALLOWED_PATHS` 为**精确匹配**，不在集合内即重定向 `/tenants` | `components/Layout.tsx` |
| 评测工具 | B5 Evaluation_Harness，复用 `search_with_trace`，支持 before/after 对比 | `app/scripts/evaluate_retrieval.py` |
| 现有评测集 | 仅 5 条 query，按 `expected_keywords` 判命中，`expected_doc_ids` 全空 | `app/scripts/eval_sets/large-kb-legal-sample.json` |
| 仓库规范 | 非平凡变更必须同 PR 提交 Agent Note（英文 + `.zh.md`） | `AGENTS.md` |

> **上游原本把测试与评测工具排除在版本管理之外，本 fork 已改为纳入。** `.gitignore` 原文注释即「本地测试文件，不纳入版本管理」与「内部评估/性能测试脚本」，另忽略 `backend/_e2e_*.py`。由此产生四个必须知道的事实：
>
> 1. **上游的新克隆里既没有测试套件、也没有评测 harness。** 本机 `aladdin` 的 `backend/tests/` 有 **50 个源文件**（49 个 `.py` + 1 个 `_base.txt`，可收集 524 个用例；另有 105 个 `__pycache__` 字节码缓存不计），`backend/app/scripts/` 有 **4 个源文件**（`__init__.py` / `evaluate_retrieval.py` / `measure_load_overhead.py` / `eval_sets/large-kb-legal-sample.json`）。
> 2. **本 fork 已把这两处改为纳入版本管理**（`.gitignore` 已放开，提交 `37cf5e0`）。所以在**本 fork 内**把回归测试写进 `backend/tests/` 会被正常提交，不存在"写了却进不去仓库"的问题；但**从上游同步时要注意**——上游对这些路径的改动不会带来测试变化。
> 3. **本机测试基线并非全绿。** 524 个用例可收集，但有 6 个文件因引用已删除/改名的模块而收集失败：`test_content_router.py`、`test_e2e_session_upload.py`、`test_final_answer_parse.py`、`test_json_field_extractor.py`、`test_milvus_session_files.py`、`test_thinking_dialect.py`。它们是**既有陈旧文件**、不在本次改造范围内；将来跑 pytest 需用 `--ignore` 排除或先清理，否则收集错误会中断整轮。
> 4. **Python 环境用 conda 的 `aladdin` 环境（3.12.13）**，不是仓库内的 `.venv`（3.13.11，与项目要求的 3.12 不符）。前端 `npm ci` 已在本 fork 工作区装好，`npm run build` 实测通过。

---

## 4. 业务逻辑

### 4.1 两级库与职责边界

**租户模型：单租户。** 法条库应用有且只有一个默认租户，全部知识库（1 个全局 + N 个个人）都在这个租户内。

| 角色 | 职责 |
|------|------|
| Super_Admin | 平台装配：建租户、配 Embedding / Rerank、签发 API Key。**不参与法条内容维护** |
| 默认租户管理员 | **维护全局法条库**（上传 / 删除 / 人工替换） |
| 调用方 | 用绑定默认租户的 Key 调检索接口，库范围由 `kb_ids` 参数决定 |

| 维度 | 全局法条库 | 个人库 |
|------|-----------|--------|
| 数量 | 全租户 1 个 | 由下游自行管理 |
| 维护 | 默认租户管理员经后台 | 下游按 `kb_id` 经现有 API |
| 检索时 | **自动带上，无需调用方传** | 由调用方在 `kb_ids` 里传入 |
| 权限判定 | 不做（见 D4） | 不做（见 D4） |

**下游（law-agent-lite-backend）承担「谁能看哪些库」的业务规则**：它用自己的用户权限与关联表算出应取哪些个人库，把 `kb_ids` 传进来。法条库不干涉这件事——Key 只是"能不能调这个接口"，调什么内容由参数决定。

**Key 选型（两把路，均已实测）**：

1. **代理 Key + `X-External-User-Id`（对齐 Artoo 原设计，推荐给多终端用户的调用方）**：
   超管签发 `external_agent` Key，调用方每次请求带 `X-External-User-Id: <自有用户ID>`；
   法条库按 `(代理Key, 外部用户ID)` 懒创建**独立外部用户身份**，该身份各自拥有私有个人库，
   互相不可见。Artoo 原本把外部用户硬锁在内置 `tenant-external-builtin`——那样跨租户读不到
   默认租户里的全局法条库（实测：列库返回 0、检索直接 `400`）。本 fork 因此新增配置
   `EXTERNAL_USER_TENANT_ID`，默认指向**默认租户**，外部用户与全局库同租户，于是既能读全局库
   又各自隔离；要退回上游形态把它设回 `tenant-external-builtin` 即可。
   建库/归属/隔离不需要新代码：`owner_user_id` 取 `identity.acting_subject_id`，
   外部用户天然落成 `external_users.id`（原设计已支持）。
2. **用户级 Key（一把 Key = 一个身份）**：`apikey_auth.py::_auth_user_level` 取
   `tenant_id=api_key.tenant_id` 并带绑定用户主体，能读 `own_ids` ∪ `public_ids`
   （全局库 `organization` + `read`）。适合"一把 Key 代所有用户"、库↔用户映射全在下游的简单接法；
   代价是**法条库内部不做终端用户隔离**，隔离完全靠下游。

两种可并存：代理 Key 面向"要按终端用户隔离"的调用方，用户级 Key 面向"库范围由下游自算"的调用方。

### 4.2 检索链路

```
下游 → POST /api/retrieval/search
   │        body：query / kb_ids（个人库，可空） / top_k（默认 5）
   ├─ 解析目标库：全局法条库 id（固定，自动加入） + 调用方传入的 kb_ids
   ├─ 检索：MultiKBRetriever 并行检索各源 → 各源同权 → RRF → Rerank → MMR → 父块扩展
   ├─ 水合：扩展现有批量水合，补 law_name / article_number
   └─ 返回：沿现有响应信封
```

### 4.3 入库链路

```
上传 → Loader（.doc 经 LibreOffice 转 .docx，artoo 原有能力）
     → 【文档级预处理：目录剥离 + 法名解析】   ← 必须在 Chunker 之前
     → Chunker(laws) → enforce_size_limits(按租户分块参数)
     → Enricher(现有，pass-through) → MetadataExtractor(章节路径等)
     → LegalMetadataExtractor(新增：条号抽取) → Embedder
     → Indexer ─┬→ Milvus：向量 + content（含法名与阿拉伯数字条号前缀）
                └→ PostgreSQL：chunk_metadata JSON（法条字段）
```

> **顺序要点**：目录剥离会改变文本，**必须在切分之前做**——否则目录行早已被切成 chunk 并写入索引，再剥就晚了。法名解析是文档级信息，同层处理。早先稿子把「目录剥离」列在切分之后的抽取器里，是错的。

> **本部署没有 OCR 阶段**：`OCR_ENABLED=false`（见 D15）。语料中仅 5 份国家象征类文档（国徽法 / 国歌法 / 国旗法 / 香港基本法 / 澳门基本法）含嵌入图片，且图片是国徽、国歌、国旗等图案，与法条正文无关；关闭 OCR 后这些图片既不识别、也不会把识别结果插进正文。

---

## 5. 关键设计决策

### D1 · 法条元数据只落 PG，不动 Milvus schema

因为本期**不做过滤**（语义检索为主），Milvus 侧不需要法条标量字段——它们只需要作为**输出**返回。

做法：检索后扩展现有的批量水合（`_build_result_items` 已经在批量查文件名），再按 `chunk_id` 批量查 `Chunk.chunk_metadata`，取出 `law_name` / `article_number` 放进结果。

这样**完全不需要改 Milvus schema**：不动 `_build_fields`、不动 `_SCALAR_INDEXES`，也就不存在"会话 collection 被同步加空列"这类副作用。

> 与早先讨论稿的差异：原稿打算把法名 / 条号写进 Milvus 标量字段并建索引。取消精确过滤车道后，这一整块不再必要。代价是每次检索多一次批量 PG 查询（`top_k=5` 时开销可忽略）。

### D2 · 新增独立抽取器，命名避开现有 `Enricher`

法条抽取不塞进 `LawsChunker`：`ChunkResult` 是所有 chunker 共享结构，往里加字段会外溢；`article_number` 是父块级属性，需经 `parent_child_map` 下发。

**命名注意**：`pipeline/enricher.py::Enricher` 已存在，语义是「对 chunk 文本做摘要 / 关键词富化」（收 `list[str]`、返回 `list[str]`，当前 pass-through）。不要复用该名字。建议命名 `LegalMetadataExtractor`，放在 `pipeline/legal_metadata.py`。

### D3 · 法条库显式指定 `chunker_type=laws`

`ChunkerRouter.select()` 对非表格文档一律返回 `naive`，`laws` 仅在 KB `config.chunker_type` 显式指定时生效。两级库都在创建时带上该标记，用户无感。

补充：`_select_chunker` 只把分块参数传给 `naive`；但 `enforce_size_limits` 对所有 chunker 统一生效，因此分块参数对法条库同样有效。

### D4 · 单租户模型：不做跨租户例外，也不做逐用户鉴权

已确认法条库是**单租户部署**（有且只有一个默认租户），全局库与所有个人库都在其中。因此：

- **不需要跨租户例外**——早先稿子里「注入 `cross_tenant_kb_ids` 放行全局库」这一整节取消
- **不需要逐用户鉴权**——调用哪些库由参数决定
- 全局库用 `visibility=organization` + `org_permission=read`：同租户身份自然可读，**零鉴权代码**

**一处重要更正**：早先稿子写「Super_Admin 登录后台维护全局库」。这在现有代码里**走不通**：

- `kb_authorization_decision` 的第一判定是跨租户硬隔离，而 Super_Admin 的 `tenant_id` 为 `None`，`None != "t1"` 恒成立 → 任何 KB 都返回 404
- `assemble_allowed_kb_ids` 对 platform 身份直接返回空集（注释原文：「内容检索不属其职权，受内容边界约束」）
- 前端 `SUPER_ADMIN_MENUS` 不含 `/knowledge-bases`，注释写明「超管无租户上下文、不参与内容」

**超管在设计上就是不参与内容的**（前后端一致）。所以维护者改为**默认租户的管理员**——它是一个普通业务身份，走现有的 owner 放行逻辑，不需要任何鉴权例外。Super_Admin 仍然存在，但只承担平台装配（建租户、配模型、签发 Key）。

### D5 · 检索以语义为主，本期不做精确过滤车道

调用方可以输入「法条名 + 条号」，但**这不是过滤条件**，而是查询文本——由语义与词法检索去匹配。因此：

- 不新增 `legal_filters` 字段
- 不做法名精确匹配（避免「库里存全称、用户说简称 → 静默返回空」这类失败）
- 结果里返回 `law_name` / `article_number`，供调用方判断命中是否正确

将来若真实查询分布显示精确条号查询占比高，再考虑补精确车道——那时需要先做法名规范化与别名映射。

### D6 · 两库合并直接复用 `MultiKBRetriever`

全局库 + 个人库并列作为检索源，各源同权、统一 rerank，按 rank 分合并排序。`MultiKBRetriever` 本就是这套语义，且支持每源独立 `expr`；`api/retrieval.py::_run_multi_source_retrieval` 是现成用法。

**已知取舍（明确接受）**：个人库内容质量不可控，同权合并下可能排在权威法条之前。缓解手段是结果带来源标记，下游可自行取舍。

**来源标注（已定）**：每条结果的来源写进已有的 `metadata`，字段名 `source`，取值 `"global"` / `"personal"`。不改响应模型——`RetrievalResultItem` 已经在下发 `metadata`。

### D7 · 非召回链路：真正删除

因为 Artoo 作为独立应用继续存在，删除法条库应用里的这些链路**不会影响任何下游**：

`app/agent/**`、`app/api/chat.py`、`app/mcp/**`、`app/mcp_server.py`、`app/api/mcp_config.py`、`app/api/skills.py`、`app/api/agent_config.py`、`app/session_upload/**`、`app/api/session_upload.py`、`app/api/session.py`

以及前端对应页面与路由。

**删除前必须处理的六处耦合**（按目录删会炸掉别的东西；以下为逐文件核查结果）：

| # | 耦合点 | 位置 | 处置 |
|---|--------|------|------|
| 1 | `session_upload/limits.py` **不是会话专属**，是普通上传的容量校验器 | `api/document.py:34`、`pipeline/pipeline.py:50,571` | 先挪到中立位置（如 `app/pipeline/limits.py`）再删 `session_upload/` |
| 2 | `session_upload.service` 被检索接口当会话附件源引用 | `api/retrieval.py:31` | 随会话附件一并移除该分支 |
| 3 | `session_upload.memory::recommend_kb_chunk_cap` 被系统配置接口引用 | `api/system.py:26` | 一并挪到中立位置或内联 |
| 4 | `agent.tools.mcp_client::invalidate_mcp_tools_cache` 被能力重载引用 | `api/capability_reload.py:59` | 移除该分支（MCP 随 D7 删除） |
| 5 | 会话上传的 EventHub / 队列 / 订阅循环在 lifespan 里初始化 | `main.py:251,256,290,291` | 与路由同批移除 |
| 6 | 会话上传 Worker 在 Worker 进程里启动 | `worker_main.py:194-197` | 同批移除 |

**修正一处先前的误判：`_get_llm_for_request` 仍然需要抽出。** 早先写「图谱已砍，因此不需要抽出这个函数」——但**图谱代码本身不在删除清单里**。它按 D15 的模式处理：**配置层关闭、不删模块**（`GRAPH_ENABLE=false`）。

而 `storage/graph_store.py:2388` 与 `pipeline/graph/worker.py:438` 仍在函数内 `from app.api.chat import _get_llm_for_request`。删掉 `chat.py` 后这两处就成了指向不存在模块的死引用——图谱开关一旦被打开就 `ImportError`。

处置：把 `_get_llm_for_request` 抽到中立模块（如 `app/models/llm_resolver.py`），改这两处 import。改动很小，但必须做。

**`api/query_understanding.py` 会成为孤儿**：它只被 `chat.py:22` 引用，随 D7 一并删除。

**入库侧本就没有 LLM**：`pipeline.py` 的 `Enricher(llm=None, enabled=False)` 是硬编码关闭的。删掉上述模块后，法条库应用在**请求路径上不含任何 LLM 调用**（图谱代码保留但永不启用）。

### D8 · Fork 带来的工程约束

前提见文首。由「Fork 仓库 + 全新部署」推出的结论：

- **收益**：无存量迁移、数据结构可自由演进、全局库可在引导时一次创建
- **代价**：fork 会与上游分叉，而上游 `9ilfoyl3/artoo` 仍在活跃演进。改造必然触碰共享文件（`pipeline/pipeline.py`、`api/retrieval.py`、`api/document.py`、前端 `Layout.tsx` / `App.tsx` / `Landing.tsx`）
- **约束**：专属逻辑尽量落在新文件；共享文件只做最小插入；**把上游配成 `upstream` remote 并定期 fetch + 合并**（`git remote add upstream https://github.com/9ilfoyl3/artoo.git`）；fork 即长期产品线的上游，不计划向原仓库提 PR
- **本地工作仓库（已就绪）**：fork 为 `gf3532690/artoo-legal`，本地克隆在 `C:\newHLSWorkspace\artoo-legal`；`origin` 指向 fork、`upstream` 指向原仓库 `9ilfoyl3/artoo`，工作分支 `develop`（基线 `3c184f5`，其上已有方案文档提交）。改造全部在这个仓库内进行，**不动**上游克隆 `C:\newHLSWorkspace\aladdin`
- **注意**：GitHub 对同一账号 + 同一仓库只允许一个 fork。原有一个 2026-05-19 的陈旧 fork（名为 `aladdin`，对应项目旧名）已按无独有提交核实后**改名为 `artoo-legal`**并同步到上游最新状态——效果等同于重建，且不需要删库权限

### D9 · UI 标识与术语：改展示层，不改契约层

**原则**：只改用户可见的展示文案，**不改** API 路径、表名与代码标识符。

| 位置 | 现状 | 改为 |
|------|------|------|
| 浏览器标题 | `frontend/index.html`：`Artoo, 一个 Agentic RAG 管理后台` | 法条库相关 |
| 侧边栏品牌 | `components/Layout.tsx`：`Artoo` | 法条库 |
| 落地页 | `pages/Landing.tsx`（6 处，含「以 ReAct Agent 为核心…」整段描述） | **重写**为法条召回定位 |
| 登录页 | `pages/Login.tsx`（4 处） | 同上 |
| 菜单项 | `Layout.tsx`：「知识库」 | 「法条库」 |
| 页面文案 | 「知识库」术语约 20+ 文件 | 「法条库」 |
| 后端展示文案 | `main.py` 的 FastAPI `title` / `description` / 根路径 | 法条库相关 |

**不改**：API 路径（`/api/knowledge-bases/*` 等）、表名（`knowledge_bases`）、字段名（`kb_id`）、代码标识符（`KnowledgeBase` 等）、`artoo-open-api.md` 里的接口路径。

**已定**：移除 `Landing.tsx` 里指向 `github.com/9ilfoyl3/artoo` 的链接；品牌走**编译期常量**（只有单一交付物，不引入运行时配置复杂度）。

**实现方式**：扩展 `frontend/src/lib/labels.ts`（已有角色名映射的集中式先例）为通用产品文案模块，各处引用常量，不做 20+ 文件的散点替换。**聊天相关组件的文案不用改**——那些页面随 D7 一并删除。

> **实际实现（已执行，与上面的设想不同）**：实测「知识库」在 27 个文件里出现 **134 次**，逐处改成引用常量意味着 134 次 import 重构——改动量与出错面都不划算。实施改为**一次性批量替换展示文案**（脚本替换，替换后复查残留为 0），品牌名同理直接替换。集中文案模块留待真正出现「一套代码多品牌」需求时再做；在那之前，它的收益只是"改一处生效"，而批量替换已经达到了同等效果。

### D10 · 目录剥离：标记驱动，绝不按位置猜

**数据**：264/345 文件含独立「目录」行，目录区合计 2,988 行（约占全部 chunk 的 11%）。这些行是「第九章　诉讼时效」这类主题标签，语义上与用户查询高度相似，会挤占召回名额并返回无用内容。

**规则（必须按标记，不能按位置）**：

1. 只在检测到**独立成行的「目录」标记**时才触发剥离
2. 剥离范围 = 该标记行之后，到第一个行首 `第X条` 之前的所有行
3. **没有目录标记就一个字节都不动**

**为什么必须标记驱动**：语料里有 37 个文件（另有 2 个 `.doc` 同类）**完全没有 `第X条` 结构**（见 D11），它们整篇都是正文。若按「丢弃第一条之前的全部内容」这种朴素规则处理，这些文件会被整篇删空。

**安全性证据**：345 个 `.docx` 中，「有目录标记」与「无 `第X条`」的交集为 **0**。因此在现有语料上，标记驱动规则不会误伤任何文件。

### D11 · 无条文结构文档（修正案 / 决定 / 规定）的处理

**数据**：37 个 `.docx` 完全没有行首 `第X条`，合计 917 行（另有 2 个 `.doc` 文件名显示属同类，需 LibreOffice 转换后确认）。分三小类：《刑法修正案》（一～十二）、全国人大常委会对某部法某条的《解释》、《批准决议 / 决定 / 规定》。其中 **20 个含行首「一、」条目，16 个两者皆无**。

**实例**（《中华人民共和国刑法修正案（十一）》，142 行 / 9,291 字 / 48 个「一、」条目 / 0 个「第X条」）：

```
中华人民共和国刑法修正案（十一）
（2020年12月26日第十三届全国人民代表大会常务委员会第二十四次会议通过）
一、将刑法第十七条修改为："已满十六周岁的人犯罪，应当负刑事责任。
…
二、在刑法第一百三十三条之一后增加一条，作为第一百三十三条之二："对行驶中的公共交通工具的驾驶人员…
```

它的结构单元是「一、二、三」，正文里的「刑法第十七条」是**引用**而非结构。

**当前行为的问题**：无任何条文命中时，`_split_into_articles` 把整篇当作一个父块，再**按单行切子块**。而这 20 个文件的「一、」条目**平均跨 2.56 行**（引号内的条文文本会换行），于是子块被从条目中间切开，产生「"已满十四周岁不满十六周岁的人…」这类既不知属于哪份修正案、也不知属于第几项的碎片。

**处置**：

1. `article_number = None` —— 这类文件本就没有条文编号，硬造反而错
2. `law_name` 按 D13 的规则提取（首行至日期行之前拼接；本例标题只占一行，结果即「中华人民共和国刑法修正案（十一）」）
3. **给 `LawsChunker` 加一个条件化 fallback**：当整篇**零个行首 `第X条`** 时，退化用「一、二、三、」作为父块边界；只要有任意一个 `第X条` 命中，就完全走现有逻辑

**为什么必须条件化**：普通法律里「一、二、三」是**条文内部**的项（《国籍法》第七条含三个项）。若无条件把「一、」当父块边界，会把这些项从所属条文里拆出去，破坏本来正确的结构。

**可选增强（建议做）**：这类条目的正文几乎都会点名它改的是哪一条（「第一百六十二条后增加一条」）。把条目内出现的「第X条」抽出来写进该 chunk 的 BM25 前缀：

```
[中华人民共和国刑法修正案 第一百六十二条] 一、第一百六十二条后增加一条，作为第一百六十二条之一：…
```

这样查「刑法第一百六十二条」时，修改过该条的修正案能一并被召回。这不是图谱，只是把文中已有的数字写进检索前缀，成本几乎为零。

**剩余 16 个**既无条文也无「一、」条目的文件（多为批准决议），保持现状——纯叙述性短文本，整篇作父块由尺寸护栏处理。

**两个从真实样本得到的分层细节**：

- 「一、」条目内部还可能再嵌一层「（一）（二）」，例如《刑法修正案》（19991225）第六条下属四个「（一）～（四）」。fallback 只把「一、」当父块边界即可，嵌套层自然落为该父块内的子块。
- 首个「一、」之前可能有一段引语（如「为了惩治破坏社会主义市场经济秩序的犯罪…作如下补充修改：」）。沿用 `_split_into_articles` 既有的「标记之前的内容作为第一个父块」行为即可，不需要额外处理。

**头部日期解析的鲁棒性**：345 个 `.docx` **全部**都能解析出「YYYY年M月D日」，没有例外；括号风格 344 个是全角「（」，1 个是半角「(」（即 19991225 这一份）。解析器必须同时接受两种括号，但不必处理"完全无日期"的情况。

### D12 · 条号数字形态归一：命中「第146条」的查询

**问题**：正文写的是「第一百四十六条」，用户输入的是「民法典第146条」。中文数字与阿拉伯数字在 BM25 上是不同的词，词法层匹配不上；语义向量对这种形态差异也不可靠。

**做法（复用既有机制）**：`pipeline.py` 给写入 Milvus 的 `content` 加了 `[文件名]` 前缀，本来就是为 BM25 服务的。把该前缀扩展为**包含法名与阿拉伯数字条号**，例如：

```
[中华人民共和国民法典 第146条] 第一百四十六条　具备下列条件的民事法律行为有效：…
```

这样「民法典 第146条」这类查询能在词法层命中。改动集中在写入路径一处，不涉及任何契约。

**一个必须处理的副作用**：`content` 是**索引字段**，它会原样出现在检索结果里——`direct` 模式下结果 `content` 直接就是它；`hybrid` 模式下它出现在 `child_content`（`content` 被父块内容替换，父块来自 PG，不带前缀）。加了前缀之后，下游会在结果文本里看到 `[中华人民共和国民法典 第146条]` 这串东西。

处置：在 `_build_result_items` 组装响应时**剥离前缀**，让 `content` / `child_content` 只保留纯法条文本；前缀只服务于 Milvus 内部的词法匹配。这样既不牺牲检索效果，也不污染对外返回内容。

### D13 · 法名解析：以日期行为边界，不能「取首行」

**数据**：345 个文件中 **34 个（9.9%）标题跨行**——首行是「全国人民代表大会常务委员会关于」，法名在第 2 行甚至第 3 行。这类全部是《解释》《决定》体裁。而 **345/345 都能解析出「YYYY年M月D日」的日期行**（此前已测）。

**规则**：`law_name` = 从首行起、到**日期行之前**的所有行拼接（去空白）。日期行是普遍存在且位置稳定的天然边界，因此这条规则对所有文件都有定义，不需要额外兜底。

**为什么不能取首行**：那 34 个文件会得到「全国人民代表大会常务委员会关于」这种无意义的法名，而且它看起来像个正常字符串，不会报错——又是一类静默失败。

### D14 · `section_path` 清洗：只保留「第X编 / 第X章 / 第X节」

**风险**：`MetadataExtractor._HEADING_PATTERNS` 把行首「（一）…」也当作 level-3 标题，唯一的护栏是「标题长度 ≤ 40 字」（`_MAX_HEADING_LEN`）。而语料里行首「（一）」共 **11,063 行，其中 8,950 行（81%）≤40 字**——也就是说**大部分会通过护栏进入 `section_path`**。

`section_path` 有两个下游：`context_embedder.py` 会把它拼进 embedding 输入；我们还要把它作为 `chapter` / `section` 对外返回。后果是条文里的「项」内容会混进章节字段。

**处置**：**不改 `metadata.py`**（那是 Artoo 的公共行为，改动面大且会影响 Artoo 那边）。在 `LegalMetadataExtractor` 里对 `section_path` 做一次清洗——**只保留形如「第X编 / 第X分编 / 第X章 / 第X节」的元素**，其余丢弃。符合 D8「专属逻辑放新文件」的约束。

> **「分编」不能漏**：《民法典》的层级是「编 → 分编 → 章 → 节」。清洗规则若只写「第X编」会误删「第一分编　通则」这一级——§7.3 的响应示例本身就是这个形态。

### D15 · OCR 与 ASR 关闭（配置层，不删模块）

**数据**：345 个 `.docx` 中只有 **5 个含嵌入图片**——国徽法（4 张）、国歌法（3 张）、国旗法（2 张）、香港基本法（3 张）、澳门基本法（3 张）。这些图片是国徽 / 国歌 / 国旗等**国家象征**，与法条正文无关。

**结论**：法条库部署设 `OCR_ENABLED=false`；ASR 同理关闭（语料是 `.doc` / `.docx`，无音频）。

**不只是"没必要"，而是"应该关"**：现有 pipeline 的设计是「自动提取嵌入图片并并发 OCR，**按页位置插入识别文本**」。对《国徽法》跑 OCR，会把国徽图片的识别结果插进法条正文中间，检索时这些噪声会与条文一起被召回——等于给法条正文掺入无法识别的字符。

**处置范围**：这两项都是**配置层**改动（`config.py::ocr_enabled` 已由 `/api/system` 暴露），**不删 OCR / ASR 模块**——删除属于 D7 之外的范围蔓延，无收益且增加与 `develop` 的分叉面。

**菜单与页面**：「OCR 服务」「ASR 服务」两项从左侧菜单移除，对应前端页面
（`pages/OcrServices.tsx` / `pages/AsrServices.tsx`）与路由一并删除——删页面能实打实
减少打包产物体积，而**后端模块与配置 API 全部保留**，将来要重新开放只需补回页面。

### D16 · LLM 配置页：隐藏，不删除

**确认前提（四条独立证据）**：`/api/retrieval/search` 与 LLM 模型配置完全无关——

1. 检索链路只用 `manager.embedder`（稠密 + 稀疏）与 `manager.reranker`（精排）
2. `models/manager.py` **不构造 LLM 实例**（只有 `self.embedder`、`self.reranker`）
3. `_get_llm_for_request` 的调用方全在 `chat.py` / `skills.py` / `agent_config.py` / `graph_store.py`，检索路径一个都没有
4. 配置存储也是分开的：检索参数在 `retrieval_configs`，LLM 在 `llm_configs`，检索不读后者

**处置：隐藏菜单项，保留页面 / API / 数据表。**

理由：模型管理页只有一个页面 + 一个 API 模块 + 一张表，删除的收益很小；而它与 `api/system.py`（下发 `llm_provider`）和 `models/` 下的 LLM 实现仍有残留引用，要删干净就得连带改动这些，范围会扩出去。隐藏只动菜单，零风险且可逆。

**与 D7「删除非召回链路」的关系**：两者标准不同，不是矛盾——

- **删除**：整条功能栈（Chat / Agent / MCP / Skills / 会话附件）。它们前端页面多、后端路由多、还有后台常驻任务，与法条库定位完全无关，删掉能实打实收窄契约面与攻击面。
- **隐藏**：单一配置页（模型管理）。体量小、删除收益低、且存在残留耦合。

**注意：这只针对 LLM，不是说检索不用模型。** 检索链路依赖**两个外部模型服务**，二者都是硬依赖：

| 模型 | 用在哪 | 调用点 |
|---|---|---|
| Embedding | query 稠密向量 | `VectorRetriever` → `embedder.embed([query])` |
| Embedding | query 稀疏向量 | `SparseRetriever` → `embedder.embed_sparse([query])` |
| Rerank | 精排 | `hybrid.py::_rerank` → `self.reranker.rerank(...)` |
| — | 全文检索 | BM25，Milvus 原生，无外部模型 |

入库侧同理核实过：`pipeline.py` 构造的是 `Enricher(llm=None, enabled=False)`，**硬编码无 LLM 且关闭**。

**两个页面的管辖边界（已核实）**：「模型管理」页（`Models.tsx`）只调 `llmConfigApi`，是纯 LLM 配置页；「Embedding」页（`EmbedConfig.tsx`）同时过滤 `config_type === 'embedding'` 与 `'rerank'`，**一页管两个，必须保留**。

**部署硬约束**：Embedding 与 Rerank 都是外部 HTTP 服务（`RemoteEmbedder` / `RemoteReranker`），法条库部署必须能访问它们。容错上不是完全瘫痪——Embedding 不可用时只剩 BM25 顶着（响应标 `degraded`），Rerank 不可用时回退 RRF 排序——但两者都缺会显著拉低召回质量。

---

## 6. 数据模型与配置

### 6.1 法条元数据字段（每个 child chunk）

| 字段 | 类型 | 说明 |
|------|------|------|
| `law_name` | str \| None | 法律全名（首行至**日期行之前**拼接，见 D13；不能简单取首行） |
| `article_number` | int \| None | 条号；无条文结构文档为 `None` |
| `article_label` | str \| None | 原文条号（如「第一百四十六条」） |
| `referenced_articles` | list[str] | 该 chunk 正文里出现的「第X条」引用；无条号的修正案 / 决定类文档用它构造 BM25 前缀（见 D11） |
| `chapter` | str \| None | 章节，取自 `section_path` 清洗后用 `" / "` 拼接（见 D14） |
| `issuing_authority` | str \| None | 发布机关（从正文括注解析） |
| `publish_date` | str \| None | 通过 / 发布日期（从正文括注解析） |
| `has_toc` | bool | **本次入库是否剥离了目录区**（标记驱动，见 D10；供排查） |
| `extraction_method` | str | rule / manual |
| `confidence` | float | 抽取置信度 |

时效性相关字段（`legal_status` / `effective_date` / `expiry_date` / `superseded_by`）本期暂缓。

**已砍掉的两个字段**：`paragraph_index`（款号）与 `item_index`（项号）。款在中文立法体例里没有标号，款号只能靠"该父块内第几段"推导；项号需要另一套「（一）」解析逻辑。两者各需一份推导逻辑与测试，而检索结果经父块扩展后返回的是**整条法条**，款 / 项号当前没有下游用途支撑。等出现真实需求（例如下游要做「第X条第Y款」的精确跳转）再加。

### 6.2 存储落点

- **PostgreSQL**：全量字段写入 `Chunk.chunk_metadata` JSON，**无 schema 迁移**
- **Milvus**：**不新增任何字段**（见 D1）。仅写入路径的 `content` 前缀有变化

### 6.3 存储拓扑：所有法条库共用一张 collection

**明确不做**：不为每个用户建 collection，也不为每个法条库建 collection。1 个全局库 + N 个个人库，只是同一张表里的 N+1 个 `kb_id` 值。

- 物理表名 = `<collection base>_<dim>`，例如 `artoo_chunks_1024`
- `_base_of(kb_id)` 对任何 kb_id（会话哨兵除外）都返回同一个 base
- `kb_id` 是 **Partition Key**，hash 分到固定的 64 个分桶；配置注释原文即「**不是知识库数量上限**」
- 检索与删除都带 `kb_id` 条件，按分区裁剪
- 删除个人库用 `delete_by_kb(kb_id)`，不需要 drop collection

**唯一会分表的情形是向量维度变化**（Milvus 的 `dim` 建表固定），这是 schema 硬约束，不是按库拆分。

> 注：D7 删除会话附件后，会话 collection（`artoo_session_chunks`）不再写入，可一并停建。

### 6.4 存量数据迁移 → 不适用

全新部署、不带存量数据：不需要加列、不需要重灌、没有迁移窗口。

### 6.5 配置默认值变更（相对 Artoo）

设计变了，默认值就要跟着变。下表按入库 / 检索 / 产品面分类，**只有标注「改」的才需要动**。

**入库侧**

| 参数 | Artoo 默认 | 法条库 | 说明 |
|---|---|---|---|
| `chunker_type`（KB config） | 未设（自动路由到 `naive`） | **改：`laws`** | 核心改造 |
| `parent_chunk_size` | 2500 | 不改 | 平均每条 123 字，远低于阈值 |
| `child_chunk_size` | 450 | 不改 | 同上 |
| `chunk_overlap` | 70 | 不改 | 仅当子块超 450 字被再切时生效，本语料几乎不触发 |
| `upload_max_file_mb` | 10 | **不改（本期维持 10）** | 语料实测最大 `.docx` 为 701KB；虽提及可能有几 MB 的文件，但本期先保持默认，超出时再调 |
| `OCR_ENABLED` | true | **改：false** | 见 D15 |
| `ASR_ENABLED` | true | **改：false** | 见 D15 |
| `GRAPH_ENABLE` | false | 不改 | 不做图谱 |
| `kb_chunk_cap` | 1,000,000 | 不改 | 语料约 2.8 万 chunk，占 2.8% |

**检索侧**

| 参数 | Artoo 默认 | 法条库 | 说明 |
|---|---|---|---|
| 请求 `top_k` | 10 | **改：5** | 已定 |
| `recall_k` / `rerank_candidate_k` | 128 / 50 | 不改 | 通用值 |
| `rrf_k` | 60 | 不改 | 通用最优 |
| `composite_rerank_weight` / `_base_weight` / `_source_weight` | 0.6 / 0.3 / 0.1 | 不改 | |
| `rerank_threshold` / `threshold_degradation_enabled` | 0.2 / true | **实际不生效** | 检索接口传 `apply_rerank_filter=False`，软阈值本就跳过；无需改值 |
| `rerank_top_k` | 10 | 不改 | |
| `mmr_lambda` / `mmr_threshold` | 0.7 / 0.7 | 不改 | |
| `hnsw_ef` / `hnsw_ef_construction` / `hnsw_m` | 128 / 200 / 16 | 不改 | |

**产品面**

| 参数 | Artoo 默认 | 法条库 | 说明 |
|---|---|---|---|
| `registration_mode` | `invite_only` | 不改 | 正好符合「无自助注册、无邀请」——账号由管理员创建 |
| `content_view_boundary_open` | false | 不改 | 与「超管不参与内容」的设计一致 |
| `llm_*` | 有默认值 | **无用途** | 删掉 Chat / Agent / 图谱 / 入库 LLM 后，**请求路径上没有任何 LLM 调用** |
| `session_upload_*` | 有 | 随 D7 移除 | |

**一个由此推出的菜单结论**：既然请求路径上没有任何 LLM 调用，**「模型管理」（LLM）页在本部署中是死配置，采取「隐藏」而非删除**（见 D16）。「Embedding」页同时管理 Embedding 与 Rerank（`embed_config.py` 的 `config_type: embedding | rerank`），必须保留。于是平台能力菜单只剩 **Embedding + API Key**。

---

## 7. 接口契约

### 7.1 不新增端点，改造现有端点

法条检索就是 **`POST /api/retrieval/search` 本身**，改造点是「默认带上全局法条库」。

| 层 | 变化 |
|----|------|
| 路径 | 不变 |
| 请求模型 | 字段本身不变；`kb_ids` 传个人库，`session_id` 已移除（显式传入返回 `400`，见 D7） |
| 认证 | 不变（沿用现有 Key 通道） |
| 错误模型 | 不变 |
| 响应信封 | 不变 |
| 单条结果 | 不变（法条字段放进已有的 `metadata`） |

### 7.2 行为变更（需明确接受）

**默认带上全局库。** 调用方不传任何库时，检索仍会返回全局法条库的结果。现状是「三者至少其一，否则 400」，改造后该 400 不再触发。

**`top_k` 默认值改为 5。** 字段名保持 `top_k` 不变（因为就是同一个接口，改名会让"同一接口"的定位变模糊）；本部署的默认值从 10 调为 5。下游若复用代码且依赖默认值，需注意这一点。

### 7.2.1 多源时的既有行为（沿用，不改）

检索目标为两个及以上源时，`_run_multi_source_retrieval` **不读 `mode` 参数**，一律执行 hybrid；响应里 `mode` 返回 `"hybrid"`、`trace` 为 `null`。

**这是 Artoo 的既有行为，不是本次改动引入的**——`RetrievalTestRequest.mode` 的字段说明原文即「多源（多库或含会话附件）统一按 hybrid 混合召回口径执行，direct 仅在单库单源时生效」，Open API 6.1 也写明了。本次改动只是让它在法条库里**更容易被触发**（只要调用方传了个人库就必然是多源）。

**多源本来就是既有的一等能力**，不是边缘情况：多库联合检索（`kb_ids`）是 Open API 6.1 的正式用法，多源响应结构（`trace: null` / `degraded` / `failed_source_count`）也是为它专门定义的。因此「自动带全局库」是搭在一条已被充分使用的代码路径上——不新增分支、不新增算法。

对法条库无实际影响：`direct` 丢掉 BM25 与父块扩展，对法条检索是全面劣化，本就不应使用。调用方若要确认实际执行模式，读响应里的 `mode` 字段即可。

**源的失败语义（沿用）**：某个**可读**源异常或超时 → 该源返回空、其 `kb_id` 计入 `failed_kb_ids`；响应 `degraded=true`、`failed_source_count=N`，其余源照常返回。

**注意区分两种情况（已实测）**：

- `kb_ids` 里的库**不存在或不可读** → 在触达 Milvus 之前就被读授权拦下，响应 **404 资源不存在**（存在性不泄露），不是 `degraded`；
- 库存在且可读、但**检索本身失败**（源异常 / 超时）→ 才是上面的 `degraded=true` + `failed_source_count=N`。

### 7.3 请求 / 响应示例

```json
POST /api/retrieval/search
Authorization: Bearer sk-xxx

{
  "query": "民法典第146条",
  "kb_ids": ["kb_personal_xxx"],
  "top_k": 5
}
```

```json
{
  "query": "民法典第146条",
  "mode": "hybrid",
  "total": 1,
  "elapsed_ms": 96,
  "results": [
    {
      "chunk_id": "ck-1",
      "doc_id": "doc-71bc...",
      "filename": "中华人民共和国民法典_20200528.docx",
      "source_type": "knowledge_base",
      "content": "第一百四十六条　具备下列条件的民事法律行为有效：…",
      "child_content": "第一百四十六条　具备下列条件的民事法律行为有效：…",
      "score": 0.95,
      "rrf_score": 0.031,
      "rerank_score": 0.95,
      "routes": ["dense", "bm25"],
      "metadata": {
        "law_name": "中华人民共和国民法典",
        "article_number": 146,
        "article_label": "第一百四十六条",
        "chapter": "第三编　合同 / 第一分编　通则",
        "source": "global"
      }
    }
  ],
  "trace": null,
  "degraded": false,
  "failed_source_count": 0
}
```

### 7.4 内容维护

全部复用现有接口，不新增：`POST /api/knowledge-bases/{kb_id}/documents/upload`、`GET /api/knowledge-bases/{kb_id}/documents`、`DELETE /api/documents/{doc_id}`、`POST /api/documents/{doc_id}/retry`、`DELETE /api/knowledge-bases/{kb_id}`。

**注意**：删库目前是 owner-only 闸门。若下游要按 `kb_id` 删除自己创建的个人库，需确认该闸门对相应身份放行。

**重复上传的既有行为（已验证）**：上传接口按 `file_hash`（SHA256，作用域为同一 `kb_id`）去重。同内容重复上传**不报错**——返回 HTTP 201 且 `status="duplicate"`，附带「该文件已存在…内容相同」的说明，不会新建文档。内容变化则哈希不同 → 新建文档，而旧文档保留。

这与「人工删除 + 上传新版」的更新流程一致：**换版必须先删旧文档**，否则新旧两版会同时留在库里。

---

## 8. 复用清单与新增清单

### 8.1 直接复用，零改动

| # | 需求 | 已有可复用物 |
|---|------|-------------|
| 1 | 全局库 + 个人库合并排序 | `MultiKBRetriever`（各源同权、统一 rerank、每源独立 `expr`） |
| 2 | 结果承载法条字段 | `RetrievalResultItem.metadata: dict` 已在下发 |
| 3 | 章节路径 | `ChunkMetadata.section_path`（已含第X编 / 第X章 / 第X节） |
| 4 | 「第X条」父块切分 | `LawsChunker` |
| 5 | 超长兜底切分 | `chunker.py::enforce_size_limits` |
| 6 | 分块参数调优 | `parent_chunk_size` 等已是 KB 级可配参数 |
| 7 | 法条元数据落库 | `Chunk.chunk_metadata` JSON + `asdict(meta)` |
| 8 | 结果水合 | `_build_result_items` 的批量查询模式（扩展它，不新建） |
| 9 | 内容维护 | `api/document.py` + `api/knowledge_base.py` 全套 |
| 10 | 前端能力开关 | `/api/system/frontend-config` + `useGraphGating.ts` 的现成门控模式 |
| 11 | 引导框架 | `auth/bootstrap.py::run_bootstrap`（幂等） |
| 12 | 评测工具 | `app/scripts/evaluate_retrieval.py`（不用新建）。上游忽略该目录，**本 fork 已纳入版本管理**（见 §3 注） |
| 13 | BM25 内容增强 | `content` 的 `[前缀]` 机制（扩展它做条号归一） |

### 8.2 需改现有代码

- `pipeline/pipeline.py`：接入新的法条抽取器；扩展写入 Milvus 的 `content` 前缀
- `pipeline/chunkers/laws.py`：新增「整篇无 `第X条`」时的 fallback 切分分支（按「一、」切父块），见 D11
- `api/retrieval.py`：检索目标默认并入全局库；水合扩展 `law_name` / `article_number` / `source`；移除会话附件分支（随 D7）
- `api/capability_reload.py` / `api/system.py` / `main.py` / `worker_main.py`：随 D7 移除对 `app.agent` 与 `app.session_upload` 的引用（见 D7 的六处耦合表）
- `api/chat.py`：把 `_get_llm_for_request` 抽到中立模块（如 `app/models/llm_resolver.py`），并改图谱侧两处 import（见 D7）
- `auth/bootstrap.py`：增加全局法条库引导步骤
- `api/document.py` / `pipeline/pipeline.py`：随 D7 把 `limits.py` 的引用改到新位置
- 前端：菜单、路由、术语与品牌文案

### 8.3 必须新写（逐项确认过无现成物）

| # | 新写内容 | 为什么没有现成物 |
|---|----------|------------------|
| 1 | 中文数字 → 阿拉伯数字转换 | 全仓库 4 处中文数字正则**只匹配、不转换** |
| 2 | 法条正文头部解析（法名 / 机关 / 日期） | `MetadataExtractor` 只提取章节标题与元素类型 |
| 3 | 目录剥离（标记驱动） | 无现成实现 |
| 4 | 法条元数据抽取器主体（`LegalMetadataExtractor`）+ 条号提取 | `ChunkMetadata` 无法条字段；`LawsChunker` 只切割不产编号 |
| 5 | 抽取置信度评估与分流 | 无同类机制 |
| 6 | 全局法条库引导创建步骤 | `run_bootstrap` 提供的是框架，这一步本身是新的 |

**已确认不需要新写的**：法条引用解析（因取消精确过滤车道）、Milvus 转义 helper（因不再拼法名进 expr）、个人库 get-or-create（由下游经现有 KB API 管理）。

---

## 9. 分阶段实施

> **提交与推送的容错约定**：每个阶段结束做一次 `git commit`（本地操作，必须成功）并尝试 `git push origin develop`。**push 失败不得中断改造**——记下「本次未推送」后直接进入下一阶段，全部完成后再统一重试一次。网络原因导致的推送失败不能引起任务中断，这一点是明确要求。

### Phase 0 · 契约冻结与基线

| 任务 | 文件 | 产出 |
|------|------|------|
| 冻结接口契约 | `artoo-open-api.md` | 记录「默认带全局库」与 `top_k` 默认值变更 |
| 冻结法条字段字典 | 本文 §6.1 | 字段名 / 类型 / 可空性 |
| ~~建评测集~~ | — | 已确认延后（见 §1 暂缓）；验证改以单测 / 集成 / 端到端 + 人工抽查为准 |
| 补检索回归基线 | `backend/tests/` | 改动前召回结果快照 |
| 创建 Agent Note | `.agents/notes/proposed/feature/` | 英文 + `.zh.md` |

### Phase 1 · 入库结构化

| 任务 | 文件 | 说明 |
|------|------|------|
| 中文数字转换 | 新建共享工具 | 供抽取器复用 |
| `LawsChunker` fallback | `pipeline/chunkers/laws.py` | 无条文结构时用「一、」切父块，见 D11 |
| 目录剥离 | `pipeline/legal_metadata.py` | 标记驱动，见 D10 |
| `LegalMetadataExtractor` | `pipeline/legal_metadata.py`（新增） | 法名 / 条号 / 款项；`chapter` 取 `section_path` |
| 置信度标记 | 同上 | 失败不阻塞入库，标记后可见 |
| pipeline 集成 | `pipeline/pipeline.py` | 在 `MetadataExtractor` 之后接入 |
| `content` 前缀扩展 | `pipeline/pipeline.py` ≈L683 | 加 `[法名 第N条]`，见 D12 |

交付判据：样本语料端到端入库；**抽取结果全量核对**——输出「每份文件的法名 + 条号数」清单，逐份人工核对，目标是不出错（不设百分比目标，因为在法条场景下一个错条号的代价很高，百分比会暗示"允许错几个"）；37+ 个无条文文档不被误删；目录区不进检索。

### Phase 2 · 检索接口改造

| 任务 | 文件 | 说明 |
|------|------|------|
| 全局库默认并入 | `api/retrieval.py` | 检索目标自动带全局库 |
| 结果水合扩展 | `api/retrieval.py::_build_result_items` | 批量补 `law_name` / `article_number` / `source` |
| `top_k` 默认值 | `api/retrieval.py` | 本部署改为 5 |

交付判据：不传 kb_ids 也能返回全局库结果；「民法典第146条」可命中；结果带法名与条号。

### Phase 3 · 默认租户、全局库与维护入口

| 任务 | 文件 | 说明 |
|------|------|------|
| 默认租户引导 | `auth/bootstrap.py` | 幂等创建唯一默认租户，并创建该租户的管理员（凭据走 env，与 Super_Admin 同模式） |
| 全局库引导 | `auth/bootstrap.py` | 幂等创建全局法条库：owner = 默认租户管理员，`chunker_type=laws`，`organization` + `read` |
| 左侧菜单 | `Layout.tsx` | 「法条库」菜单**只给默认租户管理员**；**不要**加入 `SUPER_ADMIN_MENUS` |
| 库列表入口 | `Layout.tsx` / `App.tsx` | 「法条库」菜单落在**库列表**（`/knowledge-bases`），由用户自己点进某个库；后台**不**做「直达某个库」的入口 |
| 文档列表显示法名 | `frontend/src/pages/Documents.tsx` | 展示 `law_name`，人工替换法律时同名重复可见 |

**两处易踩的点**：

1. `Documents.tsx` 的库 id 一律来自 `useParams().id`（`/knowledge-bases/:id`）。**不要**为「直达某个库」再加一条旁路参数：库列表是唯一的选库入口（见 §15.9）。
2. `SUPER_ADMIN_ALLOWED_PATHS` 是**精确匹配**的 `Set`，且**只约束 Super_Admin**。租户管理员不受它限制，因此本项**不需要改白名单**——早先稿子要求把它加进去，是基于"超管维护"的错误前提，已更正。

> **已调整（2026-09-11 晚）**：Phase 3 最初实现过一条 `/legal` 直达路由（自动解析全局库 id 后
> 渲染维护页），并把「法条库」菜单指向它。落地后确认这不合适：**后台可能同时存在全局库和本人名下
> 的库，替用户选一个会把「我在看哪个库」藏起来**。因此 `/legal` 路由与 `pages/LegalLibrary.tsx`
> 删除，菜单回到库列表；只有 lite 端（单一库、菜单即内容）才点菜单直接进库。详见
> [drop-legal-direct-entry](../.agents/notes/implemented/simplification/2026-09-11-drop-legal-direct-entry.md)。

### Phase 4 · UI 标识改造

见 D9。先建集中文案模块，再逐处引用；`Landing.tsx` 的产品描述整段重写。

**左侧菜单最终清单**（已确认）：

| 菜单项 | 处置 | 可见性 |
|---|---|---|
| 法条库 | **新增**（替代「知识库」） | 租户管理员 |
| 检索测试 | 保留 | 超管 / 租户管理员 |
| Embedding（含 Rerank） | 保留 | 超管 |
| API Key | 保留 | 超管 |
| 租户管理 | 保留 | 超管 |
| 用户管理 | 保留 | 租户管理员 |
| 审计日志 | 保留 | 租户管理员 / 超管 |
| 智能体 / 技能 / MCP 服务 | 删除 | 随 D7 |
| OCR 服务 / ASR 服务 | 删除 | 配置层关闭（D15） |
| 模型管理（LLM） | **隐藏**（保留页面 / API / 表） | 详见 D16 |
| 邀请链接 | 删除 | 单租户，账号由管理员创建 |

可访问路径白名单同步收缩：超管的 `SUPER_ADMIN_ALLOWED_PATHS` 保留 `/tenants`、`/embed-config`、`/api-keys`、`/audit-logs`（移掉 `/models`、`/ocr-services`、`/asr-services`、`/mcp-servers`、`/agent-config`）。

> 注意 `/models` 只是从菜单与白名单移出（D16 的「隐藏」），**页面与后端 API 不删**；而 `/ocr-services`、`/asr-services`、`/mcp-servers`、`/agent-config` 是随 D7 / D15 一起删除页面。
>
> **已执行**：`pages/OcrServices.tsx`、`pages/AsrServices.tsx`、`pages/Invitations.tsx`
> 及三者的路由已删除；`pages/Models.tsx` 与路由保留（隐藏）。`InviteAccept`
> （`/invite/:token`）是邀请**领取页**而非菜单项，且后端 `invitation_routes.py` 保留，
> 故一并保留。

### Phase 5 · 删除非召回链路

见 D7。顺序：先把 `limits.py` 挪到中立位置、把 `_get_llm_for_request` 抽到中立模块 → 再清掉六处耦合引用 → 最后删模块与前端页面。会话 collection（`artoo_session_chunks`）随之停建。

---

## 10. 验证策略

| 层 | 内容 |
|----|------|
| 单测 | 中文数字转换（覆盖到万位）；目录剥离（含「有目录 / 无目录 / 无条文」三类）；头部解析；条号抽取 |
| 集成 | 样本语料走完整 pipeline，校验 PG 元数据与 Milvus `content` 前缀 |
| 端到端 | 语义查询命中正确法条；「民法典第146条」词法命中；`top_k` 边界（请求 10 条但只有 3 条时返回 3 条） |
| ~~评测~~ | 评测集本期不做（见 §1 暂缓）。`evaluate_retrieval.py` 保留为后续补建时的现成工具 |
| 回归 | 改动前后对既有查询的召回结果对比 |
| 前端 | 菜单可直达全局库维护页；品牌文案无残留 |

---

## 11. 风险与回滚

| 风险 | 影响 | 对策 |
|------|------|------|
| 目录剥离误伤正文 | 内容丢失且静默 | 标记驱动；无条文文档与「有目录标记」交集为零；单测覆盖三类样本 |
| 中文数字转换错误 | 条号错位 | 单测覆盖；保留 `article_label` 原文可核对 |
| 全局库不在调用方租户内 | 检索不到 | 引导时建在同一租户；端到端用例验证 |
| 默认带全局库改变接口行为 | 下游依赖旧 400 语义 | Phase 0 记录契约变更并与下游确认 |
| `top_k` 默认值变更 | 下游复用代码时结果条数变化 | 文档标注；建议下游显式传值 |
| 误删 `session_upload/limits.py` | 普通上传容量校验断裂 | 先挪位置再删目录（D7） |
| 人工更新漏删旧版 | 同一条文的旧版与新版同时可召回，且无时效字段可区分 | 文档列表展示 `law_name`，同名重复可见；替换后由维护者自查 |
| 维护者身份选错 | 全局库打不开或改不了 | 维护者必须是**默认租户的管理员**（作为全局库 owner）；Super_Admin 在设计上无权访问任何 KB |
| 本地未装 LibreOffice | `.doc` 上传报错 | 容器已内置；文档注明本地依赖 |
| fork 与上游长期分叉 | 合并成本累积 | 专属逻辑放新文件；配好 `upstream` remote 并定期同步 |

回滚以能力开关与 fork 内提交回退为核心，数据可重建（全新部署、无存量依赖）。

---

## 12. 里程碑

| 阶段 | 产出 | 依赖 |
|------|------|------|
| M0 | 契约冻结 + 回归基线（评测集已延后） | — |
| M1 | 入库结构化可用（含目录剥离与条号归一） | M0 |
| M2 | 检索接口改造完成 | M1 |
| M3 | 全局库引导与入口 | M2 |
| M4 | UI 标识改造 | M2 |
| M5 | 非召回链路删除 | M2 |

M0–M2 硬串行，M3–M5 可并行。

---

## 13. 决策清单

| # | 决策 |
|---|------|
| 1 | **Fork 仓库改造，不切新分支**；fork 即长期产品线的上游 |
| 2 | Artoo 与法条库两个应用完全独立部署、互不干涉 |
| 3 | 全新部署，不带存量数据，无迁移 |
| 4 | 检索以语义为主；不做法条精确过滤车道 |
| 5 | **检索接口就是现有 `/api/retrieval/search`**，改造为默认带上全局法条库 |
| 6 | 全局库**全租户**一个，自动带上；个人库由下游传 `kb_ids` |
| 7 | 不做逐用户鉴权，不做跨租户例外；Key 只承担接口调用权限 |
| 8 | `top_k` 字段名不变，本部署默认值 5 |
| 9 | 不做批量端点 |
| 10 | 目录剥离采用标记驱动规则 |
| 11 | 条号做中文 / 阿拉伯数字归一（BM25 前缀增强） |
| 12 | 无条文结构文档（37 个 `.docx` + 2 个 `.doc`）：`article_number = None`，内容照常入库 |
| 13 | 砍掉引用图谱与入库 LLM 抽取 |
| 14 | 非召回链路真正删除（不是隐藏） |
| 15 | UI 标识改为「法条库」；只改展示层；移除上游 GitHub 链接；品牌用编译期常量 |
| 16 | 时效性暂缓，设计保留 |
| 17 | 评测工具复用现有 harness（评测集本身延后，见 #19） |
| 18 | 法律修订由人工删除 + 上传更新，不做自动替换 |
| 19 | 评测集本期不做（已确认延后） |
| 20 | 法条库为**单租户**部署；默认租户管理员维护全局库；Super_Admin 只做平台装配、不参与内容 |
| 21 | 调用方可用 `user_level` Key（一把 Key = 一个身份），**也可用 `external_agent` 代理 Key + `X-External-User-Id`**（每终端用户一个外部身份，见 §15.7）；后者需把 `EXTERNAL_USER_TENANT_ID` 指向默认租户（本 fork 默认值） |
| 22 | 抽取结果**全量核对**（输出法名 + 条号数清单逐份核对），不设百分比验收目标 |
| 23 | 砍掉 `paragraph_index` / `item_index`（款号 / 项号）两字段 |
| 24 | 关闭 OCR 与 ASR（配置层，不删模块）；原因不只是"用不上"，而是 OCR 会把图片识别结果插进法条正文造成污染 |
| 25 | LLM 模型配置页**隐藏**（保留页面 / API / 表），不从菜单进入；检索与之完全无关（D16） |
| 26 | `upload_max_file_mb` 本期维持默认 10MB，不调整 |

---

## 14. 仓库规范要求

按 `AGENTS.md`，本次改造属于非平凡变更，**必须在同一 PR 内提交 Agent Note**：

- 路径：`.agents/notes/{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`
- 建议拆分：法条入库结构化（`feature`）、检索接口改造与语义定位（`architecture`）、非召回链路删除（`simplification`）
- 每份英文 Note 配一份同名 `.zh.md`；日期取首次提出日

验证命令：后端 `backend/` 下 pytest、前端 `frontend/` 下 build / 测试、协议变更同步 `artoo-open-api.md`、部署变更跑 compose 校验。**只报告实际执行过的命令与结果。**

> **本期不实际运行验证**（已确认）：改造以「代码改动 + 提交」为准，不启动服务、不跑全量测试与构建。上面列的是将来做实际验证时的命令，本期不作为交付判据。

---

## 15. 交付后核查（2026-09-11）

一次「拿着决策清单逐条对照代码」的回查。范围：§0.1 的差异表、D1～D26、Phase 0～5 的每条动作、前端死代码与残留文案、后端测试套件。结论分三段：**符合**（未改动）、**补齐**（发现缺口并修）、**遗留**（明确接受或属存量问题）。

> 本节推翻了 §14 末尾「本期不实际运行验证」的约定：核查以「实际跑通」为判据，因此本轮真的跑了后端 pytest 与前端 build / test，结果见 §15.3。**只报告实际执行过的命令与结果。**

### 15.1 符合项（逐条核对通过，未改动）

| 决策 | 核对点与结论 |
|---|---|
| D1 | Milvus schema 未动：`_build_fields` 里没有 `law_name` / `article_number` 等标量字段；法条字段只出现在 PG `chunk_metadata` |
| D4 | 没有引入任何跨租户例外或零鉴权例外；`cross_tenant_kb_ids` 仍是上游原样的点对点分享语义；全局库用 `organization` + `read` 授权 |
| D5 | 代码里不存在 `legal_filters` 或任何精确过滤车道 |
| D7 边界 | 图谱代码保留、`GRAPH_ENABLE` 仍为 `false` 且注释写明本部署不得开启；OCR / ASR 保留模块、仅配置层关闭 |
| D15 | `config.py::ocr_enabled` / `asr_enabled` 默认值已是 `false` |
| D16 | `Models.tsx` 与 `llm-configs` 后端 API 保留，仅从菜单与超管路径白名单移出 |
| D20 | 单租户引导（默认租户 + 租户管理员 + 全局法条库）与「默认库禁删 / `chunker_type` 禁改」两处保护闸门都在 |
| D21 | 未新增端点：本轮改动前后路由数都是 **117** |
| D3 | 个人库创建时默认补 `chunker_type=laws`，不会退化成 `naive` 路由 |
| D6 | 全局库与个人库并列走 `MultiKBRetriever`，结果 `metadata.source` 标 `global` / `personal` |

### 15.2 本轮补齐的缺口

| # | 缺口 | 处置 |
|---|---|---|
| 1 | **后端测试套件根本跑不起来**：非召回链路删掉后，15 个测试模块在 `pytest` 收集阶段就报 `ModuleNotFoundError`（`app.agent` / `app.session_upload` / `app.api.chat` …） | 12 个属已删链路的测试模块删除；`test_json_field_extractor.py` 的目标模块在基线 `3c184f5` 就不存在，一并删除；另 6 个测试文件把 import 指向新位置（`app.pipeline.limits`、`app.retrieval.memory`、`is_deepseek_thinking_model`） |
| 2 | `test_upload_file_size_gate.py` 的「端点行为」5 个用例按**已被移除的**模块级 `document._UPLOAD_DIR` 打桩（落盘早已迁到 `app/storage/object_store.py`），fixture 必然 `AttributeError` | 删除该层用例与配套 fixture；同文件保留谓词属性层与单源 / 单例层，并在模块 docstring 注明原因 |
| 3 | 后端展示文案仍是 Agent 时代：FastAPI `title` / `description` 与根路径 `GET /` 都写 `Agentic RAG System` | 改为 `法条库 · 法条召回服务` / 法条召回定位；根路径返回 `Legal recall service is running`；`artoo-open-api.md` 第 5 行同步 |
| 4 | **Lite 落地页仍在演示已删除的能力**：`Landing.tsx` 渲染 `<AgentDemo />`（ReAct 对话演示），能力卡片仍在讲 ReAct Agent、知识图谱、MCP 工具、邀请注册 | 删除 `AgentDemo` 组件并重写落地页：Hero 改条文级召回，能力卡片改为「条文级语义召回 / 入库即结构化 / 目录不入库 / 全局库+个人库 / 接口即能力 / 轻量可私有化部署 / 人工可控的语料治理」 |
| 5 | 品牌残留：落地页、注册页、改密页、邀请领取页仍写 `Artoo` | 展示文案统一改为「法条库」（`auth.ts` 的 `artoo.jwt` 存储键、CSS 类名、compose 服务名等**代码标识符按 D9 不动**） |
| 6 | **前端保留指向已删除后端的客户端**：`sessionApi` / `sessionFileApi` / `mcpConfigApi` / `skillsApi` / `agentPresetApi` 及其类型定义仍在 `lib/api.ts` | 删除 347 行死代码；`ArtifactPanel` 与 `artifactStore` 里只服务于会话附件的 `session-file` 分支一并移除（现在只剩 `document` 一种来源） |
| 7 | 登录后默认落地页指向**已不存在的** `/chat`（非超管会跳到空路由） | 先改为 `/legal`；随后两轮调整：先删 `/home` 概览页、再把 `/legal` 直达入口一并去掉（§15.9）。**当前**：超管 → `/tenants`，租户管理员 → `/retrieval` |
| 8 | 菜单按 Phase 4 收缩了，但 `/ocr-services`、`/asr-services`、`/invitations` 三张页面与路由仍在 | 页面与路由删除（后端 OCR / ASR / 邀请模块与 API 全部保留） |
| 9 | `ChatMessagesSkeleton` 组件已无任何引用（随对话链路一起死掉） | 删除 |
| 10 | 残留「会话」文案：`SettingsDialog` 的平台配置说明、上传限制说明、重置确认弹窗、`Layout.tsx` 与 `lib/api.ts` 的注释仍在描述会话配额与超管配 MCP 预设 | 逐处改写为当前实际语义（单库 chunk 上限、能力配置菜单） |
| 11 | Milvus collection 的 `description` 仍写 `Artoo 向量集合` | 改为 `法条库向量集合`（仅影响新建 collection 的描述元数据） |

### 15.3 实际执行过的验证

环境：conda 环境 `aladdin`（Python 3.12.13），`backend/` 下 `JWT_SECRET=import-check-only`。

| 命令 | 结果 |
|---|---|
| `python -c "from app.main import app; ..."` | 通过，`title=法条库 · 法条召回服务`，`routes=117` |
| `pytest --collect-only -q` | **515 collected，0 errors**（修复前是 459 collected + 15 errors） |
| `pytest -q`（法条 + 上传限制 + 内存推荐 + 租户等本轮直接相关文件） | **126 passed** |
| `pytest -q --ignore=tests/test_pre_embed_gate_property.py`（全量） | **44 failed, 457 passed** —— 失败集合是基线 `3c184f5`（同一套忽略条件：**66 failed, 418 passed, 5 errors**）的**真子集**，逐条比对**无任何新增失败** |
| `npm run build`（`frontend/`） | 通过 |
| `npm test`（`frontend/`） | **34 passed / 7 files** |

基线对比方式：`git worktree add --detach <tmp> 3c184f5` + `git checkout 37cf5e0 -- backend/tests`，在同一 Python 环境下跑同一批用例，跑完已 `git worktree remove`。

### 15.4 遗留与取舍（明确不处理）

1. **基线上 44 个失败测试属于存量问题**，与法条库改造无关：集中在 `test_degraded_propagation`、`test_e2e_retrieval_pipeline`（SSE 口径）、`test_h3_degraded_propagation`、`test_milvus_b3` / `test_milvus_ef` / `test_milvus_index_params`（Milvus 内部 API 漂移）、`test_multi_kb_concurrency_property`、`test_rbac_schema_bootstrap`、`test_tenant_auth_*` 等。本轮把「已删功能」的 stale 测试清掉后，失败数由 66 降到 44；**修这些存量测试不在本次范围内**，但建议作为独立任务排期。
2. **`tests/test_pre_embed_gate_property.py` 在基线即挂起**（首个用例 `test_property_kb_gate_iff_used_plus_incoming_exceeds_cap` 不结束），已在 `3c184f5` 的独立 worktree 上复现，非本次改动引入。因此全量 pytest 需 `--ignore` 该文件；这也是它成为「全量跑不通」唯一原因。
3. **MCP 配置字段与 DB 模型保留**：`config.py` 的 `mcp_*` 设置、`schema/db.py` 的 `mcp_configs` / `agent_presets` / `custom_skills` / `chat_sessions` / `session_files` 表与 ORM 模型都还在。理由是按 D8「共享文件只做最小改动」与「除非明显提升轻量化与部署精简，否则先隐藏」——删表还要连带改 `storage/database.py` 的迁移语句与 `tenant_repo` 的隔离类清单，收益低、分叉面大。`api/document.py` 的 `/api/files/{file_id}/content` 仍会读 `SessionFile`，属保留代码路径。
4. **前端仍保留 OCR / ASR / 邀请的 API 客户端**（`ocrConfigApi` / `asrConfigApi` / `adminApi.invitations*` / `inviteApi`）：对应后端端点都还在，属「模块保留、UI 收缩」的 API 面，不删。
5. **邀请领取页 `/invite/:token` 保留**：它是深链页面而非菜单项，后端 `invitation_routes.py` 也保留；单租户部署下的正常流程是管理员在「用户管理」里建账号，用不到邀请。

### 15.5 实机验证（2026-09-11，真跑）

本机 Docker Desktop（Compose v2.40）拉起完整栈：`etcd / minio / milvus / postgres / redis` +
`backend / worker / frontend` 共 8 个容器全部 healthy。镜像由本 fork 现构建
（`artoo-backend:legal` / `artoo-frontend:legal`，避免覆盖 aladdin 的 `:latest`）。

**环境**：单租户引导自动完成——租户 `tenant-legal-default`、租户管理员 `lawadmin`、全局法条库
（`config={"chunker_type":"laws","is_default_legal_kb":true}`、`organization` + `read`）。
远程模型服务用同一个 Infinity 实例 `10.30.1.6:7997`（`/embeddings` dim=1024、`/rerank` 均可用）。

**验证结果**（逐项实测，非推断）：

| 验证项 | 结果 |
|---|---|
| `GET /` | `{"message":"Legal recall service is running"}` |
| 不传 `kb_ids` 检索 | **200**（不再 400），默认并入全局法条库 |
| `top_k` 默认值 | 返回 5 条；显式 `top_k=2` 返回 2 条 |
| 显式传 `session_id` | **400** `本部署不支持 session_id：会话附件链路已移除…` |
| 删除全局法条库 | **403** `全局法条库不允许删除…` |
| 改全局库 `chunker_type` | **403** `…不允许修改 chunker_type：法条结构化入库依赖它` |
| 入库《国籍法》 | 父块 19 / 子块 26；`law_name`、`article_number`(24 条)、`article_label`、`issuing_authority`、`publish_date` 全部抽对 |
| Milvus `content` 前缀 | `[中华人民共和国国籍法 第1条] 第一条 …`（阿拉伯数字条号，供 BM25 命中） |
| 接口返回的 `content` | 已剥离索引前缀（`strip_content_prefix` 生效） |
| 入库《民法典》 | 子块 **2059**；2039 条带条号；**目录已剥离**（`has_toc=true`）；`第一千条`、`第一百四十六条` 数字转换正确；`chapter` 只留「编 / 章 / 节」 |
| 入库《刑法修正案》（无条文结构） | 27 子块，`article_number` 全为 `null`（符合 D11）；23 块抽到 `referenced_articles=["第一百六十二条"]` |
| 词法查询「民法典第146条」 | top1 = 《民法典》第一百四十六条（0.697）——D12 归一与 BM25 前缀生效 |
| 语义查询「虚假意思表示的民事法律行为效力」 | top1 = 第一百四十六条 |
| 个人库合并 | 自建个人库并入《烟叶税法》后，带 `kb_ids` 检索同时返回 `source=personal` 与 `source=global`，按 rank 合并（D6） |
| 前端 | `:8888` 返回 SPA，标题「法条库 · 法条召回服务」，`/api` 反向代理通 |

**实测中发现的两点（非阻塞，供后续参考）**：

1. `EMBED_SPARSE_ENABLED=true` 时会对 `{base}/embed_sparse` 发请求；当前 Infinity 实例不提供该端点
   （返回 404），代码按设计**降级为占位稀疏向量**。若稀疏路要真正生效，需要 TEI 或本仓库旁
   `embedding-rerank-server` 那类实现了 `/embed_sparse` 的服务。
2. 单源检索中某一路（如 dense）抛错时，`trace` 会记录该路异常，但响应顶层的 `degraded`
   只反映**多源**失败（上游既有语义）。即模型服务不可用时仍返回 200 + 空/少量结果而不置
   `degraded`，调用方若要感知需读 `trace.routes`。

**本地测试脚手架（不入库）**：仓库根 `.env`（含模型服务密钥，已被 `.gitignore` 忽略）、
`%TEMP%\artoo-legal-local.yml`（把应用镜像指向 `:legal` tag 的 compose 覆盖）。

### 15.6 外部系统调用与个人库维护（实机验证）

按 D4/D21 的模型，外部系统（下游 law-agent-lite-backend）**只用一把 API Key** 调用，不带 JWT：

| 步骤 | 调用 | 实测结果 |
|---|---|---|
| 签发凭据 | `POST /api/api-keys/me`（默认租户账号的 JWT 换明文 Key，仅一次返回） | `key_type=user_level`，`prefix=sk-…` |
| 列库 | `GET /api/knowledge-bases` | 200，返回全局法条库 + 本 Key 身份的库 |
| 建个人库 | `POST /api/knowledge-bases` | 201，`config.chunker_type` 自动为 `laws` |
| 入库 | `POST /api/knowledge-bases/{kb_id}/documents/upload` | 202/201 → 轮询 `GET /api/documents/{id}` 至 `completed`（实测 19 子块） |
| 列文档 | `GET /api/knowledge-bases/{kb_id}/documents` | 200，`total=1` |
| 检索 | `POST /api/retrieval/search`（`kb_ids=[个人库]`） | 200，`source=personal` 命中《城市维护建设税法》第二条（0.999） |
| 删文档 | `DELETE /api/documents/{doc_id}` | **204** |
| 删个人库 | `DELETE /api/knowledge-bases/{kb_id}` | **204**（个人库可删；全局库 403） |

**身份边界（实测）**：另建普通用户 `alice` 并签发她自己的 Key 后——

- `GET /api/knowledge-bases` 只看到全局法条库（看不到 `lawadmin` 建的私有个人库）
- 带 `kb_ids=[lawadmin 的个人库]` 检索 → **404 资源不存在**（存在性不泄露）
- 不带 `kb_ids` 检索 → 200，返回全局库结果

**由此确定的接入约定**：**一把 Key = 一个身份**。下游若为多个终端用户各建个人库，应当用同一把
（或少量几把）默认租户身份下的 Key 去建库与维护，这样同一把 Key 既能维护也能检索这些库；库与终端
用户的对应关系由下游自己的关联表维护（法条库不做逐用户鉴权）。

**「先取库、没有就建」落在下游**：`GET /api/knowledge-bases` 不会自动建库；下游发现返回列表里
没有该用户的个人库时，先 `POST /api/knowledge-bases` 建库，再取 `GET /api/knowledge-bases/{kb_id}/documents`
（首次必然为空列表）。若希望改为**服务端在列库时按约定自动建**，需要先定一个「下游用户标识 → 库」的
命名/标记约定，本轮未做。

### 15.7 外部用户（代理 Key + `X-External-User-Id`）对齐（2026-09-11）

**背景**：Artoo 原设计里，第三方用「超管级代理 Key + `X-External-User-Id`」接入，平台按
`(代理Key, 外部用户ID)` 懒创建外部用户身份——每个终端用户一个身份、各自拥有私有库，
隔离由法条库本身保证。但原实现把外部用户硬锁在内置 `tenant-external-builtin`，
而单租户法条库的全局库在 `tenant-legal-default`，跨租户读一律 404。实测（改造前）：

| 用代理 Key 调用 | 改造前 |
|---|---|
| `GET /api/knowledge-bases` | 200 但 `total=0`（看不到全局库） |
| `POST /api/retrieval/search`（不传 kb_ids） | **400**「未找到全局法条库…」（租户内解析不到全局库） |
| 显式传全局库 `kb_id` | **404 资源不存在**（跨租户硬隔离） |

**处置**：新增配置 `EXTERNAL_USER_TENANT_ID`（默认 = 默认租户；上游形态设回
`tenant-external-builtin`），代理 Key 的 `tenant_id`、外部用户行的 `tenant_id`、
外部身份上下文的 `tenant_id` 三处统一取它；与配置相同时引导不再另建内置外部租户。
建库/归属/隔离链路**零改动**——`owner_user_id` 本就取 `identity.acting_subject_id`。

**改造后实测**（同一把代理 Key，两个外部用户 ID）：

| 调用 | `alice-001` | `bob-002` |
|---|---|---|
| `GET /api/knowledge-bases` | 全局法条库 | 全局法条库（看不到 alice 的个人库） |
| `POST /api/knowledge-bases` | 201，`tenant=tenant-legal-default`、`owner_user_id=<external_users.id>` | — |
| 上传 + 入库 | completed（12 子块） | — |
| 检索 `kb_ids=[alice 的个人库]` | `source=personal` 命中《烟叶税法》第三/五/九条 | **404 资源不存在** |
| 检索（不传 kb_ids） | 200，全局库 | 200，全局库《国籍法》第四条 |
| 检索「纳税」（top_k=8） | **global 4 + personal 4**（一次响应内两库合并） | — |

**结论**：两种接入方式并存——要"每终端用户一个隔离身份"用代理 Key + `X-External-User-Id`；
要"一把 Key 代所有用户、库范围全由下游算"用用户级 Key。前者是本轮新增能力，
不影响后者已有行为。

### 15.8 与下游 law-agent-lite-backend 的对接现状（读其源码所得）

下游 `law-agent-lite-backend`（Java/Spring Boot）已有专门的 `modules/artoo` 集成包，
它的调用方式与本部署**天然契合**，但有几处必须注意的差异。

**它实际怎么调**（源码位置：`src/main/java/.../modules/artoo/`）：

| 维度 | 实现 |
|---|---|
| 凭据 | `ArtooProperties.apiKey` = **超管级代理 Key**；每次请求 `ArtooClient` 自动注入 `Authorization: Bearer <key>` **+ `X-External-User-Id`**（`ArtooClient.java` 的 `headers.setBearerAuth(...)` / `headers.set("X-External-User-Id", eu)`）；另配了 AK/SK 签名通道（access/secret key） |
| 外部身份 | 普通请求 = 当前登录用户 ID；**案件库 = 派生身份 `case-{创建人ID}`**（`CaseKbOwner`），这样案件库天然不出现在用户自己的库列表里，也改不动 |
| 库范围 | 下游用**自有映射表 `sys_kb_info` + 案件三档数据范围**先过滤 `kb_ids`，无权限直接 403（`KbAccessService.filterReadable` / `requireRead`）——正是 D4 说的"库范围由下游算" |
| 召回 | `POST /api/retrieval/search` **原样透传**（`ArtooRetrievalController`），另封装了 `/api/retrieval/search/by-case`（按案件解析 kb_id） |
| 知识库/文档 | 列表/建库/改名/删库、文件夹、上传、重试、删除、批量删/重试、分块、原件 raw/preview |
| 会话与对话 | `/api/sessions*`、会话附件、文件事件 WebSocket、SSE 对话（chat 栈） |

**对本部署的意义**：它的鉴权模型**就是** 15.7 打通的那条路。改造前把它的 `artoo.base-url`
指到法条库，结果会是"列库为空 + 召回 400"；改造后可直接复用，不需要改它的客户端。

**指过来之前必须知道的 4 件事**：

1. **`session_id` 会被拒**：法条库对显式 `session_id` 返回 400（会话链路已删）。下游目前把
   `session_id` 原样透传，因此**聊天/附件链路必须继续指向上游 Artoo**，只有知识库与召回指到法条库。
2. **会话相关端点不存在**：`/api/sessions*`、`/api/sessions/{id}/files/events`（WS）、SSE 对话在
   法条库都 404（D7）。下游若只用法条库，这些功能要在路由层摘掉。
3. **`top_k` 默认值 10 → 5**：下游文档写的是 10，本部署是 5。调用方不显式传 `top_k` 时条数会变少；
   想保持 10 就在请求里显式传。
4. **代理 Key 不能随手轮换（实测）**：外部身份的命名空间 = `(代理Key.id, X-External-User-Id)`，
   换一把 Key，同一个 eu 会解析出一个**全新身份**，旧个人库立刻 `404`（数据还在，只是归属旧身份）。
   实测：旧 Key + `alice-001` 可见 2 个库，新 Key + `alice-001` 只剩全局库，读旧个人库 404；
   库里同一个 `alice-001` 对应 3 条 `external_users`（3 把 Key）。
   → 约定：法条库这一侧的代理 Key 按**长期资产**管理；确需更换时要配套迁移（把旧外部身份名下的库
   owner 改到新身份，或重建库）。

**顺带一条下游体验项**：全局法条库不在下游 `sys_kb_info` 里，其列表按自有真值表算，所以下游用户
天然看不到、也管不了全局库（这正是我们要的）。若下游要显式展示"全局法条库"这一项，需要把它作为
只读公共库登记进 `sys_kb_info`，否则保持现状（召回时法条库会自动并入，不需要它登记）。

### 15.9 后台不直达库、lite 端直达（2026-09-11 晚，UI 收敛）

**触发**：实机点菜单时发现两个产品口径混淆——

1. 法条库后台点「法条库」直接进了**全局法条库**的维护页（Phase 3 的 `/legal` 直达路由）。
2. lite 端点「个人法条库」却先落在库列表，还要再点一次才看到文件。

**结论（两端的正确形态不同，不是同一套）**：

| 端 | 「法条库」菜单点下去 | 理由 |
|---|---|---|
| 法条库后台（本仓库） | 进**库列表**，由用户自己选库 | 后台是维护面，可能同时存在全局库与本人名下的库；替用户选中一个库等于把"我在看哪个库"藏起来 |
| lite 端（`law-agent-lite-application`） | 进**该用户的个人法条库文件列表** | lite 侧一个用户只有一个个人库，库与库名由后端按需创建并锁定，"菜单 = 内容"是唯一合理形态；此外全局库在 lite 侧无 UI，只参与召回 |

**本仓库改动**：

| 项 | 处置 |
|---|---|
| `frontend/src/pages/LegalLibrary.tsx` | 删除 |
| `App.tsx` | 删除 `/legal` 路由（Phase 3 引入的直达入口） |
| `Layout.tsx` | 「法条库」菜单改回 `/knowledge-bases`（label 与图标不变） |
| `lib/api.ts` | 删除 `knowledgeBaseApi.getGlobalLegal`（只服务于直达页） |
| `pages/Documents.tsx` | 去掉 `explicitKbId` 旁路参数，库 id 回到只取 `useParams().id` |
| 后端 | **未动**：`GET /api/knowledge-bases/legal/global` 端点保留（后端测试引用它，且它是"解析全局库"的唯一稳定契约） |

**验证**：`npm run build`（tsc + vite）通过、`npm test` **34 passed / 7 files** 通过
（改动前同样是 34 passed，用于确认没有引入回归）。本地 `artoo-frontend:legal` 镜像已重建
（含本次改动）。**但容器没有用新镜像重启过**——本次收尾按要求停栈交给运维自行启动，因此
"点菜单落在库列表"这一条**是代码层面的结论，未在浏览器里点过**。启动时若 compose 没有自动
重建容器，用 `docker compose ... up -d --force-recreate frontend`。

**lite 端**：对应改动在 `law-agent-lite-application` 仓库（web 端路由 `/workspace/legal-kb`、admin 端
`/legal-kb`），不属本仓库范围，此处仅记录两端口径的差异来源。

### 15.10 预置 API Key：下游不必先登录后台领 Key（2026-09-11 晚）

**背景**：下游 lite 要用两把 Key 才能工作——业务代理 Key（个人库 + 检索）与全局库 owner
名下的用户级 Key（admin 端维护全局库）。原流程是人工登两种账号各领一次，且**每次重建数据卷
都要重来**；更糟的失败形态是"库里 Key 没了、下游静默 401"。

**处置**：新增两项可选配置，配了就在引导阶段**幂等播种**（库里仍只存 SHA256，明文只留在 env）：

| 配置项 | 播种的 Key | 下游对应配置 |
|---|---|---|
| `LEGAL_BOOTSTRAP_PROXY_API_KEY` | `key_type=external_agent`，tenant 锁 `EXTERNAL_USER_TENANT_ID` | `legal-kb.api-key`（Bearer + `X-External-User-Id`） |
| `LEGAL_BOOTSTRAP_ADMIN_API_KEY` | `key_type=user_level`，绑定默认租户管理员 | `legal-kb.admin-api-key`（admin 端维护全局库） |

两条留空即整段跳过，保持上游 Artoo 形态（Key 由后台手工签发）。

**三个设计取舍**：

1. **Key id 由 (用途, 明文) 经 uuid5 推导，不用随机 uuid4**。代理 Key 的外部身份命名空间是
   `(api_key.id, X-External-User-Id)`（§15.8 第 4 条已记录"换 Key = 换身份 = 旧个人库 404"）。
   若 id 每次部署都变，同一个 env 值重建后也会把老用户的个人库变成"不存在"。uuid5 不可逆，
   所以 id（=签名通道的 AK）可公开而不泄漏明文。
2. **按 `key_hash` 查重、且不复活已撤销的 Key**：运维显式撤销过就保持撤销（重启复活比 401
   更难排查），只在日志里 warning 说明"下游会 401"。
3. **明文短于 16 字符即 fail-fast**：这类配置写错只会表现为下游 401，宁可启动期就拦下来。

**验证状态：未实跑**（本次改动后没有重启本地栈——由运维自行启动验证；后端镜像
`artoo-backend:legal` 已重建，含本改动）。已验证的部分只有静态检查：`ast.parse` 语法通过、
`docker build` 通过。**下列运行时行为待启动后确认**：

| 场景 | 期望 |
|---|---|
| 给 env 填两把**全新**Key → 重启后端 | 日志出现两条「已预置 API Key」，`api_keys` 表各多一行 |
| 用新代理 Key + `X-External-User-Id` 调 `GET /api/knowledge-bases` | **200** |
| 用新 owner Key 调 `GET /api/knowledge-bases/legal/global` | **200**（能解析到全局库） |
| 再次重启（幂等） | 日志「已存在，跳过」，不重复建行 |
| 撤销某把预置 Key 后重启 | 保持撤销状态，只记 warning（不复活） |
| env 留空 | 一行日志都没有，`api_keys` 表无变化（上游形态） |

**最小验证命令**（法条库栈起来之后，把 `<key>` 换成 env 里填的那把）：

```bash
# 业务代理 Key：应 200 并返回库列表（含全局法条库）
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <proxy-key>" \
     -H "X-External-User-Id: probe-1" http://127.0.0.1:8000/api/knowledge-bases

# 全局库维护 Key：应 200 并返回全局法条库对象
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <admin-key>" \
     http://127.0.0.1:8000/api/knowledge-bases/legal/global
```

> 这是**长期共享密钥**：轮换要同时改法条库 env 与下游配置；改完重启法条库即播种新值，
> 旧 Key 仍在（需要时在后台撤销）。
