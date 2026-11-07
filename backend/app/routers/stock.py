from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import MedicineOut, StockInRequest, StockInResponse, StockOutRequest, StockOutResponse
from ..services import inventory

router = APIRouter(tags=["stock"], dependencies=[Depends(get_current_user)])


@router.post("/stock/in", response_model=StockInResponse)
def stock_in(body: StockInRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lot = inventory.stock_in(
        db, user.id, body.name, body.sku, body.lot_number, body.expiry_date,
        body.quantity, body.reorder_threshold, today=date.today(),
    )
    return StockInResponse(
        medicine_id=lot.medicine_id, lot_id=lot.id, lot_number=lot.lot_number,
        expiry_date=lot.expiry_date, lot_quantity=lot.quantity,
    )


@router.post("/stock/out", response_model=StockOutResponse)
def stock_out(body: StockOutRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    allocations = inventory.dispense_fifo(db, user.id, body.medicine_id, body.quantity, today=date.today())
    return {"allocations": allocations}


@router.get("/medicines", response_model=list[MedicineOut])
def medicines(db: Session = Depends(get_db)):
    return inventory.list_medicines(db, date.today())
