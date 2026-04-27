import time
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.api.dto import SearchRequestDTO, SearchResponseDTO, PaperResultDTO
from app.services.search.mssql_search import mssql_paper_search

router = APIRouter()


@router.post("/search", response_model=SearchResponseDTO)
def search_mssql(
    request: SearchRequestDTO,
    db: Session = Depends(get_db),
):
    start_time = time.time()

    results = mssql_paper_search(
        db=db,
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
        min_score=request.min_score,
    )

    latency_ms = (time.time() - start_time) * 1000

    return SearchResponseDTO(
        query=request.query,
        results=[PaperResultDTO(**r) for r in results],
        total=len(results),
        latency_ms=round(latency_ms, 2),
        sources_queried=["mssql"],
    )