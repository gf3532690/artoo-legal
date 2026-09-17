# Agent Note: A search box for the legal-library file list

Status: implemented

English | [中文](2026-09-17-legal-document-name-search.zh.md)

## Problem

The document list had three ways to narrow it — folder, page, validity status — and none of them by
name. The global legal library holds 22,036 files, so finding one law meant paging a hundred at a
time and reading filenames by eye. The page already *had* a status filter row and a view switch; what
was missing was the ordinary "type a few characters and find the file" affordance.

## Decision

`GET /api/knowledge-bases/{kb_id}/documents` takes an optional `q` and filters on the filename with
a case-insensitive substring match (`ILIKE %q%`).

- **Filename, not the chunk-level `law_name`.** The list displays `law_name` (read from the first
  chunk's metadata), so the honest alternative was to match that too. Measured first: across 500
  sampled documents, the law name is a substring of the filename in **500/500** cases — the
  mismatches that exist are name suffixes like `…条例(2).docx`, which still contain the law name.
  Matching `law_name` instead means a per-row lookup into the chunks JSON column (22k subqueries for
  a full scan); matching the filename is one sequential scan over `documents`.
- **Wildcards in user input are escaped.** `%`, `_` and `\` are turned into literal characters
  before the pattern is built (`ESCAPE '\'`). The box means "the filename contains these
  characters"; without escaping, searching `办法_` would silently become a wildcard match.
- **No index.** `%…%` cannot use a b-tree index, and at 23k rows the sequential scan is milliseconds;
a trigram index would be infrastructure to maintain for a query that is already fast.
- **Blank means no filter.** `q` is trimmed; an empty or whitespace-only value adds no condition, so
  callers can pass the box's raw value without special-casing it.

The frontend puts a search box in the toolbar next to the validity-status tags:

- debounced 300 ms — the list is server-filtered and infinitely scrolled, so one request per
  keystroke would rebuild the pagination state and make the list jump while typing;
- the keyword is part of the query key, so changing it restarts pagination (loaded pages of two
  different filters can never be concatenated);
- **folders are hidden while searching** — they come from a different endpoint and have no name-match
  semantics; leaving them listed both looks like "the keyword did nothing" and keeps `totalItems`
  above zero, which would suppress the "nothing matched" empty state;
- the empty state distinguishes 「没有匹配「X」的文件」 from 「暂无文档」, and offers a clear button.

## Alternatives considered

**Filter the already-loaded pages in the frontend.** Rejected: with server-side paging the loaded set
is a prefix, so matches beyond it are invisible, `total` disagrees with what the user sees, and
"scroll until you find it" silently stops working.

**Match the chunk-level `law_name` as well.** Rejected on measurement (see above): the filename
covers it in practice, and the chunk lookup costs an order of magnitude more for no observed gain.

**Add a trigram index on `filename`.** Rejected: it exists to make an already-millisecond query
faster, at the price of an extension and an index that every write must maintain.

**Also filter folders by the keyword.** Rejected for now: folders are navigation, the corpus stores
files at the root, and a folder whose name matches but whose file does not would be a dead end. If
folders ever hold legal files, this should be revisited deliberately.

**Give the keyword its own endpoint.** Rejected: it is one more condition on the same query, and a
separate endpoint would need its own paging, ordering and permission checks.

## Consequences

One optional query parameter; requests without `q` behave exactly as before, including the response
shape. `q` composes with `validity_status` and paging (all three are ANDed).

Searching by a name is now O(scan) instead of O(paging by hand), which matters most in the global
library and not at all in a personal library with a handful of files — the same box serves both.

## Verification

`tests/test_legal_document_name_search.py`: the pattern builder wraps with `%…%` and escapes `%`,
`_` and `\` (backslash first, so the escape character is not escaped twice); the compiled SQL is
`ILIKE … ESCAPE '\'`; the endpoint still exposes `q`.

Frontend: `npm run build` (tsc + vite) and `npm test` — 62 tests, all passing.
