import logging
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


def rrf_fusion(
    results_list: list[list[dict]],
    k: int = 60,
    min_score: Optional[float] = None,
) -> list[dict]:
    """
    Apply Reciprocal Rank Fusion to combine results from multiple sources.
    
    Args:
        results_list: List of result lists from different sources
        k: RRF parameter (default 60)
        min_score: Optional minimum score threshold
        
    Returns:
        Fused and re-ranked results
    """
    logger.info(f"[RRF] Starting fusion. Input has {len(results_list)} source result lists")
    for i, src_results in enumerate(results_list):
        logger.info(f"[RRF] Source {i}: {len(src_results)} results")
    
    if not results_list:
        logger.warning("[RRF] Empty results_list, returning empty")
        return []
    
    doc_scores: dict[str, float] = defaultdict(float)
    doc_data: dict[str, dict] = {}
    
    for source_results in results_list:
        for rank, paper in enumerate(source_results, start=1):
            paper_id = paper.get("id")
            if not paper_id:
                continue
                
            doc_scores[paper_id] += 1 / (k + rank)
            
            if paper_id not in doc_data:
                doc_data[paper_id] = paper.copy()
    
    logger.info(f"[RRF] After processing: {len(doc_scores)} unique documents")
    
    fused = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    
    results = []
    seen_sources: dict[str, set] = defaultdict(set)
    
    logger.info(f"[RRF] Filtering with min_score={min_score}")
    
    for paper_id, score in fused:
        logger.info(f"[RRF] Checking paper_id={paper_id}, score={score}, min_score={min_score}")
        if min_score is not None and score < min_score:
            logger.info(f"[RRF] Skipping {paper_id} due to min_score filter")
            continue
            
        result = doc_data[paper_id].copy()
        result["score"] = score
        seen_sources[paper_id].add(result.get("source", "unknown"))
        result["sources"] = list(seen_sources[paper_id])
        result["fusion_sources"] = list(seen_sources[paper_id])
        
        results.append(result)
    
    logger.info(f"[RRF] Returning {len(results)} fused results")
    
    return results


def rrf_fusion_with_sources(
    results_by_source: dict[str, list[dict]],
    k: int = 60,
    top_k: int = 10,
    min_score: Optional[float] = None,
) -> tuple[list[dict], list[str]]:
    """
    Apply RRF fusion with source tracking.
    
    Args:
        results_by_source: Dict mapping source name to results
        k: RRF parameter
        top_k: Number of results to return
        min_score: Minimum score threshold
        
    Returns:
        Tuple of (fused results, list of successful sources)
    """
    results_list = list(results_by_source.values())
    successful_sources = [src for src, results in results_by_source.items() if results]
    
    fused = rrf_fusion(results_list, k=k, min_score=min_score)
    
    return fused[:top_k], successful_sources