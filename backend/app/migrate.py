"""Idempotent v0.3 schema upgrade (no Alembic). Run: python -m app.migrate

Postgres only; on any other dialect it does nothing. New installs get these
columns from init_db (create_all) and do not need this script.
"""
import sys

from sqlalchemy import Engine, text

from .db import get_engine

# (table, column) pairs this script adds; tests check them against Base.metadata.
NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("lots", "unit_cost"),
    ("stock_movements", "unit_cost"),
    ("stock_movements", "reason"),
)

STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE lots ADD COLUMN IF NOT EXISTS unit_cost NUMERIC(12,2)",
    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS unit_cost NUMERIC(12,2)",
    "ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS reason VARCHAR(20)",
    # Before v0.3 the only source of waste movements was the expiry sweep.
    "UPDATE stock_movements SET reason = 'expired' WHERE type = 'waste' AND reason IS NULL",
)


def run(engine: Engine) -> int:
    if engine.dialect.name != "postgresql":
        print("skipped (not postgresql)")
        return 0
    with engine.begin() as conn:  # one transaction: all statements or none
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
            print(f"applied: {stmt}")
    return 0


def main() -> int:
    return run(get_engine())


if __name__ == "__main__":
    sys.exit(main())
