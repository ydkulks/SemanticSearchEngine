import logging
import time
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from neo4j import Session as Neo4jSession
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.db.neo4j_ import get_neo4j_session

logger = logging.getLogger(__name__)

router = APIRouter()


# ========================
# General Paper Queries
# ========================

@router.get("/papers/count")
def count_papers(
    keyword: Optional[str] = Query(None, description="Filter by keyword"),
    venue: Optional[str] = Query(None, description="Filter by venue name"),
    year: Optional[int] = Query(None, description="Filter by publication year"),
    db: Session = Depends(get_db),
):
    start_time = time.time()
    filters = []
    params = {}

    if keyword:
        filters.append("(p.title LIKE :keyword OR p.abstract LIKE :keyword OR p.keywords LIKE :keyword)")
        params["keyword"] = f"%{keyword}%"
    if venue:
        filters.append("v.name = :venue")
        params["venue"] = venue
    if year:
        filters.append("p.year = :year")
        params["year"] = year

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""

    sql = text(f"""
        SELECT COUNT(DISTINCT p.id) AS cnt
        FROM dbo.papers p
        LEFT JOIN dbo.venues v ON p.venue_id = v.id
        {where_clause}
    """)

    result = db.execute(sql, params).scalar()
    latency_ms = (time.time() - start_time) * 1000

    return {"count": result or 0, "filters": {"keyword": keyword, "venue": venue, "year": year}, "latency_ms": round(latency_ms, 2)}


@router.get("/papers/most-cited")
def most_cited_paper(
    year: Optional[int] = Query(None, description="Filter by publication year"),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    year_filter = "WHERE p.year = :year" if year else ""
    params = {"year": year} if year else {}

    sql = text(f"""
        SELECT TOP 1
            p.id, p.title, p.year, v.name AS venue,
            COUNT(pr.id) AS citation_count
        FROM dbo.papers p
        LEFT JOIN dbo.venues v ON p.venue_id = v.id
        LEFT JOIN dbo.paper_references pr ON p.id = pr.referenced_paper_id
        {year_filter}
        GROUP BY p.id, p.title, p.year, v.name
        ORDER BY citation_count DESC
    """)

    row = db.execute(sql, params).first()
    latency_ms = (time.time() - start_time) * 1000

    if not row:
        raise HTTPException(status_code=404, detail="No papers found")

    return {
        "id": row[0],
        "title": row[1],
        "year": row[2],
        "venue": row[3],
        "citation_count": row[4],
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/papers")
def list_papers(
    min_citations: Optional[int] = Query(None, description="Minimum citation count"),
    published_after: Optional[int] = Query(None, description="Published after this year"),
    author: Optional[str] = Query(None, description="Author name"),
    cited_by_author: Optional[str] = Query(None, description="Papers cited by this author's papers"),
    cites: Optional[str] = Query(None, description="Paper title that must be cited"),
    cited_by: Optional[str] = Query(None, description="Paper title that must cite"),
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    if cites and cited_by:
        # Papers that cite A and are cited by B
        sql = text("""
            SELECT p.id, p.title, p.year, v.name AS venue, COUNT(pr.id) AS citation_count
            FROM dbo.papers p
            LEFT JOIN dbo.venues v ON p.venue_id = v.id
            LEFT JOIN dbo.paper_references pr ON p.id = pr.referenced_paper_id
            WHERE p.id IN (
                SELECT pr1.paper_id FROM dbo.paper_references pr1
                JOIN dbo.papers p1 ON pr1.referenced_paper_id = p1.id
                WHERE p1.title LIKE :cites
            ) AND p.id IN (
                SELECT pr2.referenced_paper_id FROM dbo.paper_references pr2
                JOIN dbo.papers p2 ON pr2.paper_id = p2.id
                WHERE p2.title LIKE :cited_by
            )
            GROUP BY p.id, p.title, p.year, v.name
            ORDER BY citation_count DESC
            OFFSET 0 ROWS FETCH NEXT :top_k ROWS ONLY
        """)
        params = {"cites": f"%{cites}%", "cited_by": f"%{cited_by}%", "top_k": top_k}

    elif author and cited_by_author:
        # Papers by author that are cited by another author's papers
        sql = text("""
            SELECT p.id, p.title, p.year, v.name AS venue, COUNT(pr.id) AS citation_count
            FROM dbo.papers p
            JOIN dbo.paper_authors pa ON p.id = pa.paper_id
            JOIN dbo.authors a1 ON pa.author_id = a1.id
            LEFT JOIN dbo.venues v ON p.venue_id = v.id
            LEFT JOIN dbo.paper_references pr ON p.id = pr.referenced_paper_id
            WHERE a1.name LIKE :author
            AND p.id IN (
                SELECT pr2.referenced_paper_id FROM dbo.paper_references pr2
                JOIN dbo.papers p2 ON pr2.paper_id = p2.id
                JOIN dbo.paper_authors pa2 ON p2.id = pa2.paper_id
                JOIN dbo.authors a2 ON pa2.author_id = a2.id
                WHERE a2.name LIKE :cited_by_author
            )
            GROUP BY p.id, p.title, p.year, v.name
            ORDER BY citation_count DESC
            OFFSET 0 ROWS FETCH NEXT :top_k ROWS ONLY
        """)
        params = {"author": f"%{author}%", "cited_by_author": f"%{cited_by_author}%", "top_k": top_k}

    else:
        # Simple filter query using window for citation count
        having_conditions = []
        params = {"top_k": top_k}

        if min_citations:
            having_conditions.append("citation_count >= :min_cit")
            params["min_cit"] = min_citations
        if published_after:
            having_conditions.append("p.year > :pub_after")
            params["pub_after"] = published_after

        having_clause = ("HAVING " + " AND ".join(having_conditions)) if having_conditions else ""

        sql = text(f"""
            SELECT TOP (:top_k)
                p.id, p.title, p.year, v.name AS venue, COUNT(pr.id) AS citation_count
            FROM dbo.papers p
            LEFT JOIN dbo.venues v ON p.venue_id = v.id
            LEFT JOIN dbo.paper_references pr ON p.id = pr.referenced_paper_id
            GROUP BY p.id, p.title, p.year, v.name
            {having_clause}
            ORDER BY citation_count DESC
        """)

    rows = db.execute(sql, params).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "results": [
            {"id": r[0], "title": r[1], "year": r[2], "venue": r[3], "citation_count": r[4]}
            for r in rows
        ],
        "total": len(rows),
        "latency_ms": round(latency_ms, 2),
    }


# ========================
# Paper-Specific Endpoints
# ========================

@router.get("/papers/{title}/authors")
def get_paper_authors(
    title: str,
    db: Session = Depends(get_db),
):
    start_time = time.time()

    sql = text("""
        SELECT a.id, a.name, a.affiliation, pa.author_order
        FROM dbo.papers p
        JOIN dbo.paper_authors pa ON p.id = pa.paper_id
        JOIN dbo.authors a ON pa.author_id = a.id
        WHERE p.title LIKE :title
        ORDER BY pa.author_order
    """)

    rows = db.execute(sql, {"title": f"%{title}%"}).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    if not rows:
        raise HTTPException(status_code=404, detail="Paper not found")

    return {
        "paper_title": title,
        "authors": [
            {"id": r[0], "name": r[1], "affiliation": r[2], "order": r[3]}
            for r in rows
        ],
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/papers/{title}/citing-papers")
def get_citing_papers(
    title: str,
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    sql = text("""
        SELECT TOP (:top_k)
            p.id, p.title, p.year, v.name AS venue
        FROM dbo.papers p
        JOIN dbo.paper_references pr ON p.id = pr.paper_id
        JOIN dbo.papers ref_paper ON pr.referenced_paper_id = ref_paper.id
        LEFT JOIN dbo.venues v ON p.venue_id = v.id
        WHERE ref_paper.title LIKE :title
        ORDER BY p.year DESC
    """)

    rows = db.execute(sql, {"title": f"%{title}%", "top_k": top_k}).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "paper_title": title,
        "citing_papers": [
            {"id": r[0], "title": r[1], "year": r[2], "venue": r[3]}
            for r in rows
        ],
        "total": len(rows),
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/papers/{title}/citation-count")
def get_citation_count(
    title: str,
    db: Session = Depends(get_db),
):
    start_time = time.time()

    sql = text("""
        SELECT COUNT(pr.id) AS citation_count
        FROM dbo.papers p
        LEFT JOIN dbo.paper_references pr ON p.id = pr.referenced_paper_id
        WHERE p.title LIKE :title
        GROUP BY p.id, p.title
    """)

    result = db.execute(sql, {"title": f"%{title}%"}).first()
    latency_ms = (time.time() - start_time) * 1000

    if not result:
        raise HTTPException(status_code=404, detail="Paper not found")

    return {
        "paper_title": title,
        "citation_count": result[0],
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/papers/{title}/related-by-keywords")
def get_related_by_keywords(
    title: str,
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    # First get the keywords of the paper
    kw_sql = text("""
        SELECT TOP 1 keywords FROM dbo.papers WHERE title LIKE :title
    """)
    row = db.execute(kw_sql, {"title": f"%{title}%"}).first()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Paper not found or has no keywords")

    import json
    try:
        keywords = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    except (json.JSONDecodeError, TypeError):
        keywords = []

    if not keywords:
        return {"paper_title": title, "related_papers": [], "total": 0, "latency_ms": round((time.time() - start_time) * 1000, 2)}

    # Find papers sharing keywords
    sql = text("""
        SELECT TOP (:top_k)
            p.id, p.title, p.year, v.name AS venue, p.keywords
        FROM dbo.papers p
        LEFT JOIN dbo.venues v ON p.venue_id = v.id
        WHERE p.title LIKE :title_pattern
        AND (
    """)

    # Build keyword filter
    keyword_filters = []
    params = {"title": f"%{title}%", "title_pattern": f"%{title}%", "top_k": top_k}
    for i, kw in enumerate(keywords[:5]):  # Limit to first 5 keywords
        param_name = f"kw_{i}"
        keyword_filters.append(f"p.keywords LIKE :{param_name}")
        params[param_name] = f'%"{kw}"%'

    sql = text(str(sql) + " OR ".join(keyword_filters) + ") ORDER BY p.year DESC")

    rows = db.execute(sql, params).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "paper_title": title,
        "keywords": keywords,
        "related_papers": [
            {"id": r[0], "title": r[1], "year": r[2], "venue": r[3]}
            for r in rows
        ],
        "total": len(rows),
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/papers/{titleA}/shortest-path/{titleB}")
def shortest_citation_path(
    titleA: str,
    titleB: str,
    neo4j: Neo4jSession = Depends(get_neo4j_session),
):
    start_time = time.time()

    # Use Neo4j's shortest path algorithm
    cypher = """
        MATCH (a:Paper), (b:Paper)
        WHERE a.title CONTAINS $titleA OR a.title CONTAINS $searchA
        AND (b.title CONTAINS $titleB OR b.title CONTAINS $searchB)
        MATCH path = shortestPath((a)-[:CITES*1..10]-(b))
        RETURN [node IN nodes(path) | node.id] AS paper_ids,
               [node IN nodes(path) | node.title] AS paper_titles,
               length(path) AS path_length
        LIMIT 1
    """

    result = neo4j.run(
        cypher,
        parameters={
            "titleA": titleA,
            "searchA": titleA,
            "titleB": titleB,
            "searchB": titleB,
        },
    )
    record = result.single()
    latency_ms = (time.time() - start_time) * 1000

    if not record:
        return {
            "from": titleA,
            "to": titleB,
            "path": None,
            "path_length": None,
            "message": "No citation path found between the papers",
            "latency_ms": round(latency_ms, 2),
        }

    return {
        "from": titleA,
        "to": titleB,
        "path": record["paper_titles"],
        "paper_ids": record["paper_ids"],
        "path_length": record["path_length"],
        "latency_ms": round(latency_ms, 2),
    }


# ========================
# Author Queries
# ========================

@router.get("/authors/{name}/coauthors")
def get_coauthors(
    name: str,
    db: Session = Depends(get_db),
):
    start_time = time.time()

    sql = text("""
        SELECT DISTINCT a2.id, a2.name, a2.affiliation, COUNT(p.id) AS collaboration_count
        FROM dbo.authors a1
        JOIN dbo.paper_authors pa1 ON a1.id = pa1.author_id
        JOIN dbo.papers p ON pa1.paper_id = p.id
        JOIN dbo.paper_authors pa2 ON p.id = pa2.paper_id
        JOIN dbo.authors a2 ON pa2.author_id = a2.id
        WHERE a1.name LIKE :name AND a2.id != a1.id
        GROUP BY a2.id, a2.name, a2.affiliation
        ORDER BY collaboration_count DESC
    """)

    rows = db.execute(sql, {"name": f"%{name}%"}).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "author": name,
        "coauthors": [
            {"id": r[0], "name": r[1], "affiliation": r[2], "collaboration_count": r[3]}
            for r in rows
        ],
        "total": len(rows),
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/authors/{nameA}/collab-distance/{nameB}")
def collaboration_distance(
    nameA: str,
    nameB: str,
    neo4j: Neo4jSession = Depends(get_neo4j_session),
):
    start_time = time.time()

    # Use Neo4j to find shortest collaboration path
    cypher = """
        MATCH (a1:Author), (a2:Author)
        WHERE (a1.name CONTAINS $nameA OR a1.name CONTAINS $searchA)
        AND (a2.name CONTAINS $nameB OR a2.name CONTAINS $searchB)
        MATCH path = shortestPath((a1)-[:COLLABORATES*1..10]-(a2))
        RETURN [node IN nodes(path) | node.name] AS author_names,
               length(path) AS distance
        LIMIT 1
    """

    result = neo4j.run(
        cypher,
        parameters={
            "nameA": nameA,
            "searchA": nameA,
            "nameB": nameB,
            "searchB": nameB,
        },
    )
    record = result.single()
    latency_ms = (time.time() - start_time) * 1000

    if not record:
        return {
            "author_a": nameA,
            "author_b": nameB,
            "distance": None,
            "path": None,
            "message": "No collaboration path found",
            "latency_ms": round(latency_ms, 2),
        }

    return {
        "author_a": nameA,
        "author_b": nameB,
        "distance": record["distance"],
        "path": record["author_names"],
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/authors/most-prolific")
def most_prolific_authors(
    year: Optional[int] = Query(None, description="Filter by publication year"),
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    year_filter = "WHERE p.year = :year" if year else ""
    params = {"top_k": top_k}
    if year:
        params["year"] = year

    sql = text(f"""
        SELECT TOP (:top_k)
            a.id, a.name, a.affiliation, COUNT(pa.paper_id) AS paper_count
        FROM dbo.authors a
        JOIN dbo.paper_authors pa ON a.id = pa.author_id
        JOIN dbo.papers p ON pa.paper_id = p.id
        {year_filter}
        GROUP BY a.id, a.name, a.affiliation
        ORDER BY paper_count DESC
    """)

    rows = db.execute(sql, params).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "year": year,
        "authors": [
            {"id": r[0], "name": r[1], "affiliation": r[2], "paper_count": r[3]}
            for r in rows
        ],
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/authors")
def authors_published_in_both(
    published_in: list[str] = Query(..., description="Venue names (can specify multiple)"),
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    if len(published_in) < 2:
        raise HTTPException(status_code=400, detail="Please provide at least 2 venues")

    venue_a, venue_b = published_in[0], published_in[1]

    sql = text("""
        SELECT TOP (:top_k)
            a.id, a.name, a.affiliation,
            COUNT(DISTINCT pa.paper_id) AS total_papers
        FROM dbo.authors a
        JOIN dbo.paper_authors pa ON a.id = pa.author_id
        JOIN dbo.papers p ON pa.paper_id = p.id
        JOIN dbo.venues v ON p.venue_id = v.id
        WHERE v.name = :venue_a AND a.id IN (
            SELECT a2.id
            FROM dbo.authors a2
            JOIN dbo.paper_authors pa2 ON a2.id = pa2.author_id
            JOIN dbo.papers p2 ON pa2.paper_id = p2.id
            JOIN dbo.venues v2 ON p2.venue_id = v2.id
            WHERE v2.name = :venue_b
        )
        GROUP BY a.id, a.name, a.affiliation
        ORDER BY total_papers DESC
    """)

    rows = db.execute(sql, {"venue_a": venue_a, "venue_b": venue_b, "top_k": top_k}).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "venues": published_in[:2],
        "authors": [
            {"id": r[0], "name": r[1], "affiliation": r[2], "total_papers": r[3]}
            for r in rows
        ],
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/authors/self-citing")
def self_citing_authors(
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    sql = text("""
        SELECT TOP (:top_k)
            a.id, a.name,
            COUNT(DISTINCT pr.paper_id) AS self_citations
        FROM dbo.authors a
        JOIN dbo.paper_authors pa1 ON a.id = pa1.author_id
        JOIN dbo.papers p_cited ON pa1.paper_id = p_cited.id
        JOIN dbo.paper_references pr ON p_cited.id = pr.referenced_paper_id
        JOIN dbo.paper_authors pa2 ON pr.paper_id = pa2.paper_id
        WHERE pa2.author_id = a.id AND pa1.paper_id != pr.paper_id
        GROUP BY a.id, a.name
        ORDER BY self_citations DESC
    """)

    rows = db.execute(sql, {"top_k": top_k}).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "authors": [
            {"id": r[0], "name": r[1], "self_citations": r[2]}
            for r in rows
        ],
        "latency_ms": round(latency_ms, 2),
    }


# ========================
# Venue Queries
# ========================

@router.get("/venues")
def venues_by_keyword(
    keyword: str = Query(..., description="Keyword to search for"),
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    sql = text("""
        SELECT TOP (:top_k)
            v.id, v.name, COUNT(p.id) AS paper_count
        FROM dbo.venues v
        JOIN dbo.papers p ON v.id = p.venue_id
        WHERE p.title LIKE :keyword OR p.keywords LIKE :keyword
        GROUP BY v.id, v.name
        ORDER BY paper_count DESC
    """)

    rows = db.execute(sql, {"keyword": f"%{keyword}%", "top_k": top_k}).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "keyword": keyword,
        "venues": [
            {"id": r[0], "name": r[1], "paper_count": r[2]}
            for r in rows
        ],
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/venues/{venue}/avg-citations")
def venue_avg_citations(
    venue: str,
    db: Session = Depends(get_db),
):
    start_time = time.time()

    sql = text("""
        SELECT
            v.name AS venue_name,
            COUNT(DISTINCT p.id) AS paper_count,
            AVG(CAST(cite_counts.citation_count AS FLOAT)) AS avg_citations
        FROM dbo.venues v
        JOIN dbo.papers p ON v.id = p.venue_id
        LEFT JOIN (
            SELECT referenced_paper_id, COUNT(*) AS citation_count
            FROM dbo.paper_references
            WHERE referenced_paper_id IS NOT NULL
            GROUP BY referenced_paper_id
        ) cite_counts ON p.id = cite_counts.referenced_paper_id
        WHERE v.name = :venue
        GROUP BY v.name
    """)

    row = db.execute(sql, {"venue": venue}).first()
    latency_ms = (time.time() - start_time) * 1000

    if not row:
        raise HTTPException(status_code=404, detail="Venue not found")

    return {
        "venue": row[0],
        "paper_count": row[1],
        "avg_citations": float(row[2]) if row[2] else 0.0,
        "latency_ms": round(latency_ms, 2),
    }


@router.get("/venues/top-cited")
def top_cited_venues(
    limit: int = Query(5, ge=1, le=50),
    db: Session = Depends(get_db),
):
    start_time = time.time()

    sql = text("""
        SELECT TOP (:limit)
            v.id, v.name,
            COUNT(DISTINCT p.id) AS paper_count,
            SUM(cite_counts.citation_count) AS total_citations
        FROM dbo.venues v
        JOIN dbo.papers p ON v.id = p.venue_id
        LEFT JOIN (
            SELECT referenced_paper_id, COUNT(*) AS citation_count
            FROM dbo.paper_references
            WHERE referenced_paper_id IS NOT NULL
            GROUP BY referenced_paper_id
        ) cite_counts ON p.id = cite_counts.referenced_paper_id
        GROUP BY v.id, v.name
        ORDER BY total_citations DESC
    """)

    rows = db.execute(sql, {"limit": limit}).fetchall()
    latency_ms = (time.time() - start_time) * 1000

    return {
        "venues": [
            {"id": r[0], "name": r[1], "paper_count": r[2], "total_citations": r[3] or 0}
            for r in rows
        ],
        "latency_ms": round(latency_ms, 2),
    }
