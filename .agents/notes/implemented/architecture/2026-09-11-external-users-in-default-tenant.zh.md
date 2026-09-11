# Agent Note: External users live in the legal default tenant

Status: implemented

本note取代实施计划里「不使用 `external_agent` 通道」的论证（D21）；它所依赖的租户模型见
[single-tenant-legal-bootstrap](2026-09-10-single-tenant-legal-bootstrap.zh.md)。

## Problem

上游 Artoo 用「超管级**代理 Key** + `X-External-User-Id` 请求头」接第三方：平台按
`(代理Key, 外部用户ID)` 懒创建身份，每个终端用户各自拥有私有库——**在法条库内部**
隔离终端用户，正是这条通道的意义。

单租户法条库却用不了它：代理 Key 把身份硬锁在内置 `tenant-external-builtin`，
而全局法条库在 `tenant-legal-default`，知识库读授权跨租户硬隔离。改造前在真实运行的
栈上实测：

| 用代理 Key 调用 | 结果 |
|---|---|
| `GET /api/knowledge-bases` | 200 但 `total=0`——看不到全局库 |
| `POST /api/retrieval/search`（不传 `kb_ids`） | **400**「未找到全局法条库，且未指定…范围」 |
| 显式传全局库 `kb_id` | **404**——跨租户硬隔离 |

于是唯一能用的外部接入方式是 `user_level` Key：一把 Key 对应一个身份，**所有终端用户共用一个
身份**，产品侧无法隔离；正确性完全依赖调用方传对 `kb_ids`。

## Decision

保留 Artoo 的代理 Key 通道，用一项新配置把外部用户指到默认租户：

```text
EXTERNAL_USER_TENANT_ID = tenant-legal-default   # 本 fork 默认；上游是 tenant-external-builtin
```

该配置在三个位置生效——代理 Key 行的 `tenant_id`、懒创建的 `external_users` 行的
`tenant_id`、合成身份上下文的 `tenant_id`；当它不等于内置值时，引导不再创建内置外部租户。

其余零改动：`owner_user_id` 本就经 `identity.acting_subject_id` 解析，对外部用户即
`external_users.id`，因此「按用户归属私有库 / 互相隔离 / 按 `kb_id` 维护」全部沿用原设计。

## Alternatives considered

**只保留 `user_level` Key（即原计划的决定）。** 否决其作为唯一方式：所有终端用户共用一个身份，
隔离只能在**下游**重新实现一遍，而且请求里一旦传错 `kb_id` 就会读到别人的库。
该方式**仍然保留**，只是不再是唯一选择。

**给每个终端用户各发一把 `user_level` Key。** 否决：调用方要保存与轮换 N 把 Key，
每新增一个终端用户还得走一次管理操作。代理通道存在的意义正是省掉这一步。

**让全局法条库跨租户可见。** 否决：这等于在授权内核里加一个跨租户例外，
而单租户模型的价值恰恰是「不需要任何跨租户例外」。

**复用内置外部租户，并把全局库复制一份进去。** 否决：权威语料出现两份拷贝、
两套入库结果，「全局库唯一」这条不变量直接破掉。

## Consequences

调用方现在可以二选一：要「每终端用户一个隔离身份」用代理 Key + `X-External-User-Id`；
要「一把 Key 代所有用户、库范围全由下游算」用用户级 Key。已在运行中的栈上用一把代理 Key、
两个外部用户 ID 验证：`alice-001` 在 `tenant-legal-default` 建了私有库（owner =
她的 `external_users.id`），检索时与全局库合并返回；`bob-002` 只看到全局库，
拿 `alice` 的 `kb_id` 检索得到 404。

代价与注意事项：

- 外部用户现在与租户管理员、全局库处在**同一个租户**内。他们固定为 `member` 角色、
  只拥有私有库，但相比之下「谁存在于这个租户里」确实变宽了。
- 他们**不会**出现在用户管理列表里（是 `external_users`，不是注册用户），
  所以租户的用户列表不等于完整身份清单。
- 在本次改动之前完成引导的数据库里，内置 `tenant-external-builtin` 仍然存在，只是不再被使用；
  全新部署不再创建它。
- 从上游同步的人务必不要把这项配置改回去：把外部用户指回内置外部租户，会让外部调用方
  完全读不到全局法条库。

## Testing

后端：`pytest -q tests/test_tenant_auth_db_properties.py tests/test_tenant_auth_properties.py
tests/test_tenant_auth_integration.py tests/test_tenant_auth_integration_extra.py
tests/test_legal_recall_scope.py tests/test_retrieval_contract_baseline.py` → 58 passed / 3 failed；
那 3 个失败在 fork 基线上同样失败，与本次改动无关。
`test_property_7_external_user_namespace` 已改为按配置断言，不再钉死常量。

实机：在运行中的栈上重建镜像；代理 Key 行创建时 `tenant_id=tenant-legal-default`；
用两个外部用户 ID 验证了按用户隔离与「全局库 + 个人库」合并（见上文 Consequences）。
