# ADR-011: Distributed operation ownership and recovery

## Status

Proposed — A2A lease, fenced task-save, and event-cursor foundation
implemented; full decision and acceptance pending

## Date

2026-09-12

## Context

OSA can persist Control Plane records, runtime sessions, A2A task snapshots,
and rate-limit windows in PostgreSQL, but long-running work is still owned by
the process that accepted it. A second replica can read a durable A2A task but
cannot safely take over its active executor. The same gap exists for deployment
operations: persisted intent and provider reconciliation do not prevent two
replicas from issuing competing stop, restart, rollback, or deploy actions.

The missing contract must protect tenant boundaries, prevent stale replicas
from publishing late results, make cancellation deterministic, and avoid
silently replaying agent or provider side effects after a lease expires.

The first A2A slice now implements the scoped lease/fencing/cancellation
primitive, explicit schema validation, and a fence-aware SDK task-store adapter
described below. Every durable SDK task save carries the ownership snapshot
through the call context and holds the ownership row lock through the save; a
worker that loses its lease fails closed without publishing a synthetic
failure. Independent handlers can look up shared active tasks, wait for a
remote owner to publish cancellation, and safely finalize cancellation after
an owner lease expires. Terminal replay is read-only and does not duplicate
task history. Process-boundary PostgreSQL acceptance covers shared task
creation, lookup, cancellation, and crash recovery. Terminal events drain
through the SDK consumer before lease release. Expired-owner retries fail
closed with a stable terminal failure and never replay unknown model/tool side
effects. Schema version 2 now provisions the migration-owned append-only event
cursor and bounded polling-relay foundations; the relay consumes durable
events by cursor but is not attached to a runtime stream route yet. The SDK
active-task registry and multi-process streaming/late-event acceptance are
still open, so this ADR remains proposed until the full
acceptance criteria and open review questions are resolved.

The deployment slice now applies the same conservative boundary to the
currently exposed mutating Control Plane operations: deploy creation is keyed
by agent until a deployment ID exists, while stop, restart, and rollback use
the deployment key. PostgreSQL migration 0010 owns the lease rows, provider
deploy specs carry the operation metadata, and durable deployment-record
writes lock and validate the current owner/fence before updating state. The
provider-only Kubernetes `scale()` capability is not exposed through
`DeploymentService` yet. The independent-worker PostgreSQL acceptance passes
in CI for provider-side-effect serialization, expiry/takeover, late-result
rejection, tenant isolation, and restart/reconciliation recovery. A2A
streaming/late-event acceptance and the open review questions still prevent
accepting this ADR as a whole.

## Decision drivers

- Database-backed coordination must work with the existing PostgreSQL
  deployment model and must not require a particular queue vendor.
- Ownership must be fenced, not inferred from process liveness or an
  eventually consistent cache.
- A task or deployment operation may have at most one authoritative owner at a
  time, while reads remain available from any replica.
- Recovery must be explicit about duplicate side effects and must produce an
  auditable terminal outcome when safe replay is not possible.
- Coordination rows, events, and logs must remain tenant-scoped and must never
  contain bearer tokens, secrets, prompts, or model outputs beyond the existing
  task/deployment persistence contracts.

## Proposed decision

### 1. Shared lease and fencing record

Introduce a migration-owned coordination table with these logical fields:

| Field | Contract |
|---|---|
| `tenant_id`, `operation_kind`, `operation_id` | Immutable ownership key; every read and write is tenant-scoped |
| `owner_id` | Random process/worker identity, never a user identity |
| `fencing_epoch` | Monotonically increasing integer issued on every successful takeover |
| `state` | `queued`, `running`, `cancel_requested`, or terminal |
| `lease_expires_at`, `heartbeat_at` | Database-clock timestamps used for expiry |
| `attempt` | Bounded takeover count for recovery and audit |
| `cancel_requested_at` | Immutable first-cancellation timestamp, when present |

Acquisition and takeover happen in one database transaction under a row lock.
An unexpired lease cannot be acquired by another owner. An expired lease is
acquired only by incrementing `fencing_epoch`; the new epoch is the fencing
token for every subsequent mutation. Heartbeats update only the row matching
`owner_id` and `fencing_epoch`. A stale owner that loses the lease gets a
conflict and must stop publishing work.

The table is coordination state, not a replacement for domain records. Domain
repositories remain responsible for their existing typed errors and optimistic
concurrency rules.

### 2. A2A task execution

- The initial `message/send` creates the durable task snapshot and coordination
  record before work is started. The request carries an idempotency key derived
  from the tenant, caller, and A2A task id; the key is never placed in a URL.
- Only the lease owner invokes the agent. A replica that receives a request for
  a task owned elsewhere reads the durable snapshot and waits for a terminal
  update up to the protocol/request timeout; it never starts a second local
  executor.
- Task snapshot writes are performed by the fence-aware SDK adapter while the
  ownership row is locked and the current `fencing_epoch`/lease is validated.
  Late artifacts, status changes, and failures from an old owner fail closed
  and are not published as task state.
- A completed or failed task is immutable for further execution. A caller that
  needs another attempt creates a new task id or uses an explicit retry
  operation with a new attempt and idempotency contract.

### 3. Cancellation and recovery

- An authorized cancel request sets `cancel_requested` once while the task is
  non-terminal. The owner observes that flag, cancels its local producer, and
  publishes exactly one terminal `canceled` state under the current fencing
  epoch.
- A remote cancel requester waits for the durable terminal task. If the owner
  lease expires first, it may acquire the next fencing epoch and finalize the
  task as `canceled` without invoking the agent or replaying its side effects.
  The terminal snapshot is then delivered through read-only protocol events.
- If the owner disappears, the next lease holder finalizes the task as `failed`
  with the stable owner-loss error. It must not replay an agent call whose
  tool/model side effects have no idempotency guarantee. Safe replay is a
  separate executor capability and is not enabled by the current contract.
- A late completion after cancellation or owner loss cannot win the fenced
  update. Repeated cancel requests return the already durable terminal task;
  they do not enqueue another cancellation event.
- Lease expiry, takeover, owner-loss finalization, and terminal transitions
  emit payload-free audit/telemetry metadata with a stable operation id and
  attempt number.

### 4. Deployment operations

The accepted target is for deploy, stop, restart, rollback, and scale to use
the same lease/fencing primitive, keyed by tenant and deployment resource.
Provider calls use a provider-owned idempotency key where supported.
Provider observation/reconciliation remains separate: it may update observed
status, but it cannot clear an active lease or overwrite an intent owned by a
newer fencing epoch. The current implementation covers deploy, stop, restart,
and rollback; provider-only `scale()` remains outside the Control Plane
service surface until its operation contract is added.

### 5. Telemetry boundary

Replica-wide capability telemetry uses the PostgreSQL sink and bounded event
contract accepted by [ADR-010](010-capacity-and-capability-telemetry.md). This
ADR does not make A2A/deployment operation telemetry part of that capability
sink: those operation events still need the stable operation/fencing metadata
and retention contract described here before they are persisted.

## Review-ready implementation defaults

The following defaults are recommended for review. They are deliberately not
accepted by this ADR yet; they make the remaining architecture decision
concrete without silently enabling a weaker distributed contract.

### A2A streaming and late events

- Keep inbound A2A streaming disabled in the Agent Card until the acceptance
  suite proves the full cross-replica path.
- Use the schema-version-2 migration-owned append-only task-event table keyed
  by tenant, task, fencing epoch, and a monotonically increasing per-task
  sequence. Store the protocol event under the existing A2A task-persistence
  contract; do not put bearer tokens, credentials, or unrelated telemetry
  payloads in the row. The current implementation provides the fenced storage
  primitive, and the bounded polling relay now provides resumable cursor reads;
  route integration and acceptance remain gated.
- The owner appends an event only while holding its current fence. The append
  and the ownership check share one transaction. A stale or terminal owner
  receives a conflict, and its HTTP stream must stop forwarding new events.
- A handler on another replica reads events after a client cursor with bounded
  polling. PostgreSQL `LISTEN/NOTIFY` may reduce latency, but notifications are
  only wake-ups; the durable table and cursor are the source of truth.
- Reconnects replay from the last acknowledged sequence. Active-task rows are
  retained while running and through a bounded terminal replay window; event
  cleanup must not remove data needed by an in-flight cursor.
- Per-connection queues are bounded. Slow clients receive a resumable cursor
  boundary or a stable retryable error rather than unbounded memory growth.
  Events already delivered before a lease loss are not retracted, but no late
  event from a fenced owner may be delivered afterward.

### Deployment-operation ownership

- Use one ownership key for all mutating operations on a deployment. This is
  the conservative default because stop/restart/rollback/scale can otherwise
  interfere through provider state even when their operation names differ.
  Read-only status, logs, and provider observations remain concurrent.
- Generate a stable `operation_id` per accepted command and carry its tenant,
  deployment, owner, and fencing epoch through the provider call and every
  persisted result. API input must not supply a process owner or fencing value.
- Heartbeat during long provider calls. If the fence is lost, the worker must
  stop persisting results and must not blindly retry an unknown side effect.
  The replacement first performs read-only reconciliation; retry requires an
  explicit provider idempotency guarantee.
- Where a provider supports idempotency, use a provider-owned operation key
  derived from the deployment and operation IDs. Where it does not, preserve
  an explicit `unknown` outcome requiring reconciliation instead of claiming
  success or failure from a stale worker.
- Reconciliation may refresh observed status but cannot clear a newer intent
  or release another worker's fence. Acceptance must cover concurrent commands,
  expiry, late provider results, tenant isolation, and restart recovery.

## Acceptance criteria

Implementation is not complete until a PostgreSQL-backed acceptance suite
proves all of the following with two independent app/worker instances:

1. One owner wins creation; the other replica reads the same task or operation
   without executing it.
2. Heartbeats preserve ownership, while an expired lease increments the epoch
   and prevents the old owner from writing a terminal result.
3. A2A completion, failure, lookup, and owner-loss recovery are durable and
   tenant/subject isolated across replicas.
4. Cancellation is idempotent, has one durable terminal state and one terminal
   event per handler, and wins over a late completion from a fenced owner.
5. Deployment lifecycle commands serialize per deployment and stale commands
   cannot overwrite newer intent.
6. When capability telemetry is configured, the shared sink deduplicates event
   ids across retries and enforces retention/tenant filters without payload
   leakage; A2A/deployment operation telemetry remains outside that sink until
   its ownership contract is accepted.

The suite must run in CI against PostgreSQL and a multi-process or multi-worker
environment. SQLite and the process-local stores remain development/test
implementations only.

## Consequences

### Positive

- Replica behavior becomes explicit and testable without coupling OSA to a
  particular queue or Kubernetes implementation.
- Fencing makes stale-worker behavior fail closed instead of allowing late
  results or provider commands to win races.
- Recovery policy makes duplicate side effects visible rather than silently
  replaying them.

### Negative or trade-offs

- A migration and transaction-aware coordination repository are required.
- Non-idempotent in-flight agent work may end in an owner-loss failure instead
  of automatic replay.
- Cross-replica request waiting consumes database/read capacity and needs
  bounded polling, backpressure, and gateway quotas; OSA bounds remote cancel
  waiting with `OSA_A2A_TASK_CANCEL_WAIT_SECONDS`.
- The design still requires a CI runtime capable of exercising multiple
  workers for the remaining A2A/deployment operation ownership work.

## Open review questions

- If a future executor capability opts into idempotent replay, what explicit
  declaration and side-effect evidence are required?
- What lease/heartbeat durations and takeover limits fit the supported runtime
  timeout range?
- Resolved for the current slice: serialize all mutating operations per
  deployment resource; keep read-only observations concurrent. Revisit only if
  a future provider exposes a mutation with a separately proven idempotency
  contract.
