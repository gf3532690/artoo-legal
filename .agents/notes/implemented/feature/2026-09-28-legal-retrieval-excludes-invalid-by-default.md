# Agent Note: Retrieval excludes repealed and lapsed statutes by default

Status: implemented

English | [中文](2026-09-28-legal-retrieval-excludes-invalid-by-default.zh.md)

> **Supersedes** [Retrieval reports validity status instead of filtering on it](2026-09-16-legal-retrieval-reports-status-instead-of-filtering.md),
> and restores the default first recorded in [Recall excludes repealed and lapsed statutes by default](2026-09-15-legal-exclude-repealed-by-default.md).

## Problem

The retrieval endpoint returned all six validity values and left the choice to the caller. That is a
fair contract for a client with legal staff; it is the wrong default for the third-party integrations
this Open API exists for. "Filter it yourself" costs every integrator a reading of the enum before
their first query — and this enum has already been read backwards once (`0` is 未标注, `-1` is 已失效),
which is why the label ships alongside the raw value. A repealed text handed back for a
current-law question is a wrong answer, not a preference the caller failed to express.

The 2026-09-16 decision had one solid point: filtering is only fair once the caller can *see* the
status. That stays — the status and its label are still sent on every result. What changes is which
way the default leans.

## Decision

Retrieval drops 已废止 (`1`) and 已失效 (`-1`) by default. The other four values — `3` 现行有效,
`2` 已修改, `0` 未标注, `4` 尚未生效 — are returned as before.

- `include_invalid: bool = False` lifts the exclusion, for historical questions ("行为时法").
- `filtered_invalid_count: int = 0` reports how many results the default dropped, so a short page is
  explained instead of mysterious.
- The candidate fetch is oversampled 4× while the filter is on, bounded by `_MAX_PAGINATION_WINDOW`
  (100). The invalid pair is ~11.9% of the corpus, so without oversampling a page can come back
  short.
- `validity_status` and `validity_status_label` are still sent on every result: a caller that opts
  out of the filter can still narrow further itself.
- `match_mode=exact` and `GET /api/legal/articles/{article_id}` are point lookups by article number
  and ignore the switch. Asking for one article must return it whatever its status.
- A missing `validity_status` counts as valid: reading no status is not evidence of repeal, and
  dropping a result on absent metadata is worse than returning one too many.

## Alternatives considered

**Keep returning everything and document "filter client-side"** (the 2026-09-16 decision). Rejected:
the default then *is* the answer for every caller that does not read the docs, and the cost of the
wrong default falls on the integrating application rather than on the service that knows the enum.

**Filter down to 现行有效 (`3`) only.** Rejected: `0` 未标注 is mostly repeal/amendment decisions —
documents that are themselves in force; `4` 尚未生效 is a new version a caller may be asking about;
`2` 已修改 is superseded but was the law at some point. Only the two values that state "this text has
no legal effect" are safe to hide by default.

**Apply the filter to `match_mode=exact` and the article-detail endpoint too.** Rejected: those are
point lookups by article number. Returning "not found" for an article that exists is a worse failure
than returning it with its status attached, and the caller already named the article.

**Push the filter into Milvus as a scalar field (pre-filtering) instead of post-Recall filtering.**
Rejected for this change: it is the right long-term shape — filtering before recall removes the
oversampling and the short-page problem entirely — but it requires rebuilding the collection, which
is a separate, heavier operation. Recorded here as the known proper fix.

**Filter by default but omit `filtered_invalid_count`.** Rejected: the 2026-09-16 note's second
objection was that a filtered page cannot be told apart from a page the query genuinely under-filled.
The count answers that directly, and it is one integer.

## Consequences

One request field and one response field return to the wire, having been removed on 2026-09-16.
Callers that never sent `include_invalid` will now receive fewer results for queries that previously
matched repealed or lapsed texts — that is the intended change, and `filtered_invalid_count` explains
the gap. Callers that need the old behaviour send `include_invalid: true`; the status fields they may
have come to rely on are unchanged.

Latency cost: while the filter is on, each semantic query fetches up to 4× the page window (capped at
100) before filtering, and rerank sees that larger candidate set. The 2026-09-16 note refused this
trade; it is accepted here, because a repealed statute in a current-law answer costs more than the
candidates do. The oversampling disappears once the filter moves into the vector store.

The document list keeps its own explicit `validity_status` filter and its badge: a browse surface
filters because the user asked it to, which was never the disputed part.

## Testing

`tests/test_legal_retrieval_api.py::TestValidityStatusFiltering` pins the default, the two dropped
values, the four kept values, and that a missing status counts as valid;
`TestRoutesAreRegistered` asserts both fields are present in the OpenAPI schema again.
`tests/test_retrieval_contract_baseline.py`'s response-envelope key set gains
`filtered_invalid_count`.

Verification run in this change: `python -m compileall app` (passed). The pytest suite was **not**
run here — the repository had no backend virtualenv available at the time — so the assertions above
describe the tests as written, not an observed green run.
