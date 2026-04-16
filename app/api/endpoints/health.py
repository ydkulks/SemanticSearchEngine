from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.dependencies import get_db
from app.api.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)):
    mssql_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        mssql_status = f"unhealthy: {str(e)}"

    return HealthResponse(
        status="healthy" if mssql_status == "healthy" else "degraded",
        mssql=mssql_status,
    )
