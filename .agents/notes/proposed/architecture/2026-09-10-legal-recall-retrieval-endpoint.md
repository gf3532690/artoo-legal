# Agent Note: Legal recall reuses the existing retrieval endpoint

Status: proposed

## Problem

The 法条库 deployment must always search the global statute library in addition
to whatever personal libraries the caller names. Upstream `POST
/api/retrieval/search` requires the caller to name a retrieval scope and returns
`400` otherwise, so a caller that only wants the global library has nothing to
pass. The deployment is also single-tenant, which changes what authorization
work is actually needed here.

## Decision

The legal recall service is the existing endpoint, not a new one:

- `_run_retrieval` merges the global statute library into the resolved scope
  after `resolve_kb_ids()` and before the authorization pass, so the global
  library is authorized like any other source. No exception to the tenant model
  is added.
- A request that names no scope now returns the global library's results instead
  of `400`.
- `top_k` keeps its name and its field semantics; this deployment's default
  changes from `10` to `5`.
- `_build_result_items` gains `law_name`, `article_number`, `article_label`,
  `chapter` and `source` (`global` / `personal`) inside the existing `metadata`
  dict. The response models are untouched.

## Alternatives considered

**Add a dedicated `/api/retrieval/legal` endpoint.** Rejected: it would split one
contract into two shapes for the same retrieval semantics, and the Open API
would then have to describe both while documenting that they behave identically.

**Add a `cross_tenant_kb_ids` exception so the global library is readable across
tenants.** Rejected: the deployment is single-tenant, so the global library and
the caller live in the same tenant and `organization` + `read` already grants
the access. The exception would have been the highest-risk change in the plan
for no benefit.

**Add a precise citation filter (`legal_filters` with law name and article
number).** Rejected for this release: retrieval is defined as semantic-first, and
exact law-name matching fails silently when the caller writes a short name
(「劳动合同法」 against a stored 「中华人民共和国劳动合同法」). Result metadata
lets the caller judge matches instead.

**Give the deployment its own response envelope.** Rejected: reusing the
existing envelope keeps the two deployments' contracts aligned and lets
downstream code move between them.

## Consequences

Two behaviors change for existing callers of this endpoint: the no-scope `400`
disappears, and the default result count drops to five. Both are recorded in
`artoo-open-api.md` section 0.

Because the global library makes every call multi-source, `mode=direct` is no
longer reachable whenever the caller passes a personal library, and `trace`
becomes `null` on those calls. This matches upstream's documented behavior for
multi-source retrieval rather than introducing a new rule.

Result hydration costs one extra batched query against `Chunk`, which is
negligible at `top_k = 5`.

## Testing

Tests assert that a request naming no scope reaches the global library, that
`top_k` defaults to five, that result `metadata` carries the legal fields and a
`source` marker, and that the existing envelope keys are unchanged.
