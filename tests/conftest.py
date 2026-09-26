import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, seed  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    """A fresh, seeded database + anchor log for every test."""
    db.configure(str(tmp_path / "t.db"), str(tmp_path / "anchors.log"))
    db.reset_all()
    c = db.connect()
    seed.seed(c)
    yield c
    c.close()


@pytest.fixture()
def blank(tmp_path):
    """Users only, no documents."""
    db.configure(str(tmp_path / "b.db"), str(tmp_path / "b-anchors.log"))
    db.reset_all()
    c = db.connect()
    c.executemany("INSERT INTO users VALUES(?,?,?,?)", seed.USERS)
    yield c
    c.close()
