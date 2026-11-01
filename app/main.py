import logging

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import get_db
from app.routers import admin, auth, cart, checkout, products

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Apex Sports API",
    description="API compartida para el e-commerce y el panel administrativo de Apex Sports.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready", tags=["health"])
def readiness(db=Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.error("Database readiness check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc
    return {"status": "ready"}


app.include_router(auth.router, prefix="/api/v1")
app.include_router(products.router, prefix="/api/v1")
app.include_router(cart.router, prefix="/api/v1")
app.include_router(checkout.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")


@app.exception_handler(SQLAlchemyError)
def database_error_handler(_request, _exc: SQLAlchemyError) -> JSONResponse:
    # Avoid logging exception parameters: SQL statements may contain credentials or tokens.
    logger.error("Database operation failed; verify that the documented schema is installed")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Database operation failed; verify schema setup and retry"},
    )
