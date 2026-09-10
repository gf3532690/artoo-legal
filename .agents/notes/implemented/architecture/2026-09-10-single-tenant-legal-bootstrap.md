# Agent Note: Single-tenant bootstrap for the legal library

Status: implemented

## Problem

The deployment needs a global statute library that exists by default and is
maintained by a person through the UI. Upstream has no such concept: knowledge
bases are created by users, and the platform Super_Admin **cannot touch KB
content at all** — `kb_authorization_decision` checks cross-tenant isolation
first, Super_Admin's `tenant_id` is `None`, and `assemble_allowed_kb_ids`
returns an empty set for the platform identity by design. So "the super admin
maintains the global library" is not implementable without weakening the tenant
model.

## Decision

The deployment is **single-tenant**, and bootstrap creates its world:

- `_default_legal_tenant_bootstrap` runs after the Super_Admin step and is
  idempotent. It creates the fixed-id default tenant
  (`tenant-legal-default`), an optional tenant admin, and the global statute
  library.
- The tenant admin comes from `LEGAL_TENANT_ADMIN_USERNAME` /
  `LEGAL_TENANT_ADMIN_PASSWORD`. Unlike `SUPER_ADMIN_*`, missing values do
  **not** fail fast — the deployment may instead be set up by hand through the
  admin endpoints; a warning is logged and the library's owner stays empty.
- The global library is found by its `config.is_default_legal_kb` marker rather
  than by name (the name is editable). It is created with
  `chunker_type=laws`, `visibility=organization`, `org_permission=read`, and
  `owner_user_id` pointing at the tenant admin. Same-tenant identities can
  therefore read it with **no authorization exception of any kind**, and the
  owner can maintain it.
- `GET /api/knowledge-bases/legal/global` exposes the library so the `/legal`
  entry can resolve it and render content maintenance directly, instead of
  routing through the knowledge-base list.

## Alternatives considered

**Let the Super_Admin maintain the global library.** Rejected: the current
authorization model denies the platform identity every knowledge base, and the
frontend additionally excludes `/knowledge-bases` from the Super_Admin menu.
Making it work would mean changing a deliberate boundary.

**Add an explicit authorization exception for the platform identity.**
Rejected: it would open Super_Admin visibility over every tenant's content, far
beyond the one library this deployment needs.

**Seed one statute library per tenant.** Rejected: the deployment is
single-tenant, and the downstream names libraries by `kb_id` anyway, so extra
global libraries would only create ambiguity about which one is authoritative.

**Skip the UI and load the library with a script.** Rejected: revisions are a
recurring manual operation, so there has to be a maintenance path a person can
use.

## Consequences

The deployment now requires `LEGAL_TENANT_ADMIN_USERNAME` /
`LEGAL_TENANT_ADMIN_PASSWORD` to be maintainable; without them the global
library exists but is read-only.

Every caller identity must belong to the default tenant, otherwise it cannot
read the global library — this is the premise the single-tenant model rests on,
and it is recorded in the plan's D4.

The sidebar gains one admin-only entry, 法条库, replacing the upstream 知识库
list entry. The plan's decision list still governs which entries exist.

## Testing

The bootstrap path is idempotent by construction (fixed tenant id; library
looked up by marker). Unit tests cover the marker predicate; the full bootstrap
requires a database and is exercised by the deployment, not by the unit suite.
