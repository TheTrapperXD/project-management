"""Pure inventory logic. No HTTP; `today` is always injected by the caller."""
import re
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Lot, Medicine, StockMovement

MAX_QTY = 1_000_000
MAX_UNIT_COST = Decimal("1000000")
_CENTS = Decimal("0.01")
WRITEOFF_REASONS = ("expiring", "damaged", "recalled", "other")
# Display order for the waste report's by-reason summary.
_REASON_ORDER = ("expired",) + WRITEOFF_REASONS
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class InventoryError(Exception):
    """Base for domain errors; routers map subclasses to HTTP statuses."""


class ConflictError(InventoryError):
    pass


class InsufficientStockError(InventoryError):
    def __init__(self, available: int) -> None:
        super().__init__(f"Insufficient stock: {available} available")
        self.available = available


class ExceedsRemainingError(ConflictError):
    def __init__(self, remaining: int) -> None:
        super().__init__(f"Cannot write off more than remains: {remaining} remaining")
        self.remaining = remaining


class NotFoundError(InventoryError):
    pass


class DomainValidationError(InventoryError):
    pass


def _check_quantity(quantity: int) -> None:
    if not 1 <= quantity <= MAX_QTY:
        raise DomainValidationError(f"Quantity must be between 1 and {MAX_QTY}")


def _check_unit_cost(unit_cost: Decimal | None) -> None:
    if unit_cost is None:
        return
    if not isinstance(unit_cost, Decimal) or not unit_cost.is_finite():
        raise DomainValidationError("Unit cost must be a decimal number")
    if not 0 <= unit_cost <= MAX_UNIT_COST:
        raise DomainValidationError(f"Unit cost must be between 0 and {MAX_UNIT_COST}")
    if unit_cost != unit_cost.quantize(_CENTS):
        raise DomainValidationError("Unit cost must have at most 2 decimal places")


def money(value: Decimal | None) -> str | None:
    """API representation of money: a string with exactly 2 decimals, or None."""
    return None if value is None else format(value.quantize(_CENTS), "f")


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
    unit_cost: Decimal | None = None,
) -> Lot:
    _check_quantity(quantity)
    _check_unit_cost(unit_cost)
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
        lot = Lot(
            medicine_id=medicine.id, lot_number=lot_number, expiry_date=expiry_date, quantity=quantity,
            unit_cost=unit_cost,
        )
        session.add(lot)
    elif lot.expiry_date != expiry_date:
        session.rollback()
        raise ConflictError("Lot number already exists with a different expiry date")
    elif unit_cost is not None and lot.unit_cost is not None and lot.unit_cost != unit_cost:
        session.rollback()
        raise ConflictError("Lot already has a different unit cost")
    else:
        if unit_cost is not None and lot.unit_cost is None:
            lot.unit_cost = unit_cost
        lot.quantity += quantity
    session.flush()

    session.add(
        StockMovement(
            lot_id=lot.id, type="in", quantity=quantity, occurred_on=today, user_id=user_id,
            unit_cost=lot.unit_cost,
        )
    )
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
                lot_id=lot.id, type="waste", quantity=lot.quantity, occurred_on=lot.expiry_date, user_id=system_user_id,
                reason="expired", unit_cost=lot.unit_cost,
            )
        )
        lot.quantity = 0
    session.commit()
    return len(lots)


def writeoff_lot(
    session: Session, user_id: int, lot_id: int, quantity: int | None, reason: str, today: date
) -> dict[str, Any]:
    """Pull stock from one lot (any expiry) as waste. quantity None = everything remaining."""
    if reason not in WRITEOFF_REASONS:
        raise DomainValidationError(f"Reason must be one of: {', '.join(WRITEOFF_REASONS)}")
    if quantity is not None:
        _check_quantity(quantity)

    lot = session.scalars(select(Lot).where(Lot.id == lot_id).with_for_update()).first()
    if lot is None:
        session.rollback()
        raise NotFoundError("Lot not found")
    amount = lot.quantity if quantity is None else quantity
    if amount < 1 or amount > lot.quantity:
        remaining = lot.quantity
        session.rollback()
        raise ExceedsRemainingError(remaining)

    lot.quantity -= amount
    session.add(
        StockMovement(
            lot_id=lot.id, type="waste", quantity=amount, occurred_on=today, user_id=user_id,
            reason=reason, unit_cost=lot.unit_cost,
        )
    )
    medicine_name = session.get(Medicine, lot.medicine_id).name
    result = {
        "lot_id": lot.id,
        "medicine_name": medicine_name,
        "lot_number": lot.lot_number,
        "written_off": amount,
        "remaining": lot.quantity,
        "value": money(None if lot.unit_cost is None else lot.unit_cost * amount),
    }
    session.commit()
    return result


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
                "lot_id": lot.id,
            }
        )
    items.sort(key=lambda i: (i["days_left"], i["lot_id"]))

    result: dict[str, list[dict[str, Any]]] = {"red": [], "yellow": [], "green": []}
    for item in items:
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
        .order_by(Medicine.name, Medicine.id, Lot.expiry_date, Lot.id, StockMovement.id)
    ).all()

    def new_totals() -> dict[str, Any]:
        # value stays None until a movement with a known cost contributes to it.
        return {"units": 0, "value": None, "unvalued_units": 0}

    def add(totals: dict[str, Any], quantity: int, line_value: Decimal | None) -> None:
        totals["units"] += quantity
        if line_value is None:
            totals["unvalued_units"] += quantity
        else:
            totals["value"] = (totals["value"] or Decimal("0")) + line_value

    lots: list[dict[str, Any]] = []
    per_med: dict[int, dict[str, Any]] = {}
    per_reason: dict[str | None, dict[str, Any]] = {}
    grand = new_totals()
    for mv, lot, med in rows:
        line_value = None if mv.unit_cost is None else mv.unit_cost * mv.quantity
        lots.append(
            {
                "medicine_id": med.id,
                "name": med.name,
                "sku": med.sku,
                "lot_number": lot.lot_number,
                "expiry_date": lot.expiry_date,
                "quantity": mv.quantity,
                "reason": mv.reason,
                "unit_cost": money(mv.unit_cost),
                "value": money(line_value),
            }
        )
        entry = per_med.setdefault(
            med.id,
            {"medicine_id": med.id, "name": med.name, "sku": med.sku, "lot_ids": set(), "totals": new_totals()},
        )
        entry["lot_ids"].add(lot.id)
        add(entry["totals"], mv.quantity, line_value)
        add(per_reason.setdefault(mv.reason, new_totals()), mv.quantity, line_value)
        add(grand, mv.quantity, line_value)

    medicines = [
        {
            "medicine_id": e["medicine_id"],
            "name": e["name"],
            "sku": e["sku"],
            "lots_count": len(e["lot_ids"]),
            "total_wasted": e["totals"]["units"],
            "wasted_value": money(e["totals"]["value"]),
            "unvalued_units": e["totals"]["unvalued_units"],
        }
        for e in per_med.values()
    ]

    def reason_key(r: str | None) -> tuple[int, str]:
        return (_REASON_ORDER.index(r), "") if r in _REASON_ORDER else (len(_REASON_ORDER), r or "")

    by_reason = [
        {"reason": r, "units": t["units"], "value": money(t["value"]), "unvalued_units": t["unvalued_units"]}
        for r, t in sorted(per_reason.items(), key=lambda kv: reason_key(kv[0]))
    ]
    return {
        "month": month,
        "medicines": medicines,
        "by_reason": by_reason,
        "lots": lots,
        "grand_total": grand["units"],
        "grand_total_value": money(grand["value"]),
        "grand_total_unvalued_units": grand["unvalued_units"],
    }
