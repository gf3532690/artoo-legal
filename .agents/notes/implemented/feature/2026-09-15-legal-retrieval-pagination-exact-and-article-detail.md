# Agent Note: Retrieval pagination, exact article lookup, and the article detail endpoint

Status: implemented

## Problem

Three items from PRD《法条检索基础API》's feature list were not implemented, and one of
them was exposed by real use rather than by reading the PRD.

**F-006 分页** had no parameters at all, and the response's `total` was "how many
results this call returned" — which reads like a hit count and is not one. Vector
recall has a bounded candidate pool, so a true hit count does not exist to report.

**F-002 法条编号精确检索** was only "ask for it semantically and hope". Measured on
the test environment with `"景德镇制"陶瓷保护条例 第六条`:

```text
top1  第六条       score 0.7457  rerank 1.0598
top2  第二十六条   score 0.7093  rerank 1.0569
top3  第十六条     score 0.6689  rerank 1.0461
```

Only the first result is wanted, and the neighbours are structurally confusable:
`第六条` is a substring of `第二十六条` and `第十六条`, so BM25 matches them, and being
articles of the same statute makes them near-identical to the dense route as well.
The rerank scores differ by 0.3%, so **no threshold can separate them** — the
existing `rerank_threshold` (default 0.2) would not even fire at this magnitude.

**F-201 根据法条ID查询法条详情** had no endpoint, although `article_id` was already
being handed out in every search result.

**F-105 地方性法规优先展示** was implemented as filtering while the PRD says
"优先展示". Both satisfy the PRD's acceptance case, so the wording had to be
resolved before it could be documented.

## Decision

**Pagination is a window over the ranked list.** `page` (default 1) joins the
existing `top_k`; the server fetches `page × top_k + 1` candidates, slices out the
page, and reports `page` / `page_size` / `has_more`. The extra candidate is what
makes `has_more` a fact rather than a guess — a caller asking for the last page gets
`has_more=false` instead of "maybe". `total` keeps its old meaning and the
documentation now says so explicitly. The window is capped at 100 (`page × top_k`
beyond that returns 400) because the rerank candidate pool is bounded
(`rerank_candidate_k`, default 50): promising page 5 of a 20-deep pool would be a
lie.

**Exact mode resolves the article instead of ranking it.** `match_mode` accepts
`semantic` (default) or `exact`. In `exact` the query is parsed for
「法名 + 条号」(Chinese or Arabic numerals, with or without 书名号), and the article
is located by metadata: `law_name` equality first, then "ends with the typed name"
(`民法典` → `中华人民共和国民法典`), always constrained by `article_number`. Hits come
back with `mode="exact"` and `routes=["exact"]`. When the query has no article number,
or the library has no such article, the request **falls back to semantic and says so
in `fallback_reason`** — silent degradation is what made the `law_levels` typo
footgun possible. An unknown `match_mode` is a 400.

**The detail endpoint composes the article from both chunk levels.**
`GET /api/legal/articles/{article_id}` splits `{doc_id}:{article_number}`, resolves
the document, then returns the parent chunk's text as `content` (the full article)
and the matched child as `matched_content`. Article metadata lives only on child
chunks and the parent has none, so both are needed — this is now
`retrieval/article_lookup.py::load_article_rows`, shared with exact mode, because
two implementations of "where is this article" is precisely the drift this codebase
has been bitten by.

**Read authorization became one function.** `authorize_content_read` moved from a
private helper in `api/retrieval.py` into `auth/kb_scope.py` so the detail endpoint
cannot end up with a weaker gate than search — a boundary fixed in only one place
would mean "search can't find it, detail can read it".

**F-105 is filtering.** The PRD elsewhere says 筛选 (F-003, F-004, table 4's remark),
and its own acceptance case passes under either reading, so filtering is kept and
now documented as the intended semantics rather than an accident.

**The retrieval test page exercises the same endpoints integrators do.** The page
used to post to `/api/retrieval/test`; it now posts to `/api/retrieval/search` and
carries a capability switch whose options map one to one onto the real surface —
关键词检索 and 精确检索 both POST `/api/retrieval/search` (differing only in
`match_mode`, with `mode`/`top_k` hidden for exact because it ignores them), and
法条详情 GETs `/api/legal/articles/{article_id}`. The path being called is shown next
to the switch, and a `fallback_reason` from an exact miss is surfaced rather than
swallowed. The two retrieval paths are the same implementation today; a page that
depends on that coincidence would stop proving anything the day it stops being true.

**The page's library picker is multi-select, and it always sends `kb_ids`.** The API has always
accepted a list and switches to the multi-source path when it gets more than one, but the page could
only pick a single library — so the behaviour integrators rely on was the one thing the page could
not reproduce. It now ticks any number of libraries and sends `kb_ids` whether one is selected or
five, so the payload shape does not change with the count. (The global legal library is merged
server-side, so picking a single personal library already exercises the multi-source path.)

**Results expose the `article_id` the detail endpoint takes.** Both retrieval capabilities return it
inside `metadata`, but the page showed everything else about a hit — law name, article number, dates,
status — and not that one field, leaving the raw response as the only place to read it. Each result
now renders it: clicking the id switches to the 法条详情 capability and looks that article up, and a
copy button puts the id on the clipboard.

## Alternatives considered

**A relevance threshold instead of exact mode** (`min_score`, or exposing the
existing `rerank_threshold`). Rejected on measurement: the top three results differ
by 0.3% in rerank score, so any threshold that removes the neighbours removes the
answer too. The threshold also cannot help at all here — the pipeline's soft
threshold is deliberately disabled for the recall API
(`apply_rerank_filter=False`) and its default (0.2) is far below these scores.

**Exact mode as a post-filter over semantic candidates.** Rejected: it keeps the
"did we happen to recall it" failure mode, and a candidate pool that misses the
article would report "not found" for an article that plainly exists. Parsing the
number and querying metadata is deterministic.

**Promising a hit count in `total`.** Rejected: ANN recall over a bounded pool has
no meaningful total. Reporting `has_more` — probed, not estimated — is the honest
contract, and `total` keeps its existing meaning for backward compatibility.

**Returning empty arrays for revision history / related judicial interpretations.**
Rejected: the corpus has neither (they are two of the three deferred data gaps), and
an empty array reads as "this article happens to have none", which is a different
statement from "this service cannot answer that".

**Reusing a semantic search result as the article detail.** Rejected: the article
may not be in the candidate pool, and it would give the caller a truncated
`matched_content` where the full article is available.

## Consequences

The retrieval response gains five fields (`page`, `page_size`, `has_more`,
`match_mode`, `fallback_reason`), all defaulted, so existing callers are unaffected;
`tests/test_retrieval_contract_baseline.py` was updated because pinning the envelope
is its job.

`api/legal.py` is a new router and `article_lookup.py` a new module; `main.py`
registers the router. Retrieval's private `_authorize_and_boundary` is gone, replaced
by `auth/kb_scope.authorize_content_read`.

The field-by-field integration reference for the three endpoints lives in
[`docs/legal-retrieval-api.md`](../../../../docs/legal-retrieval-api.md) (request and
response tables, the `law_levels` enum mapping, `metadata` keys, status codes, and
worked examples). It was checked against the running service's OpenAPI schema — every
documented field exists and no schema field is undocumented.

Exact mode ignores `law_levels` / `province` / `city`: the article number already
pins one article, and layering filters on top would only ever turn a hit into a
miss. This is stated in the field description and the API manual.

**What is not verified yet.** The exact-mode and detail-endpoint SQL paths are
verified at the level of generated SQL and route registration only — there is no
database in this working environment. `metadata->>'article_number' = '16'` and the
`law_name` equality/`LIKE` fallback compile to PostgreSQL correctly and match how
the pipeline writes chunk metadata (numbers as JSON numbers, `law_name` on child
chunks), but they have not been executed against a real library. Both should be
exercised on the test environment once the new image is deployed, together with the
`top_k=1`-vs-`exact` comparison on a real 法名+条号 query.

F-103 (仅返回现行有效) is **not implemented, by decision** (2026-09-15). The
`validity_status` enum is defined by the upstream data source, and that definition is
not available — the documents carry a bare integer with no label (29,957 of them, all
from `source_code=national_laws`) — so under the project's "do not build what the raw
data does not contain" rule it is dropped rather than guessed. The reading inferred
from the corpus (`3` in force, `4` not yet effective, `2` superseded, `0` repealed,
`1` likely repealed, `-1` unlabelled) is recorded in the ingest checklist for
reference only and is explicitly not part of any contract. Consequently the article
verification capability in PRD table 4 stays a usage of `/search` with `top_k=1`
instead of becoming its own endpoint — its only distinguishing feature was the
effectiveness filter, and that is now out of scope.

## Testing

`tests/test_legal_retrieval_api.py` (24 cases) pins query parsing (Chinese and
Arabic numerals, with and without 书名号, and the "no article number" case),
pagination window arithmetic and slicing including the last page and the over-cap
rejection, the 400 on an unknown `match_mode`, `article_id` splitting including
malformed inputs, and that the new route plus all new request/response fields are
present in the OpenAPI schema. The retrieval contract baseline test was updated for
the envelope extension. Together with the neighbouring retrieval, region and loader
suites this is 177 passing tests.

`frontend/src/pages/Retrieval.test.tsx` pins the page's wiring to those endpoints:
the payload each capability hands to the API layer (semantic with `mode`/`top_k`,
exact with `match_mode` only, detail by `article_id` and without a knowledge base),
that selecting two libraries hands over `kb_ids` with both, and that clicking a
result's `article_id` calls the detail endpoint with it; it also pins that the
article-detail response renders. A future refactor that quietly sends the page back
to a private path fails the suite. All the frontend tests pass and `npm run build`
succeeds.
