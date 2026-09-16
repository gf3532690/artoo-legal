# Agent Note: 检索下发效力状态而不做过滤

Status: implemented

[English](2026-09-16-legal-retrieval-reports-status-instead-of-filtering.md) | 中文

## Problem

检索此前默认排除已废止（`1`）与已失效（`-1`）的结果，用 `include_invalid` 放开、用
`filtered_invalid_count` 解释"这一页为什么少"。那条决定记录在
[检索默认排除已废止与已失效的法条](2026-09-15-legal-exclude-repealed-by-default.zh.md)，本 note
取代它。它有两处不对。

**口径不该由服务端定。** 一条已废止的法条该不该出现在答案里，取决于调用方在问什么——问 2019 年
的行为要的是当时的法，问现行规定不要。服务端分不出这两者，所以无论默认怎么设，都是把一个猜测
强加给所有调用方。

**而且调用方自己做不到。** 结果里的 `validity_status` 是个裸整数、没有描述，于是每个客户端都得
自己实现一遍那份字典——就是那份被读反过一次的字典（`0` 是未标注、`-1` 才是已失效）。"你自己
过滤"只有在状态带着含义一起到达时才是公平的答案。

## Decision

检索不按 `validity_status` 过滤。六种取值一律返回，且每条结果同时带上这个字段的两种形态：

```json
{"validity_status": -1, "validity_status_label": "已失效"}
```

- 请求里的 `include_invalid` 移除，响应里的 `filtered_invalid_count` 移除。一个唯一用途就是"放开
  一个已不存在的默认"的开关，留着就是个仍然读起来像承诺的空操作。
- 为补偿"召回后过滤"而做的约 4 倍候选放大一并移除：现在只取 `window() + 1` 条候选，而不是四倍。
- `validity_status_label` 取自 `VALIDITY_STATUS_LABELS`——与枚举端点下发的是同一份字典；字典外的
  取值**不给描述**，原值照发——编一个描述比缺一个更糟。
- `GET /api/legal/articles/{article_id}` 同样带上这个描述。
- **文件列表保留自己的状态筛选**：那是"浏览与管理"的面，筛选本来就是它的目的，而且是用户显式点的
  动作，不是隐藏的默认。检索与文件列表如今只在这一处不同，是有意为之。

## Alternatives considered

**保留开关、把默认翻过来**（`include_invalid` 默认 `true`）。否决：默认打开的开关是死重量，它会
让放大候选那条路径为一个没人走的场景继续存在；而只要现行有效的调用方，用现在总能拿到的字段自己
筛一下就行。

**下发描述但保留默认排除。** 否决：调用方仍然看不见被丢掉了什么，而那才是问题所在，不是描述。

**归一化成一个布尔值**（`in_force: true/false`）。否决：它把六个取值压成一个比特，而这些取值并不
等价——未标注（0）、尚未生效（4）、已修改（2）都"不是 1 也不是 -1"，但对读的人来说是三件不同
的事。

**全部改成客户端过滤，连文件列表也一样。** 否决：列表的分页与排序都在服务端；在客户端筛当前页会
让 `total` 与 `has_more` 在那里说谎。

## Consequences

响应信封缩小——去掉两个字段、在 `metadata` 里加一个——读 `filtered_invalid_count` 或传
`include_invalid` 的消费方需要更新。现在传 `include_invalid` 会被**忽略**而不是报错：FastAPI 对
未知字段本来就是忽略，而为了一个只会让结果集变宽的变化去硬失败，只会打断调用方。

页面再也不会因为一个看不见的原因变短：`total` 就是这一页自己的条数，`has_more` 是精确的，查询
命中的东西全在调用方手里。加上取消放大候选，喂给 rerank 的候选量减少约 4 倍——混合检索的大部分
延迟正是在那里。

状态仍然决定文件列表的默认展示（徽标）；两个面如今关于"要不要过滤"是有意不一致的，理由是：一个
是查询，另一个是浏览器。

## Verification

`tests/test_legal_retrieval_api.py` 用 `TestStatusIsReportedNotFiltered` 取代了原来的排除测试：
请求模型里没有 `include_invalid`、响应模型里没有 `filtered_invalid_count`、六个字典取值都有描述、
未知与缺值不给描述。`tests/test_retrieval_legal_metadata.py` 断言描述确实进入了结果的 `metadata`；
`tests/test_retrieval_contract_baseline.py` 的信封字段集同步更新。

本地栈实测，用那条原本被排除隐藏掉的法条（《宁夏回族自治区执行〈中华人民共和国婚姻法〉的补充规定》，
`validity_status = -1`）：

```text
查「宁夏…结婚年龄」默认口径
  改动前   该文档被过滤掉，响应里 filtered_invalid_count = 10
  改动后   该文档被返回，metadata.validity_status = -1、
           metadata.validity_status_label = "已失效"；响应里没有 filtered_invalid_count 了
详情 GET /api/legal/articles/{doc}:1 → validity_status = -1、validity_status_label = "已失效"
OpenAPI：RetrievalTestRequest 无 include_invalid、
         RetrievalTestResponse 无 filtered_invalid_count、
         LegalArticleDetail 有 validity_status_label
```
