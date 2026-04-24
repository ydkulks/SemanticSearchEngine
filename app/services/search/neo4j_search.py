import logging
from typing import Optional

from neo4j import Session as Neo4jSession
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)


CITATIONS_CYPHER = """
MATCH (p:Paper)-[:CITES]->(related:Paper)
WHERE p.title CONTAINS $query OR p.title CONTAINS $search_term
RETURN related.id AS paper_id, 'CITES' AS rel_type
LIMIT $limit
"""

CITED_BY_CYPHER = """
MATCH (p:Paper)<-[:CITES]-(related:Paper)
WHERE p.title CONTAINS $query OR p.title CONTAINS $search_term
RETURN related.id AS paper_id, 'CITED_BY' AS rel_type
LIMIT $limit
"""

COAUTHORS_CYPHER = """
MATCH (a1:Author)-[:WRITES]->(p:Paper)<-[:WRITES]-(a2:Author)
WHERE a1.name CONTAINS $query
RETURN DISTINCT a2.id AS author_id, a2.name AS author_name, 'COAUTHOR' AS rel_type
LIMIT $limit
"""

RELATED_CYPHER = """
MATCH (p:Paper)<-[:CITES*1..2]-(related:Paper)
WHERE p.title CONTAINS $query OR p.title CONTAINS $search_term
RETURN DISTINCT related.id AS paper_id, 'RELATED' AS rel_type
LIMIT $limit
"""

PAPERS_CYPHER = """
MATCH (p:Paper)
WHERE p.title CONTAINS $query OR p.abstract CONTAINS $query
RETURN p.id AS paper_id, 'EXACT' AS rel_type
LIMIT $limit
"""


def _fetch_papers_from_mssql(
    db: Session,
    paper_ids: list[str],
) -> dict:
    if not paper_ids:
        return {}

    placeholders = ", ".join([f":paper_id_{i}" for i in range(len(paper_ids))])
    params = {f"paper_id_{i}": pid for i, pid in enumerate(paper_ids)}

    sql = f"""
        SELECT
            p.id,
            p.title,
            p.abstract,
            p.year,
            v.name AS venue,
            p.keywords,
            STRING_AGG(a.name, ', ') AS authors
        FROM dbo.papers p
        LEFT JOIN dbo.venues v ON p.venue_id = v.id
        LEFT JOIN dbo.paper_authors pa ON p.id = pa.paper_id
        LEFT JOIN dbo.authors a ON pa.author_id = a.id
        WHERE p.id IN ({placeholders})
        GROUP BY p.id, p.title, p.abstract, p.year, v.name, p.keywords
    """

    result = db.execute(text(sql), params)
    rows = result.fetchall()

    paper_map = {}
    for row in rows:
        paper_map[row[0]] = {
            "id": row[0],
            "title": row[1],
            "abstract": row[2],
            "year": row[3],
            "venue": row[4],
            "keywords": row[5],
            "authors": row[6],
        }

    return paper_map


def _fetch_authors_from_mssql(
    db: Session,
    author_ids: list[str],
) -> dict:
    if not author_ids:
        return {}

    placeholders = ", ".join([f":author_id_{i}" for i in range(len(author_ids))])
    params = {f"author_id_{i}": aid for i, aid in enumerate(author_ids)}

    sql = f"""
        SELECT
            a.id,
            a.name,
            a.affiliation,
            STRING_AGG(p.title, ', ') AS papers
        FROM dbo.authors a
        LEFT JOIN dbo.paper_authors pa ON a.id = pa.author_id
        LEFT JOIN dbo.papers p ON pa.paper_id = p.id
        WHERE a.id IN ({placeholders})
        GROUP BY a.id, a.name, a.affiliation
    """

    result = db.execute(text(sql), params)
    rows = result.fetchall()

    author_map = {}
    for row in rows:
        author_map[row[0]] = {
            "id": row[0],
            "name": row[1],
            "affiliation": row[2],
            "papers": row[3],
        }

    return author_map


def neo4j_paper_search(
    neo4j_session: Neo4jSession,
    db: Session,
    query: str,
    search_type: str = "citations",
    top_k: int = 10,
) -> list[dict]:
    try:
        search_term = query.strip()

        if search_type == "citations":
            cypher_query = CITATIONS_CYPHER
        elif search_type == "cited_by":
            cypher_query = CITED_BY_CYPHER
        elif search_type == "coauthors":
            cypher_query = COAUTHORS_CYPHER
        elif search_type == "related":
            cypher_query = RELATED_CYPHER
        elif search_type == "papers":
            cypher_query = PAPERS_CYPHER
        else:
            raise ValueError(f"Unsupported search_type: {search_type}. Valid types: citations, cited_by, coauthors, related, papers")

        result = neo4j_session.run(
            cypher_query,
            parameters={"query": search_term, "search_term": search_term, "limit": top_k * 2},
        )
        records = list(result)

        if not records:
            return []

        if search_type == "coauthors":
            author_ids = [record["author_id"] for record in records if record.get("author_id")]
            author_map = _fetch_authors_from_mssql(db, author_ids)

            results = []
            for record in records:
                author_id = record.get("author_id")
                rel_type = record.get("rel_type", "COAUTHOR")
                author_data = author_map.get(author_id, {})

                results.append({
                    "id": author_id,
                    "title": author_data.get("name", ""),
                    "abstract": author_data.get("affiliation"),
                    "authors": [],
                    "year": None,
                    "venue": None,
                    "keywords": [],
                    "source": "neo4j",
                    "relationship_type": rel_type,
                    "score": 1.0,
                })

            return results[:top_k]

        paper_ids = [record["paper_id"] for record in records if record.get("paper_id")]
        paper_map = _fetch_papers_from_mssql(db, paper_ids)

        results = []
        for record in records:
            paper_id = record.get("paper_id")
            rel_type = record.get("rel_type", "RELATED")
            paper_data = paper_map.get(paper_id, {})

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
                "source": "neo4j",
                "relationship_type": rel_type,
                "score": 1.0,
            })

        return results[:top_k]

    except Exception as e:
        logger.error(f"Neo4j search failed: {e}")
        return []