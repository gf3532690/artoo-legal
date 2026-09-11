# Agent Note: Tenant administrators can issue their own key from the UI

Status: implemented

## Problem

In this deployment the global statute library is writable **only by its owner** — the
tenant administrator created at bootstrap (`LEGAL_TENANT_ADMIN_*`). Anything that needs to
maintain the corpus with that identity, notably the lite admin section, therefore needs a
long-lived credential bound to that user.

There was no way to obtain one from the product:

- the **API Key** page and its menu entry were super-administrator only, and
- the page calls the platform-level endpoints (`GET/POST /api/api-keys`,
  `POST /api/api-keys/external-agent`, `DELETE /api/api-keys/{id}`), which the tenant
  administrator cannot call at all.

Meanwhile the capability already existed on the server: `GET/POST /api/api-keys/me` are
`require_authenticated()`, i.e. any signed-in user can create and list a key bound to
themselves. The only way to reach it was a hand-written `curl` after logging in — a
one-time setup step that has to be copied correctly, and that nobody can discover.

## Decision

Open the existing self-service path in the UI:

- The **API Key** menu entry becomes visible to tenant administrators as well as super
  administrators (a dedicated `apikey` nav group instead of the super-admin-only
  `capability` group).
- The page branches on identity: super administrators keep the platform-level list /
  create / revoke; everyone else uses `GET /api/api-keys/me` and
  `POST /api/api-keys/me`, and instead of a revoke button sees
  "撤销请联系平台管理员".
- No backend change: the two `/me` endpoints already have the right guard, and they
  return the same one-time credential payload (Bearer key + AK/SK pair).

## Alternatives considered

**Leave it as documentation** ("run this curl once after deployment"). Rejected: the
operator ends up pasting a two-step login-and-create snippet against a production host,
and a wrong key silently breaks the lite admin section later. It is also undiscoverable
for whoever takes over the deployment.

**Let tenant administrators use the platform-level endpoints.** Rejected: those issue
*proxy* keys — a platform capability — and list every key in the deployment. Widening
their guard to solve a self-service need would loosen the platform boundary for everyone.

**Ship the maintenance credential as configuration** (e.g. generate a key at bootstrap
and write it to `.env`/a file). Rejected: it freezes an owner-identity credential into
deployment files, with no way for the operator to rotate or revoke it, and it would put a
secret into the offline bundle that nobody can invalidate.

## Consequences

The tenant administrator now obtains a key the same way a super administrator does: two
clicks on a page they already have, with the plaintext shown once. Because the key is
bound to their user, it inherits that user's rights — which is exactly what makes it able
to maintain the global library, and also why it must not be confused with the business
proxy key (that one can only read the global corpus).

Revocation stays a platform action (`DELETE /api/api-keys/{id}` is `require_platform`), so
the page hides the button for non-super-admins rather than offering an action that would
fail. A super administrator can still revoke any key, including these.

## Verification

`npm run build` (tsc + vite) and `npm test` (34 tests) pass in `frontend/`.

Server side is unchanged and already proven: `GET/POST /api/api-keys/me` are
`require_authenticated()`, and creating a key as the tenant administrator has been
exercised against a running deployment (that is where the maintenance credential used
earlier in this session came from).

The new UI branch itself has **not** been clicked through in a browser; the deployment
package must be rebuilt for it to appear (`dist/app-images.tar` predates this change).
