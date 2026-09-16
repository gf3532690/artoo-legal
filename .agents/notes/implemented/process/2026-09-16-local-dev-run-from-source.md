# Agent Note: Local dev runs the app from source, not from images

Status: implemented

English | [中文](2026-09-16-local-dev-run-from-source.zh.md)

## Problem

This fork's README and compose files only described the image route: `docker build` the backend and
frontend, then `docker compose --profile app up`. On the Windows dev machine that means ~1–2 minutes
of image build per iteration and no hot reload — a Python edit needs a rebuild, a frontend edit needs
a rebuild.

The repository already has the intended flow in its Makefile (`make infra` puts only the middleware
in docker; `make dev-*` runs uvicorn `--reload`, the worker and `npm run dev` on the host). It is
just not reachable there: `make` is not installed, and the recipes are written for POSIX
(`.venv/bin/python`, `VAR=value cmd`), which does not translate.

## Decision

`scripts/dev-local.ps1` — the Windows equivalent of `make infra` + `make dev`, with `-Stop`,
`-SkipInfra`, `-NoWorker` and `-Python <interpreter>`.

It stops the image-based app containers (they hold port 8000), starts the middleware with
`docker compose --profile infra up -d --wait` — the override file is auto-loaded, so the ports reach
the host — and then starts three host processes with hot reload.

Two details are deliberate:

- **The app processes run with the repository root as their working directory, with
  `PYTHONPATH=backend`.** `Settings.env_file` is `".env"`, resolved against the working directory, so
  this makes the host processes read **the same root `.env` the containers read** instead of
  requiring a second `backend/.env` to keep in sync. Only `DATABASE_URL`, `MILVUS_HOST`,
  `REDIS_URL` and `MINIO_ENDPOINT` are overridden to `localhost`, mirroring exactly what compose
  injects for the containers.
- **`-Stop` kills the recorded PID's process tree, and never kills by port.** `uvicorn --reload` and
  vite both spawn children — the child is the one listening, so killing only the listener lets the
  parent respawn it, while killing only the parent orphans the listener. And port-based killing is
  unsafe here: the sibling `aladdin` checkout runs the same services on 8000/3000.

## Alternatives considered

**Installing `make` (or running the Makefile under WSL).** Rejected: a toolchain dependency for one
script, and WSL changes what "localhost" means for the middleware ports — a large amount of
confusion for a small amount of convenience.

**Generating a `backend/.env` (as `backend/.env.example` suggests).** Rejected: it would be a second
config file to keep in sync with the root one, and the values that matter (the legal-library keys,
the embedding/rerank endpoints) live in the root `.env`.

**Keeping the image route as the default and adding hot reload to it.** Rejected: mounting source
into the images would paper over the same problem while keeping the build step; the repository has
already decided that this is the dev flow, this note only makes it runnable on Windows.

**Killing dev processes by port in `-Stop`.** Rejected: see above — it would reach into the sibling
checkout.

## Consequences

The local stack now runs from the working tree, which changes the small tooling around it: commands
that were `docker exec arag-backend python -m scripts.<x>` become `python -m scripts.<x>` from
`backend/` with the same environment, and the app containers must not be running (the script stops
them, and prints a hint if a port is still held by something it did not start).

The middleware stays in docker — it is not something to iterate on, and `--wait` gives a real
readiness gate instead of a sleep.

## Verification

Ran end to end on the Windows machine, with `-Python` pointing at an existing conda environment:

```text
.\scripts\dev-local.ps1 -Python <conda env python>
  Python: ...\envs\aladdin\python.exe
  停掉镜像版应用容器（若在跑）…         → arag-backend / worker / frontend stopped
  启动中间件（等待健康检查）…           → etcd/redis/milvus/postgres/minio Healthy
  启动应用（宿主机，改代码即时生效）…   → dev-backend / dev-worker / dev-frontend

port 8000 → uvicorn (host process)     backend openapi 200
port 3000 → vite                       frontend 200
worker pid alive
POST /api/retrieval/search (exact) → 中华人民共和国国籍法 第一条 / 现行有效
  （用的就是根 .env 里的 Key，说明读到的是与容器同一份配置）

.\scripts\dev-local.ps1 -Stop
  → 8000 / 3000 均不再监听，残留 dev 进程 0
```
