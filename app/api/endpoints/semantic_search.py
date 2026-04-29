import time
import logging
from fastapi import APIRouter

from app.db.mssql import SessionLocal
from app.db.neo4j_ import get_neo4j_session_factory
from app.api.dto import SearchRequestDTO, SearchResponseDTO, PaperResultDTO
from app.services.search.semantic_vector_search import semantic_vector_search
from app.services.embedder import encode_query

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/semantic-search", response_model=SearchResponseDTO)
def semantic_search_papers(request: SearchRequestDTO):
    start_time = time.time()

    logger.info(f"[Semantic-Search] Received query='{request.query}', top_k={request.top_k}")

    query_vector = encode_query(request.query)

    neo4j_factory = get_neo4j_session_factory()

    results, sources_queried = semantic_vector_search(
        mssql_session_factory=SessionLocal,
        neo4j_session_factory=neo4j_factory,
        query_vector=query_vector,
        query=request.query,
        top_k=request.top_k,
        filters=request.filters,
        use_reranker=request.use_reranker,
    )

    latency_ms = (time.time() - start_time) * 1000

    return SearchResponseDTO(
        query=request.query,
        results=[PaperResultDTO(**r) for r in results],
        total=len(results),
        latency_ms=round(latency_ms, 2),
        sources_queried=sources_queried or ["semantic-vector"],
    )
