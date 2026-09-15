# Agent Note: worker 只读它跑得动的量

Status: implemented

## Problem

pipeline worker 会把整条 Stream 读进 consumer group 的 PEL。消费循环读一条、为它建一个
task、马上回去读下一条；并发上限在 task **内部**才生效，所以循环没有任何停下来的理由。
而"读一条"就是把它写进 PEL，于是一次 22,036 份的入库之后，几乎整条队列都躺在 PEL 里
成了"已投递未确认"。

这把孤儿恢复路径变成了批量丢弃路径。`claim_pending` 会把 idle 超过
`pipeline_claim_min_idle_minutes`（这里是 65 分钟）的消息重新认领，前提假设是"idle 这么久
说明它的 worker 已经死了"。而**在排队等**的消息看起来完全一样：每次重新认领都会让 Redis
的投递次数 +1，超过 `max_delivery_count`（3）就被当作毒消息移入 DLQ，并通过
`on_poison_pill` 回调把对应文档标成 `failed`。

测试环境首次全量入库的实测：

- 一小时内入队 22,036 份；
- 完成 14,666 份，**7,366 份被标为 failed**，错误信息只有一句
  `重复崩溃，已停止重试: poison-pill: delivery_count=4 exceeded max=3`；
- `pipeline:dlq` 涨到 19,329 条。

被丢掉的文档本身没问题。完成的那批平均 38.2 个 chunk/份，正是这份语料解析正常时的量；
失败的只是**还没轮到处理**、投递次数就先耗尽的那些。谁活下来由进度和积压量决定，这是
排队缺陷的特征，而不是数据缺陷。

## Decision

只在有空闲并发额度时才读消息，让 PEL 承载**在途**的文档，而不是整条积压。

主循环以 `len(self._tasks) >= max_concurrent` 作为闸门：槽位占满时睡
`_CAPACITY_POLL_SECONDS` 后重查，不再消费。有空位时循环行为与原先完全一致——先快道、
再慢道，队列空时照常长轮询。

恢复路径受同一份额度约束。`RedisStreamQueue.claim_pending` 新增 `limit` 参数，
`_reclaim_from_queue` 传入自己的剩余额度。`limit=0` 会在发出 `XAUTOCLAIM` **之前**返回，
因为认领这个动作本身就会让投递次数 +1——没有空位的 worker 不该碰 PEL。单次
`XAUTOCLAIM` 的 count 也按剩余额度收窄，避免一页就超领。

图谱抽取 worker 是**镜像**这段容错循环而不是继承，因此带着同一个缺陷，这次一起加上了
同样的两道约束。

## Alternatives considered

**调大 `max_delivery_count`，或者干脆关掉毒消息判定。** 否决：这是治标。该规则的作用是
不让真正会把 worker 打崩的文档无限循环；而且投递次数仍会每个认领周期涨 1，只是换一批、
更晚被丢弃而已。

**调大 `pipeline_claim_min_idle_minutes`。** 同理否决：它只是把误判推迟。缺陷的实质是
"在排队的消息"和"孤儿消息"**因为都在 PEL 里**而无法区分，任何 idle 阈值都改变不了这一点。

**提前读进一个有界的进程内队列。** 否决：没有用。任何 `XREADGROUP` 在读取的瞬间就已经在
Redis 里把消息标成已投递，把消息缓冲到本地只会在 PEL 里留下同样的痕迹，只是归属换成了
持有缓冲的那个进程。

**改队列抽象，让 PEL 只保留最近 N 条。** 以不成比例否决：为了一个读取闸门就能提供的不变量，
去替换 Redis Stream consumer group 模型，而且将来每个消费者都要再来一遍。

## Consequences

PEL 现在反映的是在途工作量：最多 `max_concurrent` 条，外加同一轮里的慢道读取。孤儿重认领
从此只会看到真正被打断的消息，这正是该机制被设计出来要处理的情况。

吞吐不变。消费从来不是瓶颈——瓶颈是信号量，现在依然是；闸门改变的只是**什么时候**读，
不是文档处理的速度。

旧版本遗留的超大 PEL 会以每个认领周期约 `max_concurrent` 条的速度慢慢排空，而不是一次
领完。这可以接受，因为受影响的文档已经是 `failed` 状态，靠文档重试恢复，而不是靠排空
PEL。

闸门也改变了 worker 卡死时的后果。以前一次卡死会把整条积压转成投递次数、再转成 DLQ 条目；
现在槽位全被卡住时 worker 只是停止读取，积压留在 Stream 里。卡死仍然是故障，但不再有
破坏性。

`limit` 是 `RedisStreamQueue.claim_pending` 的新关键字参数，`limit=0` 是新的"空操作"契约。
不传它的调用方保持原有的"遍历整个 PEL"行为，属性测试仍然钉着这一点。

这次事故还有第二个、独立的原因：4 份文档长时间停在 `progress=50%, 正在生成向量`，占住全部
4 个槽位。读取闸门让这种卡死不再摧毁队列，但卡死本身没有在这里修——
`pipeline_task_timeout_minutes` 显然没能结束那几个任务，需要单独排查。

## Testing

`tests/test_pipeline_backpressure.py` 用 fakeredis 覆盖队列侧（`claim_pending` 最多认领给定
额度；`limit=0` 返回且不增加投递次数；不传 limit 时仍能遍历完整个 PEL），用记录型假队列
覆盖 worker 侧（队列里 20 条、`max_concurrent=3` 时恰好读走 3 条、其余 17 条留在 Stream；
排空 30 条的过程中在途数始终不超过上限）。
