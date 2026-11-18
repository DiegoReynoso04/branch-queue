# Design notes — Banco Meridional queue manager

*Leer en español: [DESIGN.es.md](DESIGN.es.md)*

Customers take a numbered ticket for one service (`deposito`, `retiro`,
`gestion_cuenta`). Each agent serves a single service and calls the customer who
has waited longest **for that service**. All the code lives in
[`branch-queue.py`](branch-queue.py); the tests are in
[`tests/test_branch_queue.py`](tests/test_branch_queue.py).

## 1. Data structure: `dict[str, deque[Ticket]]`

`BranchQueue` keeps one FIFO queue per service:

```python
self._queues: dict[str, deque[Ticket]] = {s: deque() for s in VALID_SERVICES}
```

The obvious alternative is a single list of all tickets in arrival order. The
difference shows up in `call_next(service)`:

| Operation | One `deque` per service | Single shared list |
|---|---|---|
| `issue_ticket` | O(1) — `append` | O(1) — `append` |
| `call_next(s)` | **O(1)** — `popleft` | **O(n)** to scan for the first ticket of `s` **+ O(n)** for `list.pop(i)`, which shifts every later element |
| `peek_next(s)` | O(1) — `queue[0]` | O(n) — scan |
| `stats` | O(k) = O(1) — `len` of each deque | O(n) — count everything |
| `list_waiting` | O(n) — it copies every ticket | O(n) |

*n = customers waiting across all services; k = number of services (3, fixed).*

**The cost for one service does not depend on the other services.** With a
shared list, if 500 people are waiting for `deposito` and one person for
`retiro` is at the end, the `retiro` agent walks past 500 tickets on every call.
With one deque per service, calling the next `retiro` customer is a single
`popleft()`, however long the other queues are. The dict lookup
`self._queues[service]` is O(1).

**Why `deque` and not `list`.** A `dict[str, list]` would still be wrong for a
queue: `list.pop(0)` is O(m) because it shifts the remaining m − 1 elements one
position to the left. `collections.deque` is built for both ends: `append`,
`popleft` and reading `queue[0]` are all O(1). Memory use is O(n) in both cases.

What we give up is a single global arrival order, but the global ticket number
lets us rebuild it (see section 2).

## 2. A global counter, not one per queue

`self._next_number` is shared by all services. Tickets for different services
are interleaved: `deposito` #1, `retiro` #2, `deposito` #3, `gestion_cuenta` #4.

- **One number per customer in the branch.** The display board can announce
  "#7" without ambiguity. With a counter per queue there would be three "#1"s at
  once, and we would need prefixes such as `D-1` / `R-1` to tell them apart.
- **The number records the global arrival order.** Sorting by number across
  queues gives back the exact arrival order. That is useful for reports, or for a
  future "any agent" desk, without storing a separate list.
- **FIFO equals ascending number.** The number is assigned and the ticket is
  appended under the same lock, so each deque is always sorted by number.
- **Simple synchronisation.** The counter is protected by the same lock that
  protects the deques, so no extra lock is needed.

Trade-off: the numbers inside one queue have gaps (`deposito`: 1, 3, 4…), so a
customer cannot work out their position from their number alone. "Next
customer" (`peek_next`) and the waiting list (`list_waiting`) give that
information.

## 3. Concurrent `call_next` with two agents for the same service

Two agents who serve `retiro` may press "call next" at the same moment. Calling
a customer is a *check-then-act* sequence: look at the queue, then take
something out. If those are separate steps without a lock, another thread can
run between them. There are two variants of this race.

### a) Check `if queue`, then `popleft()` separately → `IndexError`

```python
# WITHOUT A LOCK — wrong
if not queue:
    raise EmptyQueueError(service)
return queue.popleft()
```

One ticket is left (#7):

| Step | Agent A | Agent B |
|---|---|---|
| 1 | `if not queue` → 1 ticket, carry on | |
| 2 | | `if not queue` → 1 ticket, carry on |
| 3 | `popleft()` → #7 | |
| 4 | | `popleft()` → **`IndexError: pop from an empty deque`** |

Agent B gets an unexpected exception instead of `EmptyQueueError`. This is the
race that `test_concurrent_call_next_check_and_pop_are_atomic` shows. That test
slows `popleft()` down on purpose so the gap is always hit. With the lock
removed it fails 5 times out of 5; with the lock it always passes.

### b) Read `queue[0]` to announce the customer, then remove → double call

```python
# WITHOUT A LOCK — wrong
ticket = queue[0]
announce(ticket)          # "Ticket #7, Ana, please go to desk 2"
queue.popleft()
```

The queue holds #7 Ana and #9 Luis:

| Step | Agent A (desk 1) | Agent B (desk 2) |
|---|---|---|
| 1 | `ticket = queue[0]` → #7 Ana | |
| 2 | | `ticket = queue[0]` → #7 Ana |
| 3 | announces "#7 Ana → desk 1" | |
| 4 | | announces "#7 Ana → desk 2" ← **double call** |
| 5 | `popleft()` removes #7 | |
| 6 | | `popleft()` removes **#9 Luis, who was never called** |

Ana is called to two desks, and Luis disappears from the queue without being
served.

### The rule

1. **Check and remove in one critical section.** The first change must be taking
   the ticket out of the queue. "Is anyone waiting?" and `popleft()` happen
   together under `self._lock`:

   ```python
   with self._lock:
       queue = self._queues[service]
       if not queue:
           raise EmptyQueueError(service)
       return queue.popleft()
   ```

2. **Announce only a ticket that has already been removed.** `call_next` returns
   the ticket it removed, and only then does the CLI print "Llamando a: …". Once
   a ticket has left the queue, no other agent can get it. `peek_next` is for
   information only. Its result may be out of date as soon as the lock is
   released, so it must never be used to call a customer.
   `test_peek_next_does_not_modify_state` checks that it really is read-only.

`test_concurrent_call_next_has_no_duplicates` checks the end-to-end guarantee:
two threads empty a queue of N = 5000 tickets, and the numbers called are
exactly N with no duplicates. Both concurrency tests also collect any
unexpected exception raised inside a thread, so a thread that crashes does not
let the test pass silently.

**Why an explicit `Lock` if `deque.popleft()` is already atomic?** A single
`popleft()` cannot be interrupted halfway in CPython (because of the GIL, or
per-object locks in the free-threaded build). That is an implementation detail,
and we do not build correctness on it. It also covers only **one** operation. It
does not protect the *check + remove* pair, or *read counter + increment +
append* in `issue_ticket`. Without the lock, two threads could both read
`_next_number == 5`, and both customers would get ticket #5.

Other lock decisions:

- **Reads take the lock as well.** Copying a deque with `list(deque)` while
  another thread changes it raises `RuntimeError: deque mutated during
  iteration`. `peek_next` has the same check-then-act problem as `call_next`.
  `stats` needs all its counts to come from the same moment.
- **Short critical sections.** Validation happens before the lock, and there is
  no I/O inside it: the CLI prints after the lock is released. A slow terminal
  never blocks other agents.
- **One lock for everything.** Every critical section is O(1), apart from the
  `list_waiting` copy, so contention is negligible. A lock per service would
  still need the global counter protected, which adds complexity for no gain.

### Several processes or kiosks (note only, not implemented)

`threading.Lock` only works inside a single process. If several kiosks or
servers shared the queue, the atomic step would move to the shared store:

- **Database:** in a single transaction,
  `SELECT … WHERE service = ? AND status = 'waiting' ORDER BY number LIMIT 1 FOR UPDATE SKIP LOCKED`,
  then `UPDATE … SET status = 'called'`, then `COMMIT`. The global number would
  come from a database sequence or auto-increment column.
- **External queue:** one Redis list per service (`LPOP` / `BLPOP` are atomic)
  or a broker such as RabbitMQ with acknowledgements.

The rule stays the same: claiming the ticket is atomic in the shared store, and
the customer is announced only after that claim has succeeded.

## 4. Edge cases

| Case | Handling |
|---|---|
| Service with different case or spaces (`" Retiro "`) | `normalize_service` applies `strip()` + `lower()` → `"retiro"`. |
| Unknown service (`"hipoteca"`, `""`, `"4"`) or not a string | `InvalidServiceError` (a `ValueError`), with a message listing the valid services. The CLI shows it and asks again. |
| Service entered as a menu number in the CLI | `1`–`3` map to `VALID_SERVICES`. Any other number is passed to validation and rejected. |
| Empty or whitespace-only name | `issue_ticket` raises `ValueError`. The CLI checks the name first and asks again. The name is stored with surrounding spaces removed. |
| `call_next` on an empty queue | `EmptyQueueError` (a `LookupError`) carrying `.service`. The CLI shows it as a notice and goes back to the menu. |
| `peek_next` on an empty queue | Returns `None`. No exception, because an empty queue is a normal situation. |
| `list_waiting` with empty services | Every service is present, with `[]`. The CLI prints "(vacía)". |
| Changing the result of `list_waiting` | New lists are returned, so the internal queue is unaffected. `Ticket` is `frozen`, so the shared objects cannot be changed. |
| Invalid menu option (text, out of range, empty) | "Opción no válida" message, and the loop continues. |
| Ctrl+C / Ctrl+D (or input running out) | `KeyboardInterrupt` / `EOFError` are caught around the whole loop. The program shows a message and exits with code 0. |
| Two tickets with the same `issued_at` | Order comes from the position in the deque, which follows the number, not the time. `issued_at` is for display only. |
| Very large counter | Python `int` has no upper limit. |

Unexpected errors are not hidden: the CLI only catches `InvalidServiceError`,
`EmptyQueueError` and `ValueError`, never `Exception`.

**Out of scope:** the state lives in memory and is lost when the program closes.
The counter does not reset each day. There is no cancellation, no-show handling
or priority.
