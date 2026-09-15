# Agent Note: The worker only reads what it can work on

Status: implemented

## Problem

The pipeline worker drained the entire stream into its consumer-group PEL. The
consume loop read a message, created a task for it, and immediately looped to
read the next one; the concurrency limit lived *inside* the task, so the loop
never had a reason to stop. Reading a message is what puts it in the PEL, so a
22,036-document enqueue left roughly the whole queue sitting in the PEL as
delivered-but-unacknowledged messages.

That turned the orphan-recovery path into a mass-discard path. `claim_pending`
re-claims messages that have been idle longer than `pipeline_claim_min_idle_minutes`
(65 minutes here) on the assumption that an idle message belongs to a dead
worker. Waiting messages look exactly the same: every re-claim increments the
Redis delivery count, and once it exceeds `max_delivery_count` (3) the message is
treated as a poison pill, moved to the DLQ, and — through the `on_poison_pill`
callback — its document is marked `failed`.

Measured on the test environment's first full ingestion:

- 22,036 documents enqueued in one hour;
- 14,666 completed, **7,366 marked failed** with the single message
  `重复崩溃，已停止重试: poison-pill: delivery_count=4 exceeded max=3`;
- `pipeline:dlq` reached 19,329 entries.

The discarded documents were not broken. The completed ones averaged 38.2 chunks
each, which is what this corpus produces when it parses correctly; the failures
are simply the ones that had not been reached yet before their delivery count ran
out. The rate of progress and the size of the backlog decided which documents
survived, which is the signature of a queueing defect rather than a data defect.

## Decision

Read a message only when there is a free concurrency slot, so the PEL holds the
documents in flight instead of the whole backlog.

The main loop is gated on `len(self._tasks) >= max_concurrent`: when every slot is
busy it sleeps `_CAPACITY_POLL_SECONDS` and re-checks instead of consuming. When
slots are free the loop behaves exactly as before — fast lane, then slow lane,
both long-polling when the stream is empty.

Recovery is bounded by the same budget. `RedisStreamQueue.claim_pending` takes a
`limit`, and `_reclaim_from_queue` passes its spare capacity. A `limit` of 0
returns before `XAUTOCLAIM` is issued, because the claim itself is what increments
the delivery count — a worker with no free slots must not touch the PEL at all.
The per-call `XAUTOCLAIM` count is narrowed to the remaining budget so a single
page cannot over-claim beyond it.

The graph-extraction worker re-implements this tolerance loop rather than
inheriting it, so it carried the same defect and gets the same two bounds.

## Alternatives considered

**Raising `max_delivery_count` or turning the poison-pill rule off.** Rejected: it
addresses the symptom. The rule exists so a document that genuinely crashes the
worker cannot loop forever, and the counts would still climb by one per reclaim
cycle — it would only decide a different, later set of documents to discard.

**Raising `pipeline_claim_min_idle_minutes`.** Rejected for the same reason: it
delays the misjudgement instead of removing it. The defect is that a waiting
message is indistinguishable from an orphaned one *because it is in the PEL*, and
no idle threshold changes that.

**Reading ahead into a bounded in-process queue.** Rejected: it does not help.
Any `XREADGROUP` marks the message delivered in Redis at the moment it is read, so
buffering the messages locally would leave the same trail in the PEL — just owned
by the process holding the buffer.

**Changing the queue abstraction so only the last N messages are pending.**
Rejected as disproportionate: it replaces the Redis Stream consumer-group model
for one invariant that the read gate already provides, and it would have to be
redone for every future consumer.

## Consequences

The PEL now tracks work in progress: at most `max_concurrent` messages plus the
same-iteration slow-lane read. Orphan re-claim only ever sees messages whose
worker really was interrupted, which is what the mechanism was written for.

Throughput is unchanged. Consumption was never the bottleneck — the semaphore
was, and it still is; the gate only moves *when* a message is read, not how fast
documents are processed.

A PEL left oversized by an older build drains at about `max_concurrent` messages
per reclaim cycle rather than in one shot. That is acceptable because the affected
documents are already in `failed` state and are recovered through document retry,
not by draining the PEL.

The gate also changes what a wedged worker does. Previously a stall converted the
whole backlog into delivery-count increments and then into DLQ entries; now a
worker whose slots are all stuck simply stops reading and the backlog stays in the
stream. Stalling is still a fault, but it is no longer destructive.

`limit` is a new keyword argument on `RedisStreamQueue.claim_pending`, and
`limit=0` is a new no-op contract. Callers that omit it keep the previous
whole-PEL behaviour, which the property-style tests still pin.

The incident had a second, separate cause: four documents sat at
`progress=50%, 正在生成向量` for hours, holding all four slots. The read gate keeps
that stall from destroying the queue, but the stall itself is not fixed here —
`pipeline_task_timeout_minutes` apparently did not end those tasks, and that needs
its own investigation.

## Testing

`tests/test_pipeline_backpressure.py` covers the queue side with fakeredis
(`claim_pending` claims at most the given budget; `limit=0` returns without
incrementing a delivery count; paging without a limit still reaches the whole
PEL) and the worker side with a recording queue (with 20 messages queued and
`max_concurrent=3`, exactly three are read and 17 stay in the stream; while
draining 30 messages the in-flight count never exceeds the limit).
