# Agent Note: DocxLoader tolerates damaged packages and table bodies

Status: implemented

## Problem

The corpus audit found 88 documents (0.29% of 29,957) that could not be ingested
even though their text was intact:

- **51 failed outright in `Document(path)`.** 44 declare a
  `userCustomization/customUI.xml` relationship in `_rels/.rels` while the part is
  absent (all from Jiangxi sources); 7 declare the main document part as
  macro-enabled because a `.docm` was renamed to `.docx`.
- **37 loaded but produced no text.** Their entire body sits inside a Word table,
  and `DocxLoader` only read `doc.paragraphs`, which excludes table paragraphs.
  The largest of them holds 403 paragraphs and about 10,000 characters.
- **1 more loaded but produced no text** — 《清远市城市市容和环境卫生管理条例》, whose
  whole body (49 articles, 9,769 characters) sits inside text boxes
  (`w:drawing` → `w:txbxContent`), which python-docx's paragraph model cannot see
  either. It surfaced only after the table fix, when the full-mode sample still
  reported one document with zero chunks.

After version selection, 45 of those documents are the current version of their
law — 45 law names would simply not exist in the library, including most of
Jiangxi's statutes. Every one of the 51 was confirmed by running the real loader;
there were no false positives.

## Decision

`pipeline/loaders/docx_repair.py` repairs package-level declarations in memory:
it drops internal relationships whose target part is missing (external links such
as legal-database `javascript:` targets are kept), removes content-type
overrides for absent parts, and rewrites a macro-enabled main document content
type back to the standard WordprocessingML one. `DocxLoader._open_document` tries
`Document(path)` first and, only if that fails, retries on the repaired bytes —
the original file is never modified. The repairs are reported through
`LoadResult.metadata["docx_repairs"]`.

Body extraction moves from `doc.paragraphs` to an ordered walk of the body's
children (`w:p` and `w:tbl`), recursing into nested tables. Paragraphs and table
text land in one text stream in document order, so the chunker still splits on
「第X条」; a table-only statute becomes an ordinary article-structured document.
When that walk yields nothing at all, the loader falls back to
`docx_xml_text.paragraphs_from_bytes`, which reads paragraphs straight out of
`word/document.xml` — coarse, but it covers content python-docx cannot model,
including text boxes.

## Alternatives considered

**Leaving the 88 documents out and excluding them manually.** Rejected: they are
whole statutes, and the loss is concentrated by province, so the library would be
silently wrong for Jiangxi rather than visibly incomplete.

**Repairing the files on disk before ingestion.** Rejected: it mutates source
data that the crawler owns and makes re-ingestion depend on a prior repair step.
Repairing the in-memory copy keeps the loader a pure reader.

**Reading table text only for documents that are otherwise empty.** Rejected: it
would make the text extraction depend on a prior extraction result, and tables in
ordinary documents are legitimate content that was being dropped silently.

**Keeping `doc.paragraphs` and skipping tables.** Rejected: that is exactly the
defect — `doc.paragraphs` excludes table paragraphs, and the corpus has 37
documents whose whole body is a table.

**De-duplicating merged cells with a bare `id(cell._tc)` set.** Rejected on
measurement: `cell._tc` is an lxml proxy created on demand, and a freed proxy's
`id` is reused, so different rows were treated as the same merged cell and
dropped. On 《吉林省个体工商户条例》 (125 single-cell rows) only the first row
survived — 687 of 5,288 characters. The proxies are now kept alive, which makes
`id` safe.

**Reporting the text-box document as an acceptable loss.** Rejected: it is a
whole statute. The XML fallback costs about twenty lines and is only reached when
the structured walk returns nothing, so it cannot change the text of documents
that already parse.

## Consequences

The loader now returns table text for **every** `.docx`, not only the damaged
ones, and this is a shared loader: Artoo's ingestion sees the same change. That
is intentional — table content is document content — but it changes chunk text
for any document with tables, so article counts in the audit move slightly
(corpus-wide article lines went from 1,139,213 to 1,141,002 and article-less
documents from 3,276 to 3,227) and the audit's `fast` mode no longer strips
tables so that it matches the loader.

`docx_repairs` in `LoadResult.metadata` is a new loader metadata key; the
pipeline passes loader metadata through as document metadata, so it is available
for troubleshooting but is not part of any persisted contract.

The XML fallback is deliberately all-or-nothing: it triggers only when the
structured walk yields no text at all, so a document whose body mixes paragraphs
with text boxes still loses the text-box part. Merging both sources was rejected
as too risky for the common case (duplicate text) without a stronger signal.

Repair only covers declaration-level damage. Files whose `word/document.xml` is
genuinely broken still fail with `ValueError`, and the error now includes both
the original and the post-repair exception.

## Testing

`tests/test_docx_loader_repair.py` builds real docx files with python-docx and
then contaminates them the way the corpus does: it asserts a healthy package is
returned unchanged, a dangling internal relationship is removed while an external
`javascript:` link is kept, a macro-enabled main type is rewritten, and that
`DocxLoader` loads documents of both damaged kinds with `docx_repairs` recorded.
It also pins table behaviour: document order across paragraph → table →
paragraph, table-only documents, nested tables, merged cells being emitted once,
every row of a 60-row table surviving, and a text-box-only body being recovered
through the XML fallback.

Verification against the corpus: all 89 previously affected files now load with
non-empty text (44 missing-part files → 307,502 characters / 2,013 articles,
7 macro-enabled → 32,686 / 215, 37 table bodies → 204,251 / 1,649), and the
text-box document → 9,769 / 49), and the per-file result table lives in
`problem_documents.csv` next to the audit reports. The 2000-document full-mode
sample went from 1999 to 2000 documents producing complete chunk metadata.
