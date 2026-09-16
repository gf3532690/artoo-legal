# 法条库测试环境上量 runbook

> 目的：把本分支的改动上到测试环境并**首次全量入库**。按顺序执行；第 1 步的判断决定要不要做第 2 步。
> 设计依据见 `docs/legal-recall-implementation-plan.md`，前置 gate 见 `docs/legal-first-ingest-checklist.md`。

## 0. 前置

- 代码：本仓库 `develop` 分支，包含从 `53c6d27` 到 `9d84f81` 的 10 个提交。
- 镜像：`docker build -t artoo-backend:legal ./backend`（backend 与 worker 共用同一镜像）。
  注意同 tag 重建后必须 `docker compose ... up -d --force-recreate --no-deps backend worker`，
  否则 compose 不会换镜像。
- `.env`：Embedding / Rerank（远程服务）与 Milvus / PG 连接就绪。
- 语料：解压后的目录（文件名是正确 UTF-8；用原始 zip 则文件名是 cp437 误读，导入脚本会还原）。

## 1. 检查三件事（决定要不要重置）

**1.1 collection 是否已带过滤字段**（最关键）：

```bash
docker exec <backend容器> python -c "
from pymilvus import MilvusClient
c=MilvusClient(uri='http://milvus:19530')
print([f['name'] for f in c.describe_collection('artoo_chunks_1024')['fields']])
print(sorted(c.list_indexes('artoo_chunks_1024')))"
```

字段里必须含 `law_type` / `province` / `city`，索引里必须含 `idx_law_type` /
`idx_province` / `idx_city`。**缺任何一项就必须先做第 2 步**——Milvus schema 固定且未开
dynamic field，向旧表插新字段会直接 `DataNotMatchException`。

**1.2 全局法条库的粒度配置**：

```bash
docker exec -e PGPASSWORD=<pg密码> <pg容器> psql -U postgres -d artoo -t -A \
  -c "select config->>'law_child_policy' from knowledge_bases where config->>'is_default_legal_kb'='true';"
```

期望 `article`。bootstrap 建库时会写入；**已存在的库不会被自动补**，用
`PUT /api/knowledge-bases/{kb_id}` 带上完整 config（含 `chunker_type`/`is_default_legal_kb`）补一次。

**1.3 是否已有存量内容**：`select count(*) from documents;` / `chunks;`。按"全新部署、不带存量数据"
的约定，有存量就重置。

## 2. 重置知识内容（仅第 1 步判定需要时）

顺序不能变，尤其第 2 步：

```bash
# 1) 备份 PG
docker exec -e PGPASSWORD=<pg密码> <pg容器> pg_dump -U postgres artoo > backups/artoo-before-ingest.sql
# 2) 停 worker —— 必须带 profile，否则服务没停
docker compose -f docker-compose.yml -f docker-compose.local.yml --profile infra --profile app stop worker
# 3) 清空知识内容（保留租户/用户/API Key/知识库本体/模型配置）
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T backend python -m scripts.purge_data --yes
# 4) 起 worker
docker compose -f docker-compose.yml -f docker-compose.local.yml --profile infra --profile app start worker
```

核验：`documents=0`、`chunks=0`、Milvus `count(*)=0`、知识库本体仍在。

> **踩过的坑**：worker 没真停时，在途文档会在 collection 重建后继续写入，产生**孤儿向量**
> （实测 Milvus 712 条 vs PG 681 条，抽样 60 个 chunk_id 有 4 个在 PG 不存在）。重置脚本
> 只停 worker（backend 要留着承载 purge 脚本），照它来即可。

## 3. 生成/刷新入库清单

```bash
cd backend
python -m app.scripts.audit_legal_metadata <语料目录> --out <报告目录> --ingest-list <报告目录>/ingest_list.csv
```

期望（2026-09-14 全量实测基线）：29,957 份、**0 异常**、1,141,002 条文行、
**22,036 份入选 / 7,921 份淘汰**、有省份 27,579 份。耗时约 76 秒。

## 4. 小批试跑（先 50 份）

```bash
python -m app.scripts.ingest_legal_corpus \
  --list <报告目录>/ingest_list.csv --corpus <语料目录> \
  --kb-id <全局库ID> --api-key <Key> --base-url <服务地址> \
  --limit 50 --state <报告目录>/state.json --report <报告目录>/report.csv
```

期望：预检 0 问题 → 全部 `uploaded` → 稍后全部 `completed`。
核对两件事：`used_chunks` 与清单条文数接近（**一条文一子块 ≈ 1:1**）；抽样检索结果的
`metadata` 带 `law_type` / `province` / `city` / `article_id`。

## 5. 全量入库

去掉 `--limit` 即可（脚本可断点续跑：`--state` 记录已提交文件名，重跑自动跳过；服务端另按
`file_hash` 去重）。

**吞吐**：默认 `PIPELINE_MAX_CONCURRENT=4` 时约 **9–12 秒/份** → 22,036 份约 **50–55 小时**。
加速有两条路：把 `PIPELINE_MAX_CONCURRENT` 调大（写进 `.env` 后重建 worker），或加 worker
实例（compose 里 `container_name: arag-worker` 固定，`--scale` 不可用，需要额外 override
定义第二个 worker 服务）。建议先用 200 份压测确认远程 Embedding 服务不被打爆再定值。

## 6. 对账

```bash
python -m app.scripts.ingest_legal_corpus --list <报告目录>/ingest_list.csv \
  --kb-id <全局库ID> --api-key <Key> --base-url <服务地址> --verify
```

期望 `missing=0`、`failed=0`。容量核对：`used_chunks` 应落在 **约 0.82 M**（上限 1 M，留约 18%）。

## 7. 升级到带「效力状态」筛选的版本（不需要重建、不需要重灌）

文件列表要按效力状态筛选，因此 `documents` 多了一列 `validity_status` 和它的索引。这两样
**由进程启动时的幂等迁移自动补**（`app/startup._auto_migrate_legal_document_columns`），
API 与 Worker 各自会跑一次，不需要手建、不需要重置 Milvus、也不需要重新解析文档。

唯一需要手动做一次的是**存量回填**：迁移只补结构，不回填数据（回填要扫全表，不适合放在
每次启动都要跑的路径上），而已经入库的文档，其效力状态只存在于 `chunks.metadata`。

```bash
# 1) 先体检：只打印影响面，不改任何数据
docker exec arag-backend python -m scripts.backfill_document_validity --dry-run

# 2) 回填（无 TTY 时必须带 --yes）
docker exec -T arag-backend python -m scripts.backfill_document_validity --yes
```

脚本只动 `status='completed'` 且 `validity_status` 为空的文档，幂等，可重复执行；它不重跑
抽取、不重算向量，因此**可以和正在跑的全量入库并存**——新入库的文档由管道自己写这一列。
跑完再执行一次 `--dry-run` 应当报 `0`。

核对（应当看到分布不再是清一色 null）：

```bash
docker exec arag-backend python -c "
import asyncio
from sqlalchemy import text
from app.storage.database import async_session
async def main():
    async with async_session() as s:
        rows = await s.execute(text('SELECT validity_status, count(*) FROM documents GROUP BY 1 ORDER BY 2 DESC'))
        for vs, n in rows: print(vs, n)
asyncio.run(main())"
```

## 8. 失败处置速查

| 现象 | 原因 | 处置 |
|---|---|---|
| `DataNotMatchException: ... unexpected field` | collection 是旧 schema | 回到第 2 步重置 |
| 文档 `failed`，错误含"文档未提取到任何文本" | 该文档无可提取文本（OCR 已关闭） | 确认是否新形态；`DocxLoader` 已修过 89 份此类文档 |
| `UploadCapExceeded` 或批量 degraded | 超过 `kb_chunk_cap` | 核对容量方案：版本选版 + `article` 粒度 |
| 文档长时间 `pending` | worker 未起或队列阻塞 | `docker logs <worker容器>`；确认 worker 在跑 |
| 检索结果缺 `law_type` | 文档非法条语料，或早于本次 schema | 属正常（缺值整键缺失，不是 `null`） |

## 9. 本次不在范围内

- **PRD「仅返回现行有效」不由检索侧做**（2026-09-16 定稿）：`validity_status` 的枚举定义已从
  数据源字典确认（见 [legal-first-ingest-checklist.md](legal-first-ingest-checklist.md) 第四节），
  检索**返回全部状态**并在每条结果里附 `validity_status` 与 `validity_status_label`，由调用方
  自行筛选。（`2026-09-15` 曾实现为"默认排除 `1`/`-1` + `include_invalid` 放开"，该口径已取消。）
  **入库口径始终不变**：已废止/已失效的法条照收（共约 12%），以便回答"行为时法"这类问题。
- **粒度 A/B 未做**：`article` 粒度是按容量与空库可回退性选的，检索质量对比待补（需评测集）。
- 数据缺口三项（部门规章 / `expiry_date` / 关联司法解释）按约定暂缓。
