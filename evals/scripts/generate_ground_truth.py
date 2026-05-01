"""
Generate ground truth dataset for retrieval evaluation.
Samples papers from MSSQL and creates query-relevant_paper_id pairs.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.db.mssql import engine
from sqlalchemy import text


def generate_ground_truth(output_path: str, num_queries: int = 50):
    """Generate ground truth dataset by sampling papers from DB."""

    with engine.connect() as conn:
        # Sample papers with meaningful titles/abstracts
        # Join with venues table to get venue name
        result = conn.execute(
            text(f"""
                SELECT TOP {num_queries} p.id, p.title, p.abstract, p.year, v.name as venue
                FROM papers p
                LEFT JOIN venues v ON p.venue_id = v.id
                WHERE p.title IS NOT NULL AND LEN(p.title) > 10
                ORDER BY NEWID()
            """)
        )

        papers = result.fetchall()

    dataset = []
    for i, row in enumerate(papers):
        paper_id, title, abstract, year, venue = row

        # Create query from title (truncate to ~10 words for realistic query)
        query_words = title.split()[:10]
        query = " ".join(query_words).strip(".,;:")

        # Also create a variant using abstract if available
        if abstract and len(abstract) > 50:
            # Use first sentence or first 100 chars of abstract
            abs_query = abstract.split(".")[0][:100].strip()
            query = abs_query if len(abs_query) > len(query) else query

        dataset.append({
            "id": i + 1,
            "query": query,
            "relevant_paper_ids": [paper_id],
            "relevant_titles": [title],
            "metadata": {
                "year": year,
                "venue": venue,
                "authors": []
            }
        })

    with open(output_path, "w") as f:
        json.dump(dataset, f, indent=2)

    print(f"Generated {len(dataset)} ground truth queries -> {output_path}")
    return dataset


if __name__ == "__main__":
    output = Path(__file__).parent.parent / "datasets" / "ground_truth.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    generate_ground_truth(str(output), num_queries=50)
