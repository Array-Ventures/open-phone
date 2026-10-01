# Relay state and restart behavior

The relay now persists phone metadata, display names, installed-app cache,
bounded job history and completed screenshot bytes in SQLite. The normal CLI
uses owner-only `private/relay-state.sqlite`. Set `--state-file /path/state.sqlite`
to choose another location or `--ephemeral` for disposable memory-only tests.
The current single-writer lock uses Unix `flock`; this implementation supports
macOS/Linux relay hosts. Windows persistence is not implemented.

The ledger stores phone metadata and bounded job results. It does not restore
old host sessions or unfinished input after a restart.

## What survives

Phone IDs/names, dimensions, labels, registration timestamps and Mac IDs remain.
Cached app names/bundle IDs remain until refreshed. Completed/failed job results
and available screenshot downloads remain within the existing five-minute /
128-record / 32-MB image limits. Phone capacity remains 32.

Restart marks every restored phone offline. Live host sessions, completion
receipts and pending command payloads are not stored. A Mac must register with a
new live session before new input is accepted. Pending/running jobs become
failed with `RELAY_RESTARTED` after a crash; graceful shutdown records
`RELAY_STOPPED`. Neither is redelivered. Old completion receipts cannot turn an
uncertain action into a successful job after restart. The agent must inspect
state before choosing a new action; a failure cannot undo prior phone input.

The MCP transport's session IDs, cached tool responses and accepted tool-call
IDs remain process-local. SQLite persistence does not make MCP sessions durable
or allow request-ID deduplication across a new session. See [HTTP_MCP.md](HTTP_MCP.md).

## Commits and failures

The SQLite transaction commits before job acceptance, before returning a host
claim, and before confirming completion. FULL synchronization with a DELETE
journal is used. State writes include only changed rows/images; unchanged
heartbeats do not rewrite screenshot blobs. A process lock prevents two relays
from concurrently owning the same database. Schema/version markers reject an
unrelated database instead of modifying its tables.

A failed commit stops subsequent delivery and state operations in that relay
instance. Repair storage and restart; already-committed unfinished jobs then
fail through the same recovery policy. The service does not retry a phone action
because a persistence operation failed.

Live expiry uses monotonic time. Across restart, elapsed history age uses wall
time. A five-second maintenance loop prunes expired jobs/images even without
agent requests; SQLite secure deletion is enabled for freed records. Wall-clock
skew and hardware/filesystem failures beyond the tested transaction cases remain
unverified. The database and transient journal are local runtime data, not a
backup or encrypted-at-rest service.

The default directory is ignored and excluded from the source package. The
exporter also excludes a SQLite database renamed with a source-like suffix.
Runtime data, keys and compiled helpers are absent from the source artifact.

## Verification

Twelve persistence checks cover metadata/apps/images, history across monotonic
clock reset, expiry without client requests, durable timeout, writer ownership,
graceful stop, abrupt process death, old receipt rejection, failed commits,
SQLite transaction rollback, unrelated-database refusal and omitted recovery
secrets/payloads. One check starts the real HTTP service with a separate fixture
database/key pair, completes synthetic app/image jobs, kills it with SIGKILL,
restarts it, and confirms catalog/results plus zero old work for a new host.
No capture/HID helper or actual phone participates in this fixture test.

```sh
python3 -m unittest discover -s tests -p test_relay_state.py -v
```

The running HTTPS relay has also been reloaded with its real owner-only database.
The official HTTP MCP SDK still discovers 13 tools and reads its actual empty
catalog through verified TLS. Physical host restart/re-registration is pending
because the Mac currently reports no USB iPhone.
