import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager

from sqlalchemy.orm import Session
from neo4j import Session as Neo4jSession

from app.services.search.mssql_search import mssql_paper_search
from app.services.search.neo4j_search import neo4j_paper_search
from app.services.search.hbase_search import hbase_search
from app.services.search.rrf_fusion import rrf_fusion

logger = logging.getLogger(__name__)


def _run_mssql_search(
    session_factory,
    query: str,
    top_k: int,
    filters: Optional[dict],
    min_score: Optional[float],
) -> list[dict]:
    """Run MSSQL search with its own session from factory."""
    logger.info(f"[MSSQL] Starting search for query='{query}', top_k={top_k}")
    try:
        with session_factory() as db:
            results = mssql_paper_search(
                db=db,
                query=query,
                top_k=top_k,
                filters=filters,
                min_score=min_score,
            )
            logger.info(f"[MSSQL] Completed. Returned {len(results)} results")
            return results
    except Exception as e:
        logger.error(f"[MSSQL] FAILED: {e}")
        return []


def _run_neo4j_search(
    neo4j_session_factory,
    session_factory,
    query: str,
    top_k: int,
) -> list[dict]:
    """Run Neo4j search across multiple search types with separate MSSQL session."""
    logger.info(f"[Neo4j] Starting search for query='{query}', top_k={top_k}")
    try:
        with neo4j_session_factory() as neo4j:
            with session_factory() as db:
                all_results = []
                
                search_types = ["citations", "cited_by", "related", "papers"]
                
                for search_type in search_types:
                    try:
                        results = neo4j_paper_search(
                            neo4j_session=neo4j,
                            db=db,
                            query=query,
                            search_type=search_type,
                            top_k=top_k,
                        )
                        all_results.extend(results)
                        logger.info(f"[Neo4j] search_type='{search_type}' returned {len(results)} results")
                    except Exception as e:
                        logger.warning(f"[Neo4j] search_type='{search_type}' FAILED: {e}")
                        continue
                
                logger.info(f"[Neo4j] Completed. Total returned {len(all_results)} results")
                return all_results
    except Exception as e:
        logger.error(f"[Neo4j] FAILED: {e}")
        return []


def _run_hbase_search(
    query: str,
    top_k: int,
) -> list[dict]:
    """Run HBase search for keyword metrics."""
    logger.info(f"[HBase] Starting search for query='{query}', top_k={top_k}")
    try:
        results = []
        
        keyword_results = hbase_search(query or "", search_type="keyword", top_k=top_k)
        results.extend(keyword_results)
        logger.info(f"[HBase] keyword search returned {len(keyword_results)} results")
        
        if query:
            author_results = hbase_search(query, search_type="author", top_k=top_k // 2)
            results.extend(author_results)
            logger.info(f"[HBase] author search returned {len(author_results)} results")
            
            venue_results = hbase_search(query, search_type="venue", top_k=top_k // 2)
            results.extend(venue_results)
            logger.info(f"[HBase] venue search returned {len(venue_results)} results")
        
        logger.info(f"[HBase] Completed. Total returned {len(results)} results")
        return results
    except Exception as e:
        logger.error(f"[HBase] FAILED: {e}")
        return []


def hybrid_search(
    mssql_session_factory,
    neo4j_session_factory,
    query: str,
    top_k: int = 10,
    filters: Optional[dict] = None,
    min_score: Optional[float] = None,
) -> tuple[list[dict], list[str]]:
    """
    Run hybrid search across all backends with RRF fusion.
    Uses Factory Pattern - each thread gets its own session from factory.
    
    Args:
        mssql_session_factory: SQLAlchemy session factory (e.g., SessionLocal)
        neo4j_session_factory: Neo4j session factory
        query: Search query
        top_k: Number of results to return
        filters: Optional filters for MSSQL search
        min_score: Minimum score threshold for RRF
        
    Returns:
        Tuple of (fused results, list of successful sources)
    """
    results_by_source: dict[str, list[dict]] = {}
    successful_sources: list[str] = []
    
    logger.info(f"[Hybrid] Starting parallel search for query='{query}', top_k={top_k}")
    logger.info(f"[Hybrid] Submitting 3 parallel tasks to executor")
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        mssql_future = executor.submit(
            _run_mssql_search, mssql_session_factory, query, top_k, filters, min_score
        )
        neo4j_future = executor.submit(
            _run_neo4j_search, neo4j_session_factory, mssql_session_factory, query, top_k
        )
        hbase_future = executor.submit(
            _run_hbase_search, query, top_k
        )
        
        logger.info(f"[Hybrid] Waiting for futures to complete...")
        
        for future in as_completed([mssql_future, neo4j_future, hbase_future]):
            if future == mssql_future:
                try:
                    results = future.result()
                    results_by_source["mssql"] = results
                    logger.info(f"[Hybrid] MSSQL future completed. Got {len(results)} results")
                    if results:
                        successful_sources.append("mssql")
                except Exception as e:
                    logger.error(f"[Hybrid] MSSQL future EXCEPTION: {e}")
                    
            elif future == neo4j_future:
                try:
                    results = future.result()
                    results_by_source["neo4j"] = results
                    logger.info(f"[Hybrid] Neo4j future completed. Got {len(results)} results")
                    if results:
                        successful_sources.append("neo4j")
                except Exception as e:
                    logger.error(f"[Hybrid] Neo4j future EXCEPTION: {e}")
                    
            elif future == hbase_future:
                try:
                    results = future.result()
                    results_by_source["hbase"] = results
                    logger.info(f"[Hybrid] HBase future completed. Got {len(results)} results")
                    if results:
                        successful_sources.append("hbase")
                except Exception as e:
                    logger.error(f"[Hybrid] HBase future EXCEPTION: {e}")
    
    logger.info(f"[Hybrid] All futures collected. results_by_source keys: {list(results_by_source.keys())}")
    for src, results in results_by_source.items():
        logger.info(f"[Hybrid] source='{src}' has {len(results)} results")
    
    results_list = list(results_by_source.values())
    
    fused = rrf_fusion(
        results_list,
        k=60,
        min_score=min_score,
    )
    
    return fused[:top_k], successful_sources