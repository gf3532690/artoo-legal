# Agent Note: Remove the non-recall chain

Status: proposed

## Problem

法条库部署只回答检索查询。上游 Artoo 附带一整套对话链路——ReAct Agent、对话补全、
MCP 客户端与服务端、技能、智能体预设、会话与会话附件——以及配置它们的页面。它们
无一服务于法条召回产品，而其中若干部分又与本部署确实需要的检索和入库代码相互纠缠。

## Decision

在本产品线中**删除**而非隐藏这套对话链路：

`app/agent/**`、`app/api/chat.py`、`app/api/query_understanding.py`、`app/mcp/**`、
`app/mcp_server.py`、`app/api/mcp_config.py`、`app/api/skills.py`、
`app/api/agent_config.py`、`app/session_upload/**`、`app/api/session_upload.py`、
`app/api/session.py`，以及对应的前端页面与路由。

必须先解决六处耦合，否则按目录删除会打断无关功能：

1. `session_upload/limits.py` 不是会话专属——它是普通上传的容量校验器，被
   `api/document.py` 与 `pipeline/pipeline.py` 使用。删除该包之前先把它挪到中立位置。
2. `api/retrieval.py` 为会话附件源引用了 `session_upload.service`；移除该分支。
3. `api/system.py` 从 `session_upload.memory` 引用了 `recommend_kb_chunk_cap`。
4. `api/capability_reload.py` 从 `app.agent.tools.mcp_client` 引用了
   `invalidate_mcp_tools_cache`。
5. `main.py` 在 lifespan 中初始化会话上传的 EventHub、队列与订阅循环。
6. `worker_main.py` 启动会话上传 Worker。

此外，`_get_llm_for_request`（当前定义在 `api/chat.py`）必须抽到中立模块，因为
`storage/graph_store.py` 与 `pipeline/graph/worker.py` 仍在函数内 import 它。

## Alternatives considered

**隐藏页面、后端原样保留。** 本部署否决：Artoo 作为独立应用继续存在，因此没有任何
下游依赖这里的这些模块；删除它们同时收窄了对外契约（`/v1/chat/completions`、MCP
服务端）与配置面。

**连知识图谱代码一并删除。** 否决：图谱是可选检索增强而非对话功能，且在
`GRAPH_ENABLE=false` 时完全惰性。删除它会牵动 `retrieval/factory.py`、`config.py`、
`main.py`、`api/system.py`，却没有运行期收益，因此按 OCR / ASR 的先例处理——「配置
关闭、模块保留」。

**改为抽取 LLM 解析器而非删除 chat。** 这正是本决策的做法；列在此处是因为早先的草稿
声称无需抽取，而在保留图谱代码之后那个判断是错的。

## Consequences

本部署失去对话 API 与 MCP 服务端，左侧菜单收缩为法条库、检索测试、Embedding（同时
管理 Rerank）、API Key、租户管理、用户管理与审计日志。

从上游同步的人不得把这些模块重新引入；这条分叉对本产品线是有意且永久的。

`session_upload/limits.py` 移出原包会改变两个文件里的 import 路径——改动是机械的，
但必须在删除目录之前落地。

## Testing

导入与路由测试确认应用仍能启动，且检索与入库路径不再引用已删除模块。本版本不含推送期
验证。
