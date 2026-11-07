from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .routers import auth, dashboard, reports, stock
from .services.inventory import (
    ConflictError,
    DomainValidationError,
    InsufficientStockError,
    NotFoundError,
)

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "pharmacy-app"
UNSAFE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def create_app() -> FastAPI:
    settings = get_settings()  # raises (app refuses to start) on invalid config
    app = FastAPI(title="Pharmacy Inventory", docs_url=None, redoc_url=None, openapi_url=None)

    api = APIRouter(prefix="/api")
    api.include_router(auth.router)
    api.include_router(stock.router)
    api.include_router(dashboard.router)
    api.include_router(reports.router)
    app.include_router(api)

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(InsufficientStockError)
    async def _insufficient(_: Request, exc: InsufficientStockError):
        return JSONResponse({"detail": str(exc), "available": exc.available}, status_code=409)

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(DomainValidationError)
    async def _invalid(_: Request, exc: DomainValidationError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(_: Request, exc: RequestValidationError):
        # Report field locations and messages only; never echo submitted values.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if request.method in UNSAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            return JSONResponse({"detail": "Missing CSRF header"}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # Added last so it is outermost: CORS headers also appear on 403/401 responses.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.CORS_ORIGIN],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Requested-With"],
    )
    return app


app = create_app()
