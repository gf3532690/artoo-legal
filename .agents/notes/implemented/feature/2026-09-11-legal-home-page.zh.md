# Agent Note: A home page, and the legal library inside the app shell

Status: implemented

本note取代 [legal-ui-surface-alignment](2026-09-11-legal-ui-surface-alignment.zh.md)
里「非超管落地页指向 `/legal`」那一条。

## Problem

两个问题，其中一个是走一遍流程才会暴露的：

1. 登录后直接落进 **全局法条库**（内容维护页）。那是**动作**（上传/删除法条），不是**位置**；
   它默认了「用户登录后第一件事是改语料」。
2. `/legal` 当初是作为**顶层路由**注册的，在 `<Layout>` 路由之外。于是打开法条库页面
   **没有左侧菜单**，也没有 `RequireAuth` 守卫。从菜单能进来，但出去只能靠浏览器后退或改 URL。

## Decision

- 新增**首页**（`/home`）作为登录后的落地页：说明本部署是什么，并列出当前身份能去的入口
  （租户管理员：全局法条库；超管：检索测试 / Embedding / API Key / 租户管理；按需含用户管理、
   审计日志）。它是位置，不是动作。
- `DefaultLanding` 统一跳 `/home`（此前：超管 → `/tenants`，其余 → `/legal`）。
- 把 `home` 与 `legal` **都挪到 `<Layout>` 路由之下**，让它们带 `RequireAuth` 与侧栏渲染。
   这是问题 2 的修复；只改落地页会把问题藏起来而不是修掉。
- 侧栏新增「首页」，对所有已登录身份可见；并在超管路径白名单里放行 `/home`
  （否则守卫会把超管从新落地页弹回租户管理）。

## Alternatives considered

**继续把 `/legal` 当落地页。** 否决：那是维护动作，而且没解决「页面缺外壳」的问题。

**超管落租户管理、租户管理员落法条库（即原状）。** 否决：同一产品两个落地页，且租户管理员那个
仍是维护页。

**给 `/legal` 单独套一层布局包装，而不是挪进 `<Layout>`。** 否决：`<Layout>` 就是那层包装，
再写一层等于把侧栏、artifact 面板、账号菜单重复一遍。

**保持顶层路由，靠菜单返回。** 否决：菜单是 `<Layout>` 渲染的；在它之外的页面没有菜单。

## Consequences

登录后的第一屏变成概览而不是表单。`/legal` 现在与其它已登录页面行为一致：有侧栏、有
`RequireAuth`、切换路由时收起 artifact 面板。首页的入口卡片与侧栏共用同一套角色模型，
两者要出现矛盾只能是因为改了同一处代码。

API 侧没有任何变化：首页只做跳转。

## Verification

`frontend/` 下 `npm run build`（tsc + vite）与 `npm test`（34 条）通过。

在运行中的环境重建 `artoo-frontend:legal` 镜像并重启容器：`/home`、`/legal`、`/` 均返回 SPA，
且服务出去的 bundle 里含首页新文案（`首页`、`对外检索接口`），说明跑的是新构建而非旧缓存。
