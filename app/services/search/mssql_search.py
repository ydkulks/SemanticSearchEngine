import json
from typing import Optional
from sqlalchemy.orm import Session

from app.repositories.paper import search_papers_db


def mssql_paper_search(
    db: Session,
    query: str,
    top_k: int = 10,
    filters: Optional[dict] = None,
    min_score: Optional[float] = None,
) -> list[dict]:
    papers = search_papers_db(
        db=db,
        query_text=query,
        top_k=top_k,
        filters=filters,
    )

    results = []
    for row in papers:
        keywords = row["keywords"]
        if keywords is None:
            keywords = []
        elif isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except Exception:
                keywords = []

        authors: list[str] = []
        if row["authors"]:
            authors = [a.strip() for a in row["authors"].split(", ")] if isinstance(row["authors"], str) else []

        venue_name = row["venue"]
        year = row["year"]

        content_parts: list[str] = [str(row["title"])]
        if row["abstract"]:
            content_parts.append(str(row["abstract"]))
        if authors:
            content_parts.append(f"Authors: {', '.join(authors)}")
        if year:
            content_parts.append(f"Year: {year}")
        if venue_name:
            content_parts.append(f"Venue: {venue_name}")

        results.append(
            {
                "id": row["id"],
                "title": row["title"],
                "content": " | ".join(content_parts),
                "abstract": row["abstract"],
                "authors": authors,
                "year": year,
                "venue": venue_name,
                "keywords": keywords,
                "source": "mssql",
                "score": row["score"],
                "metadata": {
                    "year": year,
                    "venue": venue_name,
                    "authors": authors,
                    "keywords": keywords,
                },
            }
        )

    return results