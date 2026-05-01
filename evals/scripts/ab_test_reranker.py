"""
Reranker A/B testing: compare search results with and without reranker.
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from app.api.dto.search_dto import SearchRequestDTO, SearchResponseDTO

client = TestClient(app)

GROUND_TRUTH_PATH = Path(__file__).parent.parent / "datasets" / "ground_truth.json"
RESULTS_PATH = Path(__file__).parent.parent / "results" / "reranker_ab_test.json"


def search_semantic(query: str, top_k: int = 10, use_reranker: bool = False):
    """Call /semantic-search endpoint."""
    response = client.post(
        "/semantic-search",
        json={"query": query, "top_k": top_k, "use_reranker": use_reranker}
    )
    if response.status_code != 200:
        return None
    return SearchResponseDTO(**response.json())


def calculate_metrics(relevant_ids: list, retrieved_ids: list):
    """Calculate comparison metrics."""
    relevant_set = set(relevant_ids)
    metrics = {}

    # NDCG@10
    dcg = 0.0
    idcg = 0.0
    for i, pid in enumerate(retrieved_ids[:10]):
        if pid in relevant_ids:
            dcg += 1.0 / (i + 1)
    for i in range(min(len(relevant_ids), 10)):
        idcg += 1.0 / (i + 1)
    metrics["ndcg@10"] = dcg / idcg if idcg > 0 else 0.0

    # MRR@10
    mrr = 0.0
    for i, pid in enumerate(retrieved_ids[:10]):
        if pid in relevant_ids:
            mrr = 1.0 / (i + 1)
            break
    metrics["mrr@10"] = mrr

    # Precision@10
    if len(retrieved_ids) > 0:
        metrics["precision@10"] = len(set(retrieved_ids) & relevant_set) / len(retrieved_ids[:10])
    else:
        metrics["precision@10"] = 0.0

    return metrics


def main():
    print("Loading ground truth dataset...")
    with open(GROUND_TRUTH_PATH) as f:
        ground_truth = json.load(f)
    print(f"Loaded {len(ground_truth)} queries")

    results = []
    group_a_metrics = defaultdict(list)  # Without reranker
    group_b_metrics = defaultdict(list)  # With reranker
    latency_diffs = []

    for i, item in enumerate(ground_truth):
        query = item["query"]
        relevant_ids = item["relevant_paper_ids"]

        print(f"[{i+1}/{len(ground_truth)}] Query: {query[:50]}...")

        # Group A: Without reranker
        result_a = search_semantic(query, top_k=10, use_reranker=False)
        if result_a is None:
            continue

        # Group B: With reranker
        result_b = search_semantic(query, top_k=10, use_reranker=True)

        retrieved_a = [r.id for r in result_a.results]
        retrieved_b = [r.id for r in result_b.results] if result_b else []

        metrics_a = calculate_metrics(relevant_ids, retrieved_a)
        metrics_b = calculate_metrics(relevant_ids, retrieved_b) if result_b else {}

        for k, v in metrics_a.items():
            group_a_metrics[k].append(v)
        if metrics_b:
            for k, v in metrics_b.items():
                group_b_metrics[k].append(v)

        # Latency comparison
        if result_b:
            latency_diff = result_b.latency_ms - result_a.latency_ms
            latency_diffs.append(latency_diff)

        results.append({
            "query_id": item["id"],
            "query": query,
            "relevant_paper_ids": relevant_ids,
            "group_a": {
                "retrieved_ids": retrieved_a,
                "metrics": metrics_a,
                "latency_ms": result_a.latency_ms,
            },
            "group_b": {
                "retrieved_ids": retrieved_b,
                "metrics": metrics_b,
                "latency_ms": result_b.latency_ms if result_b else None,
            } if result_b else None,
        })

    # Aggregate results
    aggregated_a = {k: sum(v) / len(v) for k, v in group_a_metrics.items()}
    aggregated_b = {k: sum(v) / len(v) for k, v in group_b_metrics.items()} if group_b_metrics else {}

    avg_latency_diff = sum(latency_diffs) / len(latency_diffs) if latency_diffs else 0.0

    # Overlap (Jaccard) between A and B results
    overlaps = []
    for r in results:
        if r["group_b"]:
            set_a = set(r["group_a"]["retrieved_ids"])
            set_b = set(r["group_b"]["retrieved_ids"])
            if set_a or set_b:
                jaccard = len(set_a & set_b) / len(set_a | set_b)
                overlaps.append(jaccard)

    final_results = {
        "total_queries": len(results),
        "group_a_no_reranker": aggregated_a,
        "group_b_with_reranker": aggregated_b,
        "avg_latency_increase_ms": avg_latency_diff,
        "avg_result_overlap_jaccard": sum(overlaps) / len(overlaps) if overlaps else 0.0,
        "improvements": {
            k: aggregated_b[k] - aggregated_a[k] for k in aggregated_b if k in aggregated_a
        } if aggregated_b else {},
        "per_query_results": results,
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(final_results, f, indent=2)

    print(f"\nResults saved to {RESULTS_PATH}")
    print("\n=== Group A (No Reranker) ===")
    for k, v in aggregated_a.items():
        print(f"  {k}: {v:.4f}")
    print("\n=== Group B (With Reranker) ===")
    for k, v in aggregated_b.items():
        print(f"  {k}: {v:.4f}")
    print("\n=== Improvements (B - A) ===")
    if final_results["improvements"]:
        for k, v in final_results["improvements"].items():
            print(f"  {k}: {v:+.4f}")
    print(f"\nAverage latency increase: {avg_latency_diff:.2f} ms")
    print(f"Average result overlap (Jaccard): {final_results['avg_result_overlap_jaccard']:.4f}")


if __name__ == "__main__":
    main()
