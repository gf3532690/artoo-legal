# Agent Note: `install.sh` must fail fast when the legal tenant admin is unset

Status: implemented

## Problem

本产品线是单租户部署，全局法条库的 owner 是引导时按 `LEGAL_TENANT_ADMIN_USERNAME` /
`LEGAL_TENANT_ADMIN_PASSWORD` 创建的默认租户管理员。**缺这两项不是启动错误**：引导会建出租户、
打一行「未配置 LEGAL_TENANT_ADMIN_USERNAME/PASSWORD：默认租户已创建…」，然后把全局法条库的
`owner_user_id` 留空。结果是一个"看起来正常"的部署，**没有任何账号能维护全局法条库**——
上传/删除都被拒，而且只有真的去操作时才会暴露。

而 `deploy/install.sh` 只校验 `JWT_SECRET` / `SUPER_ADMIN_USERNAME` /
`SUPER_ADMIN_PASSWORD`，并且刻意**保留已存在的 `.env`** 而不是重新生成。替换部署场景让这件事
从理论变成大概率：在同一目录覆盖 Artoo 的安装时，沿用下来的旧 `.env` 按定义就没有这两个变量。

## Decision

把这两项加入 `deploy/install.sh` 的 `check_env` 必填列表，提示里写明用途（「全局法条库的
owner」），让部署在启动前就停下来，而不是产出一个静默不可维护的库。

## Alternatives considered

**让引导层直接报错。** 否决：引导是上游 Artoo 共用的，"租户已建但还没有管理员"在上游是合法
状态（平台超管后续补建）。这条产品线的要求适合写在部署脚本里。

**每次安装都从 `.env.example` 重新生成 `.env`。** 否决：那会在每次更新时静默丢掉运维设置的
JWT 密钥、口令与模型服务地址，比多填两个值糟糕得多。

## Consequences

升级既有部署时，必须先补这两个变量 `install.sh` 才会执行。对运维只是一行改动，而且会得到明确
报错，而不是一次"看起来成功"的启动。

## Verification

复核了 `check_env` 分支：`.env` 存在但任一变量为空时，脚本会把它们加入 `MISSING`、
打印 ❌ 列表，并在任何 `docker compose` 调用之前退出。

**本机未执行。** 打包机没有 `bash`（shell 是 Windows PowerShell，`bash -n` 直接报
"execvpe(/bin/bash) failed"），所以语法检查与端到端部署都没有在这台机器上跑过——
`install.sh` 由打包者在目标服务器上执行。
