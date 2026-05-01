import logging
from typing import Optional

logger = logging.getLogger(__name__)

_models = {}


def get_reranker_model(model_name: str = "BAAI/bge-reranker-base"):
    """
    Get or load the reranker model.
    
    Uses sentence-transformers with a cross-encoder for reranking.
    Falls back to None if model not available.
    """
    if model_name in _models:
        return _models[model_name]
    
    try:
        from sentence_transformers import CrossEncoder
        
        logger.info(f"[Reranker] Loading model: {model_name}")
        model = CrossEncoder(model_name, max_length=512)
        _models[model_name] = model
        logger.info(f"[Reranker] Model loaded successfully")
        return model
    except Exception as e:
        logger.warning(f"[Reranker] Failed to load model '{model_name}': {e}")
        return None


def rerank_results(
    query: str,
    results: list[dict],
    model_name: str = "BAAI/bge-reranker-base",
    top_k: Optional[int] = None,
) -> list[dict]:
    """
    Rerank search results using BGE cross-encoder.
    
    Args:
        query: Original search query
        results: List of search results to rerank
        model_name: Model identifier for reranker
        top_k: Limit reranked results (default: all)
        
    Returns:
        Reranked results with updated scores
    """
    if not results:
        logger.info("[Reranker] No results to rerank")
        return results
    
    if top_k is None:
        top_k = len(results)
    
    model = get_reranker_model(model_name)
    
    if model is None:
        logger.warning("[Reranker] Model not available, returning original results")
        return results
    
    try:
        logger.info(f"[Reranker] Reranking {len(results)} results for query: '{query}'")
        
        pairs = []
        for result in results:
            title = result.get("title", "")
            abstract = result.get("abstract", "") or ""
            text = f"{title} {abstract}".strip()
            pairs.append([query, text])
        
        scores = model.predict(pairs)
        
        reranked = []
        for i, (result, score) in enumerate(zip(results, scores)):
            r = result.copy()
            r["original_score"] = r.get("score", 0)
            r["rerank_score"] = float(score)
            r["score"] = float(score)
            reranked.append(r)
        
        reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
        
        final_results = reranked[:top_k]
        
        logger.info(f"[Reranker] Returning {len(final_results)} reranked results")
        
        return final_results
        
    except Exception as e:
        logger.error(f"[Reranker] Reranking failed: {e}")
        return results
