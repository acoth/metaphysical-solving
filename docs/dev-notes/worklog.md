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

## 2026-11-09: branch `multi-round-puzzles` (from `hunt-model`)

Done:
- `Puzzle.round` (FK) became `Puzzle.rounds = ManyToManyField(Round, blank=True, related_name='puzzles')`.
  The reverse accessor changed from `round.puzzle_set` to `round.puzzles`.
- Migration `0005_puzzle_rounds_m2m`, hand-edited. The auto-generated version did RemoveField then AddField, which would have
  **dropped every round assignment**. It now does AddField → RunPython (bulk copy of `round_id` into the through table) → RemoveField.
  Reversible: the reverse puts back the lowest-id round, which is lossy for puzzles in several rounds.
- Added `Puzzle.round_list()` (uses the prefetch cache) and `Puzzle.round_names()` (`"A, B"`).
- API: `"round"` is still a string (now comma-joined), so `puzzle.html` and `unloved.html` work unchanged. `api_puzzle` also returns
  `"rounds": [{id, name}]`.
- Zulip "solved" message: lists all rounds as `[A, B]`, and leaves out the brackets when there are none (it used to print `[None]`).
- Admin: `filter_horizontal` for rounds, and list filters by round and by round's hunt.
- Scraper: `rounds.add(round_obj)` for both new and existing puzzles, so a puzzle listed under several rounds ends up in all of them.
- Overview (minimal change on this branch; fully rewritten in `batch-queries`): uses the M2M, so a puzzle shows in every round it's in.

Verified: 14 tests OK, including a forward/backward test of the data migration and an overview test with a puzzle in two rounds.
