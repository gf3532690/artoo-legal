# Agent Note: Legal corpus metadata audit script

Status: implemented

## Problem

The verification method for this deployment is a full-corpus review: the decision
record requires every document's law name and article count to be checked and
explicitly refuses a percentage target. That review had no tool — the 347-file
sample had been checked by hand, while the ingestion corpus is 29,957 documents.

Three questions could not be answered without one:

- Do the docx-property values and the body parse disagree, and on what?
- How many child chunks does the corpus actually produce against a
  `kb_chunk_cap` of 1,000,000?
- What would version selection drop, before anything is deleted?

## Decision

`app/scripts/audit_legal_metadata.py` audits a corpus given as either an extracted
directory or the original zip, and writes `files.csv` / `groups.csv` /
`manual_review.csv` / `summary.json`.

- `fast` (default) reads only `docProps` and `word/document.xml` through
  `zipfile`, rebuilds paragraphs, runs `TextCleaner` and the legal preprocessing,
  and records law name, article count, field provenance and the fields where the
  property value differs from the body parse. The whole corpus takes about 78
  seconds.
- `full` runs the real ingestion chain (loader → cleaner → chunker → size guard →
  metadata extractors) and additionally reports chunk counts and whether every
  chunk carries the six new metadata keys. It costs about 51 ms per document, so
  it is intended for samples.

Zip entries are decoded from their cp437 mis-reading (`decode_zip_name`) unless
the archive sets the UTF-8 flag, so a zip corpus and the extracted directory give
the same law names. Fast mode strips `<w:tbl>` blocks and matches text elements as
`<w:t>` with optional attributes, because `DocxLoader` reads `doc.paragraphs`,
which excludes table paragraphs.

`select_versions` marks a preview, not a deletion: within a law name, versions
with `validity_status == 3` are preferred, then the newest `publish_date`; groups
with no active version, tied dates, or no dates at all are flagged for human
review. A single-version document whose `validity_status == 0` is flagged
`solo_inactive` and keeps `selected = true` — 1,853 of the 1,902
`修改、废止的决定` fall in that bucket, and their body is the record of what was
repealed.

## Alternatives considered

**Using the real loader for every document (full mode everywhere).** Rejected as
the default: about 51 ms per document means roughly 25 minutes for the corpus,
against 78 seconds in fast mode. `--mode full` stays available for samples and is
the authority whenever the two disagree.

**Reusing the ad-hoc `w:t` scan written earlier in this work.** Rejected: it
treated every text run as a line, and its `<w:t[^>]*>` pattern also matched
`<w:tbl>` / `<w:tc>` / `<w:tab/>`, so XML markup leaked into parsed fields. The
audit's own CSV caught this — `authority_rule` contained raw `<w:autoSpaceDE/>`
tags for some documents — which is the clearest argument for per-file CSV output.

**Selecting versions inside the ingestion path.** Rejected for now: this is a
preview report. The actual deletion policy is the version-governance phase and
depends on the producer's `validity_status` enum, which is still undocumented.

**Auto-deleting every document with `validity_status == 0`.** Rejected: 2,092
documents carry that status, and the ones without a sibling version are mostly
repeal decisions whose text is the only record of what was repealed.

**Printing a summary only.** Rejected: the review is per document by definition,
so the report has to be a per-file table that a human can scan.

## Consequences

The corpus facts are now reproducible instead of anecdotal, and they contradict
the estimate this plan was sized against: a 2000-document `full` sample measures
2.25 child chunks per article and 30.9 per article-less document, which
extrapolates to about 2.67 M chunks for the corpus and 1.85 M after version
selection, against a 1,000,000 cap — whereas the 347-document sample implied
27,800. Section 2.1 of `docs/legal-recall-implementation-plan.md` records the
measurements and the three ways out (raise the cap, split the corpus across
knowledge bases, or stop splitting articles by 款).

The same audit quantified a separate, pre-existing ingestion blocker — 88
documents (0.29%) that could not be ingested — and that finding has since been
fixed in
[DocxLoader tolerates damaged packages and table bodies](../bug-fix/2026-09-14-docx-loader-tolerates-damaged-and-table-bodies.md).
The audit is what made the defect measurable: it detected the 51 damaged packages
statically, the run of the real `DocxLoader` over every candidate confirmed them
with no false positives, and it exposed the 37 table-body documents through the
articles column.

The selection rule now lives in code, which is a decision to revisit once the
`validity_status` enum is confirmed. Its output is a candidate list, not an
instruction: `manual_review.csv` held 2,365 rows on the real corpus (1,984
`solo_inactive`, 379 groups with no active version, 2 tied dates).

Fast mode is an approximation of the pipeline text: no python-docx, no OCR, no
base64 stripping beyond `TextCleaner`. Chunk-shape conclusions must come from
full mode. Reports are disposable derived data and are not committed.

## Testing

`tests/test_legal_audit.py` pins zip-name decoding (including the case where the
restore is not valid UTF-8 and the original name must survive), paragraph
extraction (including a markup-leakage case and table exclusion), the fast audit
record, every selection-rule branch, and report writing.

On the real corpus: fast mode over 29,957 documents returned 0 errors, 0 missing
property sets, 1,141,002 article lines and 3,227 documents without article
structure; full mode over 2000 documents produced 182,589 chunks with all six new
metadata keys present in every chunk of every document that produced chunks.
