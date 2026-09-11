# Agent Note: Align the legal deployment's UI surface

Status: implemented

## Problem

法条库部署已经改过文案、也收缩过左侧菜单，但**产品面**仍在描述一个在本部署里
并不存在的产品：

- `pages/Landing.tsx` 渲染了 `<AgentDemo />`（ReAct 对话演示），能力卡片还在讲
  ReAct Agent、知识图谱、MCP 工具与邀请注册。
- `pages/OcrServices.tsx`、`pages/AsrServices.tsx`、`pages/Invitations.tsx` 三张
  页面与路由仍然存在，而已确认的菜单表已把这三项全部移除。
- 四个页面的用户可见文案里还留着上游品牌名 `Artoo`；FastAPI 的 `title` /
  `description` 与根路径消息也仍写着 `Agentic RAG System`。
- 非超管登录后的默认落地路由指向已不存在的 `/chat`，会落到没有匹配的路由。
- `lib/api.ts` 里仍导出已随非召回链路删除的端点客户端（`sessions`、会话文件、
  `mcp-configs`、`skills`、`agent-presets`），Artifact 预览面板也还留着会话附件分支。

## Decision

让产品面与已交付的能力对齐，且只改展示层：

1. 删除 `AgentDemo`，重写 `Landing.tsx`：围绕语义召回、结构化入库、目录剥离、
   全局库 + 个人库、开放检索接口、轻量可私有化部署来组织内容。
2. 删除 `OcrServices.tsx`、`AsrServices.tsx`、`Invitations.tsx` 及其路由。它们的
   **后端**模块、数据表与 API 全部保留——与 OCR / ASR 配置采用同一套
   「模块保留、UI 撤销」的切法。
3. 删除死掉的 API 客户端与 `session-file` 预览来源；面板现在只有 `document`
   一种来源。
4. 非超管的落地路由改指 `/legal`。
5. 用户可见的 `Artoo` 一律改为「法条库」；FastAPI 的标题、描述与根路径消息改为
   法条召回口径。代码标识符一律不动：`artoo.jwt` 存储键、CSS 类名、compose 服务名、
   表名与 API 路径都不属于展示层。

`InviteAccept`（`/invite/:token`）保留：它是深链页面而不是菜单项，且后端邀请能力
仍然保留。

## Alternatives considered

**落地页维持原样。** 否决：它宣传的 ReAct Agent、图谱检索与 MCP 工具在本部署里
都不存在，那个演示组件驱动的对话接口会直接 404。

**三张页面改为隐藏而不删除。** 否决：它们已无法从菜单进入，是打包产物里的死重量。
删掉路由能实打实减小构建产物，这正好符合「裁剪要能明显提升轻量化」这条标准；后端
能力仍可通过 API 使用。

**顺带删掉会话 / 会话文件 / MCP 配置 / 智能体预设 / 技能这几张表与 `mcp_*` 配置块。**
暂缓：删表会连带牵动 `storage/database.py` 的迁移语句与
`repositories/tenant_repo.py` 的隔离类清单，而 `api/document.py` 的
`/api/files/{file_id}/content` 仍在读 `SessionFile`。收益不抵与上游同步的成本。

**把文案集中到一个 labels 模块。** 改版阶段已否决：该术语出现上百次，一次性替换
能到达同样的终态，却不必做一百多处 import 重构。

## Consequences

构建产物里不再有智能体演示与 OCR / ASR / 邀请页面，也不再携带调用已删端点的客户端
——将来若要恢复这些客户端，只能从上游取，不能在本地复活。

后端契约不变：路由表仍是 117 条，保留的 OCR / ASR / 邀请端点仍可被程序调用。
有意保留的面记录在方案 §15.4（MCP 配置与聊天 / 会话 ORM 模型、OCR / ASR / 邀请的
API 客户端、邀请领取页）。

## Testing

后端：`pytest --collect-only -q` 收集 515 条、0 错误；法条召回、上传限制、内存推荐与
租户相关的用例 126 条通过；全量运行（排除基线同样挂起的那一个文件）为 44 失败 /
457 通过，失败集合是改造前基线 66 个失败的真子集。

前端：`npm run build` 通过，`npm test` 7 个文件 34 条通过。
