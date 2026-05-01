"""
Latency benchmarks for /semantic-search endpoint.
Tests various configurations and reports p50/p90/p99 latencies.
"""
import json
import sys
import time
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

RESULTS_PATH = Path(__file__).parent.parent / "results" / "latency_benchmark.json"

# Sample queries for benchmarking
BENCHMARK_QUERIES = [
    "machine learning algorithms",
    "deep neural networks",
    "natural language processing",
    "computer vision applications",
    "data mining techniques",
    "reinforcement learning methods",
    "graph neural networks",
    "transformer models attention",
    "information retrieval systems",
    "semantic search engines",
]

NUM_REQUESTS_PER_CONFIG = 20


def benchmark_config(query: str, top_k: int, use_reranker: bool) -> float:
    """Run single request and return latency in ms."""
    start = time.time()
    response = client.post(
        "/semantic-search",
        json={"query": query, "top_k": top_k, "use_reranker": use_reranker}
    )
    elapsed = (time.time() - start) * 1000
    return elapsed


def percentile(data, pct):
    """Calculate percentile."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * pct / 100
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    if f == c:
        return sorted_data[f]
    return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


def main():
    configs = [
        {"top_k": 5, "use_reranker": False},
        {"top_k": 10, "use_reranker": False},
        {"top_k": 20, "use_reranker": False},
        {"top_k": 5, "use_reranker": True},
        {"top_k": 10, "use_reranker": True},
        {"top_k": 20, "use_reranker": True},
    ]

    all_results = {}

    for config in configs:
        config_name = f"top_k={config['top_k']}, reranker={config['use_reranker']}"
        print(f"\nBenchmarking: {config_name}")

        latencies = []
        for i in range(NUM_REQUESTS_PER_CONFIG):
            query = BENCHMARK_QUERIES[i % len(BENCHMARK_QUERIES)]
            lat = benchmark_config(query, config["top_k"], config["use_reranker"])
            latencies.append(lat)
            print(f"  Request {i+1}/{NUM_REQUESTS_PER_CONFIG}: {lat:.2f} ms")

        all_results[config_name] = {
            "config": config,
            "num_requests": len(latencies),
            "latencies_ms": latencies,
            "p50": percentile(latencies, 50),
            "p90": percentile(latencies, 90),
            "p99": percentile(latencies, 99),
            "avg": sum(latencies) / len(latencies),
            "min": min(latencies),
            "max": max(latencies),
        }

        print(f"  p50: {all_results[config_name]['p50']:.2f} ms")
        print(f"  p90: {all_results[config_name]['p90']:.2f} ms")
        print(f"  p99: {all_results[config_name]['p99']:.2f} ms")
        print(f"  avg: {all_results[config_name]['avg']:.2f} ms")

    # Calculate reranker overhead
    reranker_overhead = {}
    for top_k in [5, 10, 20]:
        key_no = f"top_k={top_k}, reranker=False"
        key_yes = f"top_k={top_k}, reranker=True"
        if key_no in all_results and key_yes in all_results:
            overhead = all_results[key_yes]["avg"] - all_results[key_no]["avg"]
            reranker_overhead[f"top_k={top_k}"] = {
                "avg_latency_no_reranker": all_results[key_no]["avg"],
                "avg_latency_with_reranker": all_results[key_yes]["avg"],
                "overhead_ms": overhead,
                "overhead_pct": (overhead / all_results[key_no]["avg"] * 100) if all_results[key_no]["avg"] > 0 else 0,
            }

    final_results = {
        "configs": all_results,
        "reranker_overhead": reranker_overhead,
        "summary": {
            "fastest_config": min(all_results.items(), key=lambda x: x[1]["avg"])[0],
            "slowest_config": max(all_results.items(), key=lambda x: x[1]["avg"])[0],
        }
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w") as f:
        json.dump(final_results, f, indent=2)

    print(f"\n\nResults saved to {RESULTS_PATH}")
    print("\n=== Reranker Overhead ===")
    for k, v in reranker_overhead.items():
        print(f"  {k}: +{v['overhead_ms']:.2f} ms ({v['overhead_pct']:.1f}%)")


if __name__ == "__main__":
    main()
