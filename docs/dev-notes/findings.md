# Findings: state of the code before this work (master @ be22127)

## Puzzle ↔ Round relationship (before)

- `Puzzle.round = ForeignKey('Round', null=True, blank=True, on_delete=SET_NULL)` (`puzzles/models.py`).
  This is many-to-one: a puzzle is in **at most one** round. There's no `related_name`, so the reverse accessor is `round.puzzle_set`.
- `Round(OrderedModel)` has `name`, `description`, and `parent_round = FK('self', SET_NULL, related_name='child_rounds')`.
  Nesting depth is unlimited, and nothing prevents cycles.
- `Round.name` is not unique. The scraper uses `Round.objects.get_or_create(name=...)`, which raises `MultipleObjectsReturned` if two rounds share a name.
- The scraper (`puzzles/management/commands/puzzlescrape.py`) never sets `parent_round`, so nesting is admin-only.
- Nothing grouped rounds by hunt. Rounds from previous years would show up on the overview.

### Where `puzzle.round` was used
- `models.py` `Puzzle.save()`: the Zulip "solved" message includes `[%s]` of the round, and shows `[None]` when there isn't one.
- `views.py` `api_overview`: groups puzzles by round. Puzzles with no round are put into a synthetic `{"round": "Unassigned", "id": "unassigned"}` round.
- `views.py` `build_puzzle_dict` (`api_puzzle` / `api_update_puzzle`): `p_info["round"] = str(puzzle.round)`.
- `views.py` `api_unloved` and `api_unloved_by_spreadsheet`: `"round": str(round) or ""`.
- `admin.py` `PuzzleAdmin.fields` includes `'round'`.
- `puzzlescrape.py`: `Puzzle.objects.create(..., round=round_obj)`.
- Frontend: `templates/puzzles/puzzle.html:556` and `unloved.html:155` display `puzzle.round` as a string.
  `overview.html` consumes `data.rounds[]` with the keys `round` (name), `id`, `description`, `parent_round` (name),
  and `puzzles` (the key is present only when the round has puzzles). The round filter dropdown matches on round **name**.

## Query hotspots (N+1s) found

Let R be the number of rounds, P the number of puzzles, and L the number of access-log rows.

| Location | Problem | Approx. queries |
| --- | --- | --- |
| `api_overview` | for each puzzle: `recent_count()` (1), `unopened_theirs()` (1–2), `tag_list()` (1), `str(priority)` (1), `str(status)` (1); `round.parent_round` per round (1); the unassigned queryset is evaluated twice | ~5P + R + 6 |
| `api_unloved` | for each puzzle: last `AccessLog` (1), `effort_spent()` (1), `round` (1), `priority` (1), `status` (1), log `user` (1) | ~6P |
| `api_unloved_by_spreadsheet` / `unloved_by_spreadsheet` | `get_last_revs` indexes `acceptable_puzzles[i]` on an unevaluated queryset, which is **one query per puzzle**, plus priority/status/round per puzzle | ~4P |
| `api_puzzle_history` | `User.objects.get` per log row, plus `userprofile.location` for each row (2) | ~3L |
| `build_puzzle_dict` | `hint.user` per hint; `select_related()` with no args doesn't follow nullable FKs (`round`) | ~H + 10 |
| `answer_queue` | `SubmittedAnswer.objects.get` per queued answer, plus `qa.puzzle` in `__str__` | ~2Q |
| `hint_queue` | template touches `hint.puzzle`, `hint.user`, `puzzle.priority/status`, and calls `effort_spent` twice and `recent_solvers` twice per hint | ~8H |
| `who_what` | `el.user` and `el.puzzle` per access log; `room.user` and `room.puzzle` per Jitsi room | ~2(L+J) |
| `api_update_puzzle` (tags) | `Tag.get_or_create` per tag name; `notify_tag_on_add` does `Tag.objects.get` per added pk | ~2T |
| `puzzlescrape` | `Puzzle.objects.filter(url).exists()` plus `.get()` per scraped puzzle, and `create_puzzle` does yet another `.get()` | ~3P per run |
| `Puzzle.save()` | loads the whole old row just to compare `answer` | 1 (minor) |

## Existing bugs noticed (not caused by this work)

- `views.py` `handle_puzzle_answer` calls `puzzle.zulip_stream()`, which doesn't exist (AttributeError). It's only reached from the manual call-in path. **Not fixed** (out of scope); flagged for the owner.
- `unopened_theirs()` / `unopened_ours(user)` call `.get()` on a queryset that can hold more than one AccessLog row per (user, puzzle), because nothing enforces uniqueness. That can raise `MultipleObjectsReturned`. The batched overview avoids this by treating the puzzle as "opened" if *any* of the user's logs for it has `linkedOut=True`.
  **Reproduced 2026-11-09**: `log_on_creation` logs every new puzzle against `Config.log_user`, or against the *first User* if that's unset. If that user
  also has a log for the puzzle, the old `/api_overview` returned a 500. `puzzle_linkout` / `log_a_view` use `get_or_create`, which has the same issue.
  Recommend setting `Config.log_user` to a dedicated non-solver account, and eventually adding a unique constraint on (user, puzzle) after de-duplicating the rows.
- `overview.html` `toggleRound` uses `collapsed_rounds.splice(r_index)` without a count, which removes every element after the index too. Frontend; not touched.
- `get_last_revs` has a TODO: the Drive batch request is limited to 100 sub-requests. **Fixed** in `batch-queries` by chunking into batches of 100.
- `solving/settings.py` (tracked in git) contains the DB password in plain text. Not touched; worth moving to `local_settings.py`.

## Environment facts

- Django 6.0.1, Python 3.12, venv at `env/`. Postgres DB `puzzle_db` on localhost, all 3 migrations applied as of 2026-11-09.
- `/home/puzzle/metaphysical-solving` is the owner's **local testing environment**, not production (confirmed by the owner 2026-11-09).
  `local_settings.py` still carries production-like values (`BASE_URL`, Zulip/Google credentials in `configuration/`), but this box isn't live.
  The dev server runs as `manage.py runserver 8080` (it autoreloads on code changes). The owner views it via an SSH port-forward at `localhost:8080`.
- Migrations and tests may be run against the local Postgres DB. Take a `pg_dump` backup first (see [testing.md](testing.md)).
- `zulip_send`, `create_google_spreadsheet`, `grant_access`, and `zulip_create_user` all hit real external services and are called from model `save()` and signal handlers.
- `log_on_creation` creates `AccessLog(lastUpdate=datetime.now())`, a **naive** datetime with `USE_TZ` on. Django assumes it's UTC,
  so new puzzles' "last update" is off by the server's UTC offset (it shows up as a RuntimeWarning in tests). **Fixed** in `batch-queries` by using `timezone.now()`.
