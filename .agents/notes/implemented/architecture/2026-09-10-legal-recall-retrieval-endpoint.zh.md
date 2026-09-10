# Agent Note: Legal recall reuses the existing retrieval endpoint

Status: implemented

## Problem

法条库部署必须在调用方指定的个人库之外，**始终**检索全局法条库。而上游的
`POST /api/retrieval/search` 要求调用方显式给出检索范围，否则返回 `400`——因此
只想查全局库的调用方无处可传。同时该部署是单租户，这改变了这里真正需要的授权工作。

## Decision

法条召回服务就是现有端点，不新增：

- `_run_retrieval` 在 `resolve_kb_ids()` 之后、授权校验之前把全局法条库并入检索
  范围，因此它像其它源一样走同一套授权判定。不新增任何租户模型例外。
- 不指定任何范围的请求，现在返回全局法条库的结果，而不是 `400`。
- `top_k` 保持字段名与语义不变；本部署默认值由 `10` 改为 `5`。
- `_build_result_items` 在已有的 `metadata` 字典里追加 `law_name`、
  `article_number`、`article_label`、`chapter`、`source`（取值 `global` /
  `personal`）。响应模型不动。

## Alternatives considered

**新增专用的 `/api/retrieval/legal` 端点。** 否决：同一套检索语义分裂成两种契约形状，
Open API 还得同时描述两者并声明它们行为一致。

**加一条 `cross_tenant_kb_ids` 例外，让全局库可跨租户读取。** 否决：本部署是单租户，
全局库与调用方同租户，`organization` + `read` 已经授予该访问。这条例外会成为整个
方案里风险最高的改动，却换不来任何收益。

**增加精确引用过滤（`legal_filters`，按法名与条号）。** 本版本否决：检索被定义为
语义优先；而法名精确匹配在调用方写简称时会静默失败（「劳动合同法」对库里的
「中华人民共和国劳动合同法」）。改为在结果元数据里返回法名与条号，由调用方判断。

**给本部署单独设计响应信封。** 否决：复用现有信封让两个部署的契约保持一致，下游代码
可以在两者之间迁移。

## Consequences

该端点的既有调用方会遇到两处行为变化：无范围时的 `400` 消失，默认返回条数降为五条。
两处都记在 `artoo-open-api.md` 第 0 节。

因为全局库让每次调用都成为多源检索，所以只要调用方传了个人库，`mode=direct` 便不可达，
`trace` 也变为 `null`。这符合上游对多源检索的既有文档说明，不是新增规则。

结果水合多一次针对 `Chunk` 的批量查询，在 `top_k = 5` 时可以忽略。

## Testing

测试断言：不指定范围的请求能取到全局法条库；`top_k` 默认为五；结果 `metadata` 带法条
字段与 `source` 标记；既有信封键保持不变。
