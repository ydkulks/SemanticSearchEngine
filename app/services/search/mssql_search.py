from typing import Optional
import json


def mssql_paper_search(
    db,
    query: str,
    top_k: int = 10,
    filters: Optional[dict] = None,
    min_score: Optional[float] = None,
) -> list[dict]:
    from sqlalchemy import text

    filter_conditions = ""
    params = {"query": query, "top_k": top_k}

    if filters:
        if "year" in filters:
            filter_conditions += " AND p.year = :year"
            params["year"] = filters["year"]
        if "min_year" in filters:
            filter_conditions += " AND p.year >= :min_year"
            params["min_year"] = filters["min_year"]
        if "max_year" in filters:
            filter_conditions += " AND p.year <= :max_year"
            params["max_year"] = filters["max_year"]
        if "venue" in filters:
            filter_conditions += " AND v.name LIKE :venue"
            params["venue"] = f"%{filters['venue']}%"

    sql = f"""
        SELECT TOP ({top_k})
            p.id,
            p.title,
            p.abstract,
            p.year,
            v.name AS venue,
            p.keywords,
            STRING_AGG(a.name, ', ') AS authors,
            0.0 AS score
        FROM dbo.papers p
        LEFT JOIN dbo.venues v ON p.venue_id = v.id
        LEFT JOIN dbo.paper_authors pa ON p.id = pa.paper_id
        LEFT JOIN dbo.authors a ON pa.author_id = a.id
        WHERE p.title LIKE '%' + :query + '%'
           OR p.abstract LIKE '%' + :query + '%'
           OR p.keywords LIKE '%' + :query + '%'
           OR EXISTS (
               SELECT 1
               FROM dbo.paper_authors pa2
               JOIN dbo.authors a2 ON pa2.author_id = a2.id
               WHERE pa2.paper_id = p.id
                 AND a2.name LIKE '%' + :query + '%'
           )
        {filter_conditions}
        GROUP BY p.id, p.title, p.abstract, p.year, v.name, p.keywords
        ORDER BY p.year DESC, p.title
    """

    result = db.execute(text(sql), params)
    rows = result.fetchall()

    results = []
    for row in rows:
        keywords = row.keywords
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except Exception:
                keywords = []

        authors = row.authors.split(", ") if row.authors else []

        content_parts = [row.title or ""]
        if row.abstract:
            content_parts.append(row.abstract)
        if row.authors:
            content_parts.append(f"Authors: {row.authors}")
        if row.year:
            content_parts.append(f"Year: {row.year}")
        if row.venue:
            content_parts.append(f"Venue: {row.venue}")

        results.append(
            {
                "id": row.id,
                "title": row.title,
                "content": " | ".join(content_parts),
                "abstract": row.abstract,
                "authors": authors,
                "year": row.year,
                "venue": row.venue,
                "keywords": keywords or [],
                "source": "mssql",
                "score": row.score,
                "metadata": {
                    "year": row.year,
                    "venue": row.venue,
                    "authors": authors,
                    "keywords": keywords or [],
                },
            }
        )

    return results
