from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import app, create_app
from app.security import hash_password, login_limiter
from tests.conftest import CSRF, TEST_PW


def api_routes():
    # The OpenAPI document lists every registered route, however nested.
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api"):
            for method in ops:
                yield method.upper(), path


def test_every_route_except_login_requires_cookie(client):
    checked = 0
    for method, path in api_routes():
        if path == "/api/auth/login":
            continue
        r = client.request(method, path, headers=CSRF, json={} if method != "GET" else None)
        assert r.status_code == 401, (method, path, r.status_code)
        checked += 1
    assert checked == 7


def test_post_without_csrf_header_is_403(client, logged_in):
    r = client.post("/api/stock/out", json={"medicine_id": 1, "quantity": 1})
    assert r.status_code == 403
    r = client.post("/api/auth/login", json={"username": "alice", "password": TEST_PW})
    assert r.status_code == 403
    r = client.post("/api/stock/out", json={"medicine_id": 1, "quantity": 1}, headers={"X-Requested-With": "x"})
    assert r.status_code == 403


def test_short_secret_key_refuses_to_start():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{"SECRET_KEY": "short", "DATABASE_URL": "sqlite://", "CORS_ORIGIN": "http://x"})


def test_app_factory_fails_with_short_secret(monkeypatch):
    from app import config
    monkeypatch.setenv("SECRET_KEY", "short")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            create_app()
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()


def test_password_min_length():
    with pytest.raises(ValueError):
        hash_password("short")


def test_login_generic_error_and_cookie_flags(client, user):
    bad = client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"}, headers=CSRF)
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password"}, headers=CSRF)
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json() == {"detail": "Invalid credentials"}
    ok = client.post("/api/auth/login", json={"username": "alice", "password": TEST_PW}, headers=CSRF)
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/auth/me").json()["username"] == "alice"


def test_login_rate_limit_429_on_sixth_failure(client, user):
    body = {"username": "alice", "password": "wrong-password"}
    codes = [client.post("/api/auth/login", json=body, headers=CSRF).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    # Correct password is also blocked while limited; other usernames are unaffected.
    assert client.post("/api/auth/login", json={"username": "alice", "password": TEST_PW}, headers=CSRF).status_code == 429
    assert client.post("/api/auth/login", json={"username": "bob", "password": "x"}, headers=CSRF).status_code == 401


def test_security_headers_and_cors(client):
    r = client.get("/api/auth/me", headers={"Origin": "http://localhost:5173"})
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/api/auth/me", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_logout_clears_session(logged_in):
    assert logged_in.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert logged_in.get("/api/auth/me").status_code == 401


def test_stock_flow_over_http(logged_in):
    future = (date.today() + timedelta(days=20)).isoformat()
    body = {"name": "Paracetamol", "sku": "PCM", "lot_number": "L1", "expiry_date": future, "quantity": 10, "reorder_threshold": 50}
    r = logged_in.post("/api/stock/in", json=body, headers=CSRF)
    assert r.status_code == 200, r.text
    med_id = r.json()["medicine_id"]
    assert logged_in.post("/api/stock/in", json={**body, "expiry_date": (date.today() + timedelta(days=21)).isoformat()}, headers=CSRF).status_code == 409
    assert logged_in.post("/api/stock/in", json={**body, "lot_number": "L2", "expiry_date": "2000-01-01"}, headers=CSRF).status_code == 422
    assert logged_in.post("/api/stock/in", json={**body, "quantity": 0}, headers=CSRF).status_code == 422
    meds = logged_in.get("/api/medicines").json()
    assert meds == [{"id": med_id, "name": "Paracetamol", "sku": "PCM", "total_quantity": 10, "reorder_threshold": 50}]
    r = logged_in.post("/api/stock/out", json={"medicine_id": med_id, "quantity": 11}, headers=CSRF)
    assert r.status_code == 409 and r.json()["available"] == 10
    assert logged_in.post("/api/stock/out", json={"medicine_id": 999, "quantity": 1}, headers=CSRF).status_code == 404
    r = logged_in.post("/api/stock/out", json={"medicine_id": med_id, "quantity": 4}, headers=CSRF)
    assert r.status_code == 200 and r.json()["allocations"][0]["taken"] == 4
    dash = logged_in.get("/api/dashboard").json()
    assert dash["red"][0]["quantity"] == 6 and dash["low_stock"][0]["sku"] == "PCM"
    waste = logged_in.get("/api/reports/waste").json()
    assert waste["grand_total"] == 0
    assert logged_in.get("/api/reports/waste?month=2026-13").status_code == 422
