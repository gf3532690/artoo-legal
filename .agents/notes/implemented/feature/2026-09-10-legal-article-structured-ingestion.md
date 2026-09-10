# Agent Note: Legal article structured ingestion

Status: implemented

## Problem

This repository is the 法条库 product line. Its corpus (347 statutory documents)
has a useful retrieval unit of *one article*, but upstream ingestion only knows
chapter paths, page numbers and element types. Three measured facts drive this
change:

- 76.3% of documents carry a 目录 whose lines are topic labels. Those lines look
  exactly like user queries, so they compete for the same top-k slots.
- 37 documents (刑法修正案 / 决定 / 规定) contain no line-leading `第X条` at all.
  The chunker makes each whole document a single parent and then splits by line,
  cutting 「一、」 items mid-way (items average 2.56 lines).
- 34 documents have a title spanning two or three lines, so "take the first
  line" silently produces 「全国人民代表大会常务委员会关于」 as the law name.

## Decision

A new `LegalMetadataExtractor` (`pipeline/legal_metadata.py`) runs after
`MetadataExtractor`, which itself runs after `enforce_size_limits`:

- `law_name` = first line through the line before the date line, joined.
  345/345 `.docx` files have a parseable 「YYYY年M月D日」 line, so the boundary
  always exists.
- `article_number` = Arabic integer parsed from the parent block's leading
  「第X条」; `article_label` keeps the original Chinese numeral. Chinese numeral
  conversion is a new shared utility — the repo had four numeral *matchers* and
  no converter.
- `chapter` / `section` come from the existing `section_path`, filtered to keep
  only 「第X编 / 第X分编 / 第X章 / 第X节」. The heading extractor treats
  line-leading 「（一）…」 as a level-3 heading and 8,950 of 11,063 such lines are
  short enough to pass its length guard, so most would otherwise pollute the
  path.
- The 目录 region is stripped **before chunking**, and only when a standalone
  「目录」 line exists.
- `LawsChunker` gains a **conditional** fallback: only when a document has zero
  line-leading `第X条` does 「一、」 become a parent boundary.
- The Milvus `content` prefix grows from `[文件名]` to `[法名 第N条]` with N in
  Arabic numerals, so 「民法典第146条」 matches text written 「第一百四十六条」.
  The prefix is stripped again when the response is assembled.

## Alternatives considered

**Extract inside `LawsChunker`.** Rejected: `ChunkResult` is shared by every
chunker, and `article_number` is a parent-level property that must reach
children through `parent_child_map` regardless.

**Store law name and article number as Milvus scalar fields.** Rejected for
this release: retrieval is semantic-first with no filter lane, so the fields are
only ever returned, never filtered. `Chunk.chunk_metadata` plus the existing
batched hydration covers it without touching the Milvus schema.

**Strip the 目录 by position — "everything before the first `第X条`".**
Rejected: it would erase all 37 article-less documents, whose body starts on
line one. The intersection of "has a 目录 marker" and "has no `第X条`" is empty,
so the marker-driven rule provably cannot damage them.

**Always treat 「一、」 as a parent boundary.** Rejected: in ordinary statutes
「一、」 is an item *inside* an article (《国籍法》第七条 has three), so this would
tear items out of their article.

## Consequences

Every result now carries `law_name` and `article_number`, which is the point of
the deployment. The values are machine-extracted and not independently
verifiable downstream, so verification is a full-corpus review (each file's name
and article count checked by hand) rather than a percentage target.

Article-less documents keep `article_number = null`; they remain semantically
retrievable but cannot be addressed by article number.

The time-validity fields (`legal_status`, `effective_date`, …) are deliberately
absent: nothing here records which version of a statute is in force.

## Testing

Unit tests cover Chinese numeral conversion up to 万, the three 目录 cases (marker
present / absent / article-less document), title-to-date-line joining, article
extraction, and `section_path` filtering. 《刑法修正案》`19991225` is kept as a
fixture because it exercises three hard cases at once: items spanning lines,
nested 「（一）」 items, and a preface before the first item.
