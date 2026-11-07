import os
import secrets

# Must be set before the app is imported. No real database is ever used.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
os.environ["CORS_ORIGIN"] = "http://localhost:5173"
os.environ["COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models  # noqa: E402,F401
from app.create_user import create_user  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.security import login_limiter  # noqa: E402

TEST_PW = secrets.token_urlsafe(16)
CSRF = {"X-Requested-With": "pharmacy-app"}


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s


@pytest.fixture()
def user(session):
    return create_user(session, "alice", TEST_PW)


@pytest.fixture()
def client(engine):
    maker = sessionmaker(bind=engine, expire_on_commit=False)

    def _get_db():
        with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    login_limiter.clear()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    login_limiter.clear()


@pytest.fixture()
def logged_in(client, user):
    r = client.post("/api/auth/login", json={"username": "alice", "password": TEST_PW}, headers=CSRF)
    assert r.status_code == 200
    return client
