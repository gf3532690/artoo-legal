# Agent Note: Drop the home page and give retrieval test to the corpus maintainer

Status: implemented

Supersedes the landing-route decision in
[legal-home-page](../feature/2026-09-11-legal-home-page.md), which superseded
[legal-ui-surface-alignment](../architecture/2026-09-11-legal-ui-surface-alignment.md).

## Problem

The previous round introduced `/home` to stop logins from landing on the global statute
library (a maintenance action). The page itself only renders a few entry cards — every one
of them duplicates a left-hand menu item — so the menu gained a level of navigation that
carries no information.

Meanwhile the page that *is* useful right after login was hidden from the people who need
it: **检索测试** (`/retrieval`) is the only place to check that the corpus actually
retrieves — type a query, see the matched `law_name` / `article_label` and the per-route
trace, which is how you diagnose "I uploaded this statute, why doesn't it come back". It
sat in the super-admin-only capability group, but the super administrator is by definition
the platform-assembly identity that does not touch content; the tenant administrator is
the one who maintains the corpus and needs to verify it.

## Decision

- **Delete the home page**: `pages/Home.tsx`, its route, its menu entry and its
  super-admin whitelist entries.
- **Open 检索测试 to tenant administrators** as well: it moves out of the
  super-admin-only `capability` group into its own group visible to super administrators
  and tenant administrators.
- **Landing**: super administrator → 租户管理 (as before), tenant administrator →
  检索测试.

No backend change is needed: `POST /api/retrieval/test` and the knowledge-base list it
uses behind the page are both `require_authenticated()`, and a tenant administrator can
read the global library (organization + read).

## Alternatives considered

**Keep the home page.** Rejected: it is a second navigation layer whose entire content is
links that the sidebar already offers.

**Keep 检索测试 super-admin-only.** Rejected: it is a corpus-verification tool; the
identity that assembles the platform does not maintain the corpus, so hiding it from the
maintainer leaves the deployment without any way to check recall except calling the API by
hand.

**Land the tenant administrator on the global library (the pre-home behaviour).**
Rejected for the same reason the home page existed: it drops the user straight into a
maintenance action.

## Consequences

One page fewer. A tenant administrator's first screen is the retrieval test, which is a
reasonable "what does this deployment do / does my corpus work" entry point, and their
menu is: 法条库 / 检索测试 / API Key / 用户管理 / 审计日志. A super administrator keeps
租户管理 as its landing.

Members still see no menu entries — unchanged by this decision (the sidebar has only
tenant-admin and platform surfaces in this product line).

## Verification

`npm run build` (tsc + vite) and `npm test` (34 tests) pass in `frontend/`.

The UI was not clicked through in a browser; the change is visible only after the
deployment package is rebuilt.
