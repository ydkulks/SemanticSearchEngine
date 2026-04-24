#!/usr/bin/env python
import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings

logging.getLogger("neo4j").setLevel(logging.ERROR)

BATCH_SIZE_DEFAULT = 1000
MODEL_NAME_DEFAULT = "all-MiniLM-L6-v2"
EMBEDDING_DIM_DEFAULT = 384


def get_mssql_engine():
    return create_engine(
        settings.mssql.connection_url,
        pool_pre_ping=True,
    )


def get_neo4j_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(
        settings.neo4j.uri, auth=(settings.neo4j.username, settings.neo4j.password)
    )


def load_mssql_progress():
    progress_file = ".mssql_embeddings_progress.json"
    if not os.path.exists(progress_file):
        return None
    with open(progress_file, "r") as f:
        return json.load(f)


def save_mssql_progress(offset: int, processed: int):
    progress_file = ".mssql_embeddings_progress.json"
    progress = {
        "offset": offset,
        "processed": processed,
        "started_at": None,
        "last_updated": datetime.now().isoformat(),
    }
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r") as f:
                existing = json.load(f)
                progress["started_at"] = existing.get("started_at")
        except Exception:
            pass
    if not progress.get("started_at"):
        progress["started_at"] = datetime.now().isoformat()
    with open(progress_file, "w") as f:
        json.dump(progress, f)


def delete_mssql_progress():
    progress_file = ".mssql_embeddings_progress.json"
    if os.path.exists(progress_file):
        os.remove(progress_file)


def get_mssql_total_papers(engine):
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM papers"))
        return result.scalar()


def get_mssql_papers_without_embeddings(engine, offset: int, batch_size: int):
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


def insert_mssql_embeddings(engine, embeddings_data: list, model_name: str):
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


def log_failed_batch(offset: int, batch_size: int, error: str, target: str):
    failed_file = f".{target}_embeddings_failed_batches.json"
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


def load_neo4j_progress(entity: str):
    progress_file = f".neo4j_{entity}_progress.json"
    if not os.path.exists(progress_file):
        return None
    with open(progress_file, "r") as f:
        return json.load(f)


def save_neo4j_progress(entity: str, offset: int, processed: int, total: int):
    progress_file = f".neo4j_{entity}_progress.json"
    progress = {
        "entity": entity,
        "offset": offset,
        "processed": processed,
        "total": total,
        "started_at": None,
        "last_updated": datetime.now().isoformat(),
    }
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r") as f:
                existing = json.load(f)
                progress["started_at"] = existing.get("started_at")
        except Exception:
            pass
    if not progress.get("started_at"):
        progress["started_at"] = datetime.now().isoformat()
    with open(progress_file, "w") as f:
        json.dump(progress, f)


def delete_neo4j_progress(entity: str):
    progress_file = f".neo4j_{entity}_progress.json"
    if os.path.exists(progress_file):
        os.remove(progress_file)


def ensure_neo4j_vector_indexes(driver, embedding_dim: int):
    with driver.session() as session:
        try:
            session.run("""
                CREATE VECTOR INDEX paper_embeddings IF NOT EXISTS
                FOR (p:Paper) ON (p.paper_embedding)
                OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}
            """, dim=embedding_dim)
            print("  Vector index for Paper nodes ensured")
        except Exception as e:
            print(f"  Note: Could not create paper vector index (may already exist): {e}")

        try:
            session.run("""
                CREATE VECTOR INDEX author_embeddings IF NOT EXISTS
                FOR (a:Author) ON (a.author_embedding)
                OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}
            """, dim=embedding_dim)
            print("  Vector index for Author nodes ensured")
        except Exception as e:
            print(f"  Note: Could not create author vector index (may already exist): {e}")


def get_neo4j_papers_without_embeddings(session, batch_size: int):
    result = session.run("""
        MATCH (p:Paper)
        WHERE p.paper_embedding IS NULL
        OPTIONAL MATCH (p)-[:PUBLISHED_IN]->(v:Venue)
        RETURN p.id AS id, p.title AS title, p.abstract AS abstract,
               v.name AS venue, p.year AS year
        LIMIT $batch
    """, batch=batch_size)
    return list(result)


def get_neo4j_authors_without_embeddings(session, batch_size: int):
    result = session.run("""
        MATCH (a:Author)
        WHERE a.author_embedding IS NULL
        OPTIONAL MATCH (a)-[:WRITES]->(p:Paper)
        WITH a, COLLECT(p.title) AS paper_titles
        RETURN a.id AS id, a.name AS name, a.affiliation AS affiliation,
               paper_titles
        LIMIT $batch
    """, batch=batch_size)
    return list(result)


def generate_paper_text(title: str, abstract: str | None, venue: str | None, year: int | None) -> str:
    parts = []
    if title:
        parts.append(title)
    if abstract:
        parts.append(abstract)
    if venue:
        parts.append(f"Venue: {venue}")
    if year:
        parts.append(f"Year: {year}")
    return ". ".join(parts) if parts else ""


def generate_author_text(name: str, affiliation: str | None, paper_titles: list) -> str:
    parts = []
    if name:
        parts.append(name)
    if affiliation:
        parts.append(affiliation)
    if paper_titles:
        parts.append("Papers: " + ", ".join(paper_titles[:10]))
    return ". ".join(parts) if parts else ""


def update_neo4j_paper_embeddings(session, embeddings_data: list, model_name: str, embedding_dim: int):
    if not embeddings_data:
        return
    rows = [{"id": node_id, "embedding": embedding} for node_id, embedding in embeddings_data]
    session.run("""
        UNWIND $rows AS row
        MATCH (p:Paper {id: row.id})
        SET p.paper_embedding = vector(row.embedding, $dim, FLOAT32),
            p.embedding_model = $model,
            p.embedding_dimension = $dim
    """, rows=rows, model=model_name, dim=embedding_dim)


def update_neo4j_author_embeddings(session, embeddings_data: list, model_name: str, embedding_dim: int):
    if not embeddings_data:
        return
    rows = [{"id": node_id, "embedding": embedding} for node_id, embedding in embeddings_data]
    session.run("""
        UNWIND $rows AS row
        MATCH (a:Author {id: row.id})
        SET a.author_embedding = vector(row.embedding, $dim, FLOAT32),
            a.embedding_model = $model,
            a.embedding_dimension = $dim
    """, rows=rows, model=model_name, dim=embedding_dim)


def get_neo4j_total_papers(session):
    result = session.run("MATCH (p:Paper) WHERE p.paper_embedding IS NULL RETURN count(p) AS cnt")
    record = result.single()
    return record["cnt"] if record else 0


def get_neo4j_total_authors(session):
    result = session.run("MATCH (a:Author) WHERE a.author_embedding IS NULL RETURN count(a) AS cnt")
    record = result.single()
    return record["cnt"] if record else 0


def run_mssql(args, model):
    engine = get_mssql_engine()
    print("Generating embeddings for MSSQL papers...")

    total_papers = get_mssql_total_papers(engine)
    print(f"Total papers in database: {total_papers:,}")

    offset = 0
    processed = 0
    start_time = time.time()

    if not args.force:
        progress = load_mssql_progress()
        if progress:
            offset = progress.get("offset", 0)
            processed = progress.get("processed", 0)
            print(f"Resuming from offset {offset:,} ({processed:,} papers already processed)")

    if processed == 0:
        save_mssql_progress(0, 0)

    failed_count = 0

    while offset < total_papers:
        try:
            papers = get_mssql_papers_without_embeddings(engine, offset, args.batch)
            if not papers:
                break

            texts = [generate_text(p.title, p.abstract) for p in papers]
            paper_ids = [p.id for p in papers]

            embeddings = model.encode(texts, show_progress_bar=False)

            embeddings_data = list(zip(paper_ids, embeddings.tolist()))

            insert_mssql_embeddings(engine, embeddings_data, args.model)

            processed += len(papers)
            offset += len(papers)

            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed > 0 else 0
            eta = (total_papers - processed) / rate / 60 if rate > 0 else 0

            print(
                f"  Processed {processed:,}/{total_papers:,} papers "
                f"({rate:.0f}/sec, ETA: {eta:.1f}min)"
            )

            save_mssql_progress(offset, processed)

        except Exception as e:
            print(f"  ERROR at offset {offset:,}: {e}")
            log_failed_batch(offset, args.batch, str(e), "mssql")
            failed_count += 1
            offset += args.batch
            processed += args.batch
            save_mssql_progress(offset, processed)

    delete_mssql_progress()

    total_time = time.time() - start_time
    print(f"\nMSSQL embedding generation completed!")
    print(f"  Total papers processed: {processed:,}")
    print(f"  Total time: {total_time / 60:.1f} minutes")
    if failed_count > 0:
        print(f"  Failed batches: {failed_count} (see .mssql_embeddings_failed_batches.json)")


def run_neo4j_papers(args, model, driver):
    embedding_dim = model.get_embedding_dimension()
    print(f"Generating paper embeddings for Neo4j...")
    print(f"  Embedding dimension: {embedding_dim}")

    ensure_neo4j_vector_indexes(driver, embedding_dim)

    with driver.session() as session:
        total_papers = get_neo4j_total_papers(session)
        print(f"  Papers without embeddings: {total_papers:,}")

        processed = 0
        start_time = time.time()

        if not args.force:
            progress = load_neo4j_progress("papers")
            if progress:
                processed = progress.get("processed", 0)
                print(f"  Resuming from {processed:,} papers already processed")

        failed_count = 0

        while True:
            try:
                papers = get_neo4j_papers_without_embeddings(session, args.batch)
                if not papers:
                    break

                texts = []
                ids = []
                for record in papers:
                    ids.append(record["id"])
                    texts.append(generate_paper_text(
                        record["title"],
                        record["abstract"],
                        record["venue"],
                        record["year"]
                    ))

                embeddings = model.encode(texts, show_progress_bar=False)

                embeddings_data = list(zip(ids, embeddings.tolist()))

                update_neo4j_paper_embeddings(session, embeddings_data, args.model, embedding_dim)

                processed += len(papers)

                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (total_papers - processed) / rate / 60 if rate > 0 else 0

                print(
                    f"  Processed {processed:,}/{total_papers:,} papers "
                    f"({rate:.0f}/sec, ETA: {eta:.1f}min)"
                )

                save_neo4j_progress("papers", 0, processed, total_papers)

            except Exception as e:
                print(f"  ERROR at {processed:,}: {e}")
                log_failed_batch(processed, args.batch, str(e), "neo4j_papers")
                failed_count += 1
                processed += args.batch
                save_neo4j_progress("papers", 0, processed, total_papers)

        delete_neo4j_progress("papers")

        total_time = time.time() - start_time
        print(f"\nNeo4j paper embedding generation completed!")
        print(f"  Total papers processed: {processed:,}")
        print(f"  Total time: {total_time / 60:.1f} minutes")
        if failed_count > 0:
            print(f"  Failed batches: {failed_count} (see .neo4j_papers_embeddings_failed_batches.json)")


def run_neo4j_authors(args, model, driver):
    embedding_dim = model.get_embedding_dimension()
    print(f"Generating author embeddings for Neo4j...")
    print(f"  Embedding dimension: {embedding_dim}")

    ensure_neo4j_vector_indexes(driver, embedding_dim)

    with driver.session() as session:
        total_authors = get_neo4j_total_authors(session)
        print(f"  Authors without embeddings: {total_authors:,}")

        processed = 0
        start_time = time.time()

        if not args.force:
            progress = load_neo4j_progress("authors")
            if progress:
                processed = progress.get("processed", 0)
                print(f"  Resuming from {processed:,} authors already processed")

        failed_count = 0

        while True:
            try:
                authors = get_neo4j_authors_without_embeddings(session, args.batch)
                if not authors:
                    break

                texts = []
                ids = []
                for record in authors:
                    ids.append(record["id"])
                    texts.append(generate_author_text(
                        record["name"],
                        record["affiliation"],
                        record["paper_titles"] or []
                    ))

                embeddings = model.encode(texts, show_progress_bar=False)

                embeddings_data = list(zip(ids, embeddings.tolist()))

                update_neo4j_author_embeddings(session, embeddings_data, args.model, embedding_dim)

                processed += len(authors)

                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                eta = (total_authors - processed) / rate / 60 if rate > 0 else 0

                print(
                    f"  Processed {processed:,}/{total_authors:,} authors "
                    f"({rate:.0f}/sec, ETA: {eta:.1f}min)"
                )

                save_neo4j_progress("authors", 0, processed, total_authors)

            except Exception as e:
                print(f"  ERROR at {processed:,}: {e}")
                log_failed_batch(processed, args.batch, str(e), "neo4j_authors")
                failed_count += 1
                processed += args.batch
                save_neo4j_progress("authors", 0, processed, total_authors)

        delete_neo4j_progress("authors")

        total_time = time.time() - start_time
        print(f"\nNeo4j author embedding generation completed!")
        print(f"  Total authors processed: {processed:,}")
        print(f"  Total time: {total_time / 60:.1f} minutes")
        if failed_count > 0:
            print(f"  Failed batches: {failed_count} (see .neo4j_authors_embeddings_failed_batches.json)")


def run_neo4j(args, model):
    driver = get_neo4j_driver()
    try:
        if args.entity in ["papers", "all"]:
            run_neo4j_papers(args, model, driver)
        if args.entity in ["authors", "all"]:
            run_neo4j_authors(args, model, driver)
    finally:
        driver.close()


def main():
    parser = argparse.ArgumentParser(
        description="Generate embeddings for papers and authors",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/generate_embeddings.py --db mssql
  python scripts/generate_embeddings.py --db neo4j --entity papers
  python scripts/generate_embeddings.py --db neo4j --entity authors
  python scripts/generate_embeddings.py --db neo4j --entity all
  python scripts/generate_embeddings.py --db all --entity all
        """
    )
    parser.add_argument(
        "--db",
        required=True,
        choices=["mssql", "neo4j", "all"],
        help="Target database (required)",
    )
    parser.add_argument(
        "--entity",
        choices=["papers", "authors", "all"],
        default="papers",
        help="Entity type to embed for neo4j (default: papers)",
    )
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

    print(f"Loading model: {args.model}...")
    model = SentenceTransformer(args.model)
    print()

    if args.db == "mssql":
        run_mssql(args, model)
    elif args.db == "neo4j":
        run_neo4j(args, model)
    elif args.db == "all":
        print("=" * 60)
        run_mssql(args, model)
        print()
        print("=" * 60)
        run_neo4j(args, model)

    print()
    print(f"To retry failed batches, run with --force flag")


if __name__ == "__main__":
    main()
