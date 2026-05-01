"""
Retrieval accuracy evaluation using DeepEval and standard IR metrics.
Evaluates the /semantic-search endpoint.
"""
import json
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from deepeval import evaluate
from deepeval.metrics import ContextualPrecisionMetric, ContextualRecallMetric
from deepeval.test_case import LLMTestCase
from fastapi.testclient import TestClient

from app.main import app
from app.api.dto.search_dto import SearchRequestDTO, SearchResponseDTO

client = TestClient(app)

GROUND_TRUTH_PATH = Path(__file__).parent.parent / "datasets" / "ground_truth.json"
RESULTS_PATH = Path(__file__).parent.parent / "results" / "retrieval_accuracy.json"


def load_ground_truth():
    with open(GROUND_TRUTH_PATH) as f:
        return json.load(f)


def search_semantic(query: str, top_k: int = 10, use_reranker: bool = False):
    """Call /semantic-search endpoint."""
    response = client.post(
        "/semantic-search",
        json={"query": query, "top_k": top_k, "use_reranker": use_reranker}
    )
    if response.status_code != 200:
        print(f"Error: {response.status_code} - {response.text}")
        return None
    return SearchResponseDTO(**response.json())


def calculate_ir_metrics(relevant_ids: list, retrieved_ids: list, k_values: list = [5, 10]):
    """Calculate standard IR metrics."""
    metrics = {}

    for k in k_values:
        retrieved_k = retrieved_ids[:k]
        relevant_set = set(relevant_ids)

        # Precision@k
        if len(retrieved_k) > 0:
            precision_k = len(set(retrieved_k) & relevant_set) / len(retrieved_k)
        else:
            precision_k = 0.0
        metrics[f"precision@{k}"] = precision_k

        # Recall@k
        if len(relevant_set) > 0:
            recall_k = len(set(retrieved_k) & relevant_set) / len(relevant_set)
        else:
            recall_k = 0.0
        metrics[f"recall@{k}"] = recall_k

    # MRR@10
    mrr = 0.0
    for i, pid in enumerate(retrieved_ids[:10]):
        if pid in relevant_ids:
            mrr = 1.0 / (i + 1)
            break
    metrics["mrr@10"] = mrr

    # NDCG@10
    ndcg = 0.0
    dcg = 0.0
    idcg = 0.0
    for i, pid in enumerate(retrieved_ids[:10]):
        if pid in relevant_ids:
            dcg += 1.0 / (i + 1)
    for i in range(min(len(relevant_ids), 10)):
        idcg += 1.0 / (i + 1)
    if idcg > 0:
        ndcg = dcg / idcg
    metrics["ndcg@10"] = ndcg

    return metrics


def evaluate_with_deepeval(test_cases):
    """Run DeepEval metrics."""
    try:
        precision_metric = ContextualPrecisionMetric()
        recall_metric = ContextualRecallMetric()

        for tc in test_cases:
            tc.metrics = [precision_metric, recall_metric]

        results = evaluate(test_cases, show_indicator=False)

        # Extract aggregate scores
        precisions = [tc.metrics[0].score for tc in test_cases if tc.metrics[0].score is not None]
        recalls = [tc.metrics[1].score for tc in test_cases if tc.metrics[1].score is not None]

        return {
            "deepeval_contextual_precision": sum(precisions) / len(precisions) if precisions else 0.0,
            "deepeval_contextual_recall": sum(recalls) / len(recalls) if recalls else 0.0,
        }
    except Exception as e:
        print(f"DeepEval evaluation failed: {e}")
        return {}


def main():
    print("Loading ground truth dataset...")
    ground_truth = load_ground_truth()
    print(f"Loaded {len(ground_truth)} queries")

    results = []
    all_ir_metrics = defaultdict(list)
    deepeval_cases = []

    for i, item in enumerate(ground_truth):
        query = item["query"]
        relevant_ids = item["relevant_paper_ids"]

        print(f"[{i+1}/{len(ground_truth)}] Query: {query[:50]}...")

        # Search without reranker
        search_result = search_semantic(query, top_k=10, use_reranker=False)

        if search_result is None:
            continue

        retrieved_ids = [r.id for r in search_result.results]
        retrieved_contexts = [f"{r.title} {r.abstract or ''}" for r in search_result.results]

        # IR metrics
        ir_metrics = calculate_ir_metrics(relevant_ids, retrieved_ids)
        for k, v in ir_metrics.items():
            all_ir_metrics[k].append(v)

        # DeepEval test case
        tc = LLMTestCase(
            input=query,
            actual_output=f"Found {len(retrieved_ids)} papers",
            expected_output=f"Should find papers with IDs: {relevant_ids}",
            retrieval_context=retrieved_contexts,
        )
        deepeval_cases.append(tc)

        results.append({
            "query_id": item["id"],
            "query": query,
            "relevant_paper_ids": relevant_ids,
            "retrieved_ids": retrieved_ids,
            "latency_ms": search_result.latency_ms,
            "ir_metrics": ir_metrics,
        })

    # Aggregate IR metrics
    aggregated_ir = {k: sum(v) / len(v) for k, v in all_ir_metrics.items()}

    # DeepEval evaluation
    print("Running DeepEval metrics...")
    deepeval_results = evaluate_with_deepeval(deepeval_cases)

    final_results = {
        "total_queries": len(results),
        "aggregate_ir_metrics": aggregated_ir,
        "deepeval_metrics": deepeval_results,
        "per_query_results": results,
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(final_results, f, indent=2)

    print(f"\nResults saved to {RESULTS_PATH}")
    print("\n=== Aggregate IR Metrics ===")
    for k, v in aggregated_ir.items():
        print(f"  {k}: {v:.4f}")
    if deepeval_results:
        print("\n=== DeepEval Metrics ===")
        for k, v in deepeval_results.items():
            print(f"  {k}: {v:.4f}")


if __name__ == "__main__":
    main()
