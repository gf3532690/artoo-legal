# Agent Note: Align the legal deployment's UI surface

Status: implemented

## Problem

The 法条库 deployment rebranded its labels and shrank its left-hand menu, but its
**surface** still described a product that no longer exists here:

- `pages/Landing.tsx` rendered `<AgentDemo />` — a ReAct conversation demo — and
  its capability cards advertised ReAct agents, the knowledge graph, MCP tools
  and invite-based registration.
- `pages/OcrServices.tsx`, `pages/AsrServices.tsx` and `pages/Invitations.tsx`
  were still routed, although the approved menu table removes all three entries.
- Four pages still carried the upstream brand name `Artoo` in user-visible copy,
  and the FastAPI `title` / `description` plus the root message still said
  `Agentic RAG System`.
- The post-login landing route for non-super-admin users pointed at `/chat`,
  which no longer exists, so those users landed on an unmatched route.
- `lib/api.ts` still exported clients for endpoints that were deleted with the
  non-recall chain (`sessions`, `session files`, `mcp-configs`, `skills`,
  `agent-presets`), plus the session-file branch of the artifact preview panel.

## Decision

Bring the surface in line with the shipped feature set, changing only the
display layer:

1. Delete `AgentDemo` and rewrite `Landing.tsx` around legal recall — semantic
   retrieval, structured ingestion, table-of-contents stripping, the
   global-plus-personal library pair, the open retrieval endpoint, and
   lightweight private deployment.
2. Delete `OcrServices.tsx`, `AsrServices.tsx` and `Invitations.tsx` together
   with their routes. Their **backend** modules, tables and APIs stay: this is
   the same "module kept, UI removed" split used for OCR / ASR configuration.
3. Delete the dead API clients and the `session-file` artifact source; the
   panel now has exactly one source (`document`).
4. Point the non-super-admin landing at `/legal`.
5. Replace user-visible `Artoo` strings with 法条库, and set the FastAPI title,
   description and root message to legal-recall wording. Code identifiers stay
   untouched: the `artoo.jwt` storage key, CSS class names, compose service
   names, table names and API paths are not part of the display layer.

`InviteAccept` (`/invite/:token`) stays: it is a deep link rather than a menu
entry, and the invitation backend is retained.

## Alternatives considered

**Keep the landing page as-is.** Rejected: it advertises a ReAct agent, a graph
retriever and MCP tooling that this deployment cannot serve, and the demo
component drives a conversation API that returns 404.

**Hide the three pages instead of deleting them.** Rejected: they are
unreachable from the menu, so they are dead weight in the bundle. Deleting the
routes measurably shrinks the built output, which is exactly the bar set for
cutting surface area; the backend capabilities stay available through their
APIs.

**Also drop the ORM models for chat sessions, session files, MCP configs,
agent presets and skills, and the `mcp_*` settings block.** Deferred: removing
them drags in the migration statements in `storage/database.py` and the
isolation list in `repositories/tenant_repo.py`, while `api/document.py` still
reads `SessionFile` for `/api/files/{file_id}/content`. The upstream-sync cost
outweighs the benefit.

**Route all copy through a central labels module.** Rejected in the rebrand
phase already: the term appears over a hundred times, and a one-shot replacement
reaches the same end state without a 100-plus import refactor.

## Consequences

The built frontend no longer ships the agent demo or the OCR / ASR / invitation
pages, and it no longer carries clients that call removed endpoints — so any
future re-introduction of those clients has to come from upstream rather than
being resurrected locally.

The backend contract is unchanged: the route table still has 117 entries, and
the retained OCR / ASR / invitation endpoints remain reachable for programmatic
use. Surfaces that intentionally remain are recorded in the implementation plan
§15.4 (MCP settings and the chat/session ORM models, the OCR / ASR / invitation
API clients, and the invite accept page).

## Testing

Backend: `pytest --collect-only -q` collects 515 tests with no errors; a run of
the legal-recall, upload-limit, memory-recommendation and tenant suites passes
126 tests; a full run (excluding the one file that hangs on the baseline too)
reports 44 failures and 457 passes, whose failure set is a strict subset of the
pre-change baseline's 66 failures.

Frontend: `npm run build` succeeds and `npm test` passes 34 tests across 7 files.
