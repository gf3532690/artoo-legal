# Agent Note: Legal result metadata exposure

Status: implemented

## Problem

The field dictionary note decided that the version, provenance and region fields
stay in PostgreSQL `chunk_metadata` and never reach the wire: retrieval returned
only `law_name` / `article_number` / `article_label` / `chapter` plus `source`
(`api/retrieval.py::_LEGAL_KEYS`). That was right while retrieval was
semantic-only and nothing consumed the fields.

The 法条检索基础API PRD changes the requirement: results must carry 效力层级
(`law_type`) so callers can filter and display it, must carry the publication and
effective dates plus validity status so callers can judge which version they are
looking at, and must carry the region so local regulations can be shown and
checked against a requested province or city. All of that data is already stored
per chunk; only the exposure was missing.

## Decision

`_LEGAL_KEYS` in `api/retrieval.py` grows from four keys to eleven:
`law_name`, `article_number`, `article_label`, `chapter`, `law_type`,
`issuing_authority`, `publish_date`, `effective_date`, `validity_status`,
`province`, `city`. `_build_result_items` also derives `article_id` as
`"{doc_id}:{article_number}"` when an article number exists, giving callers a
stable handle for a specific article without a new persisted field; documents
without article structure (修正案 / 决定) get no `article_id` rather than a fake
one.

The response model is unchanged: everything still rides in the existing
`metadata` dict of `RetrievalResultItem`, and the envelope keeps its fields. The
existing convention is preserved — a key whose value is missing is **absent**,
not `null`, so clients must treat the keys as optional.

`artoo-open-api.md` section 0 records the expanded field list, and section 6.1 of
`docs/legal-recall-implementation-plan.md` records the change from "not exposed"
to "exposed" with a cross-link to this note.

## Alternatives considered

**Keeping the fields private and making the PRD's consumers read the database.**
Rejected: the consumers are the search front end and an upstream LLM application;
neither has database access, and the PRD's acceptance items (results show
效力层级; local regulations are verifiable against a requested region) are about
the API response.

**Adding the fields as top-level response fields instead of `metadata`.** Rejected:
the response model and its envelope are a published contract, and `metadata` is
the slot the previous change deliberately reserved for legal fields.

**Exposing the fields but omitting `article_id` until a detail endpoint exists.**
Rejected: `article_id` is derivable with no storage cost, and callers need a
stable per-article handle as soon as results start carrying version and region
information.

**Emitting `null` for missing values so the key set is stable.** Rejected: it
contradicts the existing hydration convention for every other optional field
(`chapter`, `article_label`), and would force every client to distinguish "absent
because the field does not apply" from "absent because the row predates the
field".

## Consequences

Responses are larger: each result can carry up to eleven metadata keys instead of
four. The values are copied from `chunk_metadata`, which is already hydrated in
one batched query, so the cost is payload size rather than extra queries.

Downstream clients must tolerate absent keys — the contract now documents that
explicitly. Anything that previously assumed the metadata dict had exactly four
legal keys (for example a strict schema on a typed client) needs updating.

`law_type` is exposed as the raw corpus category (12 values, including
`修改、废止的决定` and `法规性决定`), not as a normalised hierarchy: mapping those
to the PRD's three levels is a filter-side concern and stays in the application
layer so it can change without re-ingestion. `validity_status` is likewise
exposed as the raw integer and still is; what changed is that the enum is no
longer undocumented. It has since been obtained from the data source's own
dictionary — `3` 现行有效 / `2` 已修改 / `1` 已废止 / `-1` 已失效 / `4` 尚未生效 /
`0` 未标注 — and
[Recall excludes repealed and lapsed statutes by default](../feature/2026-09-15-legal-exclude-repealed-by-default.md)
now interprets two of its values. The recommendation to consumers is unchanged in
shape: read the raw integer, but read its definition from that note rather than
guessing.

## Testing

`tests/test_retrieval_legal_metadata.py` stubs the two hydration queries and pins
the exposure: the PRD fields are copied through, `article_id` is derived from
`doc_id` and the article number, article-less documents get no `article_id`,
region fields come through for local regulations, and missing values leave the
key absent. `tests/test_retrieval_contract_baseline.py` continues to pass, so the
response envelope and request semantics are unchanged.
