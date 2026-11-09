# Testing safely

This checkout is (or looks like) the live server, so:

- **Never** run `manage.py test` with the default settings. Django would try to create `test_puzzle_db` on the real Postgres server, and the
  model hooks would call real Zulip and Google APIs.
- Use the SQLite test settings instead:

```bash
env/bin/python manage.py test puzzles --settings=solving.test_settings
```

`solving/test_settings.py` imports the normal settings and overrides `DATABASES` to use in-memory SQLite.
`puzzles/tests.py` patches every outbound integration (`zulip_send`, `zulip_create_user`, `create_google_spreadsheet`,
`create_google_folder`, `grant_access`) in a shared base class (`IsolatedTestCase`), so the tests never make a network call.

The tests use `assertNumQueries` to pin the query counts of the optimized views. If you add a query to one of those views,
the test will fail. Change the expected number deliberately.

Check migrations without touching the DB:

```bash
env/bin/python manage.py makemigrations --check --dry-run --settings=solving.test_settings
```

Applying the migrations to production (`manage.py migrate`) is a deliberate deploy step. Take a `pg_dump` first.
