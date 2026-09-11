# Agent Note: Seed the two downstream API keys from configuration

Status: implemented

Related: [single-tenant-legal-bootstrap](../architecture/2026-09-10-single-tenant-legal-bootstrap.md)
(the tenant, admin and global library this attaches to).

## Problem

This deployment is two applications: the legal library here, and the downstream
lite backend that embeds it. Lite needs **two credentials** to work:

1. a **business proxy key** (`external_agent`) — lite sends
   `Authorization: Bearer <key>` plus `X-External-User-Id` for every personal
   library operation and for `/api/retrieval/search`;
2. a **user-level key of the global library's owner** — the write rule is
   owner-based, so the tenant administrator's key is the only thing that can
   maintain the global library, which is what lite's admin UI does.

Both used to be created by hand: log in as the super administrator, issue a proxy
key; log in as the tenant administrator, issue a key; paste both into lite's
config. Three failure modes came out of that:

- it has to be redone **every time the data volume is recreated** (a fresh
  deployment, or the "stop Artoo and deploy the legal library" cutover);
- the failure is silent on the legal-library side — lite just starts returning
  401 and nothing in the legal-library logs says why;
- the proxy key's identity namespace is `(api_key.id, X-External-User-Id)`
  (recorded in the implementation plan §15.8), so a hand-issued key is a
  document that has to survive the cutover or every user's personal library
  becomes unreachable.

## Decision

Two optional settings seed the keys at bootstrap, idempotently:

| Setting | Seeded key | Downstream config |
|---|---|---|
| `LEGAL_BOOTSTRAP_PROXY_API_KEY` | `external_agent`, tenant pinned to `EXTERNAL_USER_TENANT_ID` | `legal-kb.api-key` |
| `LEGAL_BOOTSTRAP_ADMIN_API_KEY` | `user_level`, bound to the default tenant administrator | `legal-kb.admin-api-key` |

`key_hash` is an unsalted SHA-256, so a key with a known plaintext can be seeded
directly; the database still stores only the hash, and the plaintext stays in the
env file. Leaving both empty skips the whole step, which is the upstream Artoo
shape (keys issued from the UI).

Three sub-decisions are load-bearing:

1. **The seeded key's `id` is derived with `uuid5(kind, plaintext)`, not a random
   `uuid4`.** For a proxy key the id *is* the identity namespace prefix, so a
   random id would orphan every personal library that was created before the
   restart — the exact hazard §15.8 warns about, except self-inflicted on every
   deploy. `uuid5` is not invertible, so the id (which is also the AK of the
   signing channel) can be public without leaking the key.
2. **Existing rows are matched by `key_hash`, and a revoked key is never
   revived.** An operator who revokes a seeded key meant it; silently bringing it
   back on the next restart is harder to diagnose than a 401. The bootstrap logs
   a warning naming the consequence instead.
3. **A configured value shorter than 16 characters fails startup.** These
   settings are written by hand into an env file, and the only symptom of a typo
   is a downstream 401 far away from the cause.

## Alternatives considered

**Keep issuing keys from the UI and document the steps better.** Rejected: the
cutover explicitly wipes the volume, so the documented steps have to be executed
every time, and one of the two keys cannot be re-issued by the same identity as
the other.

**Have lite provision its own keys by calling the API with administrator
credentials.** Rejected: it would put an administrator password or JWT in the
downstream application, and it makes lite able to mint credentials for the legal
library — a much larger trust surface than "here is the key I was given".

**Derive both keys from `JWT_SECRET` instead of storing them in the env.** 
Rejected: rotation would be coupled to the JWT secret, and an operator cannot
tell lite's key apart from any other derived value when debugging.

**Auto-create the keys on first use and print the plaintext to the logs.**
Rejected: a credential that appears in logs is a credential that lives in log
archives.

**Reuse one key for both roles.** Rejected: the global-library write rule is
owner-based; a proxy key resolves to an external user identity and is refused
with 403, so one credential cannot do both jobs.

## Consequences

A fresh deployment (or one restored from an empty volume) comes up with lite
already able to call it, provided the two env values match lite's config. The
keys remain ordinary rows: they show up in the API key list, count usage, and can
be revoked. Their plaintext now lives in the deployment's env file, so they are
long-lived shared secrets — rotating one means changing the env and the
downstream config together, and the old key stays valid until it is revoked in
the UI.

The identity-namespace stability only holds for a given plaintext: issuing a
*different* key value produces a different namespace and therefore a different
set of external users, exactly as a hand-issued replacement would.

The operator-facing statement of this lives in `deploy/DEPLOY.md` §4 (账号与
API Key) and §6 (全局法条库与个人法条库), which ships inside the offline package.

## Verification

Not run at runtime. The change was checked statically (`ast.parse` on the edited
modules) and `docker build -t artoo-backend:legal ./backend` succeeds; the local
stack was stopped before any restart, so no key was seeded and no request was
made with one. The implementation plan §15.10 lists the expected behaviour and
the two `curl` probes (one per key) that confirm it after startup.
