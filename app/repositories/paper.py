from typing import Optional
from sqlalchemy import literal, or_, text
from sqlalchemy.orm import Session

from app.models.database import Paper, Venue, Author, PaperAuthor

# Original SQL query string (kept for reference):
"""
SELECT TOP (:top_k)
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


def search_papers_db(
    db: Session,
    query_text: str,
    top_k: int = 10,
    filters: Optional[dict] = None,
) -> list:
    query = db.query(
        Paper.id,
        Paper.title,
        Paper.abstract,
        Paper.year,
        Venue.name.label("venue"),
        Paper.keywords,
    ).outerjoin(Venue, Paper.venue_id == Venue.id)

    search_conditions = [
        Paper.title.ilike(f"%{query_text}%"),
        Paper.abstract.ilike(f"%{query_text}%"),
        Paper.keywords.ilike(f"%{query_text}%"),
    ]

    author_subquery = (
        db.query(literal(1))
        .select_from(PaperAuthor)
        .join(Author, PaperAuthor.author_id == Author.id)
        .filter(PaperAuthor.paper_id == Paper.id)
        .filter(Author.name.ilike(f"%{query_text}%"))
        .scalar_subquery()
    )
    search_conditions.append(author_subquery == 1)

    query = query.filter(or_(*search_conditions))

    if filters:
        if "year" in filters:
            query = query.filter(Paper.year == filters["year"])
        if "min_year" in filters:
            query = query.filter(Paper.year >= filters["min_year"])
        if "max_year" in filters:
            query = query.filter(Paper.year <= filters["max_year"])
        if "venue" in filters:
            query = query.filter(Venue.name.ilike(f"%{filters['venue']}%"))

    query = query.group_by(
        Paper.id, Paper.title, Paper.abstract, Paper.year, Venue.name, Paper.keywords
    )
    query = query.order_by(Paper.year.desc(), Paper.title)
    query = query.limit(top_k)

    results = query.all()

    paper_ids = [row.id for row in results if row.id]
    author_map = {}
    if paper_ids:
        author_rows = (
            db.query(PaperAuthor.paper_id, Author.name)
            .join(Author, PaperAuthor.author_id == Author.id)
            .filter(PaperAuthor.paper_id.in_(paper_ids))
            .order_by(Author.name)
            .all()
        )
        for paper_id, author_name in author_rows:
            if author_name:
                if paper_id not in author_map:
                    author_map[paper_id] = []
                author_map[paper_id].append(author_name)

    papers = []
    for row in results:
        authors = ", ".join(author_map.get(row.id, []))
        papers.append({
            "id": row.id,
            "title": row.title,
            "abstract": row.abstract,
            "year": row.year,
            "venue": row.venue,
            "keywords": row.keywords,
            "authors": authors,
            "score": 0.0,
        })

    return papers


def check_db_health(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
