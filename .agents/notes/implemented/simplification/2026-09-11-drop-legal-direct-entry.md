# Agent Note: Drop the legal-library direct entry; the backend keeps a library list

Status: implemented

Supersedes the `/legal` entry described in
[single-tenant-legal-bootstrap](../architecture/2026-09-10-single-tenant-legal-bootstrap.md)
and the route move in [legal-home-page](../feature/2026-09-11-legal-home-page.md).

## Problem

The direct entry (`/legal` — resolve the global legal library, then render its
document page) was written when the assumption was "this deployment has exactly
one library, so the entry can pick it for the user". Two things are wrong with
that:

1. The backend can hold **more than the global library** — a tenant
   administrator may also own personal libraries, and an external user's
   libraries show up in the same list. Auto-selecting one hides *which* library
   is on screen: the menu item, the breadcrumb and the URL all look the same as
   any other library.
2. It was a **bypass around `/knowledge-bases/:id`**. That forced
   `Documents.tsx` to grow an `explicitKbId` prop whose only caller was that
   route, i.e. a second way to answer "which library am I editing?".

The confusion is asymmetric: on the lite side (`law-agent-lite-application`,
which this library backs) a user has exactly **one** personal library whose name
is created and locked by the backend, so "menu → contents" is the only sensible
shape there. That decision lives in the other repository; this note records why
the two ends deliberately differ.

## Decision

- Delete `frontend/src/pages/LegalLibrary.tsx` and its `/legal` route.
- Point the `法条库` sidebar entry back at `/knowledge-bases`. From there the
  administrator picks a library and lands on `/knowledge-bases/:id`.
- Delete `knowledgeBaseApi.getGlobalLegal` (the only consumer of the endpoint
  was the deleted page).
- Delete the `explicitKbId` prop from `Documents.tsx`; the library id comes from
  `useParams().id` again, with no second source.

The backend keeps `GET /api/knowledge-bases/legal/global`: it is the stable
contract for "resolve the global legal library", referenced by the backend test
suite, and costs nothing to keep.

## Alternatives considered

**Keep the direct entry and add a library switcher in its header.** Rejected: it
reimplements the library list inside one library's page, and the list already
exists one click away.

**Keep `/legal` but redirect to the library list when the identity owns more
than the global library.** Rejected: same screen behaves differently depending
on invisible state, which is the confusion we are removing.

**Delete the `/api/knowledge-bases/legal/global` endpoint too.** Rejected: the
backend tests exercise it, and dropping it would remove the only endpoint that
answers "which library is the global one" without a name convention (file names
are explicitly not authoritative, library names are user-editable).

**Make the lite menu go to a library list as well, for symmetry.** Rejected: a
lite user owns exactly one library, so the list is a screen with one row.

## Consequences

Maintaining the corpus in the backend costs one extra click (menu → list →
library). In exchange, "which library am I looking at" is always visible, and
`Documents.tsx` has a single source for its library id. No API, schema or
storage change; the menu label, icon and visibility are untouched.

The lite side keeps landing directly on the personal library's file list, so the
two ends no longer share one rule — this note is the place that records why.

## Verification

`npm run build` (tsc + vite) and `npm test` (34 tests) pass in `frontend/`.

The `artoo-frontend:legal` image was rebuilt and the container restarted on the
running stack; `/` and `/knowledge-bases` return the SPA, and `/legal` is no
longer a route.
