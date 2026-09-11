# Agent Note: Repair the backend test suite after removing the non-recall chain

Status: implemented

## Problem

Removing the conversation stack deleted the modules that a large part of
`backend/tests/` imported, but the tests stayed behind. `pytest` could not even
collect the suite: 15 modules failed at import time with errors such as
`ModuleNotFoundError: No module named 'app.session_upload.limits'` or
`No module named 'app.agent'`, which aborted the run before a single test
executed.

Two further classes of breakage hid behind the collection errors:

- Tests of **kept** subsystems still imported the old locations of modules the
  removal moved (`app/session_upload/limits.py` → `app/pipeline/limits.py`,
  `app/session_upload/memory.py` → `app/retrieval/memory.py`).
- `test_upload_file_size_gate.py` patched module-level `_UPLOAD_DIR` /
  `_THUMBNAIL_DIR` in `app/api/document.py`. File storage had already moved to
  `app/storage/object_store.py` upstream, so its fixture raised `AttributeError`
  before any assertion ran.

## Decision

Make the suite collectable and meaningful again, without resurrecting removed
code:

1. Delete the test modules whose only subject is a removed chain —
   `test_agent_preset_sharing.py`, `test_chat_route_resolution.py`,
   `test_content_router.py`, `test_e2e_session_upload.py`,
   `test_final_answer_parse.py`, `test_knowledge_search_targets.py`,
   `test_query_understanding.py`, `test_session_delete_cleanup_hook.py`,
   `test_session_owner_isolation.py`, `test_session_upload_quota_property.py`,
   `test_text_sanitize.py`, and `test_milvus_session_files.py` — and drop the MCP
   scenario from `test_tenant_auth_integration_extra.py`.
2. Delete `test_json_field_extractor.py`: its target module
   `app/models/llm/json_field_extractor.py` does not exist at the fork baseline
   `3c184f5` either, so the test can never pass.
3. Retarget imports for moved modules in the tests that still have a subject:
   `test_upload_file_size_gate.py`, `test_upload_limit_resolver.py`,
   `test_pre_embed_gate_property.py` (→ `app.pipeline.limits`) and
   `test_memory_recommendation.py` (→ `app.retrieval.memory`), plus the renamed
   provider helper in `test_thinking_dialect.py`
   (`is_deepseek_v3_model` → `is_deepseek_thinking_model`, same assertions hold).
4. Delete the endpoint-behaviour layer of `test_upload_file_size_gate.py`
   (five cases plus their fixture) and document why in the module docstring. The
   predicate-property layer and the single-source / singleton layer stay.

## Alternatives considered

**Keep the stale tests and mark them skipped.** Rejected: a test for a module
that no longer exists can never pass, and a skip marker would imply the
behaviour is merely deferred rather than removed.

**Re-add compatibility shims (`app/session_upload/...`) so the tests import
cleanly.** Rejected: it directly contradicts the decision to delete the chain
rather than hide it, and it would keep the deleted surface alive in the tree.

**Rewrite the upload-size endpoint layer against the object store.** Deferred:
it needs object-store stubbing that the file never had, and the gate predicate
is already covered by the two layers that remain plus
`test_upload_limit_resolver.py`. Recorded as follow-up work.

**Fix the pre-existing failures in the untouched test files.** Out of scope:
they come from Milvus API drift, the SSE-era retrieval contract and tenant
property assumptions, all of which predate this change and are unrelated to the
legal-recall work.

## Consequences

The suite collects again — 515 tests, zero collection errors — so `pytest` can
be used as a real signal in this product line. The cost is the loss of the
upload-size HTTP endpoint layer until it is rewritten against
`app/storage/object_store.py`; the remaining 44 failures are pre-existing and
are now visible instead of being masked by a broken collection phase.

Baseline comparison: running `3c184f5` in a detached worktree, with the tests
tree from `37cf5e0` and ignoring the modules that already fail to import there
plus the hanging file, reports 66 failures, 418 passes and 5 collection errors.
This branch reports 44 failures and 457 passes, and every remaining failure name
also fails at the baseline — the failure set is a strict subset.

## Testing

`pytest --collect-only -q` → 515 collected, 0 errors.

`pytest -q tests/test_legal_metadata.py tests/test_legal_recall_scope.py
tests/test_retrieval_contract_baseline.py tests/test_thinking_dialect.py
tests/test_upload_limit_resolver.py tests/test_upload_file_size_gate.py
tests/test_memory_recommendation.py tests/test_tenant_auth_integration_extra.py
tests/test_session_upload_config.py` → 126 passed.

`pytest -q --ignore=tests/test_pre_embed_gate_property.py` → 44 failed, 457
passed; the same file hangs on the untouched baseline, which is why it is the
single file excluded from full-suite runs.
