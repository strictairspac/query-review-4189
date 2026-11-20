Schema v2 adds per-settlement-group daily quota accounting on top of v1.

v1: SQLAlchemy metadata creates users/samples/batches/evidence/audit. seed_demo
initializes the separate invoices fixture table and NOLOGIN query_reader.
invoices is the only permitted SQL source. Run `python -m scripts.seed_demo` for
repeatable initialization; natural keys preserve user edits.

v2 (no standalone migration runner; `src/config/schema.py:init_schema()` runs at
app and worker startup and is idempotent):

* `groups` — settlement groups that each own their own allowance.
* `group_quotas` — one row per (group_id, day): `granted_ms`, `used_ms`,
  `reserved_ms`; invariant `balance = granted - used - reserved`.
* `quota_reservations` — one row per batch; records the upper-bound hold taken
  before execution, the day it was taken, and its `reserved` / `unverified` /
  `settled` state.
* `usage_evidence` — durable per-batch usage proof (original/candidate/total ms);
  unique on `(reservation_id, attempt_no)` so settlement cannot double-deduct.
* `users.group_id`, `batches.group_id` — ownership links.
* `batches.lease_owner`, `batches.lease_until` — execution lease so a crashed
  worker's batch can be reclaimed after expiry.

For a database that already holds v1 data, `init_schema()` applies
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for the new columns after
`create_all()`, so no rows are lost. Back up before upgrading an existing
volume. `groups`/`group_quotas` start empty; each group's ledger is created on
first touch with `QUOTA_DEFAULT_GRANT_MS`.

Inspect the ledger with:

```sql
SELECT g.name, q.day, q.granted_ms, q.used_ms, q.reserved_ms,
       q.granted_ms - q.used_ms - q.reserved_ms AS balance_ms
FROM group_quotas q JOIN groups g ON g.id = q.group_id
ORDER BY q.day DESC, g.name;
```
