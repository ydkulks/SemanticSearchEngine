import logging
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.models.database import Paper, Venue, Embedding, PaperAuthor, Author
from app.services.search.neo4j_search import _fetch_papers_from_mssql
from app.services.search.rrf_fusion import rrf_fusion

logger = logging.getLogger(__name__)


def _run_mssql_vector_search(
    session_factory,
    query_vector: list,
    top_k: int,
    filters: Optional[dict],
) -> list:
    import json
    import numpy as np

    logger.info(f"[MSSQL-Vector] Starting vector search, top_k={top_k}")
    try:
        with session_factory() as db:
            query = db.query(
                Paper.id,
                Paper.title,
                Paper.abstract,
                Paper.year,
                Paper.keywords,
                Venue.name.label("venue"),
                Embedding.embedding,
            ).select_from(
                Paper
            ).outerjoin(
                Venue, Paper.venue_id == Venue.id
            ).join(
                Embedding, Paper.id == Embedding.paper_id
            )

            if filters:
                if "year" in filters:
                    query = query.filter(Paper.year == filters["year"])
                if "min_year" in filters:
                    query = query.filter(Paper.year >= filters["min_year"])
                if "max_year" in filters:
                    query = query.filter(Paper.year <= filters["max_year"])
                if "venue" in filters:
                    query = query.filter(Venue.name.ilike(f"%{filters['venue']}%"))

            query = query.limit(500)
            results = query.all()

            papers = []
            query_vec = np.array(query_vector)

            for row in results:
                try:
                    emb = np.array(json.loads(row.embedding))
                    similarity = float(np.dot(query_vec, emb) / (np.linalg.norm(query_vec) * np.linalg.norm(emb)))
                except Exception:
                    continue

                keywords = row.keywords
                if keywords is None:
                    keywords = []
                elif isinstance(keywords, str):
                    try:
                        keywords = json.loads(keywords)
                    except Exception:
                        keywords = []

                papers.append({
                    "id": row.id,
                    "title": row.title,
                    "abstract": row.abstract,
                    "authors": [],
                    "year": row.year,
                    "venue": row.venue,
                    "keywords": keywords,
                    "source": "mssql-vector",
                    "score": similarity,
                })

            papers.sort(key=lambda x: x["score"], reverse=True)
            papers = papers[:top_k * 2]

            if papers:
                paper_ids = [p["id"] for p in papers]
                author_rows = (
                    db.query(PaperAuthor.paper_id, Author.name)
                    .join(Author, PaperAuthor.author_id == Author.id)
                    .filter(PaperAuthor.paper_id.in_(paper_ids))
                    .order_by(Author.name)
                    .all()
                )
                author_map = {}
                for paper_id, author_name in author_rows:
                    if author_name:
                        if paper_id not in author_map:
                            author_map[paper_id] = []
                        author_map[paper_id].append(author_name)
                for p in papers:
                    p["authors"] = author_map.get(p["id"], [])

            logger.info(f"[MSSQL-Vector] Completed. Returned {len(papers)} results")
            return papers
    except Exception as e:
        logger.error(f"[MSSQL-Vector] FAILED: {e}")
        return []


def _run_neo4j_vector_search(
    neo4j_session_factory,
    mssql_session_factory,
    query_vector: list,
    top_k: int,
    filters: Optional[dict],
) -> list:
    logger.info(f"[Neo4j-Vector] Starting vector search, top_k={top_k}")
    try:
        neo4j_session = neo4j_session_factory()
        with mssql_session_factory() as db:
            result = neo4j_session.run(
                """
                CALL db.index.vector.queryNodes('paper_embeddings', $top_k, $query_vector) YIELD node, score
                RETURN node.id AS paper_id, score
                """,
                parameters={"query_vector": query_vector, "top_k": top_k * 2},
            )
            records = list(result)

            if not records:
                logger.info("[Neo4j-Vector] No results found")
                return []

            paper_ids = [record["paper_id"] for record in records if record.get("paper_id")]
            score_map = {record["paper_id"]: float(record["score"]) for record in records}

            paper_map = _fetch_papers_from_mssql(db, paper_ids)

            results = []
            for paper_id in paper_ids:
                paper_data = paper_map.get(paper_id, {})
                if not paper_data:
                    continue

                authors_list = []
                if paper_data.get("authors"):
                    authors_list = [a.strip() for a in paper_data.get("authors", "").split(", ")]

                keywords = paper_data.get("keywords")
                if keywords is None:
                    keywords = []
                elif isinstance(keywords, str):
                    import json
                    try:
                        keywords = json.loads(keywords)
                    except Exception:
                        keywords = []

                results.append({
                    "id": paper_id,
                    "title": paper_data.get("title", ""),
                    "abstract": paper_data.get("abstract"),
                    "authors": authors_list,
                    "year": paper_data.get("year"),
                    "venue": paper_data.get("venue"),
                    "keywords": keywords,
                    "source": "neo4j-vector",
                    "score": score_map.get(paper_id, 0.0),
                })

            logger.info(f"[Neo4j-Vector] Completed. Returned {len(results)} results")
            return results
    except Exception as e:
        logger.error(f"[Neo4j-Vector] FAILED: {e}")
        return []
    finally:
        if 'neo4j_session' in locals() and neo4j_session:
            neo4j_session.close()


def _run_hbase_search(query: str, top_k: int) -> list:
    """Run HBase search for keyword, author, and venue metrics."""
    logger.info(f"[HBase] Starting HBase search for query='{query}', top_k={top_k}")
    results = []
    try:
        from app.services.search.hbase_search import hbase_search

        # Search keywords
        keyword_results = hbase_search(query or "", search_type="keyword", top_k=top_k)
        results.extend(keyword_results)

        # Search authors and venues if query provided
        if query:
            author_results = hbase_search(query, search_type="author", top_k=top_k // 2)
            results.extend(author_results)
            venue_results = hbase_search(query, search_type="venue", top_k=top_k // 2)
            results.extend(venue_results)

        logger.info(f"[HBase] Completed. Returned {len(results)} results")
        return results
    except Exception as e:
        logger.error(f"[HBase] FAILED: {e}")
        return []


def semantic_vector_search(
    mssql_session_factory,
    neo4j_session_factory,
    query_vector: list,
    query: str = "",
    top_k: int = 10,
    filters: Optional[dict] = None,
    use_reranker: bool = False,
) -> tuple:
    results_by_source = {}
    successful_sources = []

    logger.info(f"[Semantic-Vector] Starting parallel vector search")

    with ThreadPoolExecutor(max_workers=3) as executor:
        mssql_future = executor.submit(
            _run_mssql_vector_search, mssql_session_factory, query_vector, top_k, filters
        )
        neo4j_future = executor.submit(
            _run_neo4j_vector_search, neo4j_session_factory, mssql_session_factory, query_vector, top_k, filters
        )
        hbase_future = executor.submit(
            _run_hbase_search, query, top_k
        )

        for future in as_completed([mssql_future, neo4j_future, hbase_future]):
            if future == mssql_future:
                try:
                    results = future.result()
                    results_by_source["mssql-vector"] = results
                    if results:
                        successful_sources.append("mssql-vector")
                except Exception as e:
                    logger.error(f"[Semantic-Vector] MSSQL future EXCEPTION: {e}")

            elif future == neo4j_future:
                try:
                    results = future.result()
                    results_by_source["neo4j-vector"] = results
                    if results:
                        successful_sources.append("neo4j-vector")
                except Exception as e:
                    logger.error(f"[Semantic-Vector] Neo4j future EXCEPTION: {e}")

            elif future == hbase_future:
                try:
                    results = future.result()
                    results_by_source["hbase"] = results
                    if results:
                        successful_sources.append("hbase")
                except Exception as e:
                    logger.error(f"[Semantic-Vector] HBase future EXCEPTION: {e}")

    results_list = list(results_by_source.values())

    fused = rrf_fusion(
        results_list,
        k=60,
        min_score=None,
    )

    if use_reranker and fused:
        from app.services.reranker import rerank_results
        logger.info(f"[Semantic-Vector] Applying reranking to {len(fused)} results")
        fused = rerank_results("", fused, top_k=top_k)

    return fused[:top_k], successful_sources
