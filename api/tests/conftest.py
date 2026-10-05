"""
conftest.py
============
On the Layerbase mirror (DB_TARGET=layerbase) the tables in api/local_only.py's LOCAL_ONLY are absent on
purpose (round 8 step 10: they stay in the local database). A test (or one of its fixtures) that stops because
one of those tables doesn't exist is reported as skipped, with the table named, instead of failed. Nothing
changes locally: there the tables exist, and any other error fails the test as before.
"""

import os
import re
import sys

import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import local_only  # noqa: E402

_MISSING = re.compile(r'relation "(?:public\.)?([a-z0-9_]+)" does not exist')


def local_only_table(exc):
    """The LOCAL_ONLY table whose absence raised `exc` (directly or as the cause of a wrapping error), else None."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        m = _MISSING.search(str(exc))
        if m and m.group(1) in local_only.LOCAL_ONLY:
            return m.group(1)
        exc = exc.__cause__ or exc.__context__
    return None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """A failure or setup error caused by a LOCAL_ONLY table's absence on the mirror is reported as a skip.
    Done on the report (not by catching the error), so a module fixture's cached error skips every test that
    uses it, not only the first."""
    rep = yield
    if rep.failed and call.excinfo is not None and local_only.on_mirror():
        table = local_only_table(call.excinfo.value)
        if table:
            path, lineno, _ = item.location
            rep.outcome = "skipped"
            rep.longrepr = (path, (lineno or 0) + 1, f"Skipped: {table} is {local_only.NOTE} (api/local_only.py)")
    return rep
