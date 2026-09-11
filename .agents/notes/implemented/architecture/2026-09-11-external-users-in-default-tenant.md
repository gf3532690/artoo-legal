# Agent Note: External users live in the legal default tenant

Status: implemented

Supersedes the "no `external_agent` channel" rationale recorded in the
implementation plan (D21); see [single-tenant-legal-bootstrap](2026-09-10-single-tenant-legal-bootstrap.md)
for the tenant model this builds on.

## Problem

Upstream Artoo integrates third parties with a super-admin **proxy key** plus a
`X-External-User-Id` header: the platform lazily creates one identity per
`(proxy key, external user id)` pair, each owning its own private libraries.
Isolating end users inside the product is the point of that channel.

The single-tenant 法条库 deployment could not use it. A proxy key locks its
identities to the built-in `tenant-external-builtin` tenant, while the global
legal library lives in `tenant-legal-default`, and knowledge-base reads are
hard-isolated across tenants. Measured on a running stack before the change:

| Call with a proxy key | Result |
|---|---|
| `GET /api/knowledge-bases` | 200 with `total=0` — the global library is invisible |
| `POST /api/retrieval/search` with no `kb_ids` | **400** "no global legal library and no scope" |
| Passing the global library `kb_id` explicitly | **404** — cross-tenant isolation |

So the only working external integration was a `user_level` key, which is one
identity for every end user: the product cannot isolate them, and everything
depends on the caller passing the right `kb_ids`.

## Decision

Keep Artoo's proxy-key channel and point external users at the default legal
tenant via a new setting:

```text
EXTERNAL_USER_TENANT_ID = tenant-legal-default   # fork default; upstream is tenant-external-builtin
```

The setting is read in three places — the proxy key row's `tenant_id`, the
lazily created `external_users` row's `tenant_id`, and the synthesized identity's
`tenant_id`. Bootstrap skips creating the built-in external tenant when the
setting points elsewhere.

Nothing else changed: `owner_user_id` already resolves through
`identity.acting_subject_id`, which is `external_users.id` for external users,
so per-user library ownership, isolation and maintenance-by-`kb_id` all come
from the existing design.

## Alternatives considered

**Keep `user_level` keys only (the earlier plan decision).** Rejected as the
sole option: it forces every end user to share one identity, so isolation has
to be reimplemented entirely in the downstream system, and a mistaken `kb_id`
in the request reads another user's library. It stays supported — it is simply
no longer the only way.

**Issue one `user_level` key per end user.** Rejected: the caller would have to
store and rotate N keys, and each new end user needs an admin round trip. The
proxy channel exists precisely to avoid that.

**Make the global legal library visible across tenants.** Rejected: it would
add a cross-tenant exception to the authorization core, which the deployment
deliberately avoids; the whole point of the single-tenant model is that no such
exception is needed.

**Reuse the built-in external tenant and copy the global library into it.**
Rejected: two copies of the authoritative corpus, two sets of ingestion
results, and the "one global library" invariant broken.

## Consequences

Callers can now choose: proxy key + `X-External-User-Id` for per-end-user
isolation, or a user-level key when the downstream computes the library scope
itself. Verified on a running stack with one proxy key and two external user
ids — `alice-001` created a private library in `tenant-legal-default` (owner =
her `external_users.id`), retrieved it merged with the global library, while
`bob-002` saw only the global library and got 404 for `alice`'s `kb_id`.

Costs and caveats:

- External users now live in the same tenant as the tenant administrator and
  the global library. They are `member` role and only ever own private
  libraries, but this is a real widening of who exists inside that tenant
  compared with the built-in external tenant.
- They do **not** appear in user administration (they are `external_users`, not
  registered users), so the tenant's user list is not a complete roster of
  identities.
- The built-in `tenant-external-builtin` row is still present in databases that
  bootstrapped before this change; it is simply unused. Fresh deployments no
  longer create it.
- Anyone syncing from upstream must not revert this setting: pointing external
  users back at the built-in external tenant makes external callers unable to
  read the global legal library at all.

## Testing

Backend: `pytest -q tests/test_tenant_auth_db_properties.py
tests/test_tenant_auth_properties.py tests/test_tenant_auth_integration.py
tests/test_tenant_auth_integration_extra.py tests/test_legal_recall_scope.py
tests/test_retrieval_contract_baseline.py` → 58 passed, 3 failed; the three
failures are pre-existing at the fork baseline and unrelated to this change.
`test_property_7_external_user_namespace` now asserts against the configured
tenant instead of the hard-coded constant.

Live: rebuilt image on the running stack; the proxy key row was created with
`tenant_id=tenant-legal-default`; per-user isolation and global-plus-personal
merging verified with two external user ids (see the consequences above).
