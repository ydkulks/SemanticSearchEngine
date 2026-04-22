#!/usr/bin/env python
import argparse
import json
import os
import sys
import time
from datetime import datetime

from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings


PROGRESS_FILE = ".embeddings_progress.json"
BATCH_SIZE_DEFAULT = 1000
MODEL_NAME_DEFAULT = "all-MiniLM-L6-v2"


def get_engine():
    return create_engine(
        settings.mssql.connection_url,
        pool_pre_ping=True,
    )


def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return None
    with open(PROGRESS_FILE, "r") as f:
        return json.load(f)


def save_progress(offset: int, processed: int):
    progress = {
        "offset": offset,
        "processed": processed,
        "started_at": None,
        "last_updated": datetime.now().isoformat(),
    }
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r") as f:
                existing = json.load(f)
                progress["started_at"] = existing.get("started_at")
        except Exception:
            pass
    if not progress.get("started_at"):
        progress["started_at"] = datetime.now().isoformat()
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)


def delete_progress():
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


def get_total_papers(engine):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM papers"))
        return result.scalar()


def get_papers_without_embeddings(engine, offset: int, batch_size: int):
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT p.id, p.title, p.abstract
                FROM papers p
                LEFT JOIN embeddings e ON p.id = e.paper_id
                WHERE e.paper_id IS NULL
                ORDER BY p.id
                OFFSET :offset ROWS FETCH NEXT :batch ROWS ONLY
            """),
            {"offset": offset, "batch": batch_size},
        )
        return result.fetchall()


def generate_text(title: str, abstract: str | None) -> str:
    parts = []
    if title:
        parts.append(title)
    if abstract:
        parts.append(abstract)
    return ". ".join(parts) if parts else ""


def insert_embeddings(engine, embeddings_data: list, model_name: str):
    raw_conn = engine.connect().connection.dbapi_connection
    cursor = raw_conn.cursor()
    cursor.fast_executemany = True

    params = [
        (paper_id, json.dumps(embedding), model_name) for paper_id, embedding in embeddings_data
    ]

    cursor.executemany(
        "INSERT INTO embeddings (paper_id, embedding, model_name) VALUES (?, ?, ?)",
        params,
    )
    cursor.commit()
    cursor.close()


def log_failed_batch(offset: int, batch_size: int, error: str):
    failed_file = ".embeddings_failed_batches.json"
    failed = []
    if os.path.exists(failed_file):
        with open(failed_file, "r") as f:
            failed = json.load(f)
    failed.append(
        {
            "offset": offset,
            "batch_size": batch_size,
            "error": str(error),
            "timestamp": datetime.now().isoformat(),
        }
    )
    with open(failed_file, "w") as f:
        json.dump(failed, f)


def main():
    parser = argparse.ArgumentParser(description="Generate embeddings for papers")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force restart from beginning (ignores progress file)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=BATCH_SIZE_DEFAULT,
        help=f"Batch size (default: {BATCH_SIZE_DEFAULT})",
    )
    parser.add_argument(
        "--model",
        default=MODEL_NAME_DEFAULT,
        help=f"Model name (default: {MODEL_NAME_DEFAULT})",
    )
    args = parser.parse_args()

    engine = get_engine()

    print(f"Loading model: {args.model}...")
    model = SentenceTransformer(args.model)
    embedding_dim = model.get_embedding_dimension()
    print(f"Embedding dimension: {embedding_dim}")

    total_papers = get_total_papers(engine)
    print(f"Total papers in database: {total_papers:,}")

    offset = 0
    processed = 0
    start_time = time.time()

    if not args.force:
        progress = load_progress()
        if progress:
            offset = progress.get("offset", 0)
            processed = progress.get("processed", 0)
            print(f"Resuming from offset {offset:,} ({processed:,} papers already processed)")

    if processed == 0:
        save_progress(0, 0)

    failed_count = 0

    while offset < total_papers:
        try:
            papers = get_papers_without_embeddings(engine, offset, args.batch)
            if not papers:
                break

            texts = [generate_text(p.title, p.abstract) for p in papers]
            paper_ids = [p.id for p in papers]

            embeddings = model.encode(texts, show_progress_bar=False)

            embeddings_data = list(zip(paper_ids, embeddings.tolist()))

            insert_embeddings(engine, embeddings_data, args.model)

            processed += len(papers)
            offset += len(papers)

            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed > 0 else 0
            eta = (total_papers - processed) / rate / 60 if rate > 0 else 0

            print(
                f"  Processed {processed:,}/{total_papers:,} papers "
                f"({rate:.0f}/sec, ETA: {eta:.1f}min)"
            )

            save_progress(offset, processed)

        except Exception as e:
            print(f"  ERROR at offset {offset:,}: {e}")
            log_failed_batch(offset, args.batch, str(e))
            failed_count += 1
            offset += args.batch
            processed += args.batch
            save_progress(offset, processed)

    delete_progress()

    total_time = time.time() - start_time
    print(f"\nCompleted!")
    print(f"  Total papers processed: {processed:,}")
    print(f"  Total time: {total_time / 60:.1f} minutes")
    if failed_count > 0:
        print(f"  Failed batches: {failed_count} (see .embeddings_failed_batches.json)")
        print(f"  To retry failed batches, run: python scripts/generate_embeddings.py --force")


if __name__ == "__main__":
    main()
