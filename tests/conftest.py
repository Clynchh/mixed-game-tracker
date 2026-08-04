"""Shared test setup.

Every test in this suite runs against a throwaway SQLite file in a pytest
tmp directory - never the real tracker.db a developer might have sitting
next to the code. MGT_DB_PATH has to be set before `db` (and therefore
`app`, which calls db.init_db() at import time) is imported for the first
time, so this happens at module load, before pytest collects any tests.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_DIR = tempfile.mkdtemp(prefix="mgt-tests-")
os.environ["MGT_DB_PATH"] = os.path.join(_TMP_DIR, "test.db")
os.environ["MGT_NO_BROWSER"] = "1"
os.environ.pop("HAND_HISTORY_DIR", None)
os.environ.pop("POKERSTARS_USERNAME", None)

import db  # noqa: E402


SAMPLE_HANDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_hands", "session1.txt"
)


@pytest.fixture
def sample_hands_text():
    with open(SAMPLE_HANDS_PATH, encoding="utf-8", errors="replace") as f:
        return f.read()


@pytest.fixture(autouse=True)
def clean_tables():
    """Every test starts with an empty database, regardless of what an
    earlier test left behind - all tests share one on-disk file (swapping
    the file mid-session would leave `app`'s already-imported Flask app
    pointed at a stale path), so table contents are the isolation boundary
    instead."""
    db.init_db()
    with db.get_conn() as conn:
        for table in ("tags", "hands", "file_state", "settings", "tournaments"):
            conn.execute(f"DELETE FROM {table}")
    yield
