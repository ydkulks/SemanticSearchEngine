# Semantic Search Engine

Multi-database semantic search engine using MSSQL (papers), Neo4j (citation graph), and HBase (metrics).

## Dataset

Uses the DBLP academic citation network dataset (~619K papers).

## Setup

1. Place `dblp_demo.json` in the `data/` directory (JSON Lines format)
2. Configure environment variables in `.env`:
   ```
   MSSQL_HOST=your_host
   MSSQL_PORT=1433
   MSSQL_DATABASE=your_db
   MSSQL_USERNAME=your_user
   MSSQL_PASSWORD=your_password

   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=your_password

   HBASE_HOST=localhost
   HBASE_PORT=9090
   ```

## Initialize Database

```bash
# Check database connections
python scripts/init_db.py --check-only

# Create tables only
python scripts/init_db.py --db mssql

# Create tables and load DBLP dataset
python scripts/init_db.py --db mssql --load-data

# Load everything (MSSQL + Neo4j graph)
python scripts/init_db.py --db all --load-data

# Use custom data path
python scripts/init_db.py --db mssql --load-data --data-path /path/to/dblp.json
```

## Run Server

```bash
uvicorn app.main:app --reload
```

Access API docs at http://localhost:8000/docs

## Generate Embeddings

Generate vector embeddings for all papers using sentence-transformers:

```bash
# Install dependencies (sentence-transformers will be installed automatically)
uv sync

# Generate embeddings (default: all-MiniLM-L6-v2, batch size 1000)
python scripts/generate_embeddings.py

# With custom batch size
python scripts/generate_embeddings.py --batch 500

# With different model
python scripts/generate_embeddings.py --model all-mpnet-base-v2

# Force restart from beginning
python scripts/generate_embeddings.py --force
```

**Progress tracking:** Progress is saved to `.embeddings_progress.json`. If interrupted, re-running will resume automatically.

**Failed batches:** If a batch fails, it's logged to `.embeddings_failed_batches.json` and skipped. Use `--force` to retry.

## API Endpoints

| Method | Endpoint   | Description |
|--------|------------|-------------|
| `POST` | `/search`  | Search papers by title, abstract, author, keywords |
| `GET`  | `/sources` | List available data sources |
| `GET`  | `/health`  | Health check |

### Search Request Example

```json
{
  "query": "machine learning healthcare",
  "top_k": 10,
  "filters": {
    "min_year": 2020,
    "venue": "ICML"
  }
}
```

### Search Response Example

```json
{
  "query": "machine learning healthcare",
  "results": [
    {
      "id": "53e99784b7602d9701f3ffdd",
      "title": "Deep Learning for Healthcare",
      "abstract": "...",
      "authors": ["Author Name"],
      "year": 2021,
      "venue": "ICML",
      "keywords": ["deep learning", "healthcare"],
      "source": "mssql",
      "score": 0.0,
      "metadata": {}
    }
  ],
  "total": 10,
  "latency_ms": 45.2,
  "sources_queried": ["mssql"]
}
```
