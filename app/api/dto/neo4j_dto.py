from typing import Optional
from pydantic import BaseModel, Field


class Neo4jSearchRequestDTO(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    search_type: str = Field(default="citations", description="citations|cited_by|coauthors|related|papers")
    top_k: int = Field(default=10, ge=1, le=100)


class Neo4jPaperResultDTO(BaseModel):
    id: str
    title: str
    abstract: Optional[str] = None
    authors: list[str] = []
    year: Optional[int] = None
    venue: Optional[str] = None
    keywords: list[str] = []
    source: str = "neo4j"
    relationship_type: str
    score: float = 0.0


class Neo4jSearchResponseDTO(BaseModel):
    query: str
    search_type: str
    results: list[Neo4jPaperResultDTO]
    total: int
    latency_ms: float
    sources_queried: list[str]