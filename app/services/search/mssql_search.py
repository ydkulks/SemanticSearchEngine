from typing import Optional


def mssql_vector_search(
    db,
    query: str,
    top_k: int = 10,
    filters: Optional[dict] = None,
    min_score: Optional[float] = None,
) -> list[dict]:
    from sqlalchemy import text
    import json

    filter_conditions = ""
    params = {"query": query, "top_k": top_k}

    if filters:
        for key, value in filters.items():
            filter_conditions += f""" AND JSON_VALUE(d.metadata, '$.{key}') = :
                            {key}"""
            params[key] = str(value)

    if min_score is not None:
        filter_conditions += " AND score >= :min_score"
        params["min_score"] = min_score

    sql = f"""
        SELECT TOP ({top_k})
            d.id,
            d.content,
            d.title,
            d.metadata,
            0.0 as score
        FROM documents d
        WHERE 1=1 {filter_conditions}
        ORDER BY d.created_at DESC
    """

    result = db.execute(text(sql), params)
    rows = result.fetchall()

    results = []
    for row in rows:
        metadata = row.metadata
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception as e:
                print(f"Error parsing metadata: {e}")
                metadata = {}

        results.append(
            {
                "id": row.id,
                "content": row.content,
                "source": "mssql",
                "score": row.score,
                "metadata": metadata or {},
            }
        )

    return results
