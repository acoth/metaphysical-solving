# Settings for running the test suite without touching the production Postgres DB.
#   env/bin/python manage.py test puzzles --settings=solving.test_settings
# See docs/dev-notes/testing.md. External services (Zulip, Google) are mocked in puzzles/tests.py.
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
