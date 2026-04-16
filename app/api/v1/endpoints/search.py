import time
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.api.v1.schemas import SearchRequest, SearchResponse, SearchResult
from app.services.search.mssql_search import mssql_vector_search

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
def search_documents(
    request: SearchRequest,
    db: Session = Depends(get_db),
):
    start_time = time.time()

    results = mssql_vector_search(
        db=db,
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
        min_score=request.min_score,
    )

    latency_ms = (time.time() - start_time) * 1000

    return SearchResponse(
        query=request.query,
        results=[SearchResult(**r) for r in results],
        total=len(results),
        latency_ms=round(latency_ms, 2),
        sources_queried=["mssql"],
    )
