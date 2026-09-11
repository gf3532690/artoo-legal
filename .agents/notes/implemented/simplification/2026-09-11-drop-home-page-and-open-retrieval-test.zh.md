# Agent Note: Drop the home page and give retrieval test to the corpus maintainer

Status: implemented

本note取代 [legal-home-page](../feature/2026-09-11-legal-home-page.zh.md) 的落地页决定，
而那条又取代过 [legal-ui-surface-alignment](../architecture/2026-09-11-legal-ui-surface-alignment.zh.md)。

## Problem

上一轮加了 `/home`，防止登录后直接落在全局法条库（那是个维护动作）。但这个页面本身只画了几张
入口卡片，每一张都对应左侧菜单里已有的一项——等于让菜单多出一层不携带任何信息的导航。

而真正在登录后有用的是 **检索测试**（`/retrieval`）——它是验证语料能否召回的**唯一**界面：
输入 query，看命中的 `law_name` / `article_label` 与各路 trace，正是排查「我传了这部法条，
为什么检索不回来」的手段。它此前只在超管专属的能力组里，但超管按定义是平台装配身份、不碰内容；
维护语料、需要验收的是租户管理员。

## Decision

- **删掉首页**：`pages/Home.tsx`、它的路由、菜单项与超管白名单条目。
- **把「检索测试」开放给租户管理员**：从超管专属的 `capability` 组移出，单列一组，对超管与
  租户管理员可见。
- **落地页**：超管 → 租户管理（同前），租户管理员 → 检索测试。

后端无需改动：`POST /api/retrieval/test` 与它背后的知识库列表都是 `require_authenticated()`，
而租户管理员可读全局库（organization + read）。

## Alternatives considered

**保留首页。** 否决：它是第二层导航，内容全是侧栏已经提供的链接。

**检索测试继续只给超管。** 否决：它是语料验收工具，而装配平台的身份并不维护语料；把它对维护者
藏起来，等于这个部署除了手写 API 调用之外没有任何办法验证召回。

**租户管理员落全局法条库（加首页之前的行为）。** 否决：理由与当初加首页相同——一登录就被塞进
维护动作。

## Consequences

少一个页面。租户管理员的第一屏是检索测试（一个合理的「这个部署是干什么的 / 我的语料好不好使」
入口），菜单为：法条库 / 检索测试 / API Key / 用户管理 / 审计日志。超管仍以租户管理作为落地页。

成员仍然没有菜单项——本决定未改变这一点（这条产品线的侧栏只有租户管理员与平台两套面）。

## Verification

`frontend/` 下 `npm run build`（tsc + vite）与 `npm test`（34 条）通过。

UI 未在浏览器中手工点过；该改动的可见性依赖重打部署包。
