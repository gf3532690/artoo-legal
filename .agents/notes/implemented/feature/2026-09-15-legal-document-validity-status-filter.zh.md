# Agent Note: 法条库文件列表带效力状态并可筛选

Status: implemented

## Problem

召回已经学会默认丢掉已废止与已失效的法条
（[检索默认排除已废止与已失效的法条](2026-09-15-legal-exclude-repealed-by-default.zh.md)），
但**文件列表**——库管理员真正在浏览的那个界面——对这些文档一字未提。面对全局法条库就是
22,037 个文件名，既找不到那 2,617 份已不具法律效力的，也没法用眼睛核对入库侧的选版规则
是否留下了正确的那一版。

效力状态原本只存在于 `chunks.metadata` 里（被冗余到每个子块）。`law_name` 也是读时从那
个字典里捞出来的，但这个模式**推广不到过滤**：列表是分页的，分页之后再套谓词，`total` 会
报错，被筛掉的行还会从后面的页里漏出来。

## Decision

`validity_status` 落成 `documents` 上的一列（可空整数 + 索引），列表接口既下发它，也在它
上面过滤。

- **入库时写，写在把文档置为完成的那条语句里。** 管道本来就把完成收敛在
  `DocumentPipeline._update_status`，值由 `legal_metadata.document_validity_status` 从
  per-chunk 元数据里取出来。
- **读取就是一个普通列。** `DocumentResponse.validity_status` 是原始整数，所有返回文档的
  响应都带（列表、详情、两个 `duplicate` 分支）。
- **过滤走可重复的查询参数。** `?validity_status=1&validity_status=-1` 取**并集**而不是
  交集，编译出来就是索引列上的一个普通 `IN` 谓词。
- **入库其余部分不变，也不重新抽取。** 文档级字段没有重新派生：已经入库的 22,037 份保留
  原有 chunks 与向量，由 `scripts/backfill_document_validity.py` 把值从**本来就躺在那里**
  的元数据搬到新列上。
- **枚举是下发的，不是复制的。** `GET /api/legal/validity-statuses` 返回
  `[{value, label}]`，前端用这份响应渲染标签，自己不留拷贝。这个枚举已经被读反过一次
  ——`0` 未标注、`-1` 已失效。

### 为什么落列而不是读时派生

在 PostgreSQL 上按真实语料的规模建表实测（22,037 文档 / 1,100,000 chunk / 命中约 2,600
行），同一谓词、同一会话：

```text
documents.validity_status IN (1, -1)        Bitmap Index Scan   执行  24.7 ms    147 buffers
EXISTS (chunks c WHERE ... IN ('1','-1'))   Parallel Seq Scan on chunks
                                                                执行 3958 ms  9,173 buffers
```

差距是结构性的而不是常数级：第二种形态与 **chunk 数**成正比，而 chunk 数随"文档 × 每份
的条文数"增长；第一种与文档数成正比，且走索引。

## Alternatives considered

**读时从 `chunks.metadata` 派生，也就是 `law_name` 那套。** 按上面的实测否决，且它有正确性
问题：分页之后再套的谓词报不出真实的 `total`，也给不出稳定的 `has_more`。`law_name` 能这么
活下来，只是因为它纯粹用来展示，从不进 `WHERE`。

**在前端对已经加载的页做过滤。** 否决：用户只能筛自己滚到过的地方；界面上的计数会变成"这一页
有多少"而不是"库里有多少"；而且它和同样条件的 API 查询会悄悄给出不同答案。

**把回填放进启动迁移里。** 否决：那等于每次进程启动都扫一遍全库。结构适合放在幂等的启动
路径上，一次性的数据搬运适合放在显式、可重复执行的脚本里。

**所有知识库都显示这个筛选。** 否决：该字段只对法条文档有值。在普通知识库上这个控件永远
匹配不到东西，用户读到的不是"这个筛选在这里不适用"，而是"我的文件不见了"。列表在
`config.is_default_legal_kb` 置位时才显示它。

**在文件列表响应里直接返回渲染好的 `validity_status_label`。** 否决：标签是展示用词表。把它
挡在响应体之外，措辞就能改而不算改契约；而当初想加它，唯一的原因就是怕枚举被抄成两份——这个
问题已经由枚举端点解决了。

**改成把 `validity_status` 做成 Milvus 标量字段。** 这不是替代方案，是另一个面：那个是给召回
做预过滤，这个是给可浏览的列表用。两个都要，Milvus 那一半照旧推迟到下一个重建窗口，见上面
链接的那份 note。

## Consequences

文件列表契约多了一个查询参数，`DocumentResponse` 多了一个字段，钉住响应形状的客户端需要更新。
`null` 的含义是"尚未确定"：未解析完成、解析失败，以及所有非法条文档。因此传了
`validity_status` 的请求会**排除**这些行——等值匹配，不是"空值也算命中"——参数说明里写了
这一点。

`POST /api/documents/{doc_id}/retry` 会清空该值。它是本次解析的产物，而一份已经回到 `pending`
的文档上挂着一个上一轮的状态，比没有状态更糟。

启动迁移会给 `documents` 加一列和一个索引；建索引会短暂持有 `ACCESS EXCLUSIVE` 锁，22k 行
上是毫秒级。两个进程各自调用一次，所以 Worker 不依赖 API 先起来。

这一列与"每个子块元数据里也有一份"构成冗余。冗余是有意的——有一条读路径需要按文档拿它——
但这意味着将来若出现"写 chunk 元数据却不走完成路径"的写入点，两者会漂移。目前这样的写入点
只有一个。

这次改动**暴露**但没有解决入库侧的老问题：入库清单是一次性快照，因此快照时最新版本还是 `4`
（尚未生效）的法，库里留下的仍是旧版（共 12 部这样的法）。

## Testing

`tests/test_legal_document_status_filter.py`（15 项）钉住：枚举六个取值（含曾读反的那两个）、
文档级取值的选择（`0` 是取值不是缺值；坏值跳过）、过滤确实编译成
`documents.validity_status IN (...)` 而未过滤的请求不产生该谓词、`[0]` 被当作真实过滤条件
而不是"没传"、以及参数与响应字段都出现在 OpenAPI schema 里。

`frontend/src/components/documents/FileItem.test.tsx`（8 项）钉住徽标：标签来自服务端下发的
枚举、`0` 渲染成「未标注」、未知取值如实显示 `取值 N` 而不去猜、没有词表时整块不渲染
——而不是显示一个裸数字。

本地栈实测，全局法条库回填后（10 份：8 现行有效、1 未标注、1 已废止）：

```text
不加过滤                                     total=10  vs=[1,3,0,3,3,3,3,3,3,3]
?validity_status=3                           total=8   vs=[3,3,3,3,3,3,3,3]
?validity_status=1                           total=1   vs=[1]
?validity_status=1&validity_status=-1        total=1   vs=[1]
?validity_status=3&2&4&0                     total=9
```

同一语料上的回填体检与执行：10 份已完成文档全部从 chunk 元数据取到了值，0 份留空。
