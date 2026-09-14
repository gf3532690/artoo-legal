# Agent Note: Legal filter scalars must be decided before the first ingest

Status: proposed

## Problem

The legal-retrieval PRD requires filtering results by 效力层级 (law / administrative
regulation / all) and by 省份 or 城市, and ranking national law above judicial
interpretation. D1 of the implementation plan decided the opposite: legal fields
live only in PostgreSQL `chunk_metadata`, and the Milvus schema is not touched.

That decision was made when retrieval was semantic-only and nothing filtered. It
now collides with a cost asymmetry that the code makes concrete:

- The Milvus collection has a **fixed field list and no dynamic field**
  (`storage/milvus.py::_build_fields`, scalar indexes only for `doc_id`,
  `tenant_id`, `file_type`, `element_type`). A filter key must exist in the schema
  and be written per row.
- Backfilling a filter value later is not a small migration: Milvus has no
  "update one scalar field" operation, and `upsert` requires the complete entity
  including dense and sparse vectors. Filling in the value therefore means
  **re-embedding every chunk**.
- PostgreSQL is cheap by comparison: `chunk_metadata` is a JSON column, so a new
  key needs no migration and its value can be backfilled by a script over stored
  documents (originals remain available through `GET /api/documents/{doc_id}/raw`).

The deployment is green-field, so the schema decision costs nothing today and a
great deal later.

## Decision

Everything that is written at ingest time is decided **before the first load**,
and the gate is recorded in `docs/legal-first-ingest-checklist.md`. Concretely:

- Decide whether to add `law_type`, `province` and `city` as Milvus scalar fields
  (with scalar indexes) in this first schema, and write them from
  `pipeline.py::milvus_data`.
- Store the **raw** `law_type`; translate the PRD's three levels into a set of
  `law_type` members in the application layer, so that changing the level
  taxonomy later never requires re-ingestion.
- Store `province` and `city` as separate normalised fields rather than one raw
  `region` string, because "search by province" would otherwise have to expand
  into hundreds of city names in a Milvus `expr`.
- Do **not** enable `enable_dynamic_field` as a hedge: it avoids a schema change
  but not the backfill, so old rows would still miss the value.

Interface-level concerns are explicitly out of the gate, because they do not
change stored data: request parameters for level and region, hierarchy ranking
weights, pagination, response `metadata` exposure, `article_id`, and an article
detail endpoint can all be added later.

## Alternatives considered

**Filter in PostgreSQL after the Milvus recall.** Requires no schema change and
no backfill, and `law_type` is already in `chunk_metadata`. Rejected on
selectivity: 法律 is 477 of 29,957 documents (1.6%), so filtering a `recall_k` of
128 candidates would leave roughly two usable hits. Post-filtering only works for
weakly selective conditions.

**Defer filtering entirely and keep D1 intact.** Rejected: two of the PRD's
acceptance items (「仅法律」 excludes judicial interpretations; 「物业费」+「北京市」
returns the Beijing regulation) are unreachable without a filter lane, and the
7,921 duplicate versions make an unfiltered library wrong rather than merely
imprecise.

**Enable the dynamic field now as future-proofing.** Rejected: it removes the
schema-change step but not the value backfill — pre-existing rows have no value in
the dynamic JSON, so a filter over them silently misses. It buys nothing that
deciding the fields now does not.

**Decide the schema after seeing retrieval quality.** Rejected: quality work
happens after the first ingest, by which point the schema is frozen and the
filter fields would cost a full re-embed.

## Consequences

The first ingest is gated on the checklist, not on code completeness. If the
filter fields are added, `_build_fields`, `_SCALAR_INDEXES` and the Milvus write
path all change — small edits today, but they are the kind that cannot be made
cheaply once rows exist.

Adding `province` makes a city → province mapping table (about 340 rows) an
ingest-time dependency: without it the 11,812 city-level regulations would carry
an empty province. 763 county-level regulations (自治县 and similar) need an
explicit归属 decision.

The PRD items backed by data we do not hold stay out of scope by current
agreement: 部门规章 (absent from the corpus), `expiry_date` / `superseded_by`
(absent), and 关联司法解释 (only 115 of 846 judicial interpretations name a
statute in their title).

## Testing

Verification for the schema half is structural rather than unit-level: after
`ensure_collection`, `describe_collection` must list the new fields and their
scalar indexes, and a search with an `expr` over them must include and exclude
the expected chunks. The checklist's 现状快照 section pins the corpus numbers
those decisions are based on (12 `law_type` values, 31 province names, 350 city
names, 2.88 M / 1.19 M chunk estimates), all produced by
`app/scripts/audit_legal_metadata.py`.
