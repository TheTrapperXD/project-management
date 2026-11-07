from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import Lot, StockMovement
from app.services.inventory import (
    ConflictError,
    DomainValidationError,
    InsufficientStockError,
    dashboard_buckets,
    dispense_fifo,
    stock_in,
    sweep_expired,
    waste_report,
)

TODAY = date(2026, 6, 15)


def add(session, user, lot, days, qty, sku="S1", name="Med", threshold=None):
    return stock_in(session, user.id, name, sku, lot, TODAY + timedelta(days=days), qty, threshold, TODAY)


def qty_by_lot(session):
    return {l.lot_number: l.quantity for l in session.scalars(select(Lot))}


def test_earliest_expiry_first_regardless_of_insertion_order(session, user):
    med = add(session, user, "LATE", 100, 10).medicine_id
    add(session, user, "EARLY", 10, 10)
    add(session, user, "MID", 50, 10)
    alloc = dispense_fifo(session, user.id, med, 5, TODAY)
    assert [a["lot_number"] for a in alloc] == ["EARLY"]
    assert qty_by_lot(session) == {"LATE": 10, "EARLY": 5, "MID": 10}


def test_deduction_spans_three_lots(session, user):
    med = add(session, user, "A", 10, 5).medicine_id
    add(session, user, "B", 20, 5)
    add(session, user, "C", 30, 5)
    alloc = dispense_fifo(session, user.id, med, 12, TODAY)
    assert [(a["lot_number"], a["taken"]) for a in alloc] == [("A", 5), ("B", 5), ("C", 2)]
    assert qty_by_lot(session) == {"A": 0, "B": 0, "C": 3}
    outs = session.scalars(select(StockMovement).where(StockMovement.type == "out")).all()
    assert len(outs) == 3


def test_exact_zero_deduction(session, user):
    med = add(session, user, "A", 10, 4).medicine_id
    add(session, user, "B", 20, 6)
    dispense_fifo(session, user.id, med, 10, TODAY)
    assert qty_by_lot(session) == {"A": 0, "B": 0}


def test_insufficient_stock_changes_nothing(session, user):
    med = add(session, user, "A", 10, 4).medicine_id
    add(session, user, "B", 20, 6)
    before_moves = len(session.scalars(select(StockMovement)).all())
    with pytest.raises(InsufficientStockError) as exc:
        dispense_fifo(session, user.id, med, 11, TODAY)
    assert exc.value.available == 10
    assert qty_by_lot(session) == {"A": 4, "B": 6}
    assert len(session.scalars(select(StockMovement)).all()) == before_moves


def test_expired_lots_skipped_by_dispense(session, user):
    med = add(session, user, "FRESH", 10, 5).medicine_id
    # Expired lot inserted directly (stock_in rejects past expiry).
    session.add(Lot(medicine_id=med, lot_number="OLD", expiry_date=TODAY - timedelta(days=1), quantity=100))
    session.commit()
    with pytest.raises(InsufficientStockError) as exc:
        dispense_fifo(session, user.id, med, 6, TODAY)
    assert exc.value.available == 5
    dispense_fifo(session, user.id, med, 5, TODAY)
    assert qty_by_lot(session)["OLD"] == 100


def test_lot_valid_through_expiry_date(session, user):
    med = add(session, user, "A", 0, 5).medicine_id
    assert dispense_fifo(session, user.id, med, 5, TODAY)[0]["taken"] == 5


def test_same_lot_restock_merges(session, user):
    add(session, user, "A", 10, 5)
    add(session, user, "A", 10, 7)
    lots = session.scalars(select(Lot)).all()
    assert len(lots) == 1 and lots[0].quantity == 12


def test_same_lot_different_expiry_conflicts(session, user):
    add(session, user, "A", 10, 5)
    with pytest.raises(ConflictError):
        add(session, user, "A", 11, 5)
    assert qty_by_lot(session) == {"A": 5}


def test_existing_medicine_name_not_overwritten(session, user):
    lot = add(session, user, "A", 10, 5, name="Original")
    add(session, user, "B", 10, 5, name="Different")
    from app.models import Medicine
    assert session.get(Medicine, lot.medicine_id).name == "Original"


def test_stock_in_rejects_past_expiry_and_bad_quantity(session, user):
    with pytest.raises(DomainValidationError):
        add(session, user, "A", -1, 5)
    with pytest.raises(DomainValidationError):
        add(session, user, "A", 10, 0)
    with pytest.raises(DomainValidationError):
        add(session, user, "A", 10, 1_000_001)
    assert session.scalars(select(Lot)).all() == []


@pytest.mark.parametrize(
    "days,bucket",
    [(0, "red"), (30, "red"), (31, "yellow"), (60, "yellow"), (61, "green"), (90, "green"), (91, None)],
)
def test_bucket_boundaries(session, user, days, bucket):
    add(session, user, f"L{days}", days, 3)
    result = dashboard_buckets(session, TODAY)
    for name in ("red", "yellow", "green"):
        expected = 1 if name == bucket else 0
        assert len(result[name]) == expected, (days, name)
    if bucket:
        item = result[bucket][0]
        assert item["days_left"] == days and item["quantity"] == 3


def test_bucket_sorted_and_excludes_empty_and_expired(session, user):
    add(session, user, "B", 20, 1, sku="S2")
    add(session, user, "A", 5, 1)
    med = add(session, user, "Z", 7, 2, sku="S3").medicine_id
    dispense_fifo(session, user.id, med, 2, TODAY)
    session.add(Lot(medicine_id=med, lot_number="EXP", expiry_date=TODAY - timedelta(days=1), quantity=9))
    session.commit()
    red = dashboard_buckets(session, TODAY)["red"]
    assert [i["lot_number"] for i in red] == ["A", "B"]


def test_low_stock(session, user):
    add(session, user, "A", 10, 4, sku="LOW", threshold=10)
    add(session, user, "B", 10, 20, sku="OK", threshold=10)
    add(session, user, "C", 10, 1, sku="NOTHRESH")
    session.add(Lot(medicine_id=1, lot_number="EXP", expiry_date=TODAY - timedelta(days=1), quantity=50))
    session.commit()
    low = dashboard_buckets(session, TODAY)["low_stock"]
    assert [m["sku"] for m in low] == ["LOW"]
    assert low[0]["total_quantity"] == 4


def test_sweep_expired_zeroes_writes_waste_and_is_idempotent(session, user):
    med = add(session, user, "FRESH", 10, 5).medicine_id
    exp1 = TODAY - timedelta(days=3)
    exp2 = TODAY - timedelta(days=1)
    session.add_all([
        Lot(medicine_id=med, lot_number="O1", expiry_date=exp1, quantity=7),
        Lot(medicine_id=med, lot_number="O2", expiry_date=exp2, quantity=2),
        Lot(medicine_id=med, lot_number="O3", expiry_date=exp2, quantity=0),
    ])
    session.commit()
    assert sweep_expired(session, TODAY) == 2
    q = qty_by_lot(session)
    assert q["O1"] == 0 and q["O2"] == 0 and q["FRESH"] == 5
    waste = session.scalars(select(StockMovement).where(StockMovement.type == "waste")).all()
    assert sorted((w.quantity, w.occurred_on) for w in waste) == [(2, exp2), (7, exp1)]
    assert sweep_expired(session, TODAY) == 0
    assert len(session.scalars(select(StockMovement).where(StockMovement.type == "waste")).all()) == 2


def test_expiry_day_itself_is_not_swept(session, user):
    add(session, user, "A", 0, 5)
    assert sweep_expired(session, TODAY) == 0
    assert sweep_expired(session, TODAY + timedelta(days=1)) == 1


def test_waste_report_monthly_grouping(session, user):
    med = add(session, user, "FRESH", 100, 1).medicine_id
    other = add(session, user, "X", 100, 1, sku="S2", name="Other").medicine_id
    session.add_all([
        Lot(medicine_id=med, lot_number="M1", expiry_date=date(2026, 5, 10), quantity=4),
        Lot(medicine_id=med, lot_number="M2", expiry_date=date(2026, 5, 31), quantity=6),
        Lot(medicine_id=other, lot_number="M3", expiry_date=date(2026, 5, 1), quantity=1),
        Lot(medicine_id=med, lot_number="APR", expiry_date=date(2026, 4, 30), quantity=50),
        Lot(medicine_id=med, lot_number="JUN", expiry_date=date(2026, 6, 1), quantity=70),
    ])
    session.commit()
    sweep_expired(session, TODAY)
    report = waste_report(session, "2026-05")
    by_sku = {m["sku"]: m for m in report["medicines"]}
    assert by_sku["S1"]["total_wasted"] == 10 and by_sku["S1"]["lots_count"] == 2
    assert by_sku["S2"]["total_wasted"] == 1 and by_sku["S2"]["lots_count"] == 1
    assert report["grand_total"] == 11
    assert sorted(l["lot_number"] for l in report["lots"]) == ["M1", "M2", "M3"]
    assert waste_report(session, "2026-07")["grand_total"] == 0
    assert waste_report(session, "2026-04")["grand_total"] == 50


def test_waste_report_rejects_bad_month(session):
    for bad in ("2026-13", "2026-5", "abc", "0000-01"):
        with pytest.raises(DomainValidationError):
            waste_report(session, bad)
