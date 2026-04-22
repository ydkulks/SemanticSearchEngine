from typing import Optional
from pydantic import BaseModel, Field


class MSSQLSearchRequestDTO(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=100)
    filters: Optional[dict] = None
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class MSSQLPaperResultDTO(BaseModel):
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


class MSSQLSearchResponseDTO(BaseModel):
    query: str
    results: list[MSSQLPaperResultDTO]
    total: int
    latency_ms: float
    sources_queried: list[str]