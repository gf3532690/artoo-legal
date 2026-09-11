# Agent Note: A home page, and the legal library inside the app shell

Status: implemented

Supersedes the landing-route part of [legal-ui-surface-alignment](2026-09-11-legal-ui-surface-alignment.md)
("point the non-super-admin landing at `/legal`").

## Problem

Two problems, one of them invisible until someone walks the flow:

1. After login the deployment dropped the user straight into **全局法条库** — the
   content maintenance page. That is an *action* (upload/delete statutes), not a
   place; landing there assumes the first thing a user wants to do after logging
   in is edit the corpus.
2. `/legal` was registered as a **top-level route, outside the `<Layout>` route**.
   It was introduced that way when the entry was added, so opening the legal
   library rendered the page **without the sidebar** — and without `RequireAuth`.
   From the menu the user could get in, but the only way out was the browser's
   back button or editing the URL.

## Decision

- Add a **home page** (`/home`) that acts as the post-login landing: it states
  what the deployment is and lists the entries the current identity can reach
  (global legal library for tenant admins, retrieval test / Embedding / API keys
  / tenants for super admins, users / audit logs as appropriate). It is a
  position, not an action.
- `DefaultLanding` now redirects every authenticated identity to `/home`
  (previously: super admin → `/tenants`, everyone else → `/legal`).
- Move **both** `home` and `legal` under the `<Layout>` route so they render
  inside the app shell with `RequireAuth` and the sidebar. This is the fix for
  problem 2; the landing change alone would have hidden it rather than fixed it.
- Add a `首页` sidebar entry visible to every authenticated identity, and let
  `/home` through the super-admin path guard (`SUPER_ADMIN_MENUS` /
  `SUPER_ADMIN_ALLOWED_PATHS`) — otherwise the guard would bounce super admins
  off the new landing back to tenant management.

## Alternatives considered

**Keep `/legal` as the landing page.** Rejected: it is a maintenance action, and
it leaves the missing-shell bug in place for anyone who opens the page.

**Land super admins on tenant management and tenant admins on the legal
library (the previous state).** Rejected: two different landings for one
product, and the tenant-admin one is still the maintenance page.

**Give `/legal` its own layout wrapper instead of moving it inside `<Layout>`.**
Rejected: `<Layout>` *is* that wrapper — a second one would duplicate the
sidebar, the artifact panel and the account menu.

**Keep the top-level route and rely on the menu to navigate back.** Rejected:
the menu is rendered by `<Layout>`; a page outside it has no menu.

## Consequences

The first screen after login is an overview rather than a form. `/legal` now
behaves like every other authenticated page: sidebar present, `RequireAuth`
enforced, artifact panel closed on route change. The home page filters its cards
by the same role model as the sidebar, so the two can not drift into
contradiction without touching one file.

Nothing changed on the API side: the home page only links.

## Verification

`npm run build` (tsc + vite) and `npm test` (34 tests) pass in `frontend/`.

The `artoo-frontend:legal` image was rebuilt and the container restarted on the
running stack; `/home`, `/legal` and `/` all return the SPA, and the served
bundle contains the new home-page copy (`首页`, `对外检索接口`), so the running
deployment is the new build rather than a cached one.
