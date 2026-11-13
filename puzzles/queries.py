"""
Batched query helpers.

Each function answers, for many puzzles at once and in a fixed number of queries, a question that a
Puzzle instance method answers for one puzzle (which costs at least one query per puzzle). Results are
plain dicts keyed by puzzle id. See docs/dev-notes/plan.md (sections 3-4).

Per-puzzle method          -> batched equivalent
  Puzzle.recent_count()       recent_solver_counts()
  Puzzle.recent_solvers()     recent_solvers_by_puzzle()
  Puzzle.unopened_theirs(u)   linked_out_puzzle_ids(u)   (puzzle is unopened iff its id is NOT in the set)
  Puzzle.effort_spent()       log_summary_by_puzzle()[pid]['effort']
  last AccessLog per puzzle   log_summary_by_puzzle()[pid]['last_log']
"""
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils.timezone import now

from puzzles.models import AccessLog

# Same window as Puzzle.recent_logs().
RECENT_WINDOW = timedelta(seconds=360)


def _recent_cutoff():
    return now() - RECENT_WINDOW


def recent_solver_counts(puzzle_ids=None):
    """{puzzle_id: number of distinct users with a session ending in the recent window}. One query."""
    qs = AccessLog.objects.filter(sessionEnd__gte=_recent_cutoff())
    if puzzle_ids is not None:
        qs = qs.filter(puzzle_id__in=puzzle_ids)
    return dict(qs.order_by().values('puzzle_id')
                .annotate(n=Count('user_id', distinct=True))
                .values_list('puzzle_id', 'n'))


def recent_solvers_by_puzzle(puzzle_ids):
    """{puzzle_id: [User, ...]} of recent solvers, each list ordered by user id. One query."""
    result = defaultdict(list)
    seen = set()
    logs = (AccessLog.objects.filter(puzzle_id__in=puzzle_ids, sessionEnd__gte=_recent_cutoff())
            .select_related('user').order_by('puzzle_id', 'user_id'))
    for log in logs:
        key = (log.puzzle_id, log.user_id)
        if key not in seen:
            seen.add(key)
            result[log.puzzle_id].append(log.user)
    return result


def linked_out_puzzle_ids(user):
    """Ids of puzzles this user has opened via the link-out. One query.

    Unlike Puzzle.unopened_theirs(), this tolerates several AccessLog rows per (user, puzzle),
    where .get() would raise MultipleObjectsReturned."""
    return set(AccessLog.objects.filter(user=user, linkedOut=True).values_list('puzzle_id', flat=True))


def log_summary_by_puzzle(puzzle_ids):
    """{puzzle_id: {'last_log': AccessLog|None, 'effort': {'solvers', 'solver_hours'}}}. One query.

    'effort' replicates Puzzle.effort_spent() exactly (one entry per log row, hours rounded per row);
    'last_log' is the log with the greatest lastUpdate (its .user is already loaded)."""
    summary = {pid: {'last_log': None, 'effort': {'solvers': 0, 'solver_hours': 0}} for pid in puzzle_ids}
    logs = (AccessLog.objects.filter(puzzle_id__in=puzzle_ids)
            .select_related('user').order_by('puzzle_id', 'lastUpdate', 'id'))
    for log in logs:
        entry = summary[log.puzzle_id]
        entry['last_log'] = log  # ordered by lastUpdate, so the last one seen wins
        entry['effort']['solvers'] += 1
        entry['effort']['solver_hours'] += round(log.accumulatedMinutes / 60., 2)
    return summary

