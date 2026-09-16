# Agent Note: 本地开发从源码跑，而不是打镜像

Status: implemented

[English](2026-09-16-local-dev-run-from-source.md) | 中文

## Problem

本 fork 的 README 与 compose 只描述了镜像路线：先 `docker build` 后端与前端，再
`docker compose --profile app up`。在 Windows 开发机上这意味着每改一次都要等 1~2 分钟构建，而且
没有热重载——改一行 Python 要重建，改一行前端也要重建。

仓库其实早就有它想要的流程（Makefile：`make infra` 只把中间件放进 docker，`make dev-*` 在宿主机
跑 uvicorn `--reload`、Worker 与 `npm run dev`）。只是在那台机器上够不着：没装 `make`，而且那些
配方是按 POSIX 写的（`.venv/bin/python`、`VAR=value cmd`），照搬不过去。

## Decision

新增 `scripts/dev-local.ps1`——Windows 上等价于 `make infra` + `make dev`，带 `-Stop`、
`-SkipInfra`、`-NoWorker`、`-Python <解释器>`。

它先停掉镜像版的应用容器（它们占着 8000），用 `docker compose --profile infra up -d --wait` 起
中间件（override 文件自动叠加，端口因此可达宿主机），再起三个带热重载的宿主机进程。

两处细节是有意为之：

- **应用进程的工作目录是仓库根，并设 `PYTHONPATH=backend`。** `Settings.env_file` 是 `".env"`，
  按工作目录解析——这样宿主机进程读的就是**容器读的那一份根 `.env`**，不需要再维护一份
  `backend/.env` 去跟它同步。只有 `DATABASE_URL`、`MILVUS_HOST`、`REDIS_URL`、`MINIO_ENDPOINT`
  四个被换成 `localhost`，与 compose 给容器注入的那几个一一对应。
- **`-Stop` 按记录的 PID 收**进程树**，绝不按端口杀。** `uvicorn --reload` 与 vite 都会派生——
  监听端口的是子进程，只杀监听者父进程会把它重新拉起来；只杀父进程又会留下孤儿子进程。而按端口
  杀在这台机器上不安全：隔壁的 `aladdin` checkout 在同一对端口上跑着同样的服务。

## Alternatives considered

**装 `make`（或在 WSL 里跑 Makefile）。** 否决：为了一个脚本引入一条工具链依赖；而且 WSL 会改变
"localhost"对中间件端口的含义——一点便利换来一堆困惑。

**生成一份 `backend/.env`**（`backend/.env.example` 是这么引导的）。否决：那会多出一份需要与根
`.env` 保持同步的配置；而真正要紧的值（法条库那两把 Key、Embedding/Rerank 地址）都在根 `.env` 里。

**保留镜像路线、只给它加热重载。** 否决：那等于把源码挂进镜像里，问题还是同一个，构建步骤却留着；
仓库已经把"这才是开发流程"定下来了，本 note 只是让它能在 Windows 上跑起来。

**`-Stop` 按端口杀。** 否决：见上，会伸进隔壁那个 checkout。

## Consequences

本地栈现在从工作区跑，周边的小工具跟着变：原先的 `docker exec arag-backend python -m scripts.<x>`
变成在 `backend/` 下用同一套环境跑 `python -m scripts.<x>`；并且应用容器必须不在跑（脚本会停掉
它们，若某个端口仍被"不是本脚本起的进程"占着，它会给出提示而不是动手）。

中间件留在 docker 里——那不是需要迭代的东西，而且 `--wait` 给出的是真正的就绪闸门，不是一个
`sleep`。

## Verification

在 Windows 机器上端到端跑过，`-Python` 指向一个已有的 conda 环境：

```text
.\scripts\dev-local.ps1 -Python <conda env python>
  Python: ...\envs\aladdin\python.exe
  停掉镜像版应用容器（若在跑）…         → arag-backend / worker / frontend 已停
  启动中间件（等待健康检查）…           → etcd/redis/milvus/postgres/minio Healthy
  启动应用（宿主机，改代码即时生效）…   → dev-backend / dev-worker / dev-frontend

port 8000 → uvicorn（宿主机进程）      backend openapi 200
port 3000 → vite                       frontend 200
worker pid 存活
POST /api/retrieval/search（exact）→ 中华人民共和国国籍法 第一条 / 现行有效
  （用的就是根 .env 里的 Key，说明读到的是与容器同一份配置）

.\scripts\dev-local.ps1 -Stop
  → 8000 / 3000 均不再监听，残留 dev 进程 0
```
