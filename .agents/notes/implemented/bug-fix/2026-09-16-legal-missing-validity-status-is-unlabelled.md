# Agent Note: A missing validity status is 未标注, not null

Status: implemented

English | [中文](2026-09-16-legal-missing-validity-status-is-unlabelled.zh.md)

## Problem

`validity_status` has exactly one source: the document's own `docProps/custom.xml`. The crawl
corpus always carries it (29,957 of 29,957 documents), but any document that did not come from that
registry — a file a user uploads — has no such property. Measured on a real upload
(《中华人民共和国城市维护建设税法_20200811.docx》): its `custom.xml` holds only WPS boilerplate
(`ICV`, `KSOProductBuildVer`), and its `core.xml` holds author/revision metadata.

Such a document was stored with `validity_status = NULL`, which produced a blank where a status
belongs: no badge in the file list, and — worse — unfindable by the 未标注 filter, even though
"the source never labelled it" is exactly what 未标注 means. Reported from the field as "我上传了
一个法条文件，解析完成后他怎么没有任何状态".

## Decision

A document parsed as a legal document whose source carries no status is stored as **`0` 未标注**,
in both places the value lives: `documents.validity_status` and every child chunk's
`metadata.validity_status`.

`NULL` keeps one meaning only: *not yet a parsed legal document* — an unfinished document. In this
deployment there is no third case, because every knowledge base is a legal library (the create
endpoint defaults `chunker_type=laws`, see `api/knowledge_base.py`), so "a completed document with
no status at all" is not a state that can exist.

Existing rows are brought in line by the second pass of
`scripts/backfill_document_validity.py`, which writes `0` to completed documents whose column is
still empty (and to their child chunks).

## Alternatives considered

**Keep NULL and render 未标注 in the UI.** Rejected: the stored value is not only a display detail.
The file list filters on it with an equality predicate, and retrieval hydrates it into
`results[].metadata`. A display-only fallback would put the badge and the filter in direct
disagreement — the file says 未标注, and "只看未标注" does not return it.

**Substitute at read time (NULL → 0 in the API layer).** Rejected for the same reason in the other
direction, plus cost: the list SQL, the retrieval hydration and the article-detail endpoint would
each need the substitution, and `NULL` would lose its only useful meaning. One write at ingest is
cheaper and keeps `NULL` honest.

**Leave it to the manual 「更新状态」 action.** Rejected as the default: the manual path exists for
corrections, but "I uploaded a law and it shows nothing" is not something a user should have to
fix by hand when the machine can answer it — the source did not label this document.

**Default only for the global library, leaving personal libraries NULL.** Rejected: every knowledge
base here is a legal library, and a status that depends on *which* library the same file was
uploaded to would be worse than either alternative.

## Consequences

For a completed document, `validity_status` is now always present, in the list and in
`results[].metadata`. Consumers that treated an absent key as "unknown" should treat `0` as
未标注 — which is what the dictionary says it means. The general "a field we could not read is an
absent key" convention still holds for every other field.

A real value still comes from the custom properties and nowhere else; the default only fills in for
documents that carry no label at all.

A re-parse recomputes the value from the document's own properties, so a hand-set status still does
not survive it — unchanged from before this change.

## Verification

`tests/test_legal_document_status_filter.py` pins the resolver (empty list / all-null → `0`; a real
value, including `0` and `-1`, passes through) and the extractor's emitted field dictionary (a
header without a status emits `0`, not `None`). `tests/test_legal_metadata.py`'s
`test_extractor_emits_null_keys_without_props` was updated — it had pinned the old NULL behaviour,
and now records that `validity_status` is the one key with a persisted default.

Live on the local stack:

```text
the user's upload (custom.xml = ICV + KSOProductBuildVer only)
  before backfill   documents.validity_status = NULL   no badge, invisible to 未标注 filter
  after backfill    documents.validity_status = 0      all 4 child chunks carry 0
  filter validity_status=0 → returns it

a FRESH upload through the lite API, no backfill involved
  completed with validity_status = 0, all 4 chunks 0
```
