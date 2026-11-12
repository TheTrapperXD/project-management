from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_QTY = 1_000_000
MAX_UNIT_COST = Decimal("1000000")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(_Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class UserOut(BaseModel):
    id: int
    username: str


class StockInRequest(_Strict):
    name: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=64)
    lot_number: str = Field(min_length=1, max_length=64)
    expiry_date: date
    quantity: int = Field(ge=1, le=MAX_QTY)
    reorder_threshold: int | None = Field(default=None, ge=0, le=MAX_QTY)
    # Baht per unit. Send as a JSON string (e.g. "12.50") to keep it exact.
    unit_cost: Decimal | None = Field(default=None, ge=0, le=MAX_UNIT_COST, decimal_places=2)


class StockInResponse(BaseModel):
    medicine_id: int
    lot_id: int
    lot_number: str
    expiry_date: date
    lot_quantity: int


class StockOutRequest(_Strict):
    medicine_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=MAX_QTY)


class Allocation(BaseModel):
    lot_id: int
    lot_number: str
    expiry_date: date
    taken: int


class StockOutResponse(BaseModel):
    allocations: list[Allocation]


class WriteoffRequest(_Strict):
    lot_id: int = Field(ge=1)
    quantity: int | None = Field(default=None, ge=1, le=MAX_QTY)
    reason: Literal["expiring", "damaged", "recalled", "other"]


class WriteoffResponse(BaseModel):
    lot_id: int
    medicine_name: str
    lot_number: str
    written_off: int
    remaining: int
    value: str | None


class MedicineOut(BaseModel):
    id: int
    name: str
    sku: str
    total_quantity: int
    reorder_threshold: int
