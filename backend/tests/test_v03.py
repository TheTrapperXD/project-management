from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import config, migrate
from app.clock import today_local
from app.config import Settings
from app.db import Base
from app.main import create_app
from app.models import Lot, Medicine, StockMovement
from app.services.inventory import (
    ConflictError,
    DomainValidationError,
    ExceedsRemainingError,
    NotFoundError,
    dashboard_buckets,
    dispense_fifo,
    stock_in,
    sweep_expired,
    waste_report,
    writeoff_lot,
)
from tests.conftest import CSRF

TODAY = date(2026, 6, 15)


def add(session, user, lot, days, qty, sku="S1", name="Med", cost=None):
    return stock_in(session, user.id, name, sku, lot, TODAY + timedelta(days=days), qty, None, TODAY, unit_cost=cost)


def moves(session, type_):
    return session.scalars(select(StockMovement).where(StockMovement.type == type_).order_by(StockMovement.id)).all()


def expired_lot(session, med_id, number, expiry, qty, cost=None):
    lot = Lot(medicine_id=med_id, lot_number=number, expiry_date=expiry, quantity=qty, unit_cost=cost)
    session.add(lot)
    session.commit()
    return lot


@pytest.fixture()
def settings_env(monkeypatch):
    config.get_settings.cache_clear()
    yield monkeypatch
    monkeypatch.undo()
    config.get_settings.cache_clear()


# --- (A) business timezone ---------------------------------------------------

def test_today_local_bangkok_rolls_over_at_1700_utc(settings_env):
    assert today_local(datetime(2026, 11, 12, 18, 30, tzinfo=timezone.utc)) == date(2026, 11, 13)
    assert today_local(datetime(2026, 11, 12, 16, 59, tzinfo=timezone.utc)) == date(2026, 11, 12)


def test_today_local_timezone_override(settings_env):
    settings_env.setenv("BUSINESS_TIMEZONE", "UTC")
    config.get_settings.cache_clear()
    assert today_local(datetime(2026, 11, 12, 18, 30, tzinfo=timezone.utc)) == date(2026, 11, 12)
    settings_env.setenv("BUSINESS_TIMEZONE", "America/New_York")
    config.get_settings.cache_clear()
    assert today_local(datetime(2026, 11, 13, 3, 0, tzinfo=timezone.utc)) == date(2026, 11, 12)


def test_today_local_rejects_naive_datetime():
    with pytest.raises(ValueError):
        today_local(datetime(2026, 11, 12, 18, 30))


def test_invalid_timezone_refuses_to_start(settings_env):
    base = {"DATABASE_URL": "sqlite://", "SECRET_KEY": "x" * 48, "CORS_ORIGIN": "http://x"}
    for bad in ("Mars/Olympus_Mons", "", "../etc/passwd"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, BUSINESS_TIMEZONE=bad, **base)
    settings_env.setenv("BUSINESS_TIMEZONE", "Not/AZone")
    config.get_settings.cache_clear()
    with pytest.raises(ValidationError):
        create_app()


# --- (B) unit cost on stock in -----------------------------------------------

def test_unit_cost_stored_on_new_lot_and_in_movement(session, user):
    lot = add(session, user, "A", 10, 5, cost=Decimal("12.50"))
    assert session.get(Lot, lot.id).unit_cost == Decimal("12.50")
    assert moves(session, "in")[0].unit_cost == Decimal("12.50")


def test_unit_cost_null_then_set_on_restock(session, user):
    lot = add(session, user, "A", 10, 5)
    assert lot.unit_cost is None and moves(session, "in")[0].unit_cost is None
    add(session, user, "A", 10, 3, cost=Decimal("4.00"))
    assert session.get(Lot, lot.id).unit_cost == Decimal("4.00")
    assert moves(session, "in")[1].unit_cost == Decimal("4.00")
    # A later restock without a cost keeps the existing one and snapshots it.
    add(session, user, "A", 10, 2)
    assert session.get(Lot, lot.id).unit_cost == Decimal("4.00") and session.get(Lot, lot.id).quantity == 10
    assert moves(session, "in")[2].unit_cost == Decimal("4.00")


def test_unit_cost_conflict_changes_nothing(session, user):
    lot = add(session, user, "A", 10, 5, cost=Decimal("4.00"))
    with pytest.raises(ConflictError):
        add(session, user, "A", 10, 3, cost=Decimal("4.01"))
    assert session.get(Lot, lot.id).quantity == 5 and session.get(Lot, lot.id).unit_cost == Decimal("4.00")
    assert len(moves(session, "in")) == 1
    add(session, user, "A", 10, 3, cost=Decimal("4.0"))  # same value, different scale: no conflict
    assert session.get(Lot, lot.id).quantity == 8


@pytest.mark.parametrize("bad", [Decimal("-0.01"), Decimal("1.005"), Decimal("1000000.01"), Decimal("NaN")])
def test_unit_cost_invalid_rejected_by_service(session, user, bad):
    with pytest.raises(DomainValidationError):
        add(session, user, "A", 10, 5, cost=bad)
    assert session.scalars(select(Lot)).all() == []


def test_unit_cost_over_http(logged_in):
    future = (date.today() + timedelta(days=40)).isoformat()
    body = {"name": "Amox", "sku": "AMX", "lot_number": "L1", "expiry_date": future, "quantity": 3}
    for bad in ("-1", "1.005", "1000000.01", "abc", -1):
        r = logged_in.post("/api/stock/in", json={**body, "unit_cost": bad}, headers=CSRF)
        assert r.status_code == 422, bad
    assert logged_in.post("/api/stock/in", json={**body, "unit_cost": "1000000"}, headers=CSRF).status_code == 200
    assert logged_in.post("/api/stock/in", json={**body, "unit_cost": "999"}, headers=CSRF).status_code == 409
    assert logged_in.post("/api/stock/in", json={**body, "lot_number": "L2", "unit_cost": None}, headers=CSRF).status_code == 200


# --- (B) sweep -------------------------------------------------------------------

def test_sweep_movements_carry_reason_and_cost_snapshot(session, user):
    med = add(session, user, "FRESH", 10, 5).medicine_id
    expired_lot(session, med, "O1", TODAY - timedelta(days=2), 7, Decimal("3.25"))
    expired_lot(session, med, "O2", TODAY - timedelta(days=1), 2)
    assert sweep_expired(session, TODAY) == 2
    waste = moves(session, "waste")
    assert [(w.quantity, w.reason, w.unit_cost) for w in waste] == [(7, "expired", Decimal("3.25")), (2, "expired", None)]
    assert sweep_expired(session, TODAY) == 0
    assert len(moves(session, "waste")) == 2


# --- (C) write-off -------------------------------------------------------------

def test_writeoff_partial(session, user):
    lot = add(session, user, "A", 10, 10, name="Para", cost=Decimal("12.50"))
    res = writeoff_lot(session, user.id, lot.id, 3, "damaged", TODAY)
    assert res == {"lot_id": lot.id, "medicine_name": "Para", "lot_number": "A", "written_off": 3, "remaining": 7, "value": "37.50"}
    assert session.get(Lot, lot.id).quantity == 7
    (mv,) = moves(session, "waste")
    assert (mv.quantity, mv.reason, mv.unit_cost, mv.occurred_on, mv.user_id) == (3, "damaged", Decimal("12.50"), TODAY, user.id)


def test_writeoff_default_all_and_null_value(session, user):
    lot = add(session, user, "A", 10, 6)
    res = writeoff_lot(session, user.id, lot.id, None, "recalled", TODAY)
    assert res["written_off"] == 6 and res["remaining"] == 0 and res["value"] is None
    # Nothing left: a further default write-off is a conflict, not a zero-quantity movement.
    with pytest.raises(ExceedsRemainingError) as exc:
        writeoff_lot(session, user.id, lot.id, None, "recalled", TODAY)
    assert exc.value.remaining == 0
    assert len(moves(session, "waste")) == 1


def test_writeoff_more_than_remaining_conflicts(session, user):
    lot = add(session, user, "A", 10, 4)
    with pytest.raises(ExceedsRemainingError) as exc:
        writeoff_lot(session, user.id, lot.id, 5, "other", TODAY)
    assert exc.value.remaining == 4
    assert session.get(Lot, lot.id).quantity == 4 and moves(session, "waste") == []


@pytest.mark.parametrize("qty", [0, -1])
def test_writeoff_non_positive_quantity_invalid(session, user, qty):
    lot = add(session, user, "A", 10, 4)
    with pytest.raises(DomainValidationError):
        writeoff_lot(session, user.id, lot.id, qty, "other", TODAY)
    assert session.get(Lot, lot.id).quantity == 4


def test_writeoff_unknown_lot_and_bad_reason(session, user):
    with pytest.raises(NotFoundError):
        writeoff_lot(session, user.id, 999, 1, "other", TODAY)
    lot = add(session, user, "A", 10, 4)
    with pytest.raises(DomainValidationError):
        writeoff_lot(session, user.id, lot.id, 1, "expired", TODAY)


def test_writeoff_expired_lot(session, user):
    med = add(session, user, "FRESH", 10, 1).medicine_id
    old = expired_lot(session, med, "OLD", TODAY - timedelta(days=5), 9, Decimal("2.00"))
    res = writeoff_lot(session, user.id, old.id, 4, "expiring", TODAY)
    assert res["remaining"] == 5 and res["value"] == "8.00"
    assert moves(session, "waste")[0].occurred_on == TODAY


def test_dispense_after_writeoff_respects_reduced_quantity(session, user):
    lot = add(session, user, "A", 10, 10)
    med = lot.medicine_id
    add(session, user, "B", 20, 5)
    writeoff_lot(session, user.id, lot.id, 8, "damaged", TODAY)
    alloc = dispense_fifo(session, user.id, med, 4, TODAY)
    assert [(a["lot_number"], a["taken"]) for a in alloc] == [("A", 2), ("B", 2)]


def test_dashboard_items_include_lot_id(session, user):
    lot = add(session, user, "A", 10, 3)
    item = dashboard_buckets(session, TODAY)["red"][0]
    assert item["lot_id"] == lot.id
    assert set(item) == {"medicine_id", "name", "sku", "lot_number", "expiry_date", "quantity", "days_left", "lot_id"}


def test_writeoff_over_http(logged_in):
    future = (date.today() + timedelta(days=10)).isoformat()
    body = {"name": "Para", "sku": "PCM", "lot_number": "L1", "expiry_date": future, "quantity": 10, "unit_cost": "12.50"}
    assert logged_in.post("/api/stock/in", json=body, headers=CSRF).status_code == 200
    lot_id = logged_in.get("/api/dashboard").json()["red"][0]["lot_id"]
    r = logged_in.post("/api/stock/writeoff", json={"lot_id": lot_id, "quantity": 3, "reason": "damaged"}, headers=CSRF)
    assert r.status_code == 200, r.text
    assert r.json() == {"lot_id": lot_id, "medicine_name": "Para", "lot_number": "L1", "written_off": 3, "remaining": 7, "value": "37.50"}
    r = logged_in.post("/api/stock/writeoff", json={"lot_id": lot_id, "quantity": 8, "reason": "damaged"}, headers=CSRF)
    assert r.status_code == 409 and r.json()["remaining"] == 7
    for bad in ({"quantity": 0}, {"quantity": -1}, {"reason": "expired"}, {"reason": None}, {"extra": 1}):
        payload = {"lot_id": lot_id, "reason": "other", **bad}
        assert logged_in.post("/api/stock/writeoff", json=payload, headers=CSRF).status_code == 422, bad
    assert logged_in.post("/api/stock/writeoff", json={"lot_id": 999, "reason": "other"}, headers=CSRF).status_code == 404
    r = logged_in.post("/api/stock/writeoff", json={"lot_id": lot_id, "reason": "recalled"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["written_off"] == 7 and r.json()["remaining"] == 0


def test_writeoff_route_auth(client, logged_in):
    body = {"lot_id": 1, "reason": "other"}
    assert logged_in.post("/api/stock/writeoff", json=body).status_code == 403
    token = logged_in.headers.pop("Authorization")
    r = client.post("/api/stock/writeoff", json=body, headers=CSRF)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    r = client.post("/api/stock/writeoff", json=body, headers={**CSRF, "Authorization": token})
    assert r.status_code == 404


# --- (B) waste report ------------------------------------------------------------

def test_waste_report_values_by_reason_and_totals(session, user):
    a = add(session, user, "A", 10, 10, sku="S1", name="Alpha", cost=Decimal("12.50"))
    b = add(session, user, "B", 10, 10, sku="S2", name="Beta")
    c = add(session, user, "C", 10, 10, sku="S1", name="Alpha", cost=Decimal("0.10"))
    writeoff_lot(session, user.id, a.id, 3, "damaged", TODAY)
    writeoff_lot(session, user.id, b.id, 4, "damaged", TODAY)
    writeoff_lot(session, user.id, c.id, 3, "recalled", TODAY)
    expired_lot(session, a.medicine_id, "OLD", date(2026, 6, 1), 2, Decimal("1.99"))
    sweep_expired(session, TODAY)

    r = waste_report(session, "2026-06")
    by_sku = {m["sku"]: m for m in r["medicines"]}
    # 3 x 12.50 + 3 x 0.10 + 2 x 1.99 = 37.50 + 0.30 + 3.98 (exact, no binary rounding)
    assert by_sku["S1"] == {"medicine_id": a.medicine_id, "name": "Alpha", "sku": "S1", "lots_count": 3,
                            "total_wasted": 8, "wasted_value": "41.78", "unvalued_units": 0}
    assert by_sku["S2"]["total_wasted"] == 4 and by_sku["S2"]["wasted_value"] is None and by_sku["S2"]["unvalued_units"] == 4
    assert r["by_reason"] == [
        {"reason": "expired", "units": 2, "value": "3.98", "unvalued_units": 0},
        {"reason": "damaged", "units": 7, "value": "37.50", "unvalued_units": 4},
        {"reason": "recalled", "units": 3, "value": "0.30", "unvalued_units": 0},
    ]
    assert (r["grand_total"], r["grand_total_value"], r["grand_total_unvalued_units"]) == (12, "41.78", 4)
    rows = {(l["lot_number"], l["reason"]): l for l in r["lots"]}
    assert rows[("A", "damaged")]["unit_cost"] == "12.50" and rows[("A", "damaged")]["value"] == "37.50"
    assert rows[("B", "damaged")]["unit_cost"] is None and rows[("B", "damaged")]["value"] is None
    assert rows[("OLD", "expired")]["unit_cost"] == "1.99"


def test_waste_report_month_grouping_and_empty_month(session, user):
    med = add(session, user, "FRESH", 100, 1, cost=Decimal("5.00")).medicine_id
    expired_lot(session, med, "MAY", date(2026, 5, 31), 3, Decimal("5.00"))
    expired_lot(session, med, "APR", date(2026, 4, 30), 2)
    sweep_expired(session, TODAY)
    may = waste_report(session, "2026-05")
    assert (may["grand_total"], may["grand_total_value"], may["grand_total_unvalued_units"]) == (3, "15.00", 0)
    apr = waste_report(session, "2026-04")
    assert (apr["grand_total"], apr["grand_total_value"], apr["grand_total_unvalued_units"]) == (2, None, 2)
    empty = waste_report(session, "2026-07")
    assert empty["medicines"] == [] and empty["by_reason"] == [] and empty["grand_total_value"] is None


def test_waste_report_over_http_returns_money_strings(logged_in):
    future = (date.today() + timedelta(days=10)).isoformat()
    body = {"name": "Para", "sku": "PCM", "lot_number": "L1", "expiry_date": future, "quantity": 5, "unit_cost": "12.5"}
    assert logged_in.post("/api/stock/in", json=body, headers=CSRF).status_code == 200
    lot_id = logged_in.get("/api/dashboard").json()["red"][0]["lot_id"]
    r = logged_in.post("/api/stock/writeoff", json={"lot_id": lot_id, "quantity": 3, "reason": "expiring"}, headers=CSRF)
    assert r.json()["value"] == "37.50"
    report = logged_in.get("/api/reports/waste").json()
    assert report["grand_total_value"] == "37.50" and report["medicines"][0]["wasted_value"] == "37.50"
    assert report["lots"][0]["unit_cost"] == "12.50"


# --- migration -------------------------------------------------------------------

def test_migration_columns_exist_in_metadata():
    for table, column in migrate.NEW_COLUMNS:
        assert column in Base.metadata.tables[table].columns, (table, column)
        assert any(f"{table} ADD COLUMN IF NOT EXISTS {column} " in s for s in migrate.STATEMENTS), (table, column)
    assert Base.metadata.tables["lots"].c.unit_cost.type.precision == 12
    assert Base.metadata.tables["lots"].c.unit_cost.type.scale == 2
    assert Base.metadata.tables["stock_movements"].c.reason.type.length == 20


def test_migration_skips_on_sqlite(engine, capsys):
    assert migrate.run(engine) == 0
    assert capsys.readouterr().out.strip() == "skipped (not postgresql)"
