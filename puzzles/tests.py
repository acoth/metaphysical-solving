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
