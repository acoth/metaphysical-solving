"""
Tests for the puzzles app.

Run with SQLite and no external services:
    env/bin/python manage.py test puzzles --settings=solving.test_settings
See docs/dev-notes/testing.md.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from puzzles.models import Config, Hunt, Location, Priority, Puzzle, Round, Status


class IsolatedTestCase(TestCase):
    """Patches every outbound integration and creates the minimal Config the models need."""

    PATCHES = [
        'puzzles.models.zulip_send',
        'puzzles.models.zulip_create_user',
        'puzzles.models.grant_access',
        'puzzles.models.create_google_folder',
        'puzzles.views.zulip_send',
    ]

    def setUp(self):
        for target in self.PATCHES:
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        sheet = mock.patch('puzzles.models.create_google_spreadsheet',
                           return_value={'spreadsheetUrl': 'https://sheet.example/x/edit',
                                         'spreadsheetId': 'x'})
        sheet.start()
        self.addCleanup(sheet.stop)
        zulip_ok = mock.patch('puzzles.views.zulip_user_account_active', return_value=True)
        zulip_ok.start()
        self.addCleanup(zulip_ok.stop)

        self.status = Status.objects.create(text='new', css_name='new')
        self.solved = Status.objects.create(text='solved!', css_name='solved')
        self.priority = Priority.objects.create(text='normal', css_name='normal')
        Location.objects.create(name='unknown')
        self.config = Config.objects.create(default_status=self.status, default_priority=self.priority)
        self.user = User.objects.create(username='solver', email='solver@example.com',
                                        first_name='Sol', last_name='Ver')

    _puzzle_counter = 0

    def make_puzzle(self, title=None, **kwargs):
        IsolatedTestCase._puzzle_counter += 1
        n = IsolatedTestCase._puzzle_counter
        return Puzzle.objects.create(title=title or 'Puzzle %d' % n,
                                     url='https://hunt.example/p/%d' % n,
                                     spreadsheet='https://sheet.example/%d/edit' % n,
                                     **kwargs)


class HuntModelTests(IsolatedTestCase):
    def test_only_one_current_hunt(self):
        a = Hunt.objects.create(name='2026', is_current=True)
        b = Hunt.objects.create(name='2027', is_current=True)
        a.refresh_from_db()
        self.assertFalse(a.is_current)
        self.assertTrue(b.is_current)
        self.assertEqual(Hunt.current(), b)

    def test_current_is_none_without_hunts(self):
        self.assertIsNone(Hunt.current())

    def test_child_round_inherits_parent_hunt(self):
        hunt = Hunt.objects.create(name='2027')
        parent = Round.objects.create(name='Parent', hunt=hunt)
        child = Round.objects.create(name='Child', parent_round=parent)
        self.assertEqual(child.hunt, hunt)
        self.assertEqual(set(hunt.rounds.all()), {parent, child})
        self.assertEqual(list(hunt.top_level_rounds()), [parent])

    def test_deleting_hunt_keeps_rounds(self):
        hunt = Hunt.objects.create(name='2027')
        r = Round.objects.create(name='R', hunt=hunt)
        hunt.delete()
        r.refresh_from_db()
        self.assertIsNone(r.hunt)

    def test_clean_rejects_cycle(self):
        a = Round.objects.create(name='A')
        b = Round.objects.create(name='B', parent_round=a)
        a.parent_round = b
        with self.assertRaises(ValidationError):
            a.clean()

    def test_clean_rejects_parent_in_other_hunt(self):
        h1 = Hunt.objects.create(name='2026')
        h2 = Hunt.objects.create(name='2027')
        parent = Round.objects.create(name='P', hunt=h1)
        child = Round(name='C', hunt=h2, parent_round=parent)
        with self.assertRaises(ValidationError):
            child.clean()



class MultiRoundTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.hunt = Hunt.objects.create(name='2027', is_current=True)
        self.r1 = Round.objects.create(name='Alpha', hunt=self.hunt)
        self.r2 = Round.objects.create(name='Beta', hunt=self.hunt)
        self.client.force_login(self.user)

    def test_puzzle_in_two_rounds(self):
        meta = self.make_puzzle('Meta')
        meta.rounds.add(self.r1, self.r2)
        self.assertEqual(set(self.r1.puzzles.all()), {meta})
        self.assertEqual(set(self.r2.puzzles.all()), {meta})
        self.assertEqual(meta.round_names(), 'Alpha, Beta')

    def test_round_names_empty(self):
        self.assertEqual(self.make_puzzle().round_names(), '')

    def test_overview_lists_puzzle_under_each_round(self):
        meta = self.make_puzzle('Meta')
        meta.rounds.add(self.r1, self.r2)
        loose = self.make_puzzle('Loose')
        data = self.client.get('/api_overview').json()
        by_name = {r['round']: r for r in data['rounds']}
        self.assertEqual([p['title'] for p in by_name['Alpha']['puzzles']], ['Meta'])
        self.assertEqual([p['title'] for p in by_name['Beta']['puzzles']], ['Meta'])
        self.assertEqual([p['id'] for p in by_name['Unassigned']['puzzles']], [loose.id])

    def test_api_puzzle_reports_all_rounds(self):
        meta = self.make_puzzle('Meta')
        meta.rounds.add(self.r1, self.r2)
        data = self.client.get('/api_puzzle/%d' % meta.id).json()['puzzle']
        self.assertEqual(data['round'], 'Alpha, Beta')
        self.assertEqual([r['name'] for r in data['rounds']], ['Alpha', 'Beta'])

    def test_solved_message_lists_rounds(self):
        import puzzles.models as m
        meta = self.make_puzzle('Meta')
        meta.rounds.add(self.r1, self.r2)
        m.zulip_send.reset_mock()
        meta.answer = 'FOO'
        meta.save()
        messages = [c.kwargs['message'] for c in m.zulip_send.call_args_list]
        self.assertIn('Puzzle Meta solved [Alpha, Beta]', messages)


from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MigrationTestCase(TransactionTestCase):
    """Migrate back to `migrate_from`, seed data with historical models, then migrate forward to `migrate_to`."""
    migrate_from = None
    migrate_to = None

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('puzzles', self.migrate_from)])
        self.old_apps = executor.loader.project_state([('puzzles', self.migrate_from)]).apps
        self.seed(self.old_apps)
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([('puzzles', self.migrate_to)])
        self.apps = executor.loader.project_state([('puzzles', self.migrate_to)]).apps

    def tearDown(self):
        # Leave the schema at the latest migration for the other tests.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def seed(self, apps):
        pass


class HuntDataMigrationTests(MigrationTestCase):
    migrate_from = '0003_alter_jitsirooms_options_and_more'
    migrate_to = '0004_hunt'

    def seed(self, apps):
        Round = apps.get_model('puzzles', 'Round')
        parent = Round.objects.create(name='Parent', order=0)
        Round.objects.create(name='Child', parent_round=parent, order=1)

    def test_existing_rounds_attached_to_new_current_hunt(self):
        Hunt = self.apps.get_model('puzzles', 'Hunt')
        Round = self.apps.get_model('puzzles', 'Round')
        hunt = Hunt.objects.get()
        self.assertTrue(hunt.is_current)
        self.assertEqual(Round.objects.filter(hunt=hunt).count(), 2)



class RoundsM2MDataMigrationTests(MigrationTestCase):
    migrate_from = '0004_hunt'
    migrate_to = '0005_puzzle_rounds_m2m'

    def seed(self, apps):
        Status = apps.get_model('puzzles', 'Status')
        Priority = apps.get_model('puzzles', 'Priority')
        Round = apps.get_model('puzzles', 'Round')
        Puzzle = apps.get_model('puzzles', 'Puzzle')
        status = Status.objects.create(text='new', css_name='new', order=0)
        priority = Priority.objects.create(text='normal', css_name='normal', order=0)
        r = Round.objects.create(name='Alpha', order=0)
        common = dict(status=status, priority=priority, template=None, folder=None)
        Puzzle.objects.create(title='In round', url='https://h.example/1', round=r, order=0, **common)
        Puzzle.objects.create(title='No round', url='https://h.example/2', order=1, **common)

    def test_fk_copied_into_m2m(self):
        Puzzle = self.apps.get_model('puzzles', 'Puzzle')
        self.assertEqual([r.name for r in Puzzle.objects.get(title='In round').rounds.all()], ['Alpha'])
        self.assertEqual(Puzzle.objects.get(title='No round').rounds.count(), 0)

    def test_reverse_restores_fk(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('puzzles', self.migrate_from)])
        Puzzle = executor.loader.project_state([('puzzles', self.migrate_from)]).apps.get_model('puzzles', 'Puzzle')
        self.assertEqual(Puzzle.objects.get(title='In round').round.name, 'Alpha')
        self.assertIsNone(Puzzle.objects.get(title='No round').round)


# ---------------------------------------------------------------------------
# batch-queries branch: query counts must not grow with the number of puzzles.
# ---------------------------------------------------------------------------
from django.db import connection as _connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now as _now

from puzzles.models import AccessLog, QueuedAnswer, QueuedHint, SubmittedAnswer, Tag


class BatchedViewsTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.other = User.objects.create(username='other', email='o@example.com', first_name='O', last_name='Ther')
        self.hunt = Hunt.objects.create(name='2027', is_current=True)
        self.parent = Round.objects.create(name='Parent', hunt=self.hunt)
        self.rounds = [Round.objects.create(name='R%d' % i, hunt=self.hunt,
                                            parent_round=self.parent if i % 2 else None) for i in range(3)]
        self.tag = Tag.objects.create(name='meta')
        self.client.force_login(self.user)

    def add_puzzles(self, n):
        last = None
        for i in range(n):
            p = self.make_puzzle()
            p.rounds.add(self.rounds[i % 3])
            if i % 4 == 0:
                p.rounds.add(self.parent)  # some puzzles are in two rounds
            p.tags.add(self.tag)
            AccessLog.objects.create(user=self.other, puzzle=p, sessionEnd=_now(), lastUpdate=_now(),
                                     accumulatedMinutes=90, linkedOut=True)
            QueuedAnswer.objects.create(puzzle=p, answer='ANS%d' % i)
            SubmittedAnswer.objects.create(puzzle=p, user=self.other, answer='ANS%d' % i, backsolved=False,
                                           phone='', success=True)
            QueuedHint.objects.create(puzzle=p, user=self.other, details='help')
            last = p
        return last

    def count_queries(self, url):
        with CaptureQueriesContext(_connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return len(ctx)

    def test_query_counts_are_constant(self):
        urls = ['/api_overview', '/api_unloved/', '/api_puzzle_history/%d', '/api_puzzle/%d',
                '/answers/', '/hints/', '/whowhat/']
        p = self.add_puzzles(3)
        small = {u: self.count_queries(u % p.id if '%d' in u else u) for u in urls}
        p = self.add_puzzles(12)
        large = {u: self.count_queries(u % p.id if '%d' in u else u) for u in urls}
        self.assertEqual(small, large)
        # Pin the overview: auth (2) + config, hunt, rounds, links, puzzles, tags, solver counts,
        # linked-out, statuses, priorities. Change deliberately if the view changes.
        self.assertEqual(large['/api_overview'], 12)

    def test_overview_content(self):
        meta = self.make_puzzle('Meta')
        meta.rounds.add(self.rounds[0], self.rounds[1])
        AccessLog.objects.create(user=self.user, puzzle=meta, sessionEnd=_now(), linkedOut=True)
        AccessLog.objects.create(user=self.other, puzzle=meta, sessionEnd=_now())
        loose = self.make_puzzle('Loose')
        data = self.client.get('/api_overview').json()
        self.assertEqual(data['hunt'], {'id': self.hunt.id, 'name': '2027'})
        # Depth-first: Parent, then its child R1, then the other top-level rounds; Unassigned last.
        self.assertEqual([r['round'] for r in data['rounds']], ['Parent', 'R1', 'R0', 'R2', 'Unassigned'])
        by_name = {r['round']: r for r in data['rounds']}
        self.assertEqual(by_name['R1']['parent_round'], 'Parent')
        self.assertEqual(by_name['R1']['parent_round_id'], self.parent.id)
        self.assertNotIn('puzzles', by_name['R2'])  # empty rounds omit the key, as before
        info = by_name['R0']['puzzles'][0]
        self.assertEqual(info, by_name['R1']['puzzles'][0])
        # log_on_creation also logs the first user (self.user); that log isn't linked out but this one is.
        self.assertEqual(info['solver_count'], 2)
        self.assertFalse(info['unopened'])
        self.assertEqual(info['status'], 'new')
        self.assertEqual([p['id'] for p in by_name['Unassigned']['puzzles']], [loose.id])
        self.assertTrue(by_name['Unassigned']['puzzles'][0]['unopened'])

    def test_overview_scoped_to_hunt(self):
        old = Hunt.objects.create(name='2026')
        old_round = Round.objects.create(name='Old round', hunt=old)
        old_puzzle = self.make_puzzle('Old puzzle')
        old_puzzle.rounds.add(old_round)
        legacy = Round.objects.create(name='Legacy, no hunt')
        data = self.client.get('/api_overview').json()
        names = [r['round'] for r in data['rounds']]
        self.assertNotIn('Old round', names)
        self.assertIn('Legacy, no hunt', names)
        self.assertNotIn('Unassigned', names)  # the old puzzle has a round, just not in this hunt
        data = self.client.get('/api_overview?hunt=%d' % old.id).json()
        self.assertEqual(data['hunt']['name'], '2026')
        self.assertIn('Old round', [r['round'] for r in data['rounds']])
        self.assertEqual(self.client.get('/api_overview?hunt=99999').status_code, 404)
        self.assertEqual(self.client.get('/api_overview?hunt=abc').status_code, 404)

    def test_overview_without_any_hunt_shows_all_rounds(self):
        Hunt.objects.all().delete()
        data = self.client.get('/api_overview').json()
        self.assertIsNone(data['hunt'])
        self.assertEqual({r['round'] for r in data['rounds']}, {'Parent', 'R0', 'R1', 'R2'})

    def test_unloved_matches_per_puzzle_methods(self):
        p = self.add_puzzles(2)
        data = self.client.get('/api_unloved/').json()['puzzles']
        row = next(r for r in data if r['puzzle']['id'] == p.id)
        self.assertEqual(row['puzzle']['round'], p.round_names())
        self.assertEqual(row['updating_userid'], self.other.id)
        self.assertTrue(row['total_time'])  # 90 minutes logged
        self.assertEqual(p.effort_spent()['solver_hours'], 1.5)

    def test_answer_queue_tolerates_duplicate_successes(self):
        p = self.make_puzzle()
        QueuedAnswer.objects.create(puzzle=p, answer='X')
        for _ in range(2):
            SubmittedAnswer.objects.create(puzzle=p, user=self.other, answer='X', backsolved=False, phone='', success=True)
        response = self.client.get('/answers/')
        qa = response.context['queued_answers'][0]
        self.assertTrue(qa.success)
        self.assertEqual(qa.user, self.other)

    def test_update_tags_creates_missing_and_reuses_existing(self):
        import puzzles.models as m
        p = self.make_puzzle()
        m.zulip_send.reset_mock()
        response = self.client.post('/api_update_puzzle/%d' % p.id, data='{"tags": ["meta", "brand new"]}',
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(p.tag_list()), ['brand new', 'meta'])
        self.assertEqual(Tag.objects.filter(name='meta').count(), 1)
        streams = [c.kwargs['stream'] for c in m.zulip_send.call_args_list]
        self.assertEqual(streams.count('tags'), 3)  # new-tag notice + one "tagged as" per added tag


class ScraperSyncTests(IsolatedTestCase):
    def state(self, *rounds):
        return {'rounds': [{'title': title, 'puzzles': [
            dict({'state': 'unlocked', 'title': slug.title(), 'slug': slug}, **extra) for slug, extra in puzzles]}
            for title, puzzles in rounds]}

    def test_sync_creates_and_links_cross_round_puzzles(self):
        from puzzles.management.commands.puzzlescrape import PUZZLE_URL_PREFIX, sync_state
        hunt = Hunt.objects.create(name='2027', is_current=True)
        Round.objects.create(name='Alpha', hunt=Hunt.objects.create(name='2026'))  # same name, old hunt
        sync_state(self.state(('Alpha', [('one', {}), ('meta', {})]),
                              ('Beta', [('meta', {}), ('two', {'answer': 'TWO'})])))
        alpha = Round.objects.get(hunt=hunt, name='Alpha')
        beta = Round.objects.get(hunt=hunt, name='Beta')
        meta = Puzzle.objects.get(url=PUZZLE_URL_PREFIX + 'meta')
        self.assertEqual(set(meta.rounds.all()), {alpha, beta})
        two = Puzzle.objects.get(url=PUZZLE_URL_PREFIX + 'two')
        self.assertEqual((two.answer, two.status), ('TWO', self.solved))

        # A second identical run changes nothing and does no per-puzzle work.
        with CaptureQueriesContext(_connection) as ctx:
            sync_state(self.state(('Alpha', [('one', {}), ('meta', {})]),
                                  ('Beta', [('meta', {}), ('two', {'answer': 'TWO'})])))
        self.assertEqual(len(ctx), 5)  # solved status, hunt, rounds, puzzles, memberships
        self.assertEqual(Puzzle.objects.count(), 3)


class GetLastRevsTests(IsolatedTestCase):
    def test_maps_responses_by_request_id_and_chunks(self):
        from puzzles import googlespreadsheet as gs
        puzzles = [self.make_puzzle() for _ in range(3)]
        batches = []

        class FakeBatch:
            def __init__(self, callback):
                self.callback, self.requests = callback, []
                batches.append(self)
            def add(self, request, request_id):
                self.requests.append((request_id, request))
            def execute(self):
                for request_id, file_id in self.requests:
                    if file_id == puzzles[1].spreadsheet.split('/')[-2]:
                        self.callback(request_id, None, Exception('boom'))
                    else:
                        self.callback(request_id, {'revisions': [
                            {'modifiedTime': '2026-01-0%sT00:00:00Z' % (1 + int(request_id)), 'id': request_id}]}, None)

        service = mock.Mock()
        service.new_batch_http_request.side_effect = lambda callback: FakeBatch(callback)
        service.revisions.return_value.list.side_effect = lambda fileId: fileId
        with mock.patch.object(gs.discovery, 'build', return_value=service), \
             mock.patch.object(gs, 'get_google_credentials'), \
             mock.patch.object(gs, 'DRIVE_BATCH_LIMIT', 2):
            result = gs.get_last_revs(Puzzle.objects.filter(id__in=[p.id for p in puzzles]).order_by('id'))
        self.assertEqual(len(batches), 2)  # 3 puzzles, batches of 2
        self.assertEqual([r['puzzle'] for r in result], [puzzles[0], puzzles[2], puzzles[1]])  # untouched last
        self.assertIsNone(result[2]['rev'])
