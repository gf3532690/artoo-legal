# Agent Note: Align the Windows offline-packaging script with build.sh

Status: implemented

## Problem

离线部署包可以由 `deploy/build.sh`（Linux / macOS）或 `deploy/build.ps1`（Windows）产出，
而本项目的开发机跑的是 Windows 那份。两者已经跑偏：

1. **没有随包交付 `deploy/reset-knowledge-data.sh`**——Milvus 拓扑变化时清空知识内容的脚本。
   `build.sh` 拷它时写着「必须随包交付，否则运维在服务器上无脚本可执行」，`DEPLOY.md`（运维手册）
   同样是缺的。
2. **`docker save` 没带 `--platform`**，而 `build` / `pull` 带了。跨架构导出要么直接报
   `no suitable export target found … does not provide the specified platform`，要么在经典
   overlay2 存储下静默导出该 tag 当前持有的那个架构。
3. **拉取前没有清掉本地架构不符的副本。** 经典存储下一个 tag 只存一个架构，
   `docker pull --platform linux/arm64 postgres:16-alpine` 并不会把已有的 amd64 副本换成 arm64——
   包就会带着 amd64 的层发往 arm64 服务器。`build.sh` 早已处理这一点。

## Decision

让 `build.ps1` 与 `build.sh` 对齐，不改包结构：

- 两处 `docker save`（app-images.tar / infra-images.tar）在给了 `-Arch` 时带上
  `--platform linux/<arch>`，且非 0 退出即报错。
- 拉取每个中间件镜像前先查本地副本架构，与目标不符就删除。
- 补拷 `deploy/reset-knowledge-data.sh`（进 `dist/deploy/`）与 `deploy/DEPLOY.md`（进 `dist/`）。

## Alternatives considered

**只用 `build.sh`，让打包者在 WSL / Linux 主机上执行。** 否决：开发机就是 Windows，
`build.ps1` 存在的意义就是能在这台机器上出包；留一份已知错误的脚本，迟早会有人把 amd64 的包
发到 arm64 服务器上。

**用 buildx 打多平台 tar，让服务器自己挑。** 否决：目标只有一种架构，多平台 tar 体积大得多，
服务器侧 load 还要额外参数。

## Consequences

Windows 与 Linux 打出的包内容一致，且跨架构打包真的产出目标架构。

打包会把本地 `artoo-backend:latest` / `artoo-frontend:latest` 与中间件 tag 覆盖成目标架构——
如果本机还用这些 tag 跑 amd64 的 Artoo，事后需要重新 build 或 pull。本地法条库测试栈用的是
独立的 `artoo-*-:legal` tag，不受影响。

## Verification

打包机的环境检查：Docker Desktop + buildx 0.30.1；经典 `overlay2` 镜像存储；arm64 模拟可用
（`docker run --platform linux/arm64 alpine uname -m` → `aarch64`）；该 daemon 接受
`docker save --platform`。

**完整的 arm64 离线包没有在这里产出**——打包者要求自己执行——所以这个脚本的端到端结果在
那次运行之前仍未验证。
