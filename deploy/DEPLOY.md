# 法条库 离线部署运维手册

面向运维人员的生产 / 内网离线部署说明。开发侧已在有网机器上构建好离线包（`dist/` 目录），本手册涵盖从拿到离线包到部署、验证、日常运维的完整流程。

> 本产品线由 Artoo 派生而来：镜像名、卷名、容器名（`arag-*`）、`.env` 变量名等**代码标识符仍沿用 Artoo**，界面与业务文案是「法条库」。与上游相比有两处关键差别，本手册的第四、六章专门讲：
>
> 1. **单租户**——全平台只有一个租户、一个全局法条库；
> 2. **只做召回**——对话 / 智能体 / MCP / 知识图谱 / OCR / ASR 等链路已移除或关闭，检索链路不需要 LLM。

---

## 一、环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Linux（x86_64 / arm64，与离线包架构一致） |
| Docker Engine | 20.10 及以上 |
| Docker Compose | V1（`docker-compose`）或 V2（`docker compose`）任一即可，脚本自动探测 |
| CPU / 内存 | 建议 8 核 16G 起步（Milvus + Neo4j 内存占用较高，开图谱建议 32G） |
| 磁盘 | 建议 100G 以上（镜像 + 向量库 + 上传文件 + 日志） |

> 架构必须匹配：`arm64` 服务器只能部署 `arm64` 架构的离线包，反之亦然。拿包前先与开发确认目标架构。

---

## 二、交付物说明

开发交付的 `dist/` 目录结构如下：

```
dist/
├── app-images.tar          # 应用镜像（backend + frontend）
├── infra-images.tar        # 中间件镜像（首次完整包才有；--app-only 更新包无此文件）
├── docker-compose.yml      # 编排文件（唯一真源）
├── .env                    # 预置配置（交付包通常已带，含账号口令与预置 API Key；有它就不用填）
├── .env.example            # 配置模板（没有 .env 时，首次运行自动复制它）
├── install.sh              # 部署 / 运维一体化脚本
├── DEPLOY.md               # 本手册
├── deploy/
│   └── milvus-user.yaml    # Milvus mmap 调优配置
└── frontend/public/config.js  # 前端运行时配置（挂载覆盖，改完刷新即生效）
```

将整个 `dist/` 目录拷贝到目标服务器任意路径（如 `/opt/artoo`），后续所有命令均在该目录内执行。

> 交付包若已带 `.env`，说明账号口令、端口与两把预置 API Key 都填好了，首次部署不需要再改配置。**这份 `.env` 含明文密钥，按生产密钥管理。**

---

## 三、首次部署

进入 `dist/` 目录，执行：

```bash
cd /opt/artoo        # 你拷贝 dist/ 的实际路径
bash install.sh
```

脚本会按以下步骤自动执行：

1. **加载镜像** — 加载目录下所有 `*.tar` 镜像包
2. **检查配置** — 首次运行从 `.env.example` 生成 `.env`，并校验必填项
3. **兼容处理** — 自动适配 Compose V1/V2，处理网络冲突
4. **启动中间件** — 启动 etcd/minio/milvus/postgres/redis，等待全部 healthy（最多 150 秒）
5. **启动应用** — 启动 backend/worker/frontend

部署完成后脚本会打印访问地址：

```
前端: http://<服务器IP>:8888
后端: http://<服务器IP>:8000
```

> 本产品线的部署包默认把端口错开（`FRONTEND_PORT=8889` / `BACKEND_PORT=8001`），便于与同机既有的 Artoo 并存；`.env` 里写多少就是多少，见第十章「端口清单」。

### 首次部署后你会得到什么

首次启动时后端会**幂等引导**出下面这套东西（只创建一次，账号与库的完整逻辑见第四、六章）：

| 得到什么 | 名称 | 说明 |
| --- | --- | --- |
| 一个租户 | `tenant-legal-default`（显示名取 `LEGAL_TENANT_NAME`，默认「法条库」） | 全平台唯一；全局库与所有个人库都在它里面 |
| 超级管理员 | `SUPER_ADMIN_USERNAME`（默认 `superadmin`） | 平台装配身份：管租户、Embedding、API Key、审计；**左侧没有「法条库」菜单** |
| 租户管理员 | `LEGAL_TENANT_ADMIN_USERNAME`（默认 `lawadmin`） | 默认租户管理员，同时是**全局法条库的 owner**：维护语料、管用户、跑检索测试 |
| 一个全局法条库 | 「全局法条库」 | 空库，等租户管理员上传法条文件 |

> 两个账号的初始口令来自 `.env`，**首次登录强制改密**。`install.sh` 把这两组账号配置都列为必填：`LEGAL_TENANT_ADMIN_*` 缺失不会报错，但会静默产出「全局库没有 owner、谁都改不了」的部署。

### 关于必填配置

`.env.example` 已内置示例默认值，可直接启动。但**生产环境务必先改掉下面这些再部署**（`install.sh` 会校验，缺一项就 fail-fast，不会带着空配置启动）：

| 配置项 | 说明 | 生成方式 |
| --- | --- | --- |
| `JWT_SECRET` | JWT 签名密钥 | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `SUPER_ADMIN_PASSWORD` | 初始超级管理员密码 | 自定义强密码，如 `Admin@xxxxxx` |
| `LEGAL_TENANT_ADMIN_USERNAME` | 默认租户管理员用户名（全局法条库 owner） | 自定义，如 `lawadmin` |
| `LEGAL_TENANT_ADMIN_PASSWORD` | 该账号初始密码 | 自定义强密码；缺失会让全局法条库没有 owner、谁都维护不了 |
| `LEGAL_BOOTSTRAP_PROXY_API_KEY` / `LEGAL_BOOTSTRAP_ADMIN_API_KEY` | 预置给下游的两把 Key（选填，见 4.3） | `python3 -c "import secrets; print('sk-'+secrets.token_hex(24))"` |

修改方式：

```bash
vi .env          # 编辑配置
bash install.sh  # 重新执行部署
```

> 两个账号的用户名/口令只在**首次启动**生效：账号建好之后，改 `.env` 重启既不改密码也不重建账号，改口令要在界面里改。

---

## 四、账号与 API Key

法条库是「后台手工维护 + 下游系统按 API 维护」两种用法的叠加，所以先把**身份**讲清楚，配置项本身在第五章。

### 4.1 两个账号，两种身份

| | 超级管理员 | 租户管理员 |
| --- | --- | --- |
| 来源 | `.env` 的 `SUPER_ADMIN_USERNAME` / `SUPER_ADMIN_PASSWORD` | `.env` 的 `LEGAL_TENANT_ADMIN_USERNAME` / `LEGAL_TENANT_ADMIN_PASSWORD` |
| 归属 | 不属于任何租户 | 默认租户 `tenant-legal-default` |
| 左侧菜单 | 租户管理 / Embedding / 检索测试 / API Key / 审计日志 | 法条库 / 检索测试 / API Key / 用户管理 / 审计日志 |
| 职责 | 平台装配：建租户、配 Embedding/Rerank、签发 Key、看审计 | 维护语料：上传/删除法条文件、管本租户用户、验收召回 |
| 关键边界 | **看不到「法条库」菜单**（直接敲 `/knowledge-bases` 会被重定向回租户管理）；文档/检索接口也不对超管返回业务正文 | 全局法条库的 **owner**——全局库只有它（及其签发的用户级 Key）能写 |

为什么这么分：超管是「装平台的人」，不碰内容；语料归租户管理员。全局法条库的写判定是 **owner 制**，owner 就是这个租户管理员，所以「谁能维护法条」不需要额外的授权表。

普通成员（member）在本产品线**没有任何菜单入口**：库的读写由下游系统带着 API Key 完成，不是人登后台点出来的。

**账号只创建一次（幂等）**：

- 只在「首次启动且账号不存在」时按 `.env` 创建；**改完 `.env` 重启不会改密码、也不会重建账号**。
- 改口令走界面（首次登录强制改密）；忘记口令要么直接改数据库，要么清数据卷重来。
- 重建数据卷（`install.sh down-all`）会让账号回到 `.env` 里的初始口令，并再次要求改密。

### 4.2 API Key：三种模型，本部署用两把

平台支持三种 Key（`api_keys.key_type`），语义完全不同：

| 类型 | 身份来自 | 用途 |
| --- | --- | --- |
| `external_agent`（**代理 Key**） | 请求头 `X-External-User-Id` 指定的外部用户：按 `(代理Key, 外部用户ID)` 懒创建，彼此隔离 | 下游代**多个终端用户**用，一把 Key 服务所有人 |
| `user_level` | 绑定到某个后台用户 | 下游以**某个后台身份**行事（例如全局库 owner） |
| `tenant_level` | 租户机器身份（无角色），按 Key 的授权范围裁剪 | 上游形态的机器凭据；本产品线未使用 |

明文 Key **只在创建时返回一次**，库里只存 SHA256，列表页只看得到前缀。

下游 lite 需要**两把**：

| 下游配置 | 需要的 Key | 由谁签发 | 干什么 |
| --- | --- | --- | --- |
| `legal-kb.api-key` | 代理 Key（`external_agent`） | 超级管理员 | 个人库的建库/上传/列表/删除 + `POST /api/retrieval/search`；每次请求带 `X-External-User-Id` |
| `legal-kb.admin-api-key` | 用户级 Key（`user_level`） | 租户管理员**本人** | admin 端维护**全局法条库**（上传/删除法条、看文件列表） |

**为什么必须两把**：全局法条库是 `organization + read`，写判定要求 owner 身份；代理 Key 解析出来的是外部用户，拿它写全局库会被 **403**。反过来，用户级 Key 是某个人的身份，做不了多用户隔离。

**代理 Key 是长期资产，别随手换**：外部身份的名字空间是 `(代理 Key 的 id, X-External-User-Id)`。换一把 Key（哪怕终端用户还是同一批）会解析出**全新的身份**，老用户的个人库立刻 404——数据还在，只是归到了旧身份名下。确需更换要配套迁移归属。

### 4.3 预置 Key：不预置也能用，预置了更省事

不预置时的流程：超管登录 →「API Key」签发代理 Key → 租户管理员登录 →「API Key」给自己创建用户级 Key → 两把明文抄进下游配置。**每次重建数据卷都得重做一遍**，而失败形态是下游静默 401。

因此支持在 `.env` 里预置，**首次启动时幂等播种**（库里照样只存 SHA256，明文只在 `.env`）：

| 配置项 | 播种的 Key | 对应下游配置 |
| --- | --- | --- |
| `LEGAL_BOOTSTRAP_PROXY_API_KEY` | 代理 Key（`external_agent`），租户锁在默认租户 | `legal-kb.api-key` |
| `LEGAL_BOOTSTRAP_ADMIN_API_KEY` | 租户管理员名下的用户级 Key | `legal-kb.admin-api-key` |

四条行为约定：

1. **留空 = 不预置**，回到手工领取流程；两项都空时引导阶段什么都不做。
2. **幂等**：按 Key 的 SHA256 查重，已存在就跳过，反复重启不会重复建。
3. **已撤销的不复活**：在后台撤销过预置 Key，重启也不会把它放回来（只打一条 warning 说明下游会 401）。要恢复就换一个 env 值，或清理那一行。
4. **id 由 (用途, 明文) 推导**，同一个 env 值反复重建仍是同一个身份命名空间——「换 Key = 换身份」这个坑因此不会落到自己头上。

生成一对新 Key：

```bash
python3 -c "import secrets; print('sk-'+secrets.token_hex(24))"   # 跑两次，分别给代理 Key / 维护 Key
```

## 五、配置项说明（.env）

以下为运维常关注的配置，完整项见 `.env.example` 内注释。

### 端口与进程

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `FRONTEND_PORT` | 8888 | 前端访问端口 |
| `BACKEND_PORT` | 8000 | 后端 API 端口 |
| `BACKEND_WORKERS` | 2 | 后端进程数，单机约 50 人建议 2，最多 2~4 |
| `POSTGRES_PASSWORD` | postgres | 数据库密码，生产建议修改 |
| `TZ` | Asia/Shanghai | 时区，影响日志切分与时间显示 |

### 认证（必填）

| 配置项 | 说明 |
| --- | --- |
| `JWT_SECRET` | JWT 签名密钥，缺失则启动失败 |
| `SUPER_ADMIN_USERNAME` | 初始超管用户名，默认 `superadmin` |
| `SUPER_ADMIN_PASSWORD` | 初始超管密码，强制首次登录改密 |
| `REGISTRATION_MODE` | 注册模式：`invite_only`（默认，仅邀请）/ `self_serve`（开放注册） |

### 法条库专用（单租户 + 预置 Key）

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `LEGAL_TENANT_ADMIN_USERNAME` | 无（**必填**） | 默认租户管理员用户名，同时是全局法条库 owner |
| `LEGAL_TENANT_ADMIN_PASSWORD` | 无（**必填**） | 该账号初始口令，强制首次登录改密 |
| `LEGAL_TENANT_NAME` | 法条库 | 默认租户的显示名（后台「租户管理」里看到的名字） |
| `LEGAL_BOOTSTRAP_PROXY_API_KEY` | 空 | 预置的代理 Key，等价于下游 `legal-kb.api-key`；留空则不预置 |
| `LEGAL_BOOTSTRAP_ADMIN_API_KEY` | 空 | 预置的全局库维护 Key，等价于下游 `legal-kb.admin-api-key`；留空则不预置 |

> 两个预置 Key 的语义、幂等规则与身份命名空间见 4.3。**它们必须与下游配置按位相同**，否则下游 401。

### 模型服务（选填，也可部署后在后台「Embedding」页配置）

- `EMBED_BASE_URL` / `EMBED_MODEL` — Embedding 服务（地址填到 `/v1`）
- `RERANK_BASE_URL` / `RERANK_MODEL` — Rerank 服务
- `EMBED_SPARSE_ENABLED` — 稀疏向量开关；服务不支持 `/embed_sparse` 时置 `false`

> **检索链路不需要 LLM**：本产品线只做召回、不生成答案（`LLM_*` 是上游遗留配置，留空不影响召回）。没配 Embedding 也能启动，但检索没有向量、只会返回空结果。

### 性能调优（按硬件调整，不配用默认值）

关键项：`PIPELINE_MAX_CONCURRENT`（文档并发，GPU 推 3 / CPU 推 1-2）、`PIPELINE_EMBED_CONCURRENCY`（Embedding 并发，远程服务报 429 时调小）。其余见 `.env.example` 注释。

---

## 六、全局法条库与个人法条库

全平台只有一个租户（`tenant-legal-default`），但库里有两类：

### 6.1 两者对照

| | 全局法条库 | 个人法条库 |
| --- | --- | --- |
| 数量 | 全平台固定 **1 个**（靠 `config.is_default_legal_kb=true` 标记识别，不靠名字） | 由下游按需创建，**一个外部身份一个** |
| 归属 | 默认租户，owner = 租户管理员 | 默认租户，owner = 外部用户身份（下游的终端用户） |
| 可见性 | `organization` + `read` — 同租户身份都能读 | `private` — 只有该身份可读，别人读到的是 404 |
| 谁维护 | 租户管理员在后台维护；下游也可用 owner 的用户级 Key 走 API | 只有下游系统（lite）用代理 Key + `X-External-User-Id` |
| 能否删除 | **不能**（接口 403） | 可以 |
| 入库切分 | `chunker_type=laws` **锁定**，改会被 403 | 创建时默认 `laws` |
| 检索 | **默认并入**，调用方不用传 | 传 `kb_ids` 才并入 |

全局法条库名可以改、也可以被下游改名，**判定永远看 `config` 标记**——所以「哪一个是全局库」不依赖命名约定。同理，法条文件名、目录名都不作权威，**正文**才是（法名与条号从正文抽取）。

### 6.2 检索：一次调用同时召回两库

下游调 `POST /api/retrieval/search`，法条库负责把两库并起来：

```jsonc
{
  "query": "民法典第146条",
  "kb_ids": ["<个人库 id>"],   // 只传个人库；可省略、可传多个
  "top_k": 5,                  // 默认 5（上游是 10）；传几返回几条，不足就有几条
  "mode": "hybrid"             // hybrid（默认，三路混合）/ direct（仅稠密，单库单源时生效）
}
```

四条要点：

1. **全局库自动并入**，所以 `kb_ids` 不传或传空都是合法调用（只搜全局库）。
2. 结果按 rank 合并排序，每条结果的 `metadata.source` 标 `global` / `personal`，调用方据此区分来源。
3. **不需要 LLM**，也不接受 `session_id`（会话链路已删，显式传会 400）。
4. 每条结果带法条字段：`law_name`（法名）、`article_number`（条号）、`article_label`（如「第146条」）、`chapter`（编/章/节），可直接展示「《民法典》第一百四十六条」。

### 6.3 语料怎么更新

- **法条修订 = 删旧文件 + 传新文件**，人工执行（后台或 API 均可）。系统在**同一个库内按文件内容（SHA256）查重**：重复上传直接返回已有文档并标 `status=duplicate`，不覆盖也不重复入库；但修订后的新版与旧版内容不同，会被当成**两个文档**——所以必须先删旧再传新，否则两版一起参与召回。
- **语料格式用 `.doc` / `.docx`**（法院数据源就是这两种）。上传白名单更宽（还含 pdf / txt / md / 表格 / 图片 / 音频，属上游遗留），但图片与音频不会转成文字——OCR / ASR 在本部署已关闭。
- 入库是异步的：接口秒回 `pending`，后台列表显示进度，落定后变 `completed`。上传成功 ≠ 立刻能检索到。
- 目录行不参与检索；没有条号结构的文档（例如刑法修正案那种「一、第一百六十二条后增加一条」）整段入库，`article_number` 为空，不影响召回，只是不能按条号展示。
- 文档里的图片（例如国徽）不解析、不 OCR——本部署 OCR 已关闭，与法条内容无关。

### 6.4 两端界面的口径不一样

| 端 | 点「法条库」菜单 | 为什么 |
| --- | --- | --- |
| 法条库后台 | 进**库列表**，自己选库再看文件 | 后台同时可能有全局库和本人名下的库，替用户选一个会把「我在看哪个库」藏起来 |
| lite 端 | 直接进**该用户的个人库文件列表** | lite 里一个用户只有一个个人库，库名由后端创建并锁定，「菜单 = 内容」 |

---

## 七、日常运维命令

所有命令在 `dist/` 目录内执行。`start/stop/restart/update` 末尾可加服务名，只操作单个服务。

| 命令 | 说明 |
| --- | --- |
| `bash install.sh status` | 查看所有服务状态 |
| `bash install.sh logs [服务名] [条数]` | 查看日志（默认应用日志 100 条，实时跟踪，Ctrl+C 退出） |
| `bash install.sh restart [服务名]` | 重启应用 / 指定服务（不重建、不加载镜像） |
| `bash install.sh start [服务名]` | 启动全部 / 指定服务 |
| `bash install.sh stop [服务名]` | 停止全部 / 指定服务（**数据保留**） |
| `bash install.sh down` | 停止并删除容器（**数据卷保留**） |
| `bash install.sh down-all` | 停止并删除容器 + 数据卷（⚠️ **清除所有数据**，需二次确认） |

可用服务名：`backend` / `worker` / `frontend` / `postgres` / `milvus` / `redis` / `etcd` / `minio` / `neo4j`

示例：

```bash
bash install.sh logs backend 200     # 看后端最近 200 条日志
bash install.sh restart backend      # 单独重启后端
bash install.sh status               # 查看服务健康状态
```

---

## 八、应用更新升级

开发提供**仅应用镜像的更新包**（`app-images.tar`，通过 `make build-app` 构建，不含中间件）。更新流程：

1. 用新的 `app-images.tar` 替换 `dist/` 目录内的旧文件
2. 执行更新命令：

```bash
bash install.sh update            # 更新全部应用
bash install.sh update backend    # 只更新后端
```

`update` 会加载新镜像 → 强制重建应用容器 → 自动清理被顶替的旧镜像。中间件不受影响，数据完整保留。

> 更新只动 `backend/worker/frontend`，不重建中间件，无数据丢失风险。

---

## 九、数据与备份

所有持久化数据存放在 Docker 命名卷中，`down`（不带 `-v`）不会删除：

| 数据卷 | 内容 |
| --- | --- |
| `postgres_data` | 业务数据库：账号、API Key（只存 SHA256）、租户、知识库与文档元数据、chunk 元数据（含法名/条号） |
| `milvus_data` | 向量库 |
| `minio_data` | 对象存储（Milvus 后端 + 知识库源文件） |
| `upload_data` | 上传文档 |
| `etcd_data` / `redis_data` | Milvus 元数据 / 缓存队列 |
| `neo4j_data` | 知识图谱数据（仅开图谱时） |

备份建议：定期备份 `postgres_data`、`milvus_data`、`minio_data`、`upload_data` 四个卷。可用 `docker run --rm -v <卷名>:/data -v $(pwd):/backup alpine tar czf /backup/<卷名>.tar.gz -C /data .` 导出。

> ⚠️ `bash install.sh down-all` 会删除全部数据卷，属不可逆操作，执行前务必确认已备份。

---

## 十、端口清单

对外暴露端口（`.env` 里 `FRONTEND_PORT` / `BACKEND_PORT` 配多少就是多少；本产品线的部署包默认 **8889 / 8001**，与同机既有的 Artoo 错开）：

| 端口 | 服务 | 说明 |
| --- | --- | --- |
| 8888 | frontend | 前端 Web 访问（`FRONTEND_PORT`） |
| 8000 | backend | 后端 API（`BACKEND_PORT`） |
| 7474 | neo4j | 图谱浏览器管理台（仅开图谱时） |
| 7687 | neo4j | 图谱 Bolt 端口（仅开图谱时） |

中间件（etcd/minio/milvus/postgres/redis）在生产编排下**不对外暴露端口**，仅走容器内网 `arag-network` 互通。

---

## 十一、可选：启用知识图谱

> ⚠️ **本产品线不支持开启图谱。** 图谱代码按「配置关闭、模块保留」的策略留在仓库里（避免与上游分叉），页面入口也已移除——即使打开开关，后台也不会出现图谱菜单。`GRAPH_ENABLE` 请保持 `false`。
>
> 下面这段是上游遗留的开法，仅供对照，**不要在生产环境照做**。

知识图谱默认关闭，不开零成本。启用需满足两个条件：

1. **离线包需含图谱支持** — 开发打包时须带 `--with-graph`（应用镜像内装 Neo4j 驱动，离线包额外导出 `neo4j:5-community` 镜像）。若拿到的是普通包，需向开发索要图谱版离线包。
2. **在 `.env` 设开关**：

```bash
GRAPH_ENABLE=true
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<自定义强密码>
```

改完后重新执行 `bash install.sh`。脚本读取到 `GRAPH_ENABLE=true` 会自动把 Neo4j 一并纳入启动，无需额外命令。

> `NEO4J_URI` 由 compose 固定注入（`bolt://neo4j:7687`），无需配置。

---

## 十二、常见排查

| 现象 | 排查方向 |
| --- | --- |
| 脚本报「未找到 Docker Compose」 | 服务器未装 Docker Compose，先安装 |
| 部分中间件未就绪 | `docker ps --filter name=arag-` 查看容器状态；Milvus 依赖 etcd/minio healthy 才启动，耐心等待 |
| 应用启动失败 | `bash install.sh logs backend` 看后端日志，多为 `.env` 必填项缺失或数据库未就绪 |
| 检索无结果 / 报模型不可用 | 后台「Embedding」页确认 Embedding（与 Rerank）服务地址与 Key；检索**不需要 LLM**，别往 LLM 上查 |
| 下游报 401 | 比对下游 `legal-kb.api-key` / `admin-api-key` 与法条库 `.env` 的 `LEGAL_BOOTSTRAP_*`（或后台已签发的那两把）是否按位相同；后台撤销过的 Key 不会在重启时复活 |
| 下游能检索、但改不了全局库（403） | 全局库只有 owner（租户管理员）能写：检查下游 `admin-api-key` 用的是不是「全局库维护 Key」那把用户级 Key |
| 找不到「法条库」菜单 | 当前登录的是超级管理员（超管无内容菜单），用租户管理员登录 |
| 上传后检索不到 | 入库是异步的：等文档状态变 `completed`；再确认检索的是同一个库（全局库默认并入，个人库要传 `kb_ids`） |
| 改了 `config.js` 不生效 | 该文件挂载覆盖，改完刷新浏览器即可，无需重建 |
| 架构不匹配无法启动 | 确认离线包架构与服务器 CPU 架构一致（amd64 / arm64） |

---

## 附：手动等价命令

`install.sh` 首次部署等价于（在 `dist/` 内）：

```bash
docker compose -f docker-compose.yml up -d etcd minio milvus postgres redis   # 起中间件，等 healthy
docker compose -f docker-compose.yml up -d backend worker frontend            # 起应用
```

> 生产脚本按显式服务名启动（不依赖 profiles），以兼容 Compose V1/V2。
