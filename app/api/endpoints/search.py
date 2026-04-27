import time
import logging
from fastapi import APIRouter

from app.db.mssql import SessionLocal
from app.db.neo4j_ import get_neo4j_session_factory
from app.api.dto import SearchRequestDTO, SearchResponseDTO, PaperResultDTO
from app.services.search.hybrid_search import hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/search", response_model=SearchResponseDTO)
def search_papers(request: SearchRequestDTO):
    start_time = time.time()

    neo4j_factory = get_neo4j_session_factory()

    results, sources_queried = hybrid_search(
        mssql_session_factory=SessionLocal,
        neo4j_session_factory=neo4j_factory,
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
        min_score=request.min_score,
        use_reranker=request.use_reranker,
    )

    latency_ms = (time.time() - start_time) * 1000

    return SearchResponseDTO(
        query=request.query,
        results=[PaperResultDTO(**r) for r in results],
        total=len(results),
        latency_ms=round(latency_ms, 2),
        sources_queried=sources_queried or ["hybrid"],
    )