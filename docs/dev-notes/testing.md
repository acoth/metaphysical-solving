# Testing

This checkout is a local testing environment backed by Postgres (`puzzle_db` on localhost). There are two ways to run the suite:

**SQLite (fast, needs no DB permissions):**

```bash
env/bin/python manage.py test puzzles --settings=solving.test_settings
```

**Postgres (matches the real backend):** Django creates and then drops a throwaway `test_puzzle_db`, so the `puzzle` role needs `CREATEDB`
(it doesn't have it by default). Grant it once as the postgres superuser:

```bash
sudo -u postgres psql -c "ALTER ROLE puzzle CREATEDB;"
env/bin/python manage.py test puzzles --noinput
```

Either way, external services are mocked by the tests (below), so no Zulip or Google calls are made.

`solving/test_settings.py` imports the normal settings and overrides `DATABASES` to use in-memory SQLite.
`puzzles/tests.py` patches every outbound integration (`zulip_send`, `zulip_create_user`, `create_google_spreadsheet`,
`create_google_folder`, `grant_access`) in a shared base class (`IsolatedTestCase`), so the tests never make a network call.

`BatchedViewsTests.test_query_counts_are_constant` checks that the query counts of the optimized views don't grow with the
number of puzzles, and pins the overview at 12. If you add a query to one of those views, that test will fail. Change the expected number deliberately.

Check migrations without touching the DB:

```bash
env/bin/python manage.py makemigrations --check --dry-run --settings=solving.test_settings
```

## Before migrating the local DB, back it up

```bash
mkdir -p ~/backups
PGPASSWORD=... pg_dump -h localhost -U puzzle -Fc -f ~/backups/puzzle_db_$(date +%Y%m%d-%H%M%S).dump puzzle_db
# restore: pg_restore -h localhost -U puzzle -d puzzle_db --clean --if-exists <file>
```
