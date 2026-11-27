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

## 2026-11-13: re-landing #3–#5 after the stacked-merge mix-up

What happened on GitHub (2026-11-09):
- #3 (`hunt-model` → `master`), #4 (`multi-round-puzzles` → `hunt-model`) and #5 (`batch-queries` → `multi-round-puzzles`) were merged
  within a minute of each other. Each merged into **its own base branch**, so only #3 reached `master`; #4 and #5 landed in the
  intermediate branches. GitHub retargets a stacked PR to `master` only if its base branch is deleted *before* the PR is merged.
- #6, #7 and #11 then reverted them. `master` ended up as before plus `Dev Setup.md`, but its history still contains #3's commits.
  Git treats those as already merged, so re-merging `hunt-model` brings nothing back. PR #8 (`hunt-model` → `master`) showed only
  a `tests.py` diff because of this, and merging it would have left tests that need a Hunt model that isn't there.
- `zulip-hunt-mods` (migrations 0006–0008) is branched from `batch-queries` @ 9ea1aa6, so it relies on the original 0004/0005 migration files.

Fix: branch `reland-hunt-rounds-batch` from `origin/master`. It reverts the revert (07bbd96) and then merges `batch-queries` @ 9ea1aa6.
The resulting tree equals `batch-queries` plus `Dev Setup.md`. 23 tests OK on SQLite and on Postgres. `zulip-hunt-mods` merges onto it cleanly.
After it merges: close #8 and #9, and delete `hunt-model`, `multi-round-puzzles`, `batch-queries` and `revert-*` on GitHub.
Then open `zulip-hunt-mods` → `master`.

## 2026-11-13: branch `nested-overview` (from master @ 77d7b31), visual draft for review

Goal: nested rounds show *inside* their parent on the overview (up to 4 levels), and puzzles are individual tiles.

- Frontend only (`templates/puzzles/overview.html` and `puzzles/static/css/base.css`). `/api_overview` already returns every round with
  `parent_round_id`, including empty parents.
- `buildRoundTree()` builds the tree from the flat round list. `renderRoundTree()` renders recursively: a round's body shows its own
  puzzles, each as its own full-width box (an earlier multi-per-row tile grid was rejected by the owner as too cramped) and then its child rounds as inset boxes.
  A round is shown if it has any visible puzzle at any depth. Hiding a round with the round filter hides only its own puzzles; its children still show.
  It's guarded against parent cycles. Depth classes `depth-1`..`depth-4`; deeper levels reuse depth 4's look.
- Each header has a "solved/total" pill that counts that round and everything under it, with distinct puzzle ids so shared puzzles count once.
  Solved tiles are tinted green; meta, capstone and unopened keep their old looks.
- Fixed `toggleRound` (`splice(i)` → `splice(i, 1)`): expanding one round no longer forgets every round collapsed after it.
- Cache-buster for `base.css` bumped `?1` → `?2`.
- Demo data on the local DB: "Nesting Demo" (4 levels, 14 puzzles, every status). Created with a scratch script that stubs out Zulip
  and Google. Every demo puzzle URL starts with `https://nesting-demo.invalid/`. Backup taken first:
  `~/backups/puzzle_db_pre-nesting-demo_20261113-044430.dump`.
- Checked with no browser available: the page's JS ran in QuickJS against the real `/api_overview` JSON. Nesting, totals, filters and
  collapse behaved as expected. 23 tests OK.

## 2026-11-14: `nested-overview`, visual pass 2 (one commit, so it can be reverted on its own)

Owner's asks: make the nesting more apparent, cut down on lines, and give the overview a more consistent palette.
- The palette is scoped to `.rounds-container` (`--ov-*` variables), so other pages are unchanged. Teal (the app's `--primary`) for round
  headers and titles. Neutral chips for normal statuses and tags. Colour only where it means something: green for solved
  (answer chip; the title fades), amber for meta, cyan for capstone, orange for high/asap priority.
- Top-level rounds: white card, teal-tinted header, "solved/total" count, and a 3px green progress bar under the header.
- Nested rounds aren't boxed any more: a chevron before the title (tree-style; it points right when collapsed), and their contents are indented
  under one 2px guide line that gets lighter with depth. This replaces the borders, left rules and tinted headers.
- Puzzle rows: no borders or shadows; spacing plus a hover highlight. Unopened puzzles get a bold title and a small teal dot instead of the
  peach background and green stripe.
- CSS cache-buster `?5`. Re-checked in QuickJS against the real data: tree, totals, filters and collapse all fine. 23 tests OK.
- Follow-up (owner feedback):
  - Cards: header tint raised to 15% teal, plus a 1px teal-grey outline, so they stand out from the page background.
  - Unopened puzzles: no dot. Unopened titles are bold and dark; opened ones are regular weight and lighter; solved ones are lightest.
  - Collapse chevron is on the left at every level and points right when collapsed.
  - Right edges line up at every depth through `--ov-right-inset` (14px = body padding 6 + row padding 8). Header paddings are derived from it,
    and the low-priority status, which is plain text, has no side padding, so its text lines up with the chip edges.

## 2026-11-27 — overview: status-column redesign (branch `nested-overview`)

- Previous design preserved at branch `nested-overview-v1` (a31d5dc); the new design is one commit on top.
- Ported the prototype in ~/reference (BRIEF.md + reference.html): each top-level round is a card whose rows
  are a two-column grid (fixed status column + title column); nesting indents only the title column with
  guide lines, so statuses line up at every depth. Help statuses (insight/bodies/extraction) tint their cell.
- Status mapping: not a puzzle/not started → idle, being worked on → active, we got this! → got,
  needs extraction/insight/bodies → help kinds, currently impuzzlable → blocked, abandoned → dead,
  answer present → solved.
- Kept: 3-column masonry, filter popup (status/priority/round), show descriptions/tags toggles, collapsed
  rounds in localStorage, solver count/history dialog, link-out, 60s refresh. Quick filters (All / Unsolved /
  Needs help) are presets over the Status filter. Header counts now ignore filters (shared puzzles count once).
- Not ported: dark mode (the app has none), Atkinson Hyperlegible (kept Inter), the marker legend.
- Status column shrinks via container queries: 8.25rem when the card is ≤560px, icon-only at ≤400px.
