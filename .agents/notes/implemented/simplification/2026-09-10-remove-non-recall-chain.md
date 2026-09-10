# Agent Note: Remove the non-recall chain

Status: implemented

## Problem

The 法条库 deployment answers retrieval queries only. Upstream Artoo ships a
conversation stack — ReAct Agent, chat completions, MCP client and server,
skills, agent presets, chat sessions and session attachments — plus the pages
that configure them. None of it serves the legal recall product, and several
parts are entangled with the retrieval and ingestion code that this deployment
does need.

## Decision

Delete the conversation stack from this product line rather than hiding it:

`app/agent/**`, `app/api/chat.py`, `app/api/query_understanding.py`,
`app/mcp/**`, `app/mcp_server.py`, `app/api/mcp_config.py`, `app/api/skills.py`,
`app/api/agent_config.py`, `app/session_upload/**`, `app/api/session_upload.py`,
`app/api/session.py`, and the corresponding frontend pages and routes.

Six couplings must be resolved first, because deleting by directory would break
unrelated features:

1. `session_upload/limits.py` is not session-specific — it is the ordinary
   upload capacity checker used by `api/document.py` and `pipeline/pipeline.py`.
   It moves to `app/pipeline/limits.py`.
2. `api/retrieval.py` imports `session_upload.service` for the session
   attachment source; remove that branch.
3. `api/system.py` imports `recommend_kb_chunk_cap` from `session_upload.memory`
   — a KB chunk-cap recommendation that was never session-specific either. It
   moves to `app/retrieval/memory.py`.
4. `api/capability_reload.py` imports `invalidate_mcp_tools_cache` from
   `app.agent.tools.mcp_client`.
5. `main.py` initializes the session-upload EventHub, queue and subscribe loop
   in its lifespan.
6. `worker_main.py` starts the session-upload worker.

In addition, `_get_llm_for_request` — currently defined in `api/chat.py` — must
be extracted to a neutral module, because `storage/graph_store.py` and
`pipeline/graph/worker.py` still import it inside functions.

## Alternatives considered

**Hide the pages and leave the backend intact.** Rejected for this deployment:
Artoo continues to exist as a separate application, so nothing downstream
depends on these modules here, and removing them narrows both the public
contract (`/v1/chat/completions`, the MCP server) and the configuration surface.

**Delete the knowledge-graph code as well.** Rejected: the graph is an optional
retrieval enhancement rather than a conversation feature, and it is inert while
`GRAPH_ENABLE` is false. Removing it would touch `retrieval/factory.py`,
`config.py`, `main.py` and `api/system.py` for no runtime gain, so it follows
the OCR / ASR precedent of "disabled by configuration, module kept".

**Move the LLM resolver instead of deleting chat.** This is what the decision
does; it is listed here because the earlier draft claimed no extraction was
needed, which was wrong once the graph code was kept.

## Consequences

The deployment loses the conversation API and the MCP server, and the left-hand
menu shrinks to the legal library, retrieval test, Embedding (which also manages
Rerank), API keys, tenants, users and audit logs.

Anyone syncing from upstream must not re-import the deleted modules; the
divergence is intentional and permanent for this product line.

`session_upload/limits.py` moving out of its package changes import paths in
two files — a mechanical change, but one that must land before the directory is
removed.

## Testing

Import and route tests confirm the application still starts and that the
retrieval and ingestion paths no longer reference removed modules. Push-time
verification is out of scope for this release.
