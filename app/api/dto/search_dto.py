from typing import Optional
from pydantic import BaseModel, Field


class SearchRequestDTO(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=100)
    use_reranker: bool = True
    filters: Optional[dict] = None
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class PaperResultDTO(BaseModel):
    id: str
    title: str
    abstract: Optional[str] = None
    authors: list[str] = []
    year: Optional[int] = None
    venue: Optional[str] = None
    keywords: list[str] = []
    source: str = "mssql"
    score: float
    metadata: Optional[dict] = None


class SearchResponseDTO(BaseModel):
    query: str
    results: list[PaperResultDTO]
    total: int
    latency_ms: float
    sources_queried: list[str]


class HealthResponseDTO(BaseModel):
    status: str
    mssql: str
    neo4j: Optional[str] = None


class SourceInfoDTO(BaseModel):
    name: str
    type: str
    status: str


class SourcesResponseDTO(BaseModel):
    sources: list[SourceInfoDTO]
