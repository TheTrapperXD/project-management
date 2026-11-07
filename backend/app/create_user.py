"""CLI: python -m app.create_user <username>  (password read via getpass only)."""
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User
from .security import MIN_PASSWORD_LENGTH, hash_password


def create_user(session: Session, username: str, password: str) -> User:
    username = username.strip()
    if not 1 <= len(username) <= 64:
        raise ValueError("Username must be 1-64 characters")
    if session.scalars(select(User).where(User.username == username)).first():
        raise ValueError("User already exists")
    user = User(username=username, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    return user


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m app.create_user <username>", file=sys.stderr)
        return 2
    from .db import get_sessionmaker

    password = getpass.getpass(f"Password (min {MIN_PASSWORD_LENGTH} chars): ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match", file=sys.stderr)
        return 1
    try:
        with get_sessionmaker()() as session:
            create_user(session, sys.argv[1], password)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("User created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
