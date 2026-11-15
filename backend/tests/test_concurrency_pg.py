"""Opt-in row-locking tests against a real PostgreSQL. Skipped unless TEST_PG_URL is set.

The normal suite runs on SQLite, which ignores SELECT ... FOR UPDATE, so these
tests are the only proof that concurrent dispense / write-off cannot oversell.

How to run (from backend/):

    TEST_PG_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME' .venv/bin/pytest -q tests/test_concurrency_pg.py

Use a THROWAWAY Postgres (a local container, or a separate Neon branch).
Do NOT point TEST_PG_URL at the live database. On Neon use the direct
(non-pooled) connection string: the pooler does not accept the search_path option.

Isolation: each test creates its own schema named test_<random>, sets it as the
search_path through the connection options, creates the tables inside it and, in
teardown, drops ONLY that schema. Nothing outside that schema is created,
altered or dropped.
"""
import os
import secrets
import threading
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  (registers tables)
from app.create_user import create_user
from app.db import Base
from app.models import Lot, StockMovement
from app.services.inventory import (
    ExceedsRemainingError,
    InsufficientStockError,
    dispense_fifo,
    stock_in,
    writeoff_lot,
)

PG_URL = os.environ.get("TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="TEST_PG_URL not set (opt-in Postgres concurrency test)")

TODAY = date(2026, 6, 15)


@pytest.fixture()
def pg_maker():
    schema = f"test_{secrets.token_hex(8)}"
    admin = create_engine(PG_URL)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(PG_URL, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.connect() as conn:
            # Refuse to create anything unless we really are inside the throwaway schema.
            assert conn.execute(text("SELECT current_schema()")).scalar() == schema
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def seed(maker, quantity):
    with maker() as s:
        user = create_user(s, "alice", secrets.token_urlsafe(16))
        lot = stock_in(s, user.id, "Med", "S1", "L1", TODAY + timedelta(days=30), quantity, None, TODAY)
        return user.id, lot.medicine_id, lot.id


def race(maker, *actions):
    """Run each action(session) in its own thread and session, released together by a barrier."""
    barrier = threading.Barrier(len(actions))
    outcomes = [None] * len(actions)

    def worker(i, action):
        with maker() as s:
            barrier.wait(timeout=30)
            try:
                action(s)
                outcomes[i] = "ok"
            except (InsufficientStockError, ExceedsRemainingError) as exc:
                outcomes[i] = exc
            except Exception as exc:  # unexpected: surfaced by the assertion in the caller
                outcomes[i] = exc

    threads = [threading.Thread(target=worker, args=(i, a)) for i, a in enumerate(actions)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "worker thread did not finish (deadlock?)"
    return outcomes


def moved(session, lot_id, *types):
    return session.scalar(
        select(func.coalesce(func.sum(StockMovement.quantity), 0)).where(
            StockMovement.lot_id == lot_id, StockMovement.type.in_(types)
        )
    )


def test_two_dispenses_of_the_last_unit(pg_maker):
    user_id, med_id, lot_id = seed(pg_maker, 1)

    def dispense(s):
        dispense_fifo(s, user_id, med_id, 1, TODAY)

    outcomes = race(pg_maker, dispense, dispense)
    assert outcomes.count("ok") == 1, outcomes
    assert sum(isinstance(o, InsufficientStockError) for o in outcomes) == 1, outcomes
    with pg_maker() as s:
        assert s.get(Lot, lot_id).quantity == 0
        assert moved(s, lot_id, "out") == 1


def test_dispense_racing_writeoff_on_same_lot(pg_maker):
    start = 10
    user_id, med_id, lot_id = seed(pg_maker, start)

    def dispense(s):
        dispense_fifo(s, user_id, med_id, 7, TODAY)

    def writeoff(s):
        writeoff_lot(s, user_id, lot_id, 7, "damaged", TODAY)

    outcomes = race(pg_maker, dispense, writeoff)
    assert all(o == "ok" or isinstance(o, (InsufficientStockError, ExceedsRemainingError)) for o in outcomes), outcomes
    with pg_maker() as s:
        final = s.get(Lot, lot_id).quantity
        removed = start - final
        assert 0 <= final <= start
        assert removed <= start
        assert moved(s, lot_id, "out", "waste") == removed
        assert removed == 7 * outcomes.count("ok")
        assert outcomes.count("ok") == 1, outcomes
