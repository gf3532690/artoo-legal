# Agent Note: Retrieval excludes repealed and lapsed statutes by default

Status: implemented

[English](2026-09-28-legal-retrieval-excludes-invalid-by-default.md) | 中文

> **本note 取代** [检索只下发状态、不过滤](2026-09-16-legal-retrieval-reports-status-instead-of-filtering.zh.md)，
> 并恢复 [默认排除已废止与已失效的法条](2026-09-15-legal-exclude-repealed-by-default.zh.md) 记录过的默认口径。

## Problem

检索接口此前返回全部六种效力状态，把口径交给调用方。对有法律人员把守的富客户端来说这没问题；
但对这套 Open API 真正服务的外部集成方来说，默认值是错的。"你自己筛"的代价是每个集成方都得先
读一遍枚举才能发出第一次查询——而这个枚举已经被读反过一次（`0` 是未标注、`-1` 才是已失效），
这也是中文描述 `validity_status_label` 要跟着原值一起下发的原因。对"现行法是怎么规定的"这类
问题返回一条已废止的条文，是**错答案**，不是调用方没表达清楚的偏好。

2026-09-16 的决定有一点站得住：**只有调用方拿得到状态，过滤才谈得上公平**。这一点保留——
状态与中文描述仍然随每条结果下发。变的是默认往哪边倾。

## Decision

检索默认剔除已废止（`1`）与已失效（`-1`）；其余四种取值——`3` 现行有效、`2` 已修改、
`0` 未标注、`4` 尚未生效——照常返回。

- 请求字段 `include_invalid: bool = False` 放开过滤，用于"行为时法"这类历史查询。
- 响应字段 `filtered_invalid_count: int = 0` 给出本次被剔除的条数：一页不满要有交代，
  不能让它变成谜。
- 默认口径下候选按 4 倍过采样（上限 `_MAX_PAGINATION_WINDOW` = 100）。库内已废止/已失效共约
  11.9%，不过采样的话过滤后一页可能凑不满。
- `validity_status` 与 `validity_status_label` 仍然随每条结果下发：放开过滤的调用方可以继续
  按自己的口径再筛。
- `match_mode=exact` 与 `GET /api/legal/articles/{article_id}` 是按条号点名取，**不受该开关影响**。
  点名要一条就必须返回，不管它是什么状态。
- 取不到 `validity_status` 时**视为有效**：读不到状态不等于这条失效，凭缺失元数据丢掉结果，
  比多给一条更糟。

## Alternatives considered

**保持"全部返回 + 文档里写自行筛选"**（即 2026-09-16 的口径）。否决：那样默认值**就是**所有
不读文档的调用方实际拿到的答案，而错误默认的代价落在集成方身上，不是落在那份知道枚举的服务上。

**只保留 `3` 现行有效。** 否决：`0` 未标注主要是"修改、废止的决定"这类文件——它们本身是有效
文件；`4` 尚未生效是调用方可能正在问的新版；`2` 已修改虽被取代但曾是当时有效的法。只有明确
声明"该文本不具法律效力"的两个取值才适合默认隐藏。

**把过滤也加到 `match_mode=exact` 与法条详情端点上。** 否决：这两条是按条号点名取。对一条确实
存在的法条返回"找不到"，比返回它并附上状态更糟；何况调用方已经点名了那一条。

**把过滤下推到 Milvus 标量字段（召回前预过滤），而非召回后过滤。** 本次否决：这是正确的长期
形态——召回前过滤能彻底去掉过采样与"一页不满"的问题——但需要重建集合，是另一件更重的事。
此处记为已知的正解。

**默认过滤但不下发 `filtered_invalid_count`。** 否决：2026-09-16 的第二条反对意见是"被过滤过的
一页与查询本身没凑满的一页，外人分不出来"。这个计数正面回答了它，代价只是一个整数。

## Consequences

一个请求字段与一个响应字段回到线上（它们在 2026-09-16 被移除）。从不传 `include_invalid` 的
调用方，现在会比以前少收到那些"命中了已废止/已失效条文"的结果——这就是本次要改的东西，
`filtered_invalid_count` 负责解释差额。要旧行为的调用方传 `include_invalid: true`；它们可能
已经依赖的状态字段没有任何变化。

延迟代价：默认口径下单次语义查询最多取 4 倍分页窗口的候选（封顶 100）再过滤，rerank 看到的是
这个更大的候选集。2026-09-16 的 note 拒绝了这个交换；这里接受它，因为一条已废止条文混进
现行法答案的代价，比多取些候选高。等过滤下移到向量库之后，这部分过采样会消失。

文件列表保留自己的显式 `validity_status` 筛选与徽标：那是个浏览界面，筛选是用户主动点的，
本来就不在争议范围内。

## Testing

`tests/test_legal_retrieval_api.py::TestValidityStatusFiltering` 钉住默认值、被剔除的两个取值、
保留的四个取值，以及"缺失状态视为有效"；`TestRoutesAreRegistered` 断言两个字段都回到了
OpenAPI schema 里。`tests/test_retrieval_contract_baseline.py` 的响应信封键集合新增
`filtered_invalid_count`。

本次改动实际跑过的验证：`python -m compileall app`（通过）。**没有**跑 pytest 套件——改动时
仓库里没有可用的后端 virtualenv——所以上面描述的是测试写成了什么，不是观察到的绿灯。
