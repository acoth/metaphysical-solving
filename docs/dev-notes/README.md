# Developer notes

Working notes for the Hunt / multi-round / query-batching work started 2026-11-09.
Read these first to get context before continuing the work.

| File | What's in it |
| --- | --- |
| [findings.md](findings.md) | How the code worked before this work: the Puzzle/Round relationship, N+1 query hotspots, and existing bugs noticed along the way |
| [plan.md](plan.md) | Design decisions and the branch plan, including what's still open |
| [worklog.md](worklog.md) | Running log of what was actually done, branch by branch, and how it was verified |
| [testing.md](testing.md) | How to run the test suite safely (SQLite, external services mocked) |

## Branches (stacked, each built on the previous one)

1. `hunt-model`: adds the `Hunt` model and `Round.hunt`.
2. `multi-round-puzzles`: changes `Puzzle.round` (a FK) into `Puzzle.rounds` (an M2M).
3. `batch-queries`: rewrites the overview so it runs a fixed number of queries, and batches queries in the other views.

Merge them in that order. None of them has been pushed or deployed.
