# Worklog

## 2026-11-09: branch `hunt-model` (from master @ be22127)

Done:
- Added `Hunt(OrderedModel)` with `name`, `url`, and `is_current`. A partial `UniqueConstraint` named `only_one_current_hunt` allows only one current hunt.
  `Hunt.save()` clears the flag on the other hunts first, so marking a new hunt current never violates the constraint.
  Helpers: `Hunt.current()`, `hunt.top_level_rounds()`.
- Added `Round.hunt` (FK, nullable, `SET_NULL`, `related_name='rounds'`). In `Round.save()`, a child copies its parent's hunt.
  `Round.clean()` rejects a parent from another hunt and parent cycles (`clean()` is called by admin forms, not by `save()`).
- Migration `0004_hunt`: schema changes plus a RunPython step. If any rounds exist, it creates a single "Current Hunt" (`is_current=True`)
  and attaches every round to it. Reversible: the reverse detaches the rounds; the hunt rows are dropped along with the table.
- Admin: `HuntAdmin`, with an inline of its rounds; `RoundAdmin` with hunt and parent columns, a hunt filter, and `list_select_related`.
- `puzzlescrape`: rounds use `get_or_create(hunt=Hunt.current(), name=...)`.
- Added `solving/test_settings.py` (in-memory SQLite) and a real `puzzles/tests.py`: `IsolatedTestCase` patches Zulip and Google,
  and `MigrationTestCase` checks the data migrations.

Verified: `manage.py test puzzles --settings=solving.test_settings` gives 7 tests, OK.
`makemigrations --check` is clean. (Later migrated on the local DB; see the batch-queries entry.)

After deploying: rename the auto-created "Current Hunt" in the admin (e.g. "MIT Mystery Hunt 2027").
