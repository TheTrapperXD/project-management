from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    # No auth and no database: used to wake the instance and check it is up.
    return {"status": "ok"}
