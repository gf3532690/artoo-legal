# Agent Note: Single-tenant bootstrap for the legal library

Status: implemented

## Problem

本部署需要「默认就存在一个全局法条库、并由人通过界面维护」。上游没有这个概念：
知识库由用户创建，而平台 Super_Admin **完全不能碰 KB 内容**——
`kb_authorization_decision` 的第一判定是跨租户隔离，Super_Admin 的 `tenant_id`
为 `None`，而 `assemble_allowed_kb_ids` 对 platform 身份按设计返回空集。因此
「超管维护全局库」在不削弱租户模型的前提下无法实现。

## Decision

本部署是**单租户**，由引导创建它的世界：

- `_default_legal_tenant_bootstrap` 在 Super_Admin 步骤之后运行，且幂等。它创建
  固定 id 的默认租户（`tenant-legal-default`）、可选的租户管理员，以及全局法条库。
- 租户管理员来自 `LEGAL_TENANT_ADMIN_USERNAME` / `LEGAL_TENANT_ADMIN_PASSWORD`。
  与 `SUPER_ADMIN_*` 不同，缺失时**不** fail-fast——部署方也可能经管理端点手工
  建号；此时记 warning，且全局库的 owner 留空。
- 全局库按 `config.is_default_legal_kb` 标记查找而非按名称（名称可改）。创建时写
  `chunker_type=laws`、`visibility=organization`、`org_permission=read`，
  `owner_user_id` 指向租户管理员。于是同租户身份**不需要任何授权例外**即可读取，
  而 owner 可以维护它。
- `GET /api/knowledge-bases/legal/global` 暴露该库，使 `/legal` 入口能解析到它并
  直接渲染内容维护页，而不必经过知识库列表。

## Alternatives considered

**让 Super_Admin 维护全局库。** 否决：现行授权模型拒绝 platform 身份访问任何知识库，
前端还额外把 `/knowledge-bases` 从超管菜单里排除。让它成立意味着改动一条刻意的边界。

**为 platform 身份加一条显式授权例外。** 否决：那会把超管对所有租户内容的可见性一起
打开，远超本部署所需的这一个库。

**每个租户各播种一份法条库。** 否决：本部署是单租户，且下游本就按 `kb_id` 指定库，
多份全局库只会带来"哪一份才是权威"的歧义。

**不做界面，用脚本灌库。** 否决：修订是周期性的手工操作，必须有一条人能使用的维护路径。

## Consequences

本部署现在需要配置 `LEGAL_TENANT_ADMIN_USERNAME` / `LEGAL_TENANT_ADMIN_PASSWORD`
才可维护；缺失时全局库存在但只读。

所有调用方身份必须属于默认租户，否则读不到全局库——这是单租户模型赖以成立的前提，
已记录在方案 D4。

侧边栏新增一个仅管理员可见的入口「法条库」，取代上游的「知识库」列表入口。
哪些菜单项存在，仍以方案决策清单为准。

账号、全局库，以及它与下游创建的个人库之间关系的**面向运维**表述在
`deploy/DEPLOY.md` 第四章与第六章（随离线包一起交付）。

## Testing

引导路径在构造上即幂等（租户 id 固定；库按标记查找）。单测覆盖标记判定；完整引导需要
数据库，由部署环境验证，不在单测范围内。
