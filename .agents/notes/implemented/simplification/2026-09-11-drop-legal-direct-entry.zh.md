# Agent Note: 去掉法条库的「直达某个库」入口，后台回到库列表

Status: implemented

取代 [single-tenant-legal-bootstrap](../architecture/2026-09-10-single-tenant-legal-bootstrap.md)
里的 `/legal` 入口，以及 [legal-home-page](../feature/2026-09-11-legal-home-page.md)
里对它的路由搬移。

## Problem

直达入口（`/legal`——解析全局法条库后直接渲染它的文档页）是在「本部署只有一个库，入口
替用户选中即可」这个假设下写的。它有两处问题：

1. 后台里**不止全局库**：租户管理员名下可能还有个人库，外部用户的库也会出现在同一个
   列表里。替用户选中一个，就把「我正在看哪个库」藏了起来——菜单项、面包屑、URL 看起来
   和任何别的库都一样。
2. 它是绕开 `/knowledge-bases/:id` 的**旁路**。这逼着 `Documents.tsx` 长出
   `explicitKbId` 参数，而它唯一的调用方就是那条路由——「我在编辑哪个库」于是有了第二个
   答案来源。

这种混淆是不对称的：lite 端（`law-agent-lite-application`，由本库支撑）一个用户只有
**一个**个人库，库名由后端创建并锁定，所以那边「菜单 → 内容」是唯一合理的形态。那个决定
属于另一个仓库；本记录说明两端为什么有意不同。

## Decision

- 删除 `frontend/src/pages/LegalLibrary.tsx` 及其 `/legal` 路由。
- `法条库` 菜单指回 `/knowledge-bases`。管理员在那里选库，落到 `/knowledge-bases/:id`。
- 删除 `knowledgeBaseApi.getGlobalLegal`（该端点的唯一消费者就是被删的页面）。
- 删除 `Documents.tsx` 的 `explicitKbId` 参数；库 id 重新只来自 `useParams().id`，
  不再有第二个来源。

后端保留 `GET /api/knowledge-bases/legal/global`：它是「解析全局法条库」的稳定契约，
后端测试套件引用了它，保留也不产生成本。

## Alternatives considered

**保留直达入口，在它头部加一个库切换器。** 否决：那等于在一个库的页面里重新实现库列表，
而列表本来就在一次点击之外。

**保留 `/legal`，但身份名下不止全局库时改为跳库列表。** 否决：同一个界面会因为不可见状态
表现不同，正是我们要消掉的那种混淆。

**连 `/api/knowledge-bases/legal/global` 端点一起删。** 否决：后端测试在用，而且它是唯一
能在不引入命名约定的前提下回答「全局库是哪一个」的端点（文件名明确不作权威，库名用户可改）。

**为对称起见，让 lite 端菜单也先走库列表。** 否决：lite 用户只有一个库，那个列表只有一行。

## Consequences

在后台维护语料的代价是多一次点击（菜单 → 列表 → 库）。换来的是「我在看哪个库」始终可见，
且 `Documents.tsx` 对库 id 只有单一来源。API、schema、存储均无改动；菜单的文案、图标与
可见性不变。

lite 端继续点菜单直达个人库文件列表，两端不再共用一条规则——本记录是它为什么如此的地方。

## Verification

`frontend/` 下 `npm run build`（tsc + vite）与 `npm test`（34 个用例）通过。

`artoo-frontend:legal` 镜像已带上本次改动重建，随后栈也用这份镜像重启并复核过：容器对外
提供的产物里既没有被删页面的文案（未找到全局法条库），也没有它对
`knowledge-bases/legal/global` 的调用。「点菜单落在库列表」本身仍未在浏览器里点过——
只验了产物。
