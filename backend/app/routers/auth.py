from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import LoginRequest, TokenOut, UserOut
from ..security import TOKEN_TTL, create_token, login_limiter, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    ip = request.client.host if request.client else "unknown"
    if login_limiter.is_blocked(ip, body.username):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    user = db.scalars(select(User).where(User.username == body.username)).first()
    ok = verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None:
        login_limiter.record_failure(ip, body.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    login_limiter.reset(ip, body.username)
    return TokenOut(
        access_token=create_token(user.id),
        token_type="bearer",
        expires_in=int(TOKEN_TTL.total_seconds()),
    )


@router.post("/logout", status_code=204, dependencies=[Depends(get_current_user)])
def logout() -> None:
    # Stateless: the client discards the token; it stays valid until it expires.
    return None


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
