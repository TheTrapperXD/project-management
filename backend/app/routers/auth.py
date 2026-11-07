from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import LoginRequest, UserOut
from ..security import COOKIE_NAME, TOKEN_TTL, create_token, login_limiter, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> User:
    ip = request.client.host if request.client else "unknown"
    if login_limiter.is_blocked(ip, body.username):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    user = db.scalars(select(User).where(User.username == body.username)).first()
    ok = verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None:
        login_limiter.record_failure(ip, body.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    login_limiter.reset(ip, body.username)
    response.set_cookie(
        COOKIE_NAME,
        create_token(user.id),
        max_age=int(TOKEN_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=get_settings().COOKIE_SECURE,
        path="/",
    )
    return user


@router.post("/logout", status_code=204, dependencies=[Depends(get_current_user)])
def logout(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME, httponly=True, samesite="strict", secure=get_settings().COOKIE_SECURE, path="/"
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
