# Agent Note: Recall excludes repealed and lapsed statutes by default

Status: implemented

## Problem

Two problems met here. The library knowingly contains documents whose
`validity_status` says they are no longer in force — of the 22,036-document ingest
list, **2,581 are 已废止 (`1`)** and **36 are 已失效 (`-1`)**, 11.9% together. With no
default filter, a query about a subject a repealed statute covered can come back
with the repealed text as if it were current law.

F-103 of the PRD ("仅返回现行有效") had been dropped precisely because the enum's
definition was unavailable: the documents carry a bare integer with no label, all
29,957 from `source_code=national_laws`. The note that recorded the raw-field
decision is [`2026-09-14-legal-result-metadata-exposure.md`](../architecture/2026-09-14-legal-result-metadata-exposure.md),
which states that `validity_status` is uninterpreted and that the meaning is the
producer's to define; [`2026-09-14-legal-version-provenance-fields.md`](../architecture/2026-09-14-legal-version-provenance-fields.md)
likewise rejected interpreting the value on the grounds that the definition would
have to be guessed. **The definition has since been obtained from the data source's
own dictionary**, so that premise no longer holds and this note supersedes it. Both
notes now cross-link back here.

## Decision

The enum is: `3` 现行有效 / `2` 已修改 / `1` 已废止 / `-1` 已失效 / `4` 尚未生效 /
`0` 未标注.

Retrieval now **excludes `1` and `-1` by default**. The request gains `include_invalid`
(default `false`) which lifts the exclusion, and the response gains
`filtered_invalid_count` so a caller can tell why a page came back short.

The boundaries are deliberate:

- **Only the two values that mean "no longer in force" are excluded.** `0` (未标注) is
  overwhelmingly decision documents — 1,844 of 2,031 are `law_type=修改、废止的决定`,
  which are themselves valid instruments — and `4` (尚未生效) is a version that takes
  effect shortly. Silently dropping either would remove answers the caller wants.
- **A missing `validity_status` counts as valid.** Not reading a field is not the same
  statement as a statute having lapsed; when in doubt the API returns the hit.
- **Only semantic recall is filtered.** `match_mode=exact` and the article-detail
  endpoint are "name the one you want" paths and keep returning repealed articles,
  with the status still in `metadata`. Filtering them would turn a deliberate lookup
  into a confusing `404`/fallback.
- **Ingestion is unchanged.** The instruction was explicit: ingestion is not affected,
  the filter belongs at query time. Retaining the full set also keeps the ability to
  ask what the law said when an act was committed, which is the reason not to look for
  a way to "fix" this at ingest time later.
- Filtering happens **after** recall because `validity_status` is not a Milvus scalar
  field. The API therefore oversamples candidates by ~4× (the repealed share is ~12%)
  so a page usually still fills; a caller can still get fewer than `top_k` when a
  query's candidate pool is dominated by repealed text. True pre-filtering needs the
  field added to the collection, which requires a rebuild.

## Alternatives considered

**Excluding the 2,617 documents at ingest time.** Rejected: the instruction was
explicitly that ingestion stays as-is, and retaining the documents preserves the
ability to answer what the law said when an act was committed. It would have saved
~12% capacity, so the option stays on the table if that need ever disappears.

**Leaving the filtering to callers.** Rejected: the default would remain wrong (a
legal search that returns repealed text as current law), and every integrator would
have to reimplement the same predicate against a raw integer.

**Also excluding `2` (已修改) and `4` (尚未生效).** Rejected: `2` is the newest version
the library actually holds for 159 laws — dropping it removes those laws entirely —
and `4` is a law that will be in force shortly, which a caller may legitimately be
asking about.

**Adding `validity_status` to Milvus as a scalar field now.** Deferred, not rejected:
it is the correct long-term design (true pre-filtering, exact pagination), but the
collection must be rebuilt, and the rebuild must not collide with the full ingest
currently in flight. It is the natural companion of the next rebuild window.

**A status whitelist parameter instead of a boolean.** Recorded as the follow-up. It
is what "only `3` 现行有效" needs — the boolean cannot express it — but the boolean
covers the decided口径 with one field, and the surface grows only when a caller asks.

## Consequences

The response envelope gains `filtered_invalid_count`; the contract baseline test was
updated for it, as it was for the pagination fields.

This is a **default behaviour change** for any caller who was relying on repealed
statutes appearing in results; they must now pass `include_invalid=true`. The enum is
also no longer "uninterpreted", so the two notes linked above are superseded on that
point — the raw integer is still what the API returns, but its meaning is now documented
rather than left to the caller.

The ingest-side selection rule (prefer `validity_status == 3` within a law-name group)
was carrying the same unconfirmed assumption; the confirmed definition turns it from a
guess into a fact. What remains unresolved there is the small set of internally
inconsistent source records — 0.7% of groups with two `3`s, 0.3% where a `2` is newer
than a `3` — not the meaning of the value.

## Testing

`tests/test_legal_retrieval_api.py` pins the predicate (`{1, -1}` and nothing else, with
each of the six values asserted), that a missing status is treated as valid, that the
request default is to filter, and that both the new request field and the new response
field are present in the OpenAPI schema.

Live A/B on the local stack, using a genuinely repealed statute ingested for the test
(《宁夏回族自治区执行〈中华人民共和国婚姻法〉的补充规定》, `validity_status=1`):

```text
query「宁夏…补充规定 结婚年龄」default          → top-5 全是现行有效的《国籍法》，已废止那份一条未出，filtered_invalid_count=10
同一查询 include_invalid=true                  → top-5 全是那份已废止文档，filtered_invalid_count=0
未标注(0) 的《宽甸…三部单行条例修正案》default → 正常返回（0 不在排除集里）
```
