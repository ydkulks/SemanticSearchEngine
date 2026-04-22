from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.api.dto import HealthResponseDTO
from app.repositories.paper import check_db_health
from app.db.neo4j_ import neo4j_conn

router = APIRouter()


@router.get("/health", response_model=HealthResponseDTO)
def health_check(db: Session = Depends(get_db)):
    mssql_status = "healthy"
    try:
        if not check_db_health(db):
            mssql_status = "unhealthy"
    except Exception as e:
        mssql_status = f"unhealthy: {str(e)}"

    neo4j_status = "not_configured"
    try:
        if neo4j_conn.verify_connectivity():
            neo4j_status = "healthy"
        else:
            neo4j_status = "unhealthy"
    except Exception as e:
        neo4j_status = f"unhealthy: {str(e)}"

    if mssql_status == "healthy" and neo4j_status == "healthy":
        overall_status = "healthy"
    elif mssql_status == "healthy" or neo4j_status == "healthy":
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return HealthResponseDTO(
        status=overall_status,
        mssql=mssql_status,
        neo4j=neo4j_status,
    )