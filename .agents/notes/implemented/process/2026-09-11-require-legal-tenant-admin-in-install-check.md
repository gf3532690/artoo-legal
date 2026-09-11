# Agent Note: `install.sh` must fail fast when the legal tenant admin is unset

Status: implemented

## Problem

This product line is a single-tenant deployment whose global statute library is owned by
the tenant administrator created at bootstrap from `LEGAL_TENANT_ADMIN_USERNAME` /
`LEGAL_TENANT_ADMIN_PASSWORD`. Missing them is **not** an error at startup: bootstrap
creates the tenant, logs "未配置 LEGAL_TENANT_ADMIN_USERNAME/PASSWORD：默认租户已创建…",
and leaves the global library with `owner_user_id = NULL`. The result is a running
deployment where **no account can maintain the global library** — uploads and deletes are
rejected, and the failure only surfaces when someone tries.

`deploy/install.sh` validated only `JWT_SECRET`, `SUPER_ADMIN_USERNAME` and
`SUPER_ADMIN_PASSWORD`, and it deliberately **keeps an existing `.env`** rather than
regenerating it. The replacement scenario makes this likely rather than theoretical:
deploying over an existing Artoo installation in the same directory reuses the old `.env`,
which by definition predates these two variables.

## Decision

Add both variables to the `check_env` required list in `deploy/install.sh`, with messages
that say what they are for ("全局法条库的 owner"), so the deployment stops before starting
instead of producing a silently unmaintainable library.

## Alternatives considered

**Let bootstrap fail hard when they are missing.** Rejected: bootstrap is shared with
upstream Artoo, where "tenant exists but no admin yet" is a legitimate state (the platform
super admin adds one later). The deployment script is the right place to encode this
product line's requirement.

**Regenerate `.env` from `.env.example` on every install.** Rejected: it would silently
discard the operator's JWT secret, passwords and model endpoints on every update — far
worse than asking for two more values.

## Consequences

Upgrading an existing deployment now requires adding the two variables before `install.sh`
will run. That is a one-line fix for the operator and it fails with an explicit message
rather than after a successful-looking start.

## Verification

Reviewed the `check_env` path: with `.env` present but either variable empty, the script
appends them to `MISSING`, prints the ❌ list and exits before any `docker compose` call.

**Not executed here.** This packaging host has no `bash` (the shell is Windows
PowerShell; invoking `bash -n` fails with "execvpe(/bin/bash) failed"), so neither the
syntax check nor an end-to-end run happened on this machine — the packager runs
`install.sh` on the target server.
