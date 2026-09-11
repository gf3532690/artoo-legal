# Agent Note: Repair the backend test suite after removing the non-recall chain

Status: implemented

## Problem

删除对话链路连带删掉了 `backend/tests/` 里一大批测试所依赖的模块，但测试本身留在
了原地。`pytest` 连收集都过不去：15 个模块在 import 阶段就报
`ModuleNotFoundError: No module named 'app.session_upload.limits'`、
`No module named 'app.agent'` 之类的错误，整轮运行在第一条用例执行前就中断了。

收集错误背后还藏着两类问题：

- 被测对象**仍然保留**的测试，仍在 import 被搬走的模块旧路径
  （`app/session_upload/limits.py` → `app/pipeline/limits.py`、
  `app/session_upload/memory.py` → `app/retrieval/memory.py`）。
- `test_upload_file_size_gate.py` 还在给 `app/api/document.py` 的模块级
  `_UPLOAD_DIR` / `_THUMBNAIL_DIR` 打桩。文件落盘在上游早已迁到
  `app/storage/object_store.py`，所以它的 fixture 在任何断言之前就 `AttributeError`。

## Decision

让测试套件重新可收集、可判读，同时不复活已删代码：

1. 删除只测已删链路的测试模块：`test_agent_preset_sharing.py`、
   `test_chat_route_resolution.py`、`test_content_router.py`、
   `test_e2e_session_upload.py`、`test_final_answer_parse.py`、
   `test_knowledge_search_targets.py`、`test_query_understanding.py`、
   `test_session_delete_cleanup_hook.py`、`test_session_owner_isolation.py`、
   `test_session_upload_quota_property.py`、`test_text_sanitize.py`、
   `test_milvus_session_files.py`，并移除
   `test_tenant_auth_integration_extra.py` 里的 MCP 场景。
2. 删除 `test_json_field_extractor.py`：它的目标模块
   `app/models/llm/json_field_extractor.py` 在 fork 基线 `3c184f5` 上同样不存在，
   这条测试永远不可能通过。
3. 对仍有被测对象的测试改 import 路径：`test_upload_file_size_gate.py`、
   `test_upload_limit_resolver.py`、`test_pre_embed_gate_property.py`
   （→ `app.pipeline.limits`）与 `test_memory_recommendation.py`
   （→ `app.retrieval.memory`）；`test_thinking_dialect.py` 同步改名后的
   provider 辅助函数（`is_deepseek_v3_model` → `is_deepseek_thinking_model`，
   原有断言依然成立）。
4. 删除 `test_upload_file_size_gate.py` 的「端点行为」层（5 条用例及其 fixture），
   并在模块 docstring 注明原因。谓词属性层与「单源 / 单例」层保留。

## Alternatives considered

**保留失效测试并标记 skip。** 否决：目标模块已不存在的测试永远不可能通过，而 skip
标记会让人误以为该行为只是延后实现，而非已被移除。

**补一层兼容 shim（`app/session_upload/...`）让测试能 import。** 否决：这与「真正
删除而不是隐藏」的决策直接冲突，等于把已删的面继续留在代码树里。

**按 object store 重写那条端点用例。** 暂缓：原文件从来没有对象存储的打桩，而闸门
谓词已被保留的两层加 `test_upload_limit_resolver.py` 覆盖。记为后续任务。

**顺手修掉未触碰文件里的存量失败。** 超出范围：它们来自 Milvus API 漂移、SSE 时代的
检索契约与租户属性假设，都早于本次改动，与法条召回改造无关。

## Consequences

套件重新可收集——515 条、0 个收集错误——`pytest` 在这条产品线上重新成为有效信号。
代价是在按 `app/storage/object_store.py` 重写之前，失去上传大小闸门的 HTTP 端点层
覆盖；剩下的 44 个失败都是存量问题，现在只是从「收集阶段就崩」变成「看得见」。

基线对比：在 detached worktree 上跑 `3c184f5`，测试目录取 `37cf5e0` 的版本，并忽略
「在基线上本来就 import 失败」的模块与挂起文件，结果是 66 失败 / 418 通过 /
5 个收集错误；本分支是 44 失败 / 457 通过，且每一条剩余失败在基线上同样失败——
失败集合是真子集。

## Testing

`pytest --collect-only -q` → 515 collected，0 errors。

`pytest -q tests/test_legal_metadata.py tests/test_legal_recall_scope.py
tests/test_retrieval_contract_baseline.py tests/test_thinking_dialect.py
tests/test_upload_limit_resolver.py tests/test_upload_file_size_gate.py
tests/test_memory_recommendation.py tests/test_tenant_auth_integration_extra.py
tests/test_session_upload_config.py` → 126 passed。

`pytest -q --ignore=tests/test_pre_embed_gate_property.py` → 44 failed / 457
passed；该文件在未改动的基线上同样挂起，这也是它成为全量运行中唯一被排除文件的
原因。
