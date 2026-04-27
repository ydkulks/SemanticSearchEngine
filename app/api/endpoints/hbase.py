import time
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.api.dto import SearchRequestDTO, SearchResponseDTO, PaperResultDTO
from app.services.search import hbase_search

router = APIRouter()


@router.post("/search", response_model=SearchResponseDTO)
def search_hbase(
    query: str | None = Query(
        None, description="Search query (required for some types)"),
    search_type: str = Query(
        "keyword", description="Search type: keyword, author, venue, paper, institution, top_authors, trending, paper_stats, venue_stats"),
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    results = hbase_search.hbase_search(
        query=query,
        search_type=search_type,
        top_k=top_k,
    )

    latency_ms = (time.time() - start_time) * 1000

    return SearchResponseDTO(
        query=query or "",
        results=[PaperResultDTO(**r) for r in results],
        total=len(results),
        latency_ms=round(latency_ms, 2),
        sources_queried=["hbase"],
    )