from typing import Optional


def hbase_search(
    query: str,
    top_k: int = 10,
    filters: Optional[dict] = None,
    min_score: Optional[float] = None,
) -> list[dict]:
    return []
