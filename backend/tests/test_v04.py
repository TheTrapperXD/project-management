from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import config
from app.config import Settings
from app.main import create_app
from app.models import Lot, Medicine
from app.services.inventory import dashboard_buckets, list_medicines, stock_in
from tests.conftest import CSRF, TEST_PW

TODAY = date(2026, 6, 15)


def add(session, user, lot, days, qty, sku="S1", name="Med", threshold=None):
    return stock_in(session, user.id, name, sku, lot, TODAY + timedelta(days=days), qty, threshold, TODAY)


def settings(origin):
    return Settings(_env_file=None, DATABASE_URL="sqlite://", SECRET_KEY="x" * 32, CORS_ORIGIN=origin)


# --- (A) health --------------------------------------------------------------

def test_health_is_public_and_has_security_headers(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


# --- (E) CORS_ORIGIN validator -----------------------------------------------

@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "https://thetrapperxd.github.io", "https://example.com:8443"],
)
def test_cors_origin_valid(origin):
    assert settings(origin).CORS_ORIGIN == origin


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://example.com/",
        "https://example.com/project-management",
        "https://example.com?x=1",
        "https://example.com#frag",
        "https://example.com?",
        "ftp://example.com",
        "example.com",
        "https://",
        "https://example.com:notaport",
    ],
)
def test_cors_origin_invalid(origin):
    with pytest.raises(ValidationError) as exc:
        settings(origin)
    assert "CORS_ORIGIN must be an origin" in str(exc.value)


def test_app_refuses_to_start_with_bad_cors_origin(monkeypatch):
    monkeypatch.setenv("CORS_ORIGIN", "https://example.com/")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            create_app()
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()


# --- (B) threshold update ----------------------------------------------------

def test_threshold_updates_on_existing_sku_but_name_does_not(session, user):
    med_id = add(session, user, "A", 10, 5, name="Original").medicine_id
    assert session.get(Medicine, med_id).reorder_threshold == 0
    add(session, user, "B", 10, 5, name="Different", threshold=20)
    med = session.get(Medicine, med_id)
    assert med.reorder_threshold == 20 and med.name == "Original"
    assert [m["sku"] for m in dashboard_buckets(session, TODAY)["low_stock"]] == ["S1"]
    add(session, user, "C", 10, 5, threshold=0)
    assert session.get(Medicine, med_id).reorder_threshold == 0


def test_null_threshold_leaves_stored_value(session, user):
    med_id = add(session, user, "A", 10, 5, threshold=7).medicine_id
    add(session, user, "B", 10, 5)
    assert session.get(Medicine, med_id).reorder_threshold == 7


def test_threshold_not_changed_when_stock_in_conflicts(session, user):
    from app.services.inventory import ConflictError

    med_id = add(session, user, "A", 10, 5, threshold=7).medicine_id
    with pytest.raises(ConflictError):
        add(session, user, "A", 11, 5, threshold=99)
    session.expire_all()
    assert session.get(Medicine, med_id).reorder_threshold == 7


# --- (C) case-insensitive SKU and lot number ---------------------------------

def test_sku_and_lot_case_insensitive_in_service(session, user):
    first = add(session, user, "a1", 10, 5, sku="amox-500")
    second = add(session, user, "A1", 10, 3, sku="AMOX-500")
    assert first.id == second.id and second.quantity == 8
    assert [m.sku for m in session.scalars(select(Medicine))] == ["AMOX-500"]
    assert [l.lot_number for l in session.scalars(select(Lot))] == ["A1"]


def test_existing_mixed_case_rows_are_matched_not_rewritten(session, user):
    med = Medicine(name="Old", sku="amox-500", reorder_threshold=0)
    session.add(med)
    session.flush()
    session.add(Lot(medicine_id=med.id, lot_number="a1", expiry_date=TODAY + timedelta(days=10), quantity=4))
    session.commit()
    lot = add(session, user, "A1", 10, 2, sku="AMOX-500")
    assert lot.medicine_id == med.id and lot.quantity == 6
    assert lot.lot_number == "a1" and session.get(Medicine, med.id).sku == "amox-500"
    assert len(list_medicines(session, TODAY)) == 1


def test_sku_and_lot_normalised_over_http(logged_in):
    future = (date.today() + timedelta(days=20)).isoformat()
    body = {"name": "Amox", "sku": " amox-500 ", "lot_number": " a1 ", "expiry_date": future, "quantity": 5}
    r = logged_in.post("/api/stock/in", json=body, headers=CSRF)
    assert r.status_code == 200, r.text
    assert r.json()["lot_number"] == "A1"
    r = logged_in.post("/api/stock/in", json={**body, "sku": "AMOX-500", "lot_number": "A1", "quantity": 3}, headers=CSRF)
    assert r.status_code == 200 and r.json()["lot_quantity"] == 8
    meds = logged_in.get("/api/medicines").json()
    assert [(m["sku"], m["total_quantity"]) for m in meds] == [("AMOX-500", 8)]


# --- (D) later bucket --------------------------------------------------------

def test_later_bucket_content_order_and_boundary(session, user):
    add(session, user, "FAR", 400, 1)
    add(session, user, "D91", 91, 2)
    add(session, user, "D90", 90, 3)
    add(session, user, "RED", 5, 4)
    add(session, user, "MID", 200, 5, sku="S2")
    session.add(Lot(medicine_id=1, lot_number="EMPTY", expiry_date=TODAY + timedelta(days=300), quantity=0))
    session.commit()
    result = dashboard_buckets(session, TODAY)
    assert set(result) == {"red", "yellow", "green", "later", "low_stock"}
    assert [(i["lot_number"], i["days_left"]) for i in result["later"]] == [("D91", 91), ("MID", 200), ("FAR", 400)]
    assert set(result["later"][0]) == set(result["red"][0])
    assert [i["lot_number"] for i in result["red"]] == ["RED"]
    assert result["yellow"] == []
    assert [i["lot_number"] for i in result["green"]] == ["D90"]


def test_dashboard_over_http_has_later(logged_in):
    future = (date.today() + timedelta(days=120)).isoformat()
    body = {"name": "Para", "sku": "PCM", "lot_number": "L1", "expiry_date": future, "quantity": 10}
    assert logged_in.post("/api/stock/in", json=body, headers=CSRF).status_code == 200
    dash = logged_in.get("/api/dashboard").json()
    assert set(dash) == {"red", "yellow", "green", "later", "low_stock"}
    assert [i["lot_number"] for i in dash["later"]] == ["L1"]
    lot_id = dash["later"][0]["lot_id"]
    r = logged_in.post("/api/stock/writeoff", json={"lot_id": lot_id, "quantity": 4, "reason": "recalled"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["remaining"] == 6


# --- (G) login password whitespace -------------------------------------------

def test_password_with_surrounding_spaces_logs_in(client, session):
    from app.create_user import create_user

    pw = "  spaced password 1 "
    create_user(session, "bob", pw)
    ok = client.post("/api/auth/login", json={"username": "bob", "password": pw}, headers=CSRF)
    assert ok.status_code == 200
    stripped = client.post("/api/auth/login", json={"username": "bob", "password": pw.strip()}, headers=CSRF)
    assert stripped.status_code == 401


def test_username_is_still_stripped_and_extra_fields_rejected(client, user):
    ok = client.post("/api/auth/login", json={"username": "  alice ", "password": TEST_PW}, headers=CSRF)
    assert ok.status_code == 200
    assert client.post("/api/auth/login", json={"username": "   ", "password": TEST_PW}, headers=CSRF).status_code == 422
    extra = client.post("/api/auth/login", json={"username": "alice", "password": TEST_PW, "x": 1}, headers=CSRF)
    assert extra.status_code == 422
