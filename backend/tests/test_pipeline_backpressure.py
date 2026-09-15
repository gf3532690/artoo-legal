"""worker 背压：只在真有并发额度时才把消息从 Stream 读进 PEL。

背景是实测事故。worker 旧实现无条件连读：读一条就等于让它进入 PEL 成为未确认消息，
于是 22,036 份的队列瞬间整条变成 PEL。PEL 里 idle 超过孤儿阈值的消息会被周期性
XAUTOCLAIM 重新认领，认领一次投递次数 +1，涨到 ``max_delivery_count`` 就被判为
毒消息丢进 DLQ——一轮入库因此丢掉 7,366 份"只是在排队"的文档。

两个不变量：

1. 主循环在途任务数达到并发上限时不再读新消息；
2. ``claim_pending`` 的认领条数受调用方给定的额度约束，额度为 0 时连 XAUTOCLAIM
   都不该发出去（认领本身就会让投递次数 +1）。
"""

from __future__ import annotations

import os as _os

_os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-production")

import asyncio
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest

from app.pipeline import worker as worker_module
from app.pipeline.queue import TaskMessage, TaskQueue
from app.pipeline.worker import PipelineWorker


def _task(index: int) -> TaskMessage:
    return TaskMessage(
        doc_id=f"doc-{index}",
        kb_id="kb-1",
        file_path=f"/tmp/doc-{index}.docx",
        object_key=f"kb-1/doc-{index}.docx",
    )


async def _queue(stream_key: str = "pipeline:tasks") -> TaskQueue:
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    queue = TaskQueue(redis_client=client, stream_key=stream_key)
    await queue._ensure_group()
    return queue


class TestClaimPendingLimit:
    @pytest.mark.asyncio
    async def test_claims_at_most_the_given_budget(self) -> None:
        queue = await _queue()
        for index in range(10):
            await queue.enqueue(_task(index))
        # 先把 10 条全部投递进 PEL（等于旧实现"一次读光整条队列"的状态）
        delivered = await queue.consume("worker-test", count=10, block_ms=10)
        assert len(delivered) == 10

        claimed = await queue.claim_pending(
            "worker-test", min_idle_ms=0, max_delivery_count=99, limit=3,
        )

        assert len(claimed) == 3

    @pytest.mark.asyncio
    async def test_zero_budget_leaves_delivery_count_untouched(self) -> None:
        """额度为 0 时必须直接返回：认领本身会让投递次数 +1。"""
        queue = await _queue()
        await queue.enqueue(_task(1))
        delivered = await queue.consume("worker-test", count=1, block_ms=10)
        message_id = delivered[0][0]
        before = await queue._get_delivery_count(message_id)

        claimed = await queue.claim_pending(
            "worker-test", min_idle_ms=0, max_delivery_count=99, limit=0,
        )

        assert claimed == []
        assert await queue._get_delivery_count(message_id) == before

    @pytest.mark.asyncio
    async def test_paging_still_reaches_the_whole_pel_without_a_limit(self) -> None:
        """不传 limit 时保持原行为：一次把 PEL 里可处理的消息都认领出来。"""
        queue = await _queue()
        for index in range(7):
            await queue.enqueue(_task(index))
        await queue.consume("worker-test", count=7, block_ms=10)

        claimed = await queue.claim_pending(
            "worker-test", min_idle_ms=0, max_delivery_count=99,
        )

        assert len(claimed) == 7


class _RecordingQueue:
    """只记录"读了多少条"的假队列；其余接口留空实现。"""

    def __init__(self, tasks: list[TaskMessage]) -> None:
        self.tasks = list(tasks)
        self.read: list[str] = []

    async def consume(self, consumer_name, count=1, block_ms=5000):
        if not self.tasks:
            await asyncio.sleep(0.01)
            return []
        task = self.tasks.pop(0)
        message_id = f"1-{len(self.read) + 1}"
        self.read.append(message_id)
        return [(message_id, task)]

    async def claim_pending(self, *args, **kwargs):
        return []

    async def ack(self, message_id):  # pragma: no cover - 假队列不需要
        return None


class TestWorkerReadAhead:
    @pytest.mark.asyncio
    async def test_reads_only_up_to_concurrency(self, monkeypatch) -> None:
        """队列里有 20 条、并发 3：worker 只能先读走 3 条。"""
        # 缩短"等一个槽位空出来"的轮询间隔，测试只关心读取节奏，不关心这 0.2s。
        monkeypatch.setattr(worker_module, "_CAPACITY_POLL_SECONDS", 0.01)
        queue = _RecordingQueue([_task(i) for i in range(20)])
        worker = PipelineWorker(
            queue=queue, pipeline=object(), db_session_factory=None,
            max_concurrent=3, max_retries=1,
        )
        worker._ping_embedding = AsyncMock(return_value=True)

        release = asyncio.Event()
        started: list[str] = []

        async def fake_process(message_id, msg, queue=None):
            started.append(message_id)
            await release.wait()

        worker._process_task = fake_process

        run = asyncio.create_task(worker.start())
        try:
            await asyncio.sleep(0.4)

            assert len(started) == 3, f"在途应恰为并发上限，实际 {len(started)}"
            assert len(queue.read) == 3
            assert len(queue.tasks) == 17, "其余 17 条必须留在 Stream 里，不进 PEL"

            # 放行之后才允许继续读
            release.set()
            await asyncio.sleep(0.4)
            assert len(started) > 3
        finally:
            worker._running = False
            release.set()
            await asyncio.wait_for(run, timeout=5)

    @pytest.mark.asyncio
    async def test_never_exceeds_concurrency_while_draining(self, monkeypatch) -> None:
        """全程在途数不超过并发上限，不会出现"读光整条队列"。"""
        monkeypatch.setattr(worker_module, "_CAPACITY_POLL_SECONDS", 0.01)
        queue = _RecordingQueue([_task(i) for i in range(30)])
        worker = PipelineWorker(
            queue=queue, pipeline=object(), db_session_factory=None,
            max_concurrent=2, max_retries=1,
        )
        worker._ping_embedding = AsyncMock(return_value=True)

        in_flight = 0
        peak = 0

        async def fake_process(message_id, msg, queue=None):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1

        worker._process_task = fake_process

        run = asyncio.create_task(worker.start())
        try:
            for _ in range(100):
                if not queue.tasks:
                    break
                await asyncio.sleep(0.05)
            assert peak <= 2, f"在途峰值 {peak} 超过并发上限"
            assert len(queue.read) == 30, "队列应当被正常排干"
        finally:
            worker._running = False
            await asyncio.wait_for(run, timeout=5)
