import logging
import time
from fastapi import APIRouter, Depends, HTTPException
from neo4j import Session as Neo4jSession
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.db.neo4j_ import get_neo4j_session
from app.api.dto.neo4j_dto import (
    Neo4jSearchRequestDTO,
    Neo4jPaperResultDTO,
    Neo4jSearchResponseDTO,
)
from app.services.search.neo4j_search import neo4j_paper_search

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/neo4j/search", response_model=Neo4jSearchResponseDTO)
def search_neo4j(
    request: Neo4jSearchRequestDTO,
    db: Session = Depends(get_db),
    neo4j: Neo4jSession = Depends(get_neo4j_session),
):
    start_time = time.time()

    try:
        results = neo4j_paper_search(
            neo4j_session=neo4j,
            db=db,
            query=request.query,
            search_type=request.search_type,
            top_k=request.top_k,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Neo4j search error: {e}")
        results = []

    latency_ms = (time.time() - start_time) * 1000

    return Neo4jSearchResponseDTO(
        query=request.query,
        search_type=request.search_type,
        results=[Neo4jPaperResultDTO(**r) for r in results],
        total=len(results),
        latency_ms=round(latency_ms, 2),
        sources_queried=["neo4j"],
    )