from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.api.dto import HealthResponseDTO
from app.repositories.paper import check_db_health

router = APIRouter()


@router.get("/health", response_model=HealthResponseDTO)
def health_check(db: Session = Depends(get_db)):
    mssql_status = "healthy"
    try:
        if not check_db_health(db):
            mssql_status = "unhealthy"
    except Exception as e:
        mssql_status = f"unhealthy: {str(e)}"

    return HealthResponseDTO(
        status="healthy" if mssql_status == "healthy" else "degraded",
        mssql=mssql_status,
    )