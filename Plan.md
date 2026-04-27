# **Semantic Search Engine — Project Plan**

## **Dataset: DBLP Academic Citation Network**

This project uses the DBLP (Digital Bibliography & Library Project) citation network dataset containing ~619K academic papers with:

- Paper metadata (title, abstract, year, venue)
- Author information (name, affiliation)
- Citation relationships
- Co-authorship networks

## **System Architecture**

```mermaid
---
config:
  look: neo
  theme: default
  layout: fixed
---
flowchart TB
 subgraph subGraph0["Client Layer"]
        Client["Client Application"]
  end
 subgraph subGraph1["API Gateway"]
        FastAPI["FastAPI Service<br>/search, /health"]
  end
 subgraph subGraph2["Search Orchestration"]
        Router["Query Router"]
        RRF["RRF Merger"]
        Reranker["BGE Reranker"]
  end
 subgraph subGraph3["Data Stores"]
        MSSQL[("MSSQL<br>Papers")]
        Neo4j[("Neo4j<br>Citation Graph")]
        HBase[("HBase<br>Metrics")]
  end
 subgraph subGraph4["Embedding Services"]
        Embedder["Embedding Model<br>BGE-M3"]
  end
    FastAPI --> Router
    Router --> MSSQL & Neo4j & HBase
    Router <--> Embedder
    MSSQL --> RRF
    Neo4j --> RRF
    HBase --> RRF
    RRF -- "top-K" --> Reranker
    Reranker -- final search response --> FastAPI
    subGraph0 --> subGraph1
```

## **Data Flow**

```mermaid
---
config:
  theme: default
  look: classic
---
sequenceDiagram

    participant Client
    participant FastAPI
    participant Router
    participant MSSQL
    participant Neo4j
    participant HBase
    participant Embedder
    participant RRF
    participant Reranker

    Client->>FastAPI: POST /search {query}
    FastAPI->>Embedder: Generate query embedding
    Embedder-->>FastAPI: query_vector[1536]
    FastAPI->>Router: Distribute search
    par Parallel Execution
        Router->>MSSQL: Full-text + vector search
        Router->>Neo4j: Citation path search
        Router->>HBase: Metrics query
    end

    MSSQL-->>RRF: Top-K papers
    Neo4j-->>RRF: Top-K papers
    HBase-->>RRF: Top-K papers

    RRF->>RRF: Apply Reciprocal Rank Fusion
    RRF-->>Reranker: Merged top-50 results
    alt Reranking Enabled
        Reranker->>Reranker: BGE cross-encoder rerank
    end
    Reranker-->>FastAPI: Final ranked results
    FastAPI-->>Client: JSON response
```

## **Database Distribution**

| Database  | Content                  | Use Case                         |
| --------- | ------------------------ | -------------------------------- |
| **MSSQL** | Papers, Authors, Venues  | Full-text search, filtering      |
| **Neo4j** | Citations, Co-authorship | Graph traversal, recommendations |
| **HBase** | Citation counts, h-index | Metrics, aggregations            |

## **Component Breakdown**

| Component             | Responsibility                              | Tech Stack                     |
| --------------------- | ------------------------------------------- | ------------------------------ |
| **API Gateway**       | HTTP endpoints, auth, rate limiting         | FastAPI + Uvicorn              |
| **Query Router**      | Parse query, route to backends, parallelize | Python asyncio                 |
| **Embedding Service** | Generate text embeddings                    | BGE-M3 / sentence-transformers |
| **MSSQL Paper Store** | Store papers, authors, venues               | MSSQL                          |
| **Neo4j Graph Store** | Citation network, co-authorship             | Neo4j + Cypher                 |
| **HBase Metrics**     | Analytics, citation counts                  | HBase / HappyBase              |
| **RRF Merger**        | Merge ranked results                        | Python                         |
| **BGE Reranker**      | Cross-encoder reranking                     | BGE-reranker-base              |

## **Tech Stack**

| Category          | Technology              | Version  |
| ----------------- | ----------------------- | -------- |
| **API Framework** | FastAPI                 | >= 0.115 |
| **Server**        | Uvicorn                 | >= 0.32  |
| **Database ORM**  | SQLAlchemy              | >= 2.0   |
| **MSSQL Driver**  | pyodbc                  | >= 5.3   |
| **Neo4j Driver**  | neo4j                   | >= 6.1   |
| **Embeddings**    | sentence-transformers   | latest   |
| **Reranker**      | FlagEmbedding           | latest   |
| **Testing**       | pytest, httpx           | latest   |
| **Container**     | Docker + docker-compose | latest   |

---

## **API Design**

### Endpoints

| Method | Endpoint   | Description                |
| ------ | ---------- | -------------------------- |
| `POST` | `/search`  | Semantic search for papers |
| `GET`  | `/sources` | List available sources     |
| `GET`  | `/health`  | Health check               |
| `GET`  | `/docs`    | Swagger UI                 |

### Future Endpoints

| Method | Endpoint        | Description       |
| ------ | --------------- | ----------------- |
| `POST` | `/search/neo4j` | Neo4j-only search |
| `POST` | `/search/hbase` | HBase-only search |

### Request/Response Schemas

```python
# Request
class SearchRequest(BaseModel):
    query: str                           # Natural language query
    top_k: int = Field(default=10, ge=1, le=100)
    use_reranker: bool = True
    filters: dict | None = None          # Optional filters (year, venue)
    min_score: float | None = None     # Minimum similarity threshold

# Response
class PaperResult(BaseModel):
    id: str
    title: str
    abstract: str | None
    authors: list[str]
    year: int | None
    venue: str | None
    keywords: list[str]
    source: str
    score: float
    metadata: dict

class SearchResponse(BaseModel):
    query: str
    results: list[PaperResult]
    total: int
    latency_ms: float
    sources_queried: list[str]
```

### Example Request/Response

```json
// POST /search
{
  "query": "deep learning for healthcare",
  "top_k": 10,
  "filters": {
    "min_year": 2020,
    "venue": "NeurIPS"
  }
}
```

```json
{
  "query": "deep learning for healthcare",
  "results": [
    {
      "id": "53e99784b7602d9701f3ffdd",
      "title": "Deep Learning for Medical Image Analysis",
      "abstract": "We propose a novel...",
      "authors": ["John Doe", "Jane Smith"],
      "year": 2021,
      "venue": "NeurIPS",
      "keywords": ["deep learning", "medical imaging"],
      "source": "mssql",
      "score": 0.95,
      "metadata": {
        "year": 2021,
        "venue": "NeurIPS",
        "authors": ["John Doe", "Jane Smith"]
      }
    }
  ],
  "total": 10,
  "latency_ms": 145,
  "sources_queried": ["mssql"]
}
```

---

## **Ranking Pipeline**

### Reciprocal Rank Fusion (RRF) Formula

```python
def rrf_fusion(results_list: list[list[PaperResult]], k: int = 60) -> list[PaperResult]:
    doc_scores: dict[str, float] = defaultdict(float)

    for results in results_list:
        for rank, paper in enumerate(results, start=1):
            doc_scores[paper.id] += 1 / (k + rank)

    fused = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
    return fused
```

### Ranking Pipeline Flow

```mermaid
graph LR
    subgraph "Parallel Retrieval"
        Q1[Query] --> MSSQL1[MSSQL<br/>Top-20]
        Q1 --> N4J1[Neo4j<br/>Top-20]
        Q1 --> H1[HBase<br/>Top-20]
    end

    subgraph "RRF Merge"
        MSSQL1 --> RRF[RRF Merger<br/>k=60]
        N4J1 --> RRF
        H1 --> RRF
        RRF --> RRF20[Top-50<br/>Merged]
    end

    subgraph "Rerank"
        RRF20 --> RERANK{use_reranker?}
        RERANK -->|Yes| BGE[BGE Reranker<br/>Cross-Encoder]
        BGE --> FINAL[Final Top-K]
        RERANK -->|No| FINAL
    end
```

---

## **Folder Structure**

```text
SemanticSearchEngine/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Pydantic settings
│   ├── dependencies.py            # FastAPI dependencies
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py              # Main API router
│   │   ├── schemas.py             # Request/Response models
│   │   └── endpoints/
│   │       ├── __init__.py
│   │       ├── search.py          # /search endpoints
│   │       └── health.py          # /health endpoint
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── embedding.py           # Embedding generation
│   │   ├── reranker.py           # BGE reranker service
│   │   └── search/
│   │       ├── __init__.py
│   │       ├── router.py          # Query routing
│   │       ├── merger.py          # RRF implementation
│   │       ├── mssql_search.py    # MSSQL paper search
│   │       ├── neo4j_search.py    # Neo4j citation search
│   │       └── hbase_search.py    # HBase metrics search
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   └── database.py            # SQLAlchemy models (Paper, Author, Venue)
│   │
│   └── db/
│       ├── __init__.py
│       └── init.py                # Database initializers
│
├── data/
│   ├── README.md
│   └── dblp_demo.json             # DBLP dataset (~619K papers)
│
├── scripts/
│   └── init_db.py                 # Database initialization script
│
├── tests/
├── alembic/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── README.md
└── Plan.md
```

---

## **Implementation Phases**

### Phase 1: Foundation & MSSQL

- [x] Set up FastAPI project structure
- [x] Configure MSSQL connections
- [x] Create SQLAlchemy models (Paper, Author, Venue)
- [x] Implement MSSQL paper search
- [x] Load DBLP dataset via init script
- [x] Set up health check endpoint
- [x] Set up Docker + Makefile
- [x] Embedding generation script

### Phase 2: Neo4j Integration

- [x] Neo4j initializer for citation graph
- [x] Build citation relationships (CITES)
- [x] Build co-authorship relationships (COLLABORATES)
- [x] Implement Neo4j citation search
- [x] Add Neo4j-specific search endpoint
- [x] Embedding generation script

### Phase 3: HBase API Integration

- [x] HBase initializer (placeholder)
- [ ] Connect via API (HappyBase client, connection management, health check)
- [ ] Analytical queries (implement search methods for all 6 tables)
- [ ] Semantic search integration (wire HBase into RRF pipeline, `/search/hbase` endpoint)

---

## **HBase Table Design**

| Table | Row Key | Columns (m:qualifier) |
|---|---|---|
| `paper_metrics` | `paper_id` | `m:total_citations`, `m:first_cited_year`, `m:last_cited_year`, `m:citing_papers_count` |
| `author_metrics` | `author_id` | `m:h_index`, `m:total_citations`, `m:paper_count`, `m:first_year`, `m:last_year` |
| `author_metrics` | `author_id#YYYY` | `m:new_citations`, `m:new_papers` |
| `venue_metrics` | `venue_id` | `m:paper_count`, `m:avg_citations`, `m:median_citations`, `m:total_citations`, `m:top_year`, `m:year_range` |
| `paper_citation_velocity` | `paper_id` | `m:citations_YYYY` (per-year columns) |
| `keyword_metrics` | `keyword` | `m:paper_count`, `m:total_citations`, `m:avg_year` |
| `keyword_metrics` | `keyword#related` | `m:co_occurring_keyword:count` (sparse, up to 100 per keyword) |
| `institution_metrics` | `institution_id` | `m:author_count`, `m:paper_count`, `m:total_citations`, `m:avg_citations` |

### Estimated Storage

For ~619K papers / ~1M+ authors:

| Table | Row Count (est.) | Storage |
|---|---|---|
| `paper_metrics` | 619K | ~50 MB |
| `author_metrics` | 1M+ | ~200 MB |
| `venue_metrics` | ~5K | ~1 MB |
| `paper_citation_velocity` | 619K × ~10 years | ~500 MB |
| `keyword_metrics` | ~500K keywords | ~100 MB |
| `institution_metrics` | ~50K institutions | ~10 MB |

Total: **~860 MB**

---

### Phase 4: Unified Search + RRF

- [ ] Implement query router (parallel execution)
- [ ] Implement RRF merger
- [ ] Create unified `/search` endpoint

### Phase 5: Reranking Integration

- [ ] Integrate BGE reranker
- [ ] Add reranking toggle to API

### Phase 6: Evaluation & Demo

- [ ] Retrieval accuracy evaluation
- [ ] Latency benchmarks

---

## **MSSQL Schema Design (Academic Papers)**

```sql
-- Venues table
CREATE TABLE venues (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    type VARCHAR(50)
);

-- Authors table
CREATE TABLE authors (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(500) NOT NULL,
    affiliation VARCHAR(500),
    orcid VARCHAR(50)
);

-- Papers table
CREATE TABLE papers (
    id VARCHAR(36) PRIMARY KEY,
    title TEXT NOT NULL,
    abstract TEXT,
    year INT,
    venue_id VARCHAR(36) REFERENCES venues(id),
    keywords JSON,
    doi VARCHAR(200),
    created_at DATETIME2 DEFAULT GETUTCDATE(),
    updated_at DATETIME2 DEFAULT GETUTCDATE()
);

-- Paper-Author relationship
CREATE TABLE paper_authors (
    id VARCHAR(36) PRIMARY KEY,
    paper_id VARCHAR(36) REFERENCES papers(id),
    author_id VARCHAR(36) REFERENCES authors(id),
    author_order INT
);

-- Paper references (citations)
CREATE TABLE paper_references (
    id VARCHAR(36) PRIMARY KEY,
    paper_id VARCHAR(36) REFERENCES papers(id),
    referenced_paper_id VARCHAR(36) REFERENCES papers(id),
    reference_external_id VARCHAR(100)
);

-- Embeddings table with native vector support
CREATE TABLE embeddings (
    id VARCHAR(36) PRIMARY KEY,
    paper_id VARCHAR(36) REFERENCES papers(id),
    embedding VECTOR(1536),
    model_name VARCHAR(100) DEFAULT 'BGE-M3',
    created_at DATETIME2 DEFAULT GETUTCDATE()
);

-- Search history for analytics
CREATE TABLE search_history (
    id VARCHAR(36) PRIMARY KEY,
    query TEXT NOT NULL,
    sources_queried VARCHAR(100),
    results_count INT,
    latency_ms INT,
    created_at DATETIME2 DEFAULT GETUTCDATE()
);
```

### Search Query

```sql
SELECT TOP (@top_k)
    p.id, p.title, p.abstract, p.year,
    v.name AS venue,
    STRING_AGG(a.name, ', ') AS authors,
    1 - (e.embedding <=> @query_vector) AS similarity_score
FROM papers p
LEFT JOIN venues v ON p.venue_id = v.id
LEFT JOIN paper_authors pa ON p.id = pa.paper_id
LEFT JOIN authors a ON pa.author_id = a.id
LEFT JOIN embeddings e ON p.id = e.paper_id
WHERE p.title LIKE '%' + @query + '%'
   OR p.abstract LIKE '%' + @query + '%'
GROUP BY p.id, p.title, p.abstract, p.year, v.name, e.embedding
ORDER BY similarity_score DESC;
```

---

## **Neo4j Graph Schema**

```cypher
// Nodes
(:Paper {id, title, year})
(:Author {id, name, affiliation})
(:Venue {id, name, type})

// Relationships
(p1:Paper)-[:CITES]->(p2:Paper)
(a:Author)-[:WRITES {order: Int}]->(p:Paper)
(a1:Author)-[:COLLABORATES {count: Int}]-(a2:Author)
(p:Paper)-[:PUBLISHED_IN]->(v:Venue)
```

### Example Queries

```cypher
// Papers citing a specific paper
MATCH (p1:Paper)-[:CITES]->(p2:Paper {id: 'target_id'})
RETURN p1

// Co-authors of an author
MATCH (a1:Author)-[:COLLABORATES]->(a2:Author)
WHERE a1.name = 'John Doe'
RETURN a2

// Citation path between two papers
MATCH path = (p1:Paper)-[:CITES*1..3]->(p2:Paper)
WHERE p1.id = 'source' AND p2.id = 'target'
RETURN path
```

---

## **Search Scenarios**
