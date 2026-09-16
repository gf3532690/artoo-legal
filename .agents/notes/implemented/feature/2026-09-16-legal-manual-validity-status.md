# Agent Note: Manual maintenance of a document's validity status

Status: implemented

## Problem

`validity_status` is written exactly once, by the pipeline, when a document finishes parsing — from
the data source's own dictionary, which is authoritative but neither complete nor always right.
Across the corpus, `0` 未标注 covers 2,031 documents whose source file carries no label at all, and
the extractor can only be as good as the `docProps` it reads. There was no way to correct a value:
the file list, the status tags added in
[Legal document list carries and filters by validity status](2026-09-15-legal-document-validity-status-filter.md),
and the default "hide repealed statutes" rule on recall all key off a field a user could see was
wrong and could not touch.

## Decision

`PATCH /api/documents/{doc_id}/validity-status` with `{"validity_status": <one of the six>}`, plus a
right-click 「更新状态」 action on the file that opens a dialog to pick one.

- **It writes both stores.** `documents.validity_status` is what the file list displays and filters
  on; each child chunk's `metadata.validity_status` is what retrieval hydrates into
  `results[].metadata` and what the default invalid-exclusion rule reads. A manual edit that
  touched only one would put the same document in two states at once.
- **Only the six dictionary values.** Anything else is a 400. A value outside the dictionary
  renders as 「取值 N」 in the list and no filter option can select it, so accepting it would create
  a row nobody can find again.
- **Completed documents only.** An unfinished document has no chunks; setting the column alone
  would produce a status retrieval cannot see.
- **The same write gate as re-parsing**: `_authorize_kb_access(WRITE)`. Editing a status is
  editing content.
- **The retrieval cache is invalidated** (locally and by cross-process broadcast). Otherwise the
  next search replays cached results carrying the old status and the edit looks like it did
  nothing.
- **Re-parsing overwrites it.** Recorded rather than engineered around: manual maintenance means
  "fix what extraction got wrong", not "pin an attribute forever".
- The list view also gained the 效力状态 column it had been missing — the filter applied to both
  views while only the grid could show what it had selected.

## Alternatives considered

**Update only `documents.validity_status`.** Rejected: it is the cheaper write and would have
looked right on the page, but the document would keep its old status in every search result, and
marking a repealed statute as in force (or the reverse) would not change recall at all. The two
stores have different readers; a write path that serves one of them is a bug factory.

**Update only the chunk metadata.** Rejected: the file list and its filter read the column.

**Re-ingest the document after editing.** Rejected: minutes per document to change one integer,
and it re-runs extraction — recomputing the very value the user is overriding.

**Accept any integer and let the UI render it.** Rejected: see the six-values bullet.

**A manual-override flag so a later re-parse keeps the value.** Deferred, not rejected. That is the
right answer if manual edits ever need to survive a re-index, but it introduces a third state
(manual / extracted / unknown) and a "restore automatic" action to go with it. It is a bigger
decision than this change, and only worth taking once someone actually loses edits to a re-index.

## Consequences

The endpoint is a small transaction: update the column, merge the key into every child chunk's
metadata (the column is `json`, so the merge round-trips through `jsonb`), commit, then invalidate.
A failure before the commit writes nothing.

Manual values are **not distinguishable from extracted ones**. `meta_source` records where the
*identity* fields came from, and nothing records that a human edited the status. Anyone
diagnosing a surprising value has to ask.

## Verification

`tests/test_legal_document_status_filter.py` (4 new tests) pins that both stores are written, that
the gate is the WRITE access level, that a value outside the dictionary is rejected without
touching anything, and that an unfinished document is rejected.

Live on the local stack, against the corpus document 《宁夏回族自治区执行〈中华人民共和国婚姻法〉的补充规定》
(`validity_status = 1`, 10 child chunks):

```text
before      documents.validity_status = 1      chunks carrying metadata 1 = 10
            search 「宁夏…结婚年龄」 default → document NOT returned, filtered_invalid_count = 10
PATCH 3
after       documents.validity_status = 3      chunks carrying metadata 3 = 10
            same search, same default口径 → document IS returned,
            results[].metadata.validity_status = 3, filtered_invalid_count = 0
restore     PATCH 1 → column and all 10 chunks back to 1
PATCH 99    → 400 「效力状态取值不在数据源字典里（可选：3 现行有效 / …）」
```
