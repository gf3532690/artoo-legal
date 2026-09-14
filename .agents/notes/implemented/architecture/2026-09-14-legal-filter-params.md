# Agent Note: Legal hierarchy and region filters on the retrieval request

Status: implemented

## Problem

The PRD requires callers to narrow retrieval by 效力层级 (law / administrative
regulation / everything) and by 省份 or 城市, and its acceptance items are worded
as filtering ("指定仅法律层级，返回结果不包含司法解释"; "输入『物业费』+『北京市』，
返回《北京市物业管理条例》"). The retrieval request had no filter fields at all,
and the plan had explicitly decided against a precise filter lane.

The proposal
[Legal filter scalars must be decided before the first ingest](2026-09-14-legal-filter-scalars-before-first-ingest.md)
settled the storage half: `law_type` / `province` / `city` are Milvus scalar
fields, written per chunk. This note ships the query half.

## Decision

`RetrievalTestRequest` gains three optional fields, all combinable:

- `law_levels: list[str]` — a **level enum decoupled from the corpus categories**:
  `constitution`, `law`, `decision`, `administrative_regulation`,
  `judicial_interpretation`, `local_regulation`, `supervision_regulation`. The
  application layer maps levels to raw `law_type` values
  (`retrieval/filter.py::LEGAL_LEVEL_TYPES`), so changing the taxonomy never
  requires re-ingestion. An unknown level name is ignored rather than turning the
  query into "match nothing".
- `province` / `city` — region filters that **keep national documents**:
  `province == ""` (statutes, administrative regulations, judicial
  interpretations — documents with no region) always survives, so a region-scoped
  query narrows local regulations without hiding national law. When both are
  given, a local document must match both.

`RetrievalFilter` (the existing Milvus pre-filter dataclass) gains `law_types`,
`province` and `city` and builds the `expr`; `RetrievalTestRequest.to_filter()`
translates the request. Both retrieval paths push it down: the single-KB path
passes `expr=` to `VectorRetriever` / `HybridRetriever.search_with_trace`, the
multi-source path passes `filters=` to `MultiKBRetriever`, which already merges a
global `expr` with each source's own.

`decision` is its own level rather than part of `law`: 修改、废止的决定 exist at both
the national and the local tier and the corpus only records the category, so
forcing them into `law` would put provincial repeal decisions in the national
hierarchy. `supervision_regulation` (监察法规) is likewise its own level rather than
being folded into administrative regulations, which also resolves the open
question left in the ingest checklist.

## Alternatives considered

**Exposing raw `law_type` values as the filter parameter.** Rejected: it leaks 12
corpus categories (`修改、废止的决定`, `法规性决定`, …) into the public contract and
makes any taxonomy change a breaking API change.

**Filtering regions strictly (dropping documents with no province).** Rejected: a
"物业费 + 北京市" query would then exclude 民法典 and every national statute, which
contradicts the PRD's intent that local regulations be *triggered*, not used as an
exclusive whitelist.

**Post-filtering in PostgreSQL after recall.** Rejected in the storage proposal
and unchanged here: 法律 is 1.6% of the corpus, so post-filtering a `recall_k` of
128 candidates would leave a couple of usable hits.

**Treating `修改、废止的决定` as part of the law level.** Rejected: those decisions
come from both national and local bodies, so the mapping needs region information
the corpus does not carry per category; the follow-up is to split them by
`province` inside the level mapping once we decide the national/local boundary.

## Consequences

Filtering happens inside Milvus, so it is pre-filtering rather than post-hoc
truncation, and the recall pool stays fully usable. The three fields are optional
and default to no filtering, so existing callers see no behaviour change.

The level mapping is now a maintained artefact: adding a corpus category (for
example 部门规章, which the corpus does not have) requires a mapping entry, not a
re-ingest. `decision` and `supervision_regulation` being separate levels means a
caller asking for "law only" will not see national repeal decisions unless it
also passes `decision`; the contract documents this and the PRD's three tiers can
be expressed either way.

## Testing

`tests/test_retrieval_legal_filters.py` pins the mapping (law covers statutes and
their interpretations but not administrative or local regulations; decisions and
supervision regulations are separate levels; unknown levels are ignored) and the
expression semantics, including that a province filter keeps `province == ""` and
that quotes are escaped.

Verified against a running deployment: with five local regulations ingested,
`law_levels=["local_regulation"] + province="江西省"` returned only the Jiangxi
regulation, `province="福建省"` returned only the Fujian ones, and
`law_levels=["law"]` returned zero results because the library holds no
national-level documents yet.
