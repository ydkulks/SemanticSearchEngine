from typing import Optional
from app.db.hbase_ import hbase_conn


def hbase_keyword_search(
    query: str,
    top_k: int = 10,
) -> list[dict]:
    keyword = query.lower().strip()
    row = hbase_conn.get_table("keyword_metrics").row(keyword)

    if not row:
        return []

    return [{
        "id": f"keyword:{keyword}",
        "title": f"Keyword: {keyword}",
        "content": f"Paper count: {row.get('m:paper_count', 0)} | Total citations: {row.get('m:total_citations', 0)} | Avg year: {row.get('m:avg_year', 'N/A')}",
        "abstract": None,
        "authors": [],
        "year": int(row.get("m:avg_year", 0)) or None,
        "venue": None,
        "keywords": [keyword],
        "source": "hbase",
        "score": float(row.get("m:paper_count", 0)) / 100.0,
        "metadata": {
            "paper_count": int(row.get("m:paper_count", 0)),
            "total_citations": int(row.get("m:total_citations", 0)),
            "avg_year": row.get("m:avg_year"),
        },
    }]


def hbase_author_search(
    query: str,
    top_k: int = 10,
) -> list[dict]:
    author_id = query.strip()
    row = hbase_conn.get_table("author_metrics").row(author_id)

    if not row:
        return []

    return [{
        "id": f"author:{author_id}",
        "title": f"Author ID: {author_id}",
        "content": f"H-index: {row.get('m:h_index', 0)} | Papers: {row.get('m:paper_count', 0)} | Total citations: {row.get('m:total_citations', 0)}",
        "abstract": None,
        "authors": [author_id],
        "year": int(row.get("m:last_year", 0)) or None,
        "venue": None,
        "keywords": [],
        "source": "hbase",
        "score": float(row.get("m:h_index", 0)) / 10.0,
        "metadata": {
            "h_index": int(row.get("m:h_index", 0)),
            "paper_count": int(row.get("m:paper_count", 0)),
            "total_citations": int(row.get("m:total_citations", 0)),
            "first_year": row.get("m:first_year"),
            "last_year": row.get("m:last_year"),
        },
    }]


def hbase_venue_search(
    query: str,
    top_k: int = 10,
) -> list[dict]:
    venue_id = query.strip()
    row = hbase_conn.get_table("venue_metrics").row(venue_id)

    if not row:
        return []

    return [{
        "id": f"venue:{venue_id}",
        "title": f"Venue: {venue_id}",
        "content": f"Papers: {row.get('m:paper_count', 0)} | Avg citations: {row.get('m:avg_citations', 0)} | Median: {row.get('m:median_citations', 0)}",
        "abstract": None,
        "authors": [],
        "year": int(row.get("m:top_year", 0)) or None,
        "venue": venue_id,
        "keywords": [],
        "source": "hbase",
        "score": float(row.get("m:paper_count", 0)) / 100.0,
        "metadata": {
            "paper_count": int(row.get("m:paper_count", 0)),
            "avg_citations": int(row.get("m:avg_citations", 0)),
            "median_citations": int(row.get("m:median_citations", 0)),
            "total_citations": int(row.get("m:total_citations", 0)),
            "year_range": int(row.get("m:year_range", 0)),
        },
    }]


def hbase_paper_search(
    paper_id: str,
) -> list[dict]:
    row = hbase_conn.get_table("paper_metrics").row(paper_id)

    if not row:
        return []

    total_citations = int(row.get("m:total_citations", 0))

    return [{
        "id": paper_id,
        "title": f"Paper: {paper_id}",
        "content": f"Total citations: {total_citations} | First cited: {row.get('m:first_cited_year', 'N/A')} | Last cited: {row.get('m:last_cited_year', 'N/A')}",
        "abstract": None,
        "authors": [],
        "year": None,
        "venue": None,
        "keywords": [],
        "source": "hbase",
        "score": total_citations / 100.0,
        "metadata": {
            "total_citations": total_citations,
            "first_cited_year": row.get("m:first_cited_year"),
            "last_cited_year": row.get("m:last_cited_year"),
            "citing_papers_count": int(row.get("m:citing_papers_count", 0)),
        },
    }]


def hbase_institution_search(
    query: str,
    top_k: int = 10,
) -> list[dict]:
    institution_id = query.strip()
    row = hbase_conn.get_table("institution_metrics").row(institution_id)

    if not row:
        return []

    return [{
        "id": f"institution:{institution_id}",
        "title": f"Institution: {institution_id}",
        "content": f"Authors: {row.get('m:author_count', 0)} | Papers: {row.get('m:paper_count', 0)} | Avg citations: {row.get('m:avg_citations', 0)}",
        "abstract": None,
        "authors": [],
        "year": None,
        "venue": institution_id,
        "keywords": [],
        "source": "hbase",
        "score": float(row.get("m:author_count", 0)) / 100.0,
        "metadata": {
            "author_count": int(row.get("m:author_count", 0)),
            "paper_count": int(row.get("m:paper_count", 0)),
            "total_citations": int(row.get("m:total_citations", 0)),
            "avg_citations": row.get("m:avg_citations"),
        },
    }]


def hbase_top_authors_search(
    top_k: int = 10,
) -> list[dict]:
    from sqlalchemy import create_engine, text
    from app.config import settings

    engine = create_engine(settings.mssql.connection_url, pool_pre_ping=True)
    results = []

    try:
        with engine.connect() as conn:
            # Get author paper counts and join with citation counts
            sql = text("""
                SELECT TOP 100
                    pa.author_id,
                    COUNT(pa.paper_id) AS paper_count,
                    COALESCE(SUM(cite.citation_count), 0) AS total_citations,
                    MIN(p.year) AS first_year,
                    MAX(p.year) AS last_year
                FROM paper_authors pa
                JOIN papers p ON pa.paper_id = p.id
                LEFT JOIN (
                    SELECT referenced_paper_id, COUNT(*) AS citation_count
                    FROM paper_references
                    WHERE referenced_paper_id IS NOT NULL
                    GROUP BY referenced_paper_id
                ) cite ON p.id = cite.referenced_paper_id
                GROUP BY pa.author_id
                ORDER BY paper_count DESC
            """)
            result = conn.execute(sql)

            author_citations = {}
            for row in result:
                author_citations[row[0]] = {
                    "paper_count": row[1],
                    "total_citations": row[2],
                    "first_year": row[3],
                    "last_year": row[4],
                }

            # Calculate h-index for each author
            for author_id, data in author_citations.items():
                paper_count = data["paper_count"]
                total_citations = data["total_citations"]
                # Simplified h-index
                h_index = min(paper_count, max(
                    1, total_citations // max(1, paper_count)))
                data["h_index"] = h_index

            # Sort by h-index and take top K
            sorted_authors = sorted(author_citations.items(
            ), key=lambda x: x[1]["h_index"], reverse=True)

            for author_id, data in sorted_authors[:top_k]:
                results.append({
                    "id": f"author:{author_id}",
                    "title": f"Author: {author_id[:20]}...",
                    "content": f"H-index: {data['h_index']} | Papers: {data['paper_count']} | Citations: {data['total_citations']}",
                    "abstract": None,
                    "authors": [author_id],
                    "year": data.get("last_year"),
                    "venue": None,
                    "keywords": [],
                    "source": "hbase",
                    "score": data["h_index"] / 10.0,
                    "metadata": {
                        "h_index": data["h_index"],
                        "paper_count": data["paper_count"],
                        "total_citations": data["total_citations"],
                        "first_year": str(data["first_year"]) if data["first_year"] else None,
                        "last_year": str(data["last_year"]) if data["last_year"] else None,
                    },
                })
    except Exception as e:
        print(f"Error in top_authors: {e}")
    finally:
        engine.dispose()

    return results


def hbase_trending_keywords_search(
    top_k: int = 20,
) -> list[dict]:
    from sqlalchemy import create_engine, text
    from app.config import settings

    engine = create_engine(settings.mssql.connection_url, pool_pre_ping=True)
    results = []

    try:
        with engine.connect() as conn:
            # Get keywords with their paper counts and avg year
            sql = text("""
                SELECT TOP 50
                    p.keywords,
                    COUNT(*) AS paper_count,
                    AVG(CAST(p.year AS FLOAT)) AS avg_year
                FROM papers p
                WHERE p.keywords IS NOT NULL 
                    AND p.keywords != '[]'
                    AND p.year IS NOT NULL
                GROUP BY p.keywords
                HAVING COUNT(*) > 1
                ORDER BY avg_year DESC, paper_count DESC
            """)
            result = conn.execute(sql)

            import json
            for row in result:
                try:
                    keywords = json.loads(row[0])
                    paper_count = row[1]
                    avg_year = int(row[2]) if row[2] else 0

                    for kw in keywords[:5]:  # Take first 5 keywords
                        kw_normalized = kw.lower().strip()
                        if kw_normalized:
                            results.append({
                                "id": f"keyword:{kw_normalized}",
                                "title": f"Keyword: {kw_normalized}",
                                "content": f"Papers: {paper_count} | Avg Year: {avg_year}",
                                "abstract": None,
                                "authors": [],
                                "year": avg_year,
                                "venue": None,
                                "keywords": [kw_normalized],
                                "source": "hbase",
                                "score": paper_count / 100.0,
                                "metadata": {
                                    "paper_count": paper_count,
                                    "avg_year": str(avg_year),
                                },
                            })
                except (json.JSONDecodeError, TypeError):
                    continue
    except Exception as e:
        print(f"Error in trending: {e}")
    finally:
        engine.dispose()

    # Deduplicate by keyword and take top K
    seen = set()
    unique_results = []
    for r in results:
        kw = r["id"].split(":")[1]
        if kw not in seen:
            seen.add(kw)
            unique_results.append(r)

    return unique_results[:top_k]


def hbase_paper_stats_search(
    paper_id: str,
) -> list[dict]:
    if not paper_id:
        return []

    # Get from HBase metrics table
    metrics_table = hbase_conn.get_table("paper_metrics")
    row = metrics_table.row(paper_id)

    if not row:
        return []

    # Get velocity from citation velocity table
    velocity_table = hbase_conn.get_table("paper_citation_velocity")
    vel_row = velocity_table.row(paper_id)

    # Parse velocity data (citations_YYYY columns)
    velocity_data = {}
    for col, val in vel_row.items():
        if col.startswith("m:citations_"):
            year = col.replace("m:citations_", "")
            velocity_data[year] = val

    total_citations = int(row.get("m:total_citations", 0))

    return [{
        "id": paper_id,
        "title": f"Paper Stats: {paper_id[:20]}...",
        "content": f"Total citations: {total_citations} | First: {row.get('m:first_cited_year', 'N/A')} | Last: {row.get('m:last_cited_year', 'N/A')}",
        "abstract": None,
        "authors": [],
        "year": None,
        "venue": None,
        "keywords": list(velocity_data.keys()),
        "source": "hbase",
        "score": total_citations / 100.0,
        "metadata": {
            "total_citations": total_citations,
            "first_cited_year": row.get("m:first_cited_year"),
            "last_cited_year": row.get("m:last_cited_year"),
            "citing_papers_count": int(row.get("m:citing_papers_count", 0)),
            "citation_velocity": velocity_data,
        },
    }]


def hbase_venue_stats_search(
    query: str,
    top_k: int = 10,
) -> list[dict]:
    venue_id = query.strip()
    row = hbase_conn.get_table("venue_metrics").row(venue_id)

    if not row:
        return []

    return [{
        "id": f"venue:{venue_id}",
        "title": f"Venue: {venue_id}",
        "content": f"Papers: {row.get('m:paper_count', 0)} | Avg: {row.get('m:avg_citations', 0)} | Median: {row.get('m:median_citations', 0)}",
        "abstract": None,
        "authors": [],
        "year": int(row.get("m:top_year", 0)) if row.get("m:top_year") else None,
        "venue": venue_id,
        "keywords": [],
        "source": "hbase",
        "score": float(row.get("m:paper_count", 0)) / 100.0,
        "metadata": {
            "paper_count": int(row.get("m:paper_count", 0)),
            "avg_citations": int(row.get("m:avg_citations", 0)),
            "median_citations": int(row.get("m:median_citations", 0)),
            "total_citations": int(row.get("m:total_citations", 0)),
            "year_range": int(row.get("m:year_range", 0)),
        },
    }]


def hbase_search(
    query: str | None = None,
    search_type: str = "keyword",
    top_k: int = 10,
    filters: Optional[dict] = None,
    min_score: Optional[float] = None,
) -> list[dict]:
    if search_type == "top_authors":
        return hbase_top_authors_search(top_k)
    elif search_type == "trending":
        return hbase_trending_keywords_search(top_k)
    elif search_type == "paper_stats":
        if not query:
            return []
        return hbase_paper_stats_search(query)
    elif search_type == "venue_stats":
        if not query:
            return []
        return hbase_venue_stats_search(query, top_k)
    elif search_type == "keyword":
        return hbase_keyword_search(query or "", top_k)
    elif search_type == "author":
        return hbase_author_search(query or "", top_k)
    elif search_type == "venue":
        return hbase_venue_search(query or "", top_k)
    elif search_type == "paper":
        if not query:
            return []
        return hbase_paper_search(query)
    elif search_type == "institution":
        return hbase_institution_search(query or "", top_k)
    else:
        return hbase_keyword_search(query or "", top_k)
