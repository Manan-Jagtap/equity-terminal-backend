"""The test DB must be per-run, not a fixed shared path.

35 test modules do `os.environ.setdefault("DATABASE_URL",
"sqlite:////tmp/_pytest_terminal.db")`. That single shared file made
`test_api_budget` fail with "attempt to write a readonly database" for a whole
session — written off repeatedly as a benign pre-existing red line — and when
the file was left locked it cascaded to 16 failures plus a collection error
that vanished on `rm`. A stale artefact was impersonating broken code.
"""
import os


def test_database_url_is_not_the_shared_path():
    url = os.environ.get("DATABASE_URL", "")
    assert "_pytest_terminal.db" not in url, (
        "the rootdir conftest must claim DATABASE_URL before any test module's "
        "setdefault — a shared temp DB is how a stale file starts failing "
        "unrelated tests")


def test_conftest_claims_the_url_before_any_module():
    """The conftest must WIN over the 35 `setdefault` calls.

    Asserted on the conftest's own value rather than the live env var: some
    test modules reassign DATABASE_URL during the run (the calibration replay
    points at its own throwaway DB), so the live value is not a stable oracle
    — pinning it made this test fail while the fix was working correctly.
    """
    import conftest
    assert "equity-terminal-tests-" in conftest._DB_PATH
    assert "_pytest_terminal.db" not in conftest._DB_PATH
    assert os.path.isabs(conftest._DB_PATH)


def test_the_db_file_is_writable():
    """The actual symptom: 'attempt to write a readonly database'."""
    import sqlite3
    path = os.environ["DATABASE_URL"].replace("sqlite:///", "", 1)
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
        con.execute("INSERT INTO _probe VALUES (1)")
        con.commit()
        con.execute("DROP TABLE _probe")
        con.commit()
    finally:
        con.close()
