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

## 2026-11-09: branch `batch-queries` (from `multi-round-puzzles`)

### Measured query counts (SQLite test DB; N puzzles, each with a log, a queued answer, and a hint)

| Endpoint | Before, N=10 | Before, N=20 | After (any N) |
| --- | --- | --- | --- |
| `/api_overview` | 77 | 137 | **12** |
| `/api_unloved/` | 71 | 131 | **6** |
| `/hints/` | 107 | 207 | **8** |
| `/whowhat/` | 49 | 89 | **7** |
| `/answers/` | 25 | 45 | **6** |
| `/api_puzzle_history/<id>` | 13 | 13 (grows with log rows) | **4** |
| `/api_puzzle/<id>` | 16 | 16 (grows with hints) | **14** |

The "before" numbers were taken on this branch's starting point with a temporary probe test (since deleted).
Counts include the 2 auth/session queries. `BatchedViewsTests.test_query_counts_are_constant` now fails if any of these
starts growing with N, and pins the overview at 12.

### Done
- New `puzzles/queries.py`: batched equivalents of the per-puzzle methods (`recent_solver_counts`, `recent_solvers_by_puzzle`,
  `linked_out_puzzle_ids`, `log_summary_by_puzzle`). The per-puzzle methods are kept for single-puzzle use.
- `api_overview` rewritten (plan §3). New response keys: `hunt` and `parent_round_id`; existing keys and shape are unchanged. Supports `?hunt=<id>`
  (404 if the hunt is unknown). Rounds come out depth-first. Each puzzle's dict is built once and shared across its rounds.
- `api_unloved`: one AccessLog pass gives both the last log and the effort per puzzle. Shared `unloved_candidates()` fetches Config once and uses the exempt sets as subqueries.
  This also means the module-level helpers `unloved_exempt_statuses()` / `unloved_exempt_tags()` are no longer used by the views (they're kept in models.py).
- `get_last_revs` (Drive): materializes the puzzle list once (it used to run one query per puzzle through `qs[i]`), maps responses by explicit `request_id`,
  splits batches into groups of 100 (this resolves the old TODO), handles files with no revisions, and uses aware `now()` in the sort.
  The old code raised TypeError when some puzzles had revisions and others didn't.
- `api_puzzle_history`: one `values_list` join plus one users query. A missing puzzle id now returns empty lists instead of a 500.
- `build_puzzle_dict`: `select_related('status','priority')` and prefetches of tags, rounds, and hints with their users.
- `api_update_puzzle` tags: one `name__in` lookup; only the missing tags are created (one at a time, so the `notify_of_new_tag` signal still fires).
  `notify_tag_on_add`: one query for all added tags.
- `answer_queue`: one query for successful submissions. Duplicate successes no longer show as "No".
- `hint_queue`: `select_related`; effort and recent solvers are batched for open hints. **Template changed**: `hint-queue.html` now reads
  `hint.effort.*` / `hint.recent_solvers` instead of calling `hint.puzzle.effort_spent` / `recent_solvers` (each of those was 2 queries per hint).
- `who_what`: `select_related('user','puzzle')`.
- `Puzzle.save()`: fetches only the old `answer` column. `log_on_creation`: aware `now()`, which fixes the naive-datetime bug.
- `puzzlescrape`: extracted `sync_state(state)` and `print_dry_run(state)`. Puzzles, the hunt's rounds, and memberships are each loaded once,
  so a re-run with nothing new costs 5 queries in total. Saves are skipped when the answer and status are already correct. The `solved!` status is now
  looked up at run time instead of import time. **`--file` now works**: it used to load the JSON and then do nothing. It expects the
  `initialAllPuzzlesState` shape.

Verified: 23 tests OK (`--settings=solving.test_settings`), `makemigrations --check` clean, `manage.py check` clean, and no RuntimeWarnings.

### Not done / left for the owner
- No frontend changes beyond `hint-queue.html`. A puzzle that's in two rounds shows up in both round blocks on the overview, as intended.
  There's no hunt switcher in the UI yet.
- Deploying elsewhere: `pg_dump` → check out `batch-queries` (it contains all three branches) → `manage.py migrate`
  → restart the app server → rename "Current Hunt" in the admin.
- Not tested against Postgres. Everything uses portable ORM features (Exists, partial UniqueConstraint), but it's worth running the suite once
  against a scratch Postgres DB.
- `handle_puzzle_answer` → `puzzle.zulip_stream()` AttributeError is still there (see findings.md).

## 2026-11-09: migrated the local Postgres DB (owner confirmed this is a local test environment)

- Backup: `/home/puzzle/backups/puzzle_db_pre-hunt-m2m_20261109-013422.dump` (`pg_dump -Fc`, 38 tables).
  Restore: `pg_restore -h localhost -U puzzle -d puzzle_db --clean --if-exists <file>`.
- Before: 33 rounds (16 nested), 262 puzzles, 260 of them with a round.
- `manage.py migrate puzzles` applied `0004_hunt` and `0005_puzzle_rounds_m2m`. Checked: the (puzzle, round) pairs in `puzzles_puzzle_rounds`
  match the old `round_id` values exactly (diff of all 260). Hunt #1 "Current Hunt" (`is_current`) holds all 33 rounds.
- Read-only smoke test (RequestFactory, so no auth queries) on the real data, all returning 200: overview 10 queries / ~120 ms
  (34 round blocks incl. Unassigned, 262 puzzle entries); unloved 4; api_puzzle 11; history 2; answer queue 3; hint queue 2; who's-on-what 3.
- Postgres tests: at first blocked because the `puzzle` role lacked CREATEDB. After the owner granted it: `manage.py test puzzles --noinput` gives 23 tests, OK, on Postgres (≈13 s). The SQLite suite also passes.
- The notes were corrected via a commit on `hunt-model` (they had assumed this was production); `multi-round-puzzles` and `batch-queries` were rebased onto it.
