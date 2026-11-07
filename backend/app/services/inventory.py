"""Pure inventory logic. No HTTP; `today` is always injected by the caller."""
import re
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Lot, Medicine, StockMovement

MAX_QTY = 1_000_000
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class InventoryError(Exception):
    """Base for domain errors; routers map subclasses to HTTP statuses."""


class ConflictError(InventoryError):
    pass


class InsufficientStockError(InventoryError):
    def __init__(self, available: int) -> None:
        super().__init__(f"Insufficient stock: {available} available")
        self.available = available


class NotFoundError(InventoryError):
    pass


class DomainValidationError(InventoryError):
    pass


def _check_quantity(quantity: int) -> None:
    if not 1 <= quantity <= MAX_QTY:
        raise DomainValidationError(f"Quantity must be between 1 and {MAX_QTY}")


def stock_in(
    session: Session,
    user_id: int,
    name: str,
    sku: str,
    lot_number: str,
    expiry_date: date,
    quantity: int,
    reorder_threshold: int | None,
    today: date,
) -> Lot:
    _check_quantity(quantity)
    if expiry_date < today:
        raise DomainValidationError("Expiry date is in the past")
    if reorder_threshold is not None and reorder_threshold < 0:
        raise DomainValidationError("Reorder threshold must be >= 0")

    medicine = session.scalars(select(Medicine).where(Medicine.sku == sku)).first()
    if medicine is None:
        medicine = Medicine(name=name, sku=sku, reorder_threshold=reorder_threshold or 0)
        session.add(medicine)
        session.flush()

    lot = session.scalars(
        select(Lot)
        .where(Lot.medicine_id == medicine.id, Lot.lot_number == lot_number)
        .with_for_update()
    ).first()
    if lot is None:
        lot = Lot(medicine_id=medicine.id, lot_number=lot_number, expiry_date=expiry_date, quantity=quantity)
        session.add(lot)
    elif lot.expiry_date != expiry_date:
        session.rollback()
        raise ConflictError("Lot number already exists with a different expiry date")
    else:
        lot.quantity += quantity
    session.flush()

    session.add(StockMovement(lot_id=lot.id, type="in", quantity=quantity, occurred_on=today, user_id=user_id))
    session.commit()
    return lot


def dispense_fifo(
    session: Session, user_id: int, medicine_id: int, quantity: int, today: date
) -> list[dict[str, Any]]:
    _check_quantity(quantity)
    if session.get(Medicine, medicine_id) is None:
        raise NotFoundError("Medicine not found")

    lots = session.scalars(
        select(Lot)
        .where(Lot.medicine_id == medicine_id, Lot.quantity > 0, Lot.expiry_date >= today)
        .order_by(Lot.expiry_date.asc(), Lot.id.asc())
        .with_for_update()
    ).all()

    available = sum(lot.quantity for lot in lots)
    if available < quantity:
        session.rollback()
        raise InsufficientStockError(available)

    remaining = quantity
    allocations: list[dict[str, Any]] = []
    for lot in lots:
        if remaining == 0:
            break
        taken = min(lot.quantity, remaining)
        lot.quantity -= taken
        remaining -= taken
        session.add(StockMovement(lot_id=lot.id, type="out", quantity=taken, occurred_on=today, user_id=user_id))
        allocations.append(
            {"lot_id": lot.id, "lot_number": lot.lot_number, "expiry_date": lot.expiry_date, "taken": taken}
        )
    session.commit()
    return allocations


def sweep_expired(session: Session, today: date, system_user_id: int | None = None) -> int:
    lots = session.scalars(
        select(Lot).where(Lot.expiry_date < today, Lot.quantity > 0).order_by(Lot.id).with_for_update()
    ).all()
    for lot in lots:
        session.add(
            StockMovement(
                lot_id=lot.id, type="waste", quantity=lot.quantity, occurred_on=lot.expiry_date, user_id=system_user_id
            )
        )
        lot.quantity = 0
    session.commit()
    return len(lots)


def dashboard_buckets(session: Session, today: date) -> dict[str, list[dict[str, Any]]]:
    rows = session.execute(
        select(Lot, Medicine)
        .join(Medicine, Medicine.id == Lot.medicine_id)
        .where(Lot.quantity > 0, Lot.expiry_date >= today)
    ).all()

    items: list[dict[str, Any]] = []
    for lot, med in rows:
        items.append(
            {
                "medicine_id": med.id,
                "name": med.name,
                "sku": med.sku,
                "lot_number": lot.lot_number,
                "expiry_date": lot.expiry_date,
                "quantity": lot.quantity,
                "days_left": (lot.expiry_date - today).days,
                "_lot_id": lot.id,
            }
        )
    items.sort(key=lambda i: (i["days_left"], i["_lot_id"]))

    result: dict[str, list[dict[str, Any]]] = {"red": [], "yellow": [], "green": []}
    for item in items:
        item.pop("_lot_id")
        d = item["days_left"]
        if d <= 30:
            result["red"].append(item)
        elif d <= 60:
            result["yellow"].append(item)
        elif d <= 90:
            result["green"].append(item)

    result["low_stock"] = [m for m in list_medicines(session, today) if m["reorder_threshold"] > 0 and m["total_quantity"] < m["reorder_threshold"]]
    return result


def list_medicines(session: Session, today: date) -> list[dict[str, Any]]:
    total = func.coalesce(func.sum(Lot.quantity), 0)
    rows = session.execute(
        select(Medicine, total)
        .outerjoin(Lot, (Lot.medicine_id == Medicine.id) & (Lot.expiry_date >= today))
        .group_by(Medicine.id)
        .order_by(Medicine.name, Medicine.id)
    ).all()
    return [
        {
            "id": m.id,
            "name": m.name,
            "sku": m.sku,
            "total_quantity": int(t),
            "reorder_threshold": m.reorder_threshold,
        }
        for m, t in rows
    ]


def waste_report(session: Session, month: str) -> dict[str, Any]:
    match = _MONTH_RE.match(month)
    if not match:
        raise DomainValidationError("Month must be YYYY-MM")
    year, mon = int(match.group(1)), int(match.group(2))
    if not 1900 <= year <= 9998:
        raise DomainValidationError("Month out of range")
    start = date(year, mon, 1)
    end = date(year + (mon == 12), mon % 12 + 1, 1)

    rows = session.execute(
        select(StockMovement, Lot, Medicine)
        .join(Lot, Lot.id == StockMovement.lot_id)
        .join(Medicine, Medicine.id == Lot.medicine_id)
        .where(StockMovement.type == "waste", StockMovement.occurred_on >= start, StockMovement.occurred_on < end)
        .order_by(Medicine.name, Medicine.id, Lot.expiry_date, Lot.id)
    ).all()

    lots: list[dict[str, Any]] = []
    per_med: dict[int, dict[str, Any]] = {}
    for mv, lot, med in rows:
        lots.append(
            {
                "medicine_id": med.id,
                "name": med.name,
                "sku": med.sku,
                "lot_number": lot.lot_number,
                "expiry_date": lot.expiry_date,
                "quantity": mv.quantity,
            }
        )
        entry = per_med.setdefault(
            med.id,
            {"medicine_id": med.id, "name": med.name, "sku": med.sku, "lot_ids": set(), "total_wasted": 0},
        )
        entry["lot_ids"].add(lot.id)
        entry["total_wasted"] += mv.quantity

    medicines = [
        {
            "medicine_id": e["medicine_id"],
            "name": e["name"],
            "sku": e["sku"],
            "lots_count": len(e["lot_ids"]),
            "total_wasted": e["total_wasted"],
        }
        for e in per_med.values()
    ]
    return {
        "month": month,
        "medicines": medicines,
        "lots": lots,
        "grand_total": sum(m["total_wasted"] for m in medicines),
    }
