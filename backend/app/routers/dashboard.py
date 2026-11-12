from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..clock import today_local
from ..db import get_db
from ..deps import get_current_user
from ..services import inventory

router = APIRouter(tags=["dashboard"], dependencies=[Depends(get_current_user)])


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    today = today_local()
    inventory.sweep_expired(db, today)
    return inventory.dashboard_buckets(db, today)
