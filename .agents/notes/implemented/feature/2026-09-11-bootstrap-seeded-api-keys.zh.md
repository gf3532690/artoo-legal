# Agent Note: 用配置预置下游要用的两把 API Key

Status: implemented

相关：[single-tenant-legal-bootstrap](../architecture/2026-09-10-single-tenant-legal-bootstrap.md)
（本 Key 挂靠的默认租户、管理员与全局库那一步）。

## Problem

本部署是两套应用：这里的法条库，以及内嵌它的下游 lite 后端。lite 需要**两把凭据**才能工作：

1. **业务代理 Key**（`external_agent`）——lite 每次访问个人库的建库/上传/列表/删除，以及
   `/api/retrieval/search`，都带 `Authorization: Bearer <key>` 加 `X-External-User-Id`；
2. **全局库 owner 名下的用户级 Key**——写判定是 owner 制的，只有租户管理员那把能维护全局
   库，也就是 lite 的 admin 端在做的事。

这两把过去都要人工创建：登超管签发一把代理 Key，登租户管理员签发一把，再一起填进 lite 配置。
由此出现三种失败形态：

- **每次重建数据卷都要重来一遍**（全新部署，或者"停掉 Artoo、换上法条库"那次割接）；
- 失败在法条库这一侧是**静默**的——lite 开始 401，法条库日志里没有任何线索；
- 代理 Key 的身份命名空间是 `(api_key.id, X-External-User-Id)`（方案 §15.8 已记录），
  所以手工签发的 Key 是"必须熬过割接"的资产，否则每个用户的个人库都会变成读不到。

## Decision

两项可选配置在引导阶段幂等播种这两把 Key：

| 配置项 | 播种的 Key | 下游配置 |
|---|---|---|
| `LEGAL_BOOTSTRAP_PROXY_API_KEY` | `external_agent`，tenant 钉在 `EXTERNAL_USER_TENANT_ID` | `legal-kb.api-key` |
| `LEGAL_BOOTSTRAP_ADMIN_API_KEY` | `user_level`，绑定默认租户管理员 | `legal-kb.admin-api-key` |

`key_hash` 是不加盐的 SHA-256，所以明文已知的 Key 可以直接播种；库里存的是哈希，明文仍只在
env 文件里。两条都留空则整段跳过，即上游 Artoo 的形态（Key 从界面签发）。

三个子决定是承重的：

1. **播种 Key 的 `id` 用 `uuid5(kind, 明文)` 推导，不用随机 `uuid4`。** 对代理 Key 来说这个
   id **就是**身份命名空间前缀，随机 id 会让重启前建的每个个人库都变成孤儿——正是 §15.8
   警告的那个坑，只是改成每次部署自己踩一次。uuid5 不可逆，因此 id（同时也是签名通道的 AK）
   可公开而不泄漏 Key。
2. **按 `key_hash` 查重，且已撤销的 Key 不复活。** 运维撤销一把播种 Key 是有意为之；下次重启
   悄悄把它放回来，比 401 更难排查。引导只打一条 warning 说明后果。
3. **配置值短于 16 字符即拒绝启动。** 这两项是手写进 env 的，而写错的唯一症状是离现场很远的
   下游 401。

## Alternatives considered

**继续从界面签发 Key，把步骤写得更清楚。** 否决：割接明确要清数据卷，写清楚的步骤也得每次
重做一遍；而且这两把 Key 里有一把无法由另一把相同的身份重新签发。

**让 lite 用管理员凭据调 API 自己开通 Key。** 否决：那等于把管理员口令或 JWT 放进下游应用，
并且让 lite 具备为法条库签发凭据的能力——权限面远大于"我拿到了一把 Key"。

**两把 Key 都从 `JWT_SECRET` 派生，不落 env。** 否决：轮换会与 JWT 密钥耦合，排障时也无法
分辨哪把是 lite 用的。

**首次使用时自动建 Key，并把明文打进日志。** 否决：进日志的凭据就是进了日志归档的凭据。

**两把合成一把用。** 否决：全局库的写判定是 owner 制的，代理 Key 解析出来的是外部用户身份，
会被 403 拒掉——一把凭据做不了这两件事。

## Consequences

全新部署（或从空数据卷恢复）起来后，只要这两个 env 值与 lite 配置一致，lite 就能直接调用。
这两把 Key 仍是普通行：出现在 Key 列表里、计入调用次数、可以撤销。其明文现在存在于部署的 env
文件里，属于**长期共享密钥**——轮换要同时改 env 与下游配置，且旧 Key 在被撤销前一直有效。

身份命名空间的稳定性只对"同一个明文"成立：换一个 Key 值就是换一个命名空间，从而换一批外部
用户，与手工换一把 Key 完全一样。

面向运维的表述在 `deploy/DEPLOY.md` 的第四章（账号与 API Key）与第六章（全局法条库与个人
法条库），该文件随离线包一起交付。

## Verification

**未在运行时验证。** 改动只做了静态检查（对改过的模块跑 `ast.parse`）与
`docker build -t artoo-backend:legal ./backend` 通过；本地栈在任何重启之前就被停掉了，
因此没有播种过 Key，也没有用播种的 Key 发过请求。方案 §15.10 列出了预期行为，以及启动后
确认它的两条 `curl`（每把 Key 一条）。
