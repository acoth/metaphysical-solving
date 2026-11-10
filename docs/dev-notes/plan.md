# Plan and design decisions

Goals (from the request, 2026-11-09):
1. An overarching **Hunt** model that contains all the top-level rounds for a hunt.
2. A puzzle can belong to **more than one round**.
3. Fully optimize collecting puzzles across all rounds, e.g. for the Overview page.
4. Turn scattered DB queries into batched ones wherever possible.

## 1. Hunt model (`hunt-model` branch)

```python
class Hunt(OrderedModel):
    name = CharField(max_length=255)
    url = URLField(blank=True)
    is_current = BooleanField(default=False)   # at most one; enforced in save() + a partial unique constraint
```

- `Round.hunt = FK(Hunt, null=True, blank=True, on_delete=SET_NULL, related_name='rounds')`.
  - The FK is nullable, so existing data and rounds created in the admin keep working.
  - `SET_NULL` rather than `CASCADE`: deleting a Hunt must never wipe rounds (and with them the puzzle groupings) in the middle of a hunt.
  - **Child rounds inherit their parent's hunt** in `Round.save()`. The hunt is set on every round, not just the top-level ones,
    so "all rounds in a hunt" is a single indexed filter (`Round.objects.filter(hunt=h)`) with no recursive walk.
    `Hunt.top_level_rounds()` returns the rounds that have `parent_round IS NULL`.
  - `Round.clean()` rejects a parent from a different hunt, and rejects cycles (a round can't become its own ancestor).
- `Hunt.current()` returns the current hunt, or `None`.
- Data migration: if any rounds already exist, create a single "Current Hunt" marked `is_current=True` and attach every existing round to it.
  If there are no rounds, nothing is created.
- Admin: `Hunt` is registered with an inline list of its rounds, and `hunt` is added to the Round admin (list_display and list_filter).
- Scraper: rounds are looked up or created **within the current hunt** (`get_or_create(hunt=current, name=...)`).

## 2. Puzzle in many rounds (`multi-round-puzzles` branch)

- Replace `Puzzle.round` (FK) with `Puzzle.rounds = ManyToManyField(Round, blank=True, related_name='puzzles')`.
- Migration, in three steps, so no data is lost:
  1. AddField `rounds`.
  2. RunPython: copy each `round_id` into the M2M through table using `bulk_create` (reversible: the reverse copies the first round back).
  3. RemoveField `round`.
- A plain auto-generated through table (no extra columns), so ordering within a round still comes from `Puzzle.order` (OrderedModel).
- Display: `Puzzle.round_names()` returns the round names joined with `", "`. The API keeps the string field `"round"`, so existing
  JS keeps working, and adds a `"rounds"` list (`[{id, name}]`) for new frontend work.
- The Zulip "solved" message lists all rounds, and shows no `[None]` when the puzzle has none.
- Admin: `rounds` uses `filter_horizontal`.
- Scraper: when a puzzle that already exists shows up in another round, that round is **added** to it (M2M `.add` is idempotent).
  This is how cross-round metas get picked up automatically.
- Overview: a puzzle in N rounds appears in each of those N round blocks (intended).

## 3. Overview: constant query count (`batch-queries` branch)

`api_overview` becomes a fixed set of queries, no matter how many rounds or puzzles there are:

1. Config (default priority) via `select_related`.
2. Rounds for the hunt in view, `select_related('parent_round')`.
3. Puzzles with `select_related('status', 'priority')`, `prefetch_related('tags')`, which runs one more query for tags.
4. The round↔puzzle through table, as `values_list` (one query, no model instances).
5. Recent solver counts: `AccessLog` filtered by `sessionEnd >= now-360s`, `values('puzzle').annotate(Count('user', distinct=True))`.
6. The current user's linked-out puzzle ids (one query).
7. Statuses and priorities (two small queries).

The JSON is then assembled in Python from dicts. Each puzzle's info dict is built once and reused for every round it's in.

- Which hunt: `?hunt=<id>` if given, otherwise `Hunt.current()`. Rounds shown are the ones whose hunt is the selected hunt **or** whose hunt is NULL
  (unassigned legacy rounds, so nothing disappears). If there are no hunts at all, every round is shown, as before.
- Puzzles shown: puzzles in the shown rounds, plus "Unassigned" puzzles (no rounds at all). Puzzles that belong only to other hunts' rounds are hidden.
- Round order: depth-first (each parent followed by its children, ordered by `order`), so nested rounds render next to their parent.
- Response shape is unchanged (`round`, `id`, `description`, `parent_round`, `puzzles` present only when non-empty). It adds a top-level
  `hunt: {id, name}` (or null) and a per-round `parent_round_id`.

## 4. Batching elsewhere (`batch-queries` branch)

See the hotspot table in [findings.md](findings.md). Approach for each:

- `api_unloved`: one query over the AccessLog rows of the acceptable puzzles. Compute the latest log and the total minutes per puzzle in Python.
  Puzzles via `select_related('status','priority')` and `prefetch_related('rounds')`. Config is fetched once, not twice.
- `get_last_revs`: materialize the puzzle list once and map responses by `request_id`; chunk the Drive batches into groups of 100.
- `api_puzzle_history`: one `values()` query that joins user name and location for the log rows; current viewers via `select_related('userprofile__location')`.
- `build_puzzle_dict`: `select_related('status','priority')`, `prefetch_related('tags','rounds')`, hints with `select_related('user')`.
- `answer_queue`: one query for the successful submissions; match by (puzzle_id, answer) in Python.
- `hint_queue`: `select_related('puzzle__status','puzzle__priority','user')`. Hint effort and recent solvers are precomputed in bulk and attached to each hint.
- `who_what`: `select_related('user','puzzle')` on both querysets.
- Tags in `api_update_puzzle`: one `filter(name__in=...)` query; create only the missing tags (individually, so the
  `notify_of_new_tag` post_save signal still fires; `bulk_create` would silently skip it). `notify_tag_on_add`: one `filter(id__in=...)` query.
- `puzzlescrape`: load the existing puzzles keyed by URL in one query at the start of a run.
- `Puzzle.save()`: fetch only the old `answer` with `values_list`.

Deliberately **not** changed: signal semantics, Zulip message text (apart from round names), the frontend JS.

## Open questions for the owner
- Should the overview offer a hunt switcher in the UI? `?hunt=<id>` works on the API, but there's no UI for it yet.
- Should `Round.name` be unique per hunt? Not enforced yet, because existing data might violate it.
