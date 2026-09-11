# Agent Note: Tenant administrators can issue their own key from the UI

Status: implemented

## Problem

本部署里全局法条库**只有它的 owner 能写**——也就是引导时按 `LEGAL_TENANT_ADMIN_*` 创建的
租户管理员。因此任何"以他的身份维护语料"的东西（典型是 lite 的 admin 段）都需要一把绑定
该用户的长期凭据。

而产品里没有领取入口：

- 「API Key」页与其菜单项都是超管专属；
- 该页调用的是平台级端点（`GET/POST /api/api-keys`、`POST /api/api-keys/external-agent`、
  `DELETE /api/api-keys/{id}`），租户管理员根本调不动。

但服务端其实已经有这个能力：`GET/POST /api/api-keys/me` 是 `require_authenticated()`，
任何登录用户都能创建/列出**绑定自己**的 Key。只是除了登录后手写一段 `curl`，没有别的路径——
而这是一次性配置，抄错一次后面就悄悄坏掉，也没人能自己发现。

## Decision

把这个已有的自助路径在 UI 上打开：

- 「API Key」菜单对租户管理员也可见（用独立的 `apikey` 菜单组，取代原来超管专属的
  `capability` 组）。
- 页面按身份分流：超管继续用平台级的列表/创建/撤销；其他身份改用
  `GET /api/api-keys/me` 与 `POST /api/api-keys/me`，并把撤销按钮换成一句
  「撤销请联系平台管理员」。
- 后端零改动：两个 `/me` 端点的鉴权本就正确，返回的也是一次性凭据（Bearer Key + AK/SK）。

## Alternatives considered

**只写文档**（"部署后跑一次这段 curl"）。否决：运维要在生产主机上手抄「先登录取 JWT 再创建」
两段命令，抄错时不会立刻报错，而是以后 lite 的 admin 段静默失效；接手的人也无从发现这条路径。

**让租户管理员直接用平台级端点。** 否决：那里签发的是**代理 Key**（平台能力），且能列出部署内
所有 Key。为了自助需求放宽这两个端点的鉴权，等于对所有身份松开平台边界。

**把维护凭据做成配置下发**（例如引导时生成一把 Key 写进 `.env`/文件）。否决：把"owner 身份的
凭据"固化进部署文件，运维既无法轮换也无法吊销，而且离线包里会多一个谁都废不掉的密钥。

## Consequences

租户管理员现在和超管一样，两下点击拿到 Key（明文只显示一次）。因为这个 Key 绑定他的用户，
它会继承该用户的权限——这正是它能维护全局库的原因，也是它**不能**和业务代理 Key 混用的原因
（后者只能读全局库）。

撤销仍是平台动作（`DELETE /api/api-keys/{id}` 是 `require_platform`），所以页面把非超管的撤销
按钮换成了提示，而不是给一个必然失败的动作。超管依然可以撤销任何 Key，包括这把。

## Verification

`frontend/` 下 `npm run build`（tsc + vite）与 `npm test`（34 条）通过。

服务端未改动且已被验证：`GET/POST /api/api-keys/me` 是 `require_authenticated()`，
并且此前已在运行中的部署上用租户管理员身份真实创建过 Key（就是本会话里用到的那把维护凭据）。

新加的 UI 分支**没有在浏览器里手工点过**；要让它在部署里出现，需要重打部署包
（`dist/app-images.tar` 早于本次改动）。
