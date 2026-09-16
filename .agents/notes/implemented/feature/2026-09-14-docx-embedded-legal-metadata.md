# Agent Note: Authoritative legal metadata from docx properties

Status: implemented

## Problem

Document-level legal fields are derived from the document body.
`parse_legal_header` (`pipeline/legal_metadata.py`) takes every line up to the
first 「YYYY年M月D日」 line as `law_name`, and reads `issuing_authority` and
`publish_date` out of that same parenthesized line. Two measured properties of
the corpus make that heuristic fragile:

- 34 of 345 documents (9.9%) have a title spanning two or three lines, so
  "take the first line" silently yields 「全国人民代表大会常务委员会关于」.
- The rule has no fallback when the date line is missing, and the incoming crawl
  corpus is far larger than the 345-file sample: 29,957 `.docx` files, 3,824 of
  which (12.8%) carry no machine-readable date in the body head at all.

Every one of those files also carries the same fields as OOXML document
properties. `docProps/custom.xml` holds 20 business fields (`title`, `authority`,
`publish_date`, `effective_date`, `validity_status`, `law_type`, `external_id`,
…), and `docProps/core.xml` mirrors `title` / `creator` / `subject` plus a date
summary. Coverage measured over the full corpus: `title` and `authority` 100%,
`publish_date` 87.2%, `effective_date` 75.2%.

## Decision

A new `pipeline/docx_meta.py` reads the two `docProps` parts with the standard
library (`zipfile` + `xml.etree`) and returns a `DocxProps` dataclass.
`DocumentPipeline.process_to_vectors` calls it for `docx` input between Load and
the legal preprocessing step, then passes the result to
`analyze_legal_document(text, props=...)`.

Precedence is **authoritative source first, heuristic second**, applied per
field:

- `law_name`: `props.title` → first line through the line before the date line.
- `issuing_authority`: `props.authority` → parenthesized clause with trailing
  verbs stripped.
- `publish_date`: `props.publish_date` → parenthesized clause.

`core.xml` mirrors `title` / `subject` and a date summary, and those mirrors are consumed per
field. Its `dc:creator` is **not**: that field holds the document's author — the Word/WPS account
of whoever last saved the file — not the issuing authority. The mirror existed to cover third-party
documents that carry only `core.xml`, but it never fires for this corpus (all 29,957 documents carry
`custom.xml` and all 29,957 have `authority`; the count carrying `core.xml` alone is zero), so its
only reachable effect was on user-uploaded files, where it wrote the author's account into
`issuing_authority` — observed as `YF-INT6` on an uploaded 税法 docx whose `custom.xml` held nothing
but WPS boilerplate (`ICV`, `KSOProductBuildVer`). Removing it leaves the body-derived authority
standing: a real authority carrying a date prefix beats an account name, and "no authority" is
honest where "the author's account" is not.

The two sources are not redundant for `publish_date`: the body's first
parenthesized date is the passage date of the statute text, whereas
`props.publish_date` identifies the version the file actually contains. In a
300-document sample the two disagree for 196 documents for exactly that reason
(e.g. 山东省道路运输条例: body `2010-11-25`, property `2022-03-30`). The property
value is the correct key for ordering versions.

`LegalDocumentHeader` gains `meta_source` (`rule` / `docx-props` / `rule+docx`)
and the pipeline logs it. Reader constraints: any failure returns `None`, so a
file that is not a zip, missing `docProps`, or carrying a broken part degrades
instead of failing ingestion; one broken part loses only that part; value
elements are read generically rather than by hardcoding `vt:lpwstr`; dates that
are not `YYYY-MM-DD` are not trusted; `validity_status` is converted to `int`
without inferring the enum's meaning.

The reader returns 8 fields. This change consumed only the three identity fields;
the remaining five (`effective_date`, `validity_status`, `law_type`,
`external_id`, `source_code`) now flow as well, recorded in
[Legal version and provenance fields in chunk_metadata](../architecture/2026-09-14-legal-version-provenance-fields.md).

## Alternatives considered

**Parsing properties through python-docx.** Not possible: `DocxLoader` already
uses python-docx, but it exposes only `core_properties` and has no custom
property API, while the business fields live in `docProps/custom.xml`.

**Returning the properties from `DocxLoader` instead of adding a module.**
Rejected: `LoadResult.metadata` is the shared loader contract across every file
type, and document properties are not extracted content — both the KB and the
session path would inherit fields most loaders cannot produce.

**Keeping body parsing primary and treating properties as the fallback.**
Rejected: measured coverage runs the other way. Properties cover `title` and
`authority` at 100%, whereas body parsing reaches 99.83% on `publish_date` only
because 3,773 of the 3,824 documents missing the property still have a date line.
Merging per field means the heuristic only ever fills gaps.

**Adding a configuration switch.** Rejected: the change is strictly additive. A
missing or unreadable property set falls back to the previous behavior, so a
switch would add state to maintain without changing any outcome.

**Persisting all eight fields in this change.** Rejected for now: five of them
are new `chunk_metadata` keys, which is a persisted-contract change. Reading them
now and consuming them later keeps that decision separate.

## Consequences

`law_name`, `issuing_authority` and `publish_date` now come from the file itself
wherever the file provides them, removing the silent-failure class the body
heuristic had: a plausible-looking but wrong law name for documents whose title
spans lines, or a missing date line.

The change is additive at runtime and in storage. No `chunk_metadata` key, no
Milvus field, and no response field changes; `parse_legal_header(text)` without
`props` behaves exactly as before, and non-docx input or docx files without
`docProps` keep the previous behavior.

`meta_source` carries field provenance on the header and is persisted into
`chunk_metadata` alongside the five version and provenance fields.

`.doc` files lose properties entirely: the loader converts them through
LibreOffice, which drops `docProps`. The current corpus is 100% `.docx` (59,914
archive entries), so no input is affected today.

## Testing

`tests/test_docx_meta.py` builds synthetic docx archives in `tmp_path` and pins
the reader's boundaries: known fields from `custom.xml`, the `core.xml` mirror
alone, `custom.xml` winning over `core.xml`, `dc:creator` not becoming `issuing_authority`,
missing `docProps`, non-zip input, a missing file, non-`lpwstr` value types, empty value elements,
non-ISO dates, unknown keys landing in `extra`, and one broken part leaving the other usable.

`tests/test_legal_metadata.py` adds `TestDocxPropsOverride` for the precedence
rules: full override, partial props keeping the rule fallback, `props=None`
leaving the rule result untouched, article-structure detection staying
body-derived, and the end-to-end `analyze_legal_document` path.

A 300-document sample of the crawl corpus was also run through both paths.
Properties were present in all 300; `meta_source` came out `docx-props` for 264
and `rule+docx` for 34. Relative to body parsing alone, the merge changed
`law_name` for 35 documents (zero-width characters, and titles that swallowed
the following body text) and `issuing_authority` for 298.

Two of the 300 files failed to open in python-docx — one macro-enabled document
and one declaring a missing `userCustomization/customUI.xml` part. That is a
pre-existing `DocxLoader` limitation rather than a regression here: the property
reader only unzips `docProps` and never goes through python-docx, so it still
produced metadata for both files.
