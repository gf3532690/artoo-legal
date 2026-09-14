# Agent Note: Legal version and provenance fields in chunk_metadata

Status: implemented

## Problem

The field dictionary carried document *identity* only: `law_name`,
`issuing_authority`, `publish_date`, `article_number`, `chapter`. Everything that
identifies *which version* of a statute a chunk belongs to, or where the document
came from, was unavailable to any consumer. Measured on the crawl corpus (29,957
`.docx` files):

- 6,107 law names exist in 2–6 versions — 7,919 extra documents — and nothing in
  the stored metadata distinguishes them, so an older and a newer version of the
  same statute are equally retrievable with no way for a caller to tell them
  apart.
- 2,092 documents are marked `validity_status = 0` and 17,235 `= 3`, and 7,419
  carry no `effective_date` in the body head, but none of that reached storage.
- The ingestion review has no way to distinguish an authoritative value from a
  heuristic one after the fact.

The companion note
[Authoritative legal metadata from docx properties](2026-09-14-docx-embedded-legal-metadata.md)
already reads eight fields from `docProps` and deliberately consumed only three,
because the remaining five are new persisted keys.

## Decision

`LegalDocumentHeader` gains `effective_date`, `validity_status`, `law_type`,
`external_id` and `source_code`, and `LegalMetadataExtractor.extract` emits six
new keys per child chunk: those five plus `meta_source`.

- `effective_date`: property first, then a body fallback that matches
  「自…起施行」 and takes the **last** match — the 施行 clause belongs to 附则 at
  the end of the statute.
- `validity_status`: stored as the raw integer. The producer's enum is
  undocumented, so nothing interprets it.
- `law_type`, `external_id`, `source_code`: property-only. The body contains no
  equivalent information.
- `meta_source` (`rule` / `docx-props` / `rule+docx`) is persisted alongside the
  existing diagnostics field `has_toc` so the full-corpus review can tell
  authoritative values from heuristic ones.

The keys reach PostgreSQL through the existing `Chunk.chunk_metadata` JSON column
— no migration. At the time of this change nothing was exposed on the wire
(`_LEGAL_KEYS` selected only `law_name` / `article_number` / `article_label` /
`chapter`); they are now returned to callers as well, recorded in
[Legal result metadata exposure](2026-09-14-legal-result-metadata-exposure.md),
and `law_type` / `province` / `city` are also Milvus scalar fields (see the
filter-scalars proposal).

## Alternatives considered

**Deferring all five fields until an ingestion-side version-selection flow
exists.** Rejected: the fields cost nothing to persist (JSON column, no
migration), and the ordering key for versions has to be stored before any
selection flow can act on it. Storing fields first and acting on them later is
also what makes the flow testable against real data.

**Using the body-derived `publish_date` as the version key.** Rejected: in a
300-document sample the body date and the property disagree for 196 documents,
because the body's first parenthesized date is the passage date of the statute
text while the property identifies the version the file contains.

**Interpreting `validity_status` into a `legal_status` enum now.** Rejected: only
two values have evidence — `0` for `修改、废止的决定` (1,902/1,902) and `3` for
`宪法` and `修正案` (19/19). Storing the raw integer keeps the option open
without asserting a meaning the producer never documented.

**Exposing the new fields in the retrieval response.** Rejected for this change:
nothing filters or ranks on them, and every response field is a downstream
contract. The response shape stays as is.

**Reading the 施行 clause from the document head only.** Rejected: the clause
sits in 附则 at the end, so a head-only scan misses it for the documents that
need it most. Scanning the full text and taking the last match is both simpler
and more accurate.

**Keeping `meta_source` in logs only.** Rejected: the full-corpus review is the
verification method for this deployment, and it needs provenance per document
after ingestion; `has_toc` is precedent for persisting a diagnostics-only field.

## Consequences

Every child chunk now carries six more keys, and because document-level fields
are denormalised into each child — as `law_name` already was — the same values
repeat across a document's chunks. The JSON column absorbs this without a
migration, but the storage and hydration cost grows with the field count.

Version governance becomes *possible*, not implemented: older and newer versions
of a statute remain equally retrievable until an ingestion-side selection flow
ships. A constraint that flow must respect is already visible in the data: 2,031
documents carry `validity_status = 0` and have no sibling version, most of them
`修改、废止的决定`, whose body is the record of what was repealed. They are
informative and must not be deleted on the strength of that status alone.

`effective_date` stays null for roughly 22% of documents — the property is absent
and no 施行 clause is found — and is deliberately left unknown rather than
guessed. `validity_status` is likewise uninterpreted: a consumer that needs
「current or not」 must consult the producer's enum definition first.

`.doc` inputs lose all six fields, because LibreOffice conversion drops
`docProps`; the current corpus is 100% `.docx`.

The property `title` is the source registry's canonical name rather than always a
verbatim copy of the printed title on the first line: 9 of the 200 smallest
documents differ, e.g. the registry form drops a meeting-session prefix
(「海西蒙古族藏族自治州第十四届人民代表大会第六次会议关于废止…」) while the body
keeps it. That is the desired behaviour for grouping versions by law name, but it
means a consumer must not treat `law_name` as the document's printed heading.

Two failure modes move from the logs into the database, which is the point of
`meta_source`. A wrong `title` in the source metadata now silently overwrites a
correct body parse (property-first is intentional, but it is no longer
invisible), and `rule+docx` marks documents whose property set was incomplete.

## Testing

`tests/test_legal_metadata.py::TestVersionAndProvenanceFields` pins the new
behaviour: properties populate the five fields; without properties the values are
null while the keys remain present (a stable field shape); `effective_date` falls
back to the body clause and takes the last match; a property value wins over that
clause; and `LegalMetadataExtractor` emits all six keys into the per-chunk dict
with `confidence` reaching 1.0 for article chunks once the identity fields are
satisfied.

A 300-document sample of the crawl corpus confirmed the shape and the sources:
every chunk of every document carried all six keys; properties supplied
`validity_status`, `law_type`, `external_id` and `source_code` for all 300
documents; and `effective_date` came from the property for 220 documents, from
the 施行-clause fallback for 53, and stayed null for 27.
