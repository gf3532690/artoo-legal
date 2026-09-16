# Agent Note: Retrieval reports validity status instead of filtering on it

Status: implemented

English | [中文](2026-09-16-legal-retrieval-reports-status-instead-of-filtering.zh.md)

## Problem

Retrieval excluded 已废止 (`1`) and 已失效 (`-1`) results by default, with `include_invalid` to lift
the exclusion and `filtered_invalid_count` to explain a short page. That decision is recorded in
[Recall excludes repealed and lapsed statutes by default](2026-09-15-legal-exclude-repealed-by-default.md);
this note supersedes it. Two things were wrong with it.

**The口径 is not the service's to choose.** Whether a repealed statute belongs in the answer depends
on what the caller is asking — a question about conduct in 2019 needs the law as it stood, and a
question about current rules does not. The service cannot tell those apart, so a default either way
is a guess imposed on every caller.

**And the caller could not do it themselves.** Results carried `validity_status` as a bare integer
with no description, so every client would have had to re-implement the dictionary — the same
dictionary that has already been read backwards once (`0` is 未标注, `-1` is 已失效). "Filter it
yourself" is only a fair answer if the status arrives with its meaning attached.

## Decision

Retrieval does not filter on `validity_status`. All six values are returned, and every result
carries both forms of the field:

```json
{"validity_status": -1, "validity_status_label": "已失效"}
```

- `include_invalid` is removed from the request; `filtered_invalid_count` is removed from the
  response. A switch whose only purpose was to lift a default that no longer exists would be a
  no-op that still reads like a promise.
- The ~4× candidate oversampling that compensated for post-recall filtering is removed with it, so
  a query now fetches `window() + 1` candidates instead of four times that.
- `validity_status_label` is derived from `VALIDITY_STATUS_LABELS` — the same dictionary the enum
  endpoint serves — and a value outside the dictionary gets **no** label: the raw integer still
  goes out, because inventing a description is worse than omitting one.
- `GET /api/legal/articles/{article_id}` gains the same label.
- The **document list** keeps its own status filter: that is a browse-and-manage surface where
  filtering is the point, and it is an explicit UI action rather than a hidden default. Retrieval
  and the file list now differ in exactly this respect, deliberately.

## Alternatives considered

**Keeping the filter as opt-in with the default reversed** (`include_invalid` defaulting to `true`).
Rejected: a default-on switch is dead weight, it would keep the oversampling path alive for a case
nobody exercises, and a caller that wants only in-force results can filter the field it now always
receives.

**Sending the label but keeping the default exclusion.** Rejected: the caller would still be unable
to see what had been dropped, which is the problem, not the label.

**A normalised boolean instead of the enum** (`in_force: true/false`). Rejected: it collapses six
values into one bit, and the values are not equivalent — 未标注 (0), 尚未生效 (4) and 已修改 (2) all
mean "not 1 and not -1" while meaning three different things to a reader.

**Filtering in the client for everything, including the file list.** Rejected: the list is paged
and sorted server-side; filtering a page client-side would make `total` and `has_more` lie there
instead.

## Consequences

The response envelope shrinks — two fields removed, one added inside `metadata` — so consumers that
read `filtered_invalid_count` or sent `include_invalid` need updating. Sending `include_invalid` is
now ignored rather than rejected, because FastAPI ignores unknown query/body fields and a hard
failure here would break callers for a change that only ever widens their result set.

Pages can no longer come back short for an invisible reason: `total` is the page's own size,
`has_more` is exact, and everything the query matched is in the caller's hands. Along with the
oversampling removal this also cuts the candidate set fed to rerank by ~4×, which is where most of
a hybrid query's latency lives.

The status still drives the file list's default presentation (a badge) — the two surfaces now
disagree about filtering on purpose, and the reason is that one is a query and the other is a
browser.

## Verification

`tests/test_legal_retrieval_api.py` replaces the old exclusion tests with
`TestStatusIsReportedNotFiltered`: the request model has no `include_invalid`, the response model has
no `filtered_invalid_count`, the label covers all six dictionary values, and unknown/absent values
get no label. `tests/test_retrieval_legal_metadata.py` asserts the label reaches a result's
`metadata`. `tests/test_retrieval_contract_baseline.py`'s envelope key set is updated.

Live on the local stack, using the document the exclusion used to hide
(《宁夏回族自治区执行〈中华人民共和国婚姻法〉的补充规定》, `validity_status = -1`):

```text
search 「宁夏…结婚年龄」 default
  before   the document was filtered out; response carried filtered_invalid_count = 10
  after    the document is returned, metadata.validity_status = -1,
           metadata.validity_status_label = "已失效"; response has no filtered_invalid_count
detail GET /api/legal/articles/{doc}:1 → validity_status = -1, validity_status_label = "已失效"
OpenAPI: RetrievalTestRequest has no include_invalid,
         RetrievalTestResponse has no filtered_invalid_count,
         LegalArticleDetail has validity_status_label
```
