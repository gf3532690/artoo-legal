# Agent Note: Legal document list carries and filters by validity status

Status: implemented

## Problem

Recall learned to drop repealed and lapsed statutes by default
([Recall excludes repealed and lapsed statutes by default](2026-09-15-legal-exclude-repealed-by-default.md)),
but the **file list** — the surface a custodian actually browses — said nothing about which
documents those are. Looking at the global legal library meant 22,037 filenames with no way to
find the 2,617 that are no longer in force, and no way to check by eye that the ingest-side
version-selection rule had kept the right one.

The status existed only inside `chunks.metadata`, denormalised onto every child chunk.
`law_name` is read back out of that dict the same way, at read time, and that pattern does not
extend to filtering: the list is paged, so a predicate applied after paging reports the wrong
`total` and lets filtered rows leak onto later pages.

## Decision

`validity_status` becomes a column on `documents` (nullable integer, indexed), and the list
endpoint both returns it and filters on it.

- **Written at ingest, in the same statement that completes the document.** The pipeline
  already funnels completion through `DocumentPipeline._update_status`; the value is picked out
  of the per-chunk metadata there by `legal_metadata.document_validity_status`.
- **Read as a plain column.** `DocumentResponse.validity_status` is the raw integer, on every
  response that returns a document (list, detail, the two `duplicate` branches).
- **Filtered with a repeatable query parameter.** `?validity_status=1&validity_status=-1` is a
  union, not an intersection, and compiles to a plain `IN` predicate on the indexed column.
- **Ingestion otherwise unchanged, and no re-extraction.** Document-level fields are not
  re-derived: the 22,037 documents already ingested keep their chunks and vectors and get the
  column filled by `scripts/backfill_document_validity.py`, which reads the value out of the
  metadata that is already there.
- **The enum is served, not duplicated.** `GET /api/legal/validity-statuses` returns
  `[{value, label}]`. The frontend renders labels from that response; it does not carry its own
  copy. This enum has been read backwards once already — `0` 未标注 and `-1` 已失效.
- **The control is a row of status tags, not a dropdown.** Clicking a tag toggles whether that
  status is shown; with none selected everything is shown, and the leading 全部 tag is what
  expresses that state. Hiding both the options and the current selection behind one more click is
  the wrong shape on the one page whose whole job is looking at files by status.
  The tags sit on the left, and the root breadcrumb is **not** rendered at the root: 「全部文件」
  and the 全部 tag say the same thing, and two labels for one state only invite the reader to guess
  which one filters. Inside a folder the breadcrumb comes back — there it is navigation, not a
  title.

### Why a column instead of deriving at read time

Measured in PostgreSQL over tables sized like the real corpus (22,037 documents, 1,100,000
chunks, ~2,600 matching rows), same predicate, same session:

```text
documents.validity_status IN (1, -1)   Bitmap Index Scan   execution  24.7 ms   147 buffers
EXISTS (chunks c WHERE ... IN ('1','-1'))  Parallel Seq Scan on chunks
                                                           execution 3958 ms  9,173 buffers
```

The gap is structural rather than constant: the second shape is linear in *chunks*, and chunks
grow with documents × articles per document. The first is linear in documents and index-backed.

## Alternatives considered

**Deriving the value at read time from `chunks.metadata`, exactly as `law_name` is.** Rejected on
the measurement above, and on correctness: a predicate that runs after paging cannot report a
truthful `total` or a stable `has_more`. `law_name` survives that pattern only because it is
display-only and never enters a `WHERE`.

**Filtering in the frontend over the pages already loaded.** Rejected: the user can only filter
what they have scrolled to, the counts shown would be counts of a page rather than of the
library, and it would silently disagree with the same filter applied through the API.

**Backfilling inside the startup migration.** Rejected: it would scan the whole corpus on every
process boot. Structure belongs in an idempotent startup path; a one-off data move belongs in an
explicit, re-runnable script.

**Showing the filter on every knowledge base.** Rejected: the field is only ever populated for
legal documents. On a normal knowledge base the control could never match anything, which reads
as "my files disappeared" rather than "this filter does not apply here". The list shows it when
`config.is_default_legal_kb` is set.

**Returning a pre-rendered `validity_status_label` in the document list.** Rejected: the label
is a display vocabulary. Keeping it out of the response body means the wording can change
without changing a persisted contract, and the enum endpoint already removes the duplication
that was the only reason to consider it.

**Exposing `validity_status` as a Milvus scalar field instead.** Not an alternative — a
different surface. That one is about pre-filtering recall; this one is about a browsable list.
Both are wanted; the Milvus half stays deferred to the next rebuild window, as recorded in the
note above.

## Consequences

The documents-list contract gains a query parameter and `DocumentResponse` gains a field, so
clients that pin the response shape need updating. `null` means "not established": unfinished
documents, failed documents, and every non-legal document. A request that passes
`validity_status` therefore **excludes** those rows — equality matching, not "empty counts as a
match" — and the parameter description says so.

`POST /api/documents/{doc_id}/retry` clears the value. It is a product of the parse, and a stale
status on a document that is back in `pending` would be worse than no status.

The startup migration adds a column and an index to `documents`; the index build takes a brief
`ACCESS EXCLUSIVE` lock, which on 22k rows is milliseconds. Both processes call it, so the
worker does not depend on the API having started first.

The column duplicates a value that also lives in every child chunk's metadata. That redundancy
is deliberate — one read path wants it per document — but it means the two can drift if some
future path writes chunk metadata without going through completion. There is exactly one such
write point today.

The ingest-side corpus still carries the older problem this exposes rather than fixes: the
ingest list is a one-shot snapshot, so a law whose newest version was `4` (尚未生效) at snapshot
time stays behind (12 such laws).

The field is no longer write-once: a manual correction path now exists, recorded in
[Manual maintenance of a document's validity status](2026-09-16-legal-manual-validity-status.md).
That is what makes the 2,031 documents carrying 未标注 fixable by hand instead of permanently
unfilterable.

## Testing

`tests/test_legal_document_status_filter.py` (15 tests) pins the enum values including the two
that were once read backwards, the document-level picker (`0` is a value, not a missing one;
junk is skipped), that the filter compiles to `documents.validity_status IN (...)` and that an
unfiltered request emits no such predicate, that `[0]` is honoured rather than treated as "no
filter", and that the parameter and the response field are present in the OpenAPI schema.

`frontend/src/components/documents/FileItem.test.tsx` (8 tests) pins the badge: labels come from
the served enum, `0` renders 未标注, an unknown value renders as `取值 N` rather than a
guess, and with no label table nothing renders at all — not a bare integer.

Live on the local stack, the global legal library after backfill (10 documents: 8 现行有效,
1 未标注, 1 已废止):

```text
no filter                                    total=10  vs=[1,3,0,3,3,3,3,3,3,3]
?validity_status=3                           total=8   vs=[3,3,3,3,3,3,3,3]
?validity_status=1                           total=1   vs=[1]
?validity_status=1&validity_status=-1        total=1   vs=[1]
?validity_status=3&2&4&0                     total=9
```

Backfill dry-run and run on the same corpus: 10 of 10 completed documents recovered a value
from chunk metadata, 0 left empty.
