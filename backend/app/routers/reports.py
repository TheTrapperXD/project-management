from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..clock import today_local
from ..db import get_db
from ..deps import get_current_user
from ..services import inventory

router = APIRouter(tags=["reports"], dependencies=[Depends(get_current_user)])


@router.get("/reports/waste")
def waste(
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    db: Session = Depends(get_db),
):
    today = today_local()
    inventory.sweep_expired(db, today)
    return inventory.waste_report(db, month or today.strftime("%Y-%m"))
