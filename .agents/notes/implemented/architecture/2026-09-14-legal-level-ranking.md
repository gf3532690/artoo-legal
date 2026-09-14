# Agent Note: Legal hierarchy weighting in rerank

Status: implemented

## Problem

The PRD's acceptance item is explicit: with no hierarchy filter given, results of
the national-law level must rank above judicial interpretations. Retrieval
previously ranked purely by relevance — RRF fusion, then the reranker, then the
existing composite score (`composite_rerank_weight` / `_base_weight` /
`_source_weight`), which mixes the rerank score with the RRF score and a
position prior and knows nothing about legal hierarchy.

Two mechanical obstacles had to be solved first:

- the reranker truncates to `top_k` **itself**, so a national statute sitting at
  rank `k+1` could never enter the final list no matter how it was scored
  afterwards;
- `law_type` was not part of the Milvus query projection, so the retriever had no
  hierarchy signal at ranking time.

## Decision

`retrieval/legal_level.py` owns the level vocabulary — the filter enum
(`LEGAL_LEVEL_TYPES`, moved here so filtering and ranking share one definition)
plus `LEGAL_LEVEL_TIERS`, a `[0, 1]` ladder: 宪法 `1.0`, 法律 / 法律解释 / 修正案
`0.9`, 法规性决定与重大决定 `0.85`, 行政法规 `0.8`, 监察法规 `0.75`, 司法解释
`0.7`, 地方法规 `0.6`. 「修改、废止的决定」exist at both tiers, so their tier is
`0.85` without a province and `0.6` with one.

`HybridRetriever._rerank` multiplies each score by `(1 + w × tier)` where `w` is
the new retrieval config `legal_level_weight` (default `0.1`, range `0..1`,
`0` disables). When `w > 0` it asks the reranker for more candidates
(`max(top_k × 2, top_k + 5)`) and truncates to `top_k` itself after re-sorting, so
the boost can actually promote a lower-ranked higher-tier hit; when `w == 0` the
fetch size and every code path stay exactly as before.

`law_type` and `province` were added to `_OUTPUT_FIELDS` (a query-side projection
only — no schema change) and to the metadata the three sub-retrievers build.

## Alternatives considered

**Applying the boost only in `_apply_composite_scoring`.** Rejected: that runs
after the reranker has already truncated to `top_k`, so it can only permute the
survivors. The PRD's acceptance case needs promotion, not permutation.

**Exposing raw `law_type` in the ranking code and keeping tiers there.**
Rejected: filtering and ranking would then hold two copies of the taxonomy; the
shared `legal_level.py` keeps one.

**Making the weight large by default (e.g. `0.5`).** Rejected: the PRD asks for
"综合排序" (combined ranking), not hierarchy-dominant ordering. At `0.1` the
boost is about two percentage points between 法律 and 司法解释 — enough to break
a near-tie, not enough to float an irrelevant statute above a clearly better
interpretation. Deployments that want stronger hierarchy can raise the value.

**Only widening the candidate pool for legal corpora.** Rejected as unnecessary:
the extra fetch is a no-op for non-legal corpora because the boost requires
`law_type`, which they do not have — the ladder simply does not apply. (The fetch
is still widened whenever `w > 0`, which costs nothing extra at the rerank
service: it scores all candidates regardless of how many it returns.)

## Consequences

This is a **default behaviour change** for the legal deployment: results of the
same query may come back in a different order than upstream Artoo's. It is
recorded in `artoo-open-api.md` section 0 and in the plan's retrieval-defaults
table.

Non-legal knowledge bases are unaffected (no `law_type` → no tier → no
adjustment), which keeps the shared retrieval path safe for Artoo.

Adding a retrieval-config field is not a one-file change: this needed the spec
entry, the `RetrievalConfig` field, `RetrievalConfigSection` and
`RetrievalConfigUpdate` in the system-config API, a `RetrievalConfigRow` column
and an `ALTER TABLE` entry, plus the field-set assertion in
`tests/test_retrieval_config.py`. The weight is therefore tunable per deployment
through the existing config API (`legal_level_weight`, `0` disables).

The ladder is a maintained artefact: a new corpus category needs a tier entry,
and the national/local split for 「修改、废止的决定」 still relies on `province`
because the corpus records only the category.

## Testing

`tests/test_legal_level_rank.py` stubs the reranker and pins the behaviour: the
ladder's ordering, the national-vs-local decision tier, `w=0` preserving pure
relevance order and the original fetch size, a near-tie being resolved by
hierarchy, a clearly better relevance score still winning, a lower-ranked
statute being promoted when the pool is widened, and results without `law_type`
being untouched.

`tests/test_milvus_legal_fields.py` now asserts the schema declaration
(`LEGAL_FILTER_FIELD_LENGTHS`, scalar indexes, and that `_build_fields` consumes
that declaration) instead of building `FieldSchema` objects — the suite contains a
test module that injects a fake `pymilvus` through `sys.modules`, which made the
runtime assertion order-dependent. The real schema was verified against the
running Milvus with `describe_collection`.
