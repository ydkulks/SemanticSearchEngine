import logging
from functools import lru_cache
from sentence_transformers import SentenceTransformer

from app.config import settings

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def get_embedder_model() -> SentenceTransformer:
    global _model
    if _model is None:
        model_name = getattr(settings, "EMBEDDER_MODEL", "all-MiniLM-L6-v2")
        logger.info(f"Loading embedder model: {model_name}")
        _model = SentenceTransformer(model_name)
        logger.info(f"Embedder model loaded. Dimension: {_model.get_embedding_dimension()}")
    return _model


def encode_query(query: str) -> list[float]:
    model = get_embedder_model()
    embedding = model.encode(query, show_progress_bar=False)
    return embedding.tolist()
