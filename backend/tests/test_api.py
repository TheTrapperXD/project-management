import base64
import json
from datetime import date, datetime, timedelta, timezone

import jwt
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import app, create_app
from app.config import get_settings
from app.security import create_token, hash_password, login_limiter
from tests.conftest import CSRF, TEST_PW


def api_routes():
    # The OpenAPI document lists every registered route, however nested.
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api"):
            for method in ops:
                yield method.upper(), path


def test_every_route_except_login_requires_bearer_token(client):
    checked = 0
    for method, path in api_routes():
        if path == "/api/auth/login":
            continue
        r = client.request(method, path, headers=CSRF, json={} if method != "GET" else None)
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.headers["www-authenticate"] == "Bearer", (method, path)
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


def test_login_generic_error_and_token_response(client, user):
    bad = client.post("/api/auth/login", json={"username": "alice", "password": "wrong-password"}, headers=CSRF)
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong-password"}, headers=CSRF)
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json() == {"detail": "Invalid credentials"}
    ok = client.post("/api/auth/login", json={"username": "alice", "password": TEST_PW}, headers=CSRF)
    assert ok.status_code == 200
    body = ok.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 7200
    assert "set-cookie" not in ok.headers
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["username"] == "alice"


def _forge(payload, key, alg="HS256"):
    return jwt.encode(payload, key, algorithm=alg)


def _payload(user_id=1, **over):
    now = datetime.now(timezone.utc)
    return {"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=1), **over}


def _b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_valid_cookie_alone_is_rejected(client, user):
    token = create_token(user.id)
    client.cookies.set("access_token", token)
    r = client.get("/api/auth/me")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_bad_tokens_rejected(client, user):
    key = get_settings().SECRET_KEY
    now = datetime.now(timezone.utc)
    none_token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64({'sub': str(user.id), 'exp': int(now.timestamp()) + 3600})}."
    bad = {
        "malformed": "not-a-jwt",
        "expired": _forge(_payload(user.id, iat=now - timedelta(hours=3), exp=now - timedelta(hours=1)), key),
        "wrong-signature": _forge(_payload(user.id), "x" * 48),
        "alg-none": none_token,
        "no-exp": _forge({"sub": str(user.id)}, key),
        "no-sub": _forge({"exp": now + timedelta(hours=1)}, key),
    }
    for name, tok in bad.items():
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401, name
        assert r.headers["www-authenticate"] == "Bearer", name
        assert tok not in r.text, name
    for header in ("Bearer", "Bearer ", "Basic abc", create_token(user.id)):
        assert client.get("/api/auth/me", headers={"Authorization": header}).status_code == 401, header


def test_valid_bearer_token_works_and_ttl_is_two_hours(client, user):
    token = create_token(user.id)
    claims = jwt.decode(token, get_settings().SECRET_KEY, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 7200
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["username"] == "alice"


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
    assert "access-control-allow-credentials" not in r.headers
    other = client.get("/api/auth/me", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_cors_preflight(client):
    pre = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    ok = client.options("/api/auth/me", headers={"Origin": "http://localhost:5173", **pre})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "authorization" in ok.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in ok.headers
    bad = client.options("/api/auth/me", headers={"Origin": "http://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers


def test_logout_requires_auth_and_is_stateless(client, logged_in):
    assert logged_in.post("/api/auth/logout", headers=CSRF).status_code == 204
    token = logged_in.headers["Authorization"]
    del logged_in.headers["Authorization"]
    assert logged_in.post("/api/auth/logout", headers=CSRF).status_code == 401
    # Stateless: the token is simply discarded by the client; it is not revoked server-side.
    assert logged_in.get("/api/auth/me", headers={"Authorization": token}).status_code == 200


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
