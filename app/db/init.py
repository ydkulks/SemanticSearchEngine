import json
import os
from typing import Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.config import settings
from app.db.base import BaseDBInitializer, InitResult
from app.models.database import Base, Venue, Author, Paper, PaperAuthor, PaperReference


class MSSQLInitializer(BaseDBInitializer):
    def __init__(self):
        self._engine: Engine | None = None
        self._batch_size = 5000

    def _get_db_engine(self) -> Engine:
        return create_engine(
            settings.mssql.connection_url,
            pool_pre_ping=True,
            fast_executemany=True,
        )

    def check_connection(self) -> bool:
        try:
            engine = self._get_db_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def create_tables(self) -> bool:
        engine = self._get_db_engine()
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        tables_to_create = [
            "venues",
            "authors",
            "papers",
            "paper_authors",
            "paper_references",
            "embeddings",
            "search_history",
        ]
        needs_creation = [t for t in tables_to_create if t not in existing_tables]

        if not needs_creation:
            return False

        Base.metadata.create_all(bind=engine)
        return True

    def drop_tables(self) -> bool:
        engine = self._get_db_engine()
        tables_to_drop = [
            "paper_authors",
            "paper_references",
            "embeddings",
            "search_history",
            "papers",
            "authors",
            "venues",
        ]
        try:
            with engine.begin() as conn:
                for table in tables_to_drop:
                    conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
            print("Dropped all tables.")
            return True
        except Exception as e:
            print(f"Error dropping tables: {e}")
            return False

    def load_dblp_data(self, json_path: str) -> dict:
        import time

        if not os.path.exists(json_path):
            raise FileNotFoundError(f"JSON file not found: {json_path}")

        engine = self._get_db_engine()
        stats = {"papers": 0, "authors": 0, "venues": 0, "paper_authors": 0, "references": 0}

        venue_map = {}
        author_map = {}
        paper_id_map = {}

        print("Loading DBLP data...")
        start_time = time.time()

        with open(json_path, "r", encoding="utf-8") as f:
            batch_venues = []
            batch_authors = []
            batch_papers = []
            batch_paper_authors = []
            batch_references = []
            pa_counter = 0
            ref_counter = 0

            for line_num, line in enumerate(f, start=1):
                if not line.strip():
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                paper_id = record.get("id", "")
                if not paper_id:
                    continue

                venue_raw = record.get("venue", {}).get("raw", "")
                venue_id = None
                if venue_raw:
                    venue_key = venue_raw.lower()
                    if venue_key not in venue_map:
                        venue_id = f"v_{len(venue_map)}"
                        venue_map[venue_key] = venue_id
                        batch_venues.append(
                            (venue_id, venue_raw, self._infer_venue_type(venue_raw))
                        )
                    else:
                        venue_id = venue_map[venue_key]

                authors = record.get("authors", [])
                author_ids = []
                for auth in authors:
                    author_id = auth.get("id")
                    if not author_id:
                        continue
                    if author_id not in author_map:
                        author_map[author_id] = author_id
                        batch_authors.append(
                            (author_id, auth.get("name", "")[:500], auth.get("affiliation", ""))
                        )
                    author_ids.append((author_id, auth.get("order", 0)))

                keywords = record.get("keywords", [])
                if keywords and isinstance(keywords, list):
                    keywords = [k for k in keywords if k and len(k) < 100]
                    if len(keywords) > 20:
                        keywords = keywords[:20]

                abstract = record.get("abstract", "")
                if abstract and len(abstract) > 10000:
                    abstract = abstract[:10000]

                batch_papers.append(
                    (
                        paper_id,
                        record.get("title", "")[:2000],
                        abstract,
                        record.get("year"),
                        venue_id,
                        json.dumps(keywords) if keywords else None,
                        None,
                    )
                )

                for author_id, order in author_ids:
                    pa_counter += 1
                    batch_paper_authors.append(
                        (f"{paper_id}_{author_id}_{pa_counter}", paper_id, author_id, order)
                    )

                references = record.get("references", [])
                for ref_id in references[:100]:
                    if not ref_id:
                        continue
                    ref_counter += 1
                    batch_references.append(
                        (
                            f"{paper_id}_{ref_id}_{ref_counter}",
                            paper_id,
                            ref_id if ref_id in paper_id_map else None,
                            ref_id,
                        )
                    )

                stats["papers"] += 1
                paper_id_map[paper_id] = paper_id

                if stats["papers"] % 10000 == 0:
                    self._flush_batch(
                        engine,
                        batch_venues,
                        batch_authors,
                        batch_papers,
                        batch_paper_authors,
                        batch_references,
                        stats,
                    )
                    batch_venues, batch_authors, batch_papers = [], [], []
                    batch_paper_authors, batch_references = [], []
                    elapsed = time.time() - start_time
                    rate = stats["papers"] / elapsed
                    eta = (619525 - stats["papers"]) / rate / 60 if rate > 0 else 0
                    print(
                        f"  Processed {stats['papers']:,} papers... ({rate:.0f}/sec, ETA: {eta:.1f}min)"
                    )

            self._flush_batch(
                engine,
                batch_venues,
                batch_authors,
                batch_papers,
                batch_paper_authors,
                batch_references,
                stats,
            )

        elapsed = time.time() - start_time
        print(
            f"Loaded: {stats['papers']:,} papers, {stats['authors']:,} authors, "
            f"{stats['venues']:,} venues, {stats['paper_authors']:,} paper-authors, "
            f"{stats['references']:,} references ({elapsed:.1f}s)"
        )
        return stats

    def _flush_batch(
        self,
        engine: Engine,
        venues: list,
        authors: list,
        papers: list,
        paper_authors: list,
        references: list,
        stats: dict,
    ):
        if not engine:
            return

        with engine.begin() as conn:
            raw_conn = conn.connection.dbapi_connection
            cursor = raw_conn.cursor()
            cursor.fast_executemany = True

            if venues:
                cursor.executemany(
                    "INSERT INTO venues (id, name, type) VALUES (?, ?, ?)",
                    venues,
                )
                stats["venues"] += len(venues)

            if authors:
                cursor.executemany(
                    "INSERT INTO authors (id, name, affiliation, orcid) VALUES (?, ?, ?, ?)",
                    [(a[0], a[1], a[2], None) for a in authors],
                )
                stats["authors"] += len(authors)

            if papers:
                cursor.executemany(
                    "INSERT INTO papers (id, title, abstract, year, venue_id, keywords, doi) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    papers,
                )

            if paper_authors:
                cursor.executemany(
                    "INSERT INTO paper_authors (id, paper_id, author_id, author_order) VALUES (?, ?, ?, ?)",
                    paper_authors,
                )
                stats["paper_authors"] += len(paper_authors)

            if references:
                cursor.executemany(
                    "INSERT INTO paper_references (id, paper_id, referenced_paper_id, reference_external_id) VALUES (?, ?, ?, ?)",
                    references,
                )
                stats["references"] += len(references)

            cursor.commit()
            cursor.close()

    def _infer_venue_type(self, venue_name: str) -> str:
        name_lower = venue_name.lower()
        if any(x in name_lower for x in ["conf", "symposium", "workshop", "proc", "conference"]):
            return "conference"
        if any(x in name_lower for x in ["trans", "journal", "ieee", "acm"]):
            return "journal"
        return "other"

    def initialize(self, data_path: Optional[str] = None) -> InitResult:
        try:
            tables_created = self.create_tables()

            data_stats = None
            if data_path:
                data_stats = self.load_dblp_data(data_path)

            message = f"MSSQL: Tables {'created' if tables_created else 'already exist'}"
            if data_stats:
                message += f", loaded {data_stats['papers']:,} papers, {data_stats['authors']:,} authors, {data_stats['venues']:,} venues"

            return InitResult(
                tables_created=tables_created,
                message=message,
            )
        except Exception as e:
            return InitResult(
                tables_created=False,
                message=f"MSSQL initialization failed: {str(e)}",
            )


class Neo4jInitializer(BaseDBInitializer):
    CHECKPOINT_FILE = ".neo4j_init_checkpoint.json"

    def check_connection(self) -> bool:
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                settings.neo4j.uri, auth=(settings.neo4j.username, settings.neo4j.password)
            )
            with driver.session() as session:
                session.run("RETURN 1")
            driver.close()
            return True
        except Exception:
            return False

    def create_tables(self) -> bool:
        return True

    def _load_checkpoint(self) -> Optional[dict]:
        if os.path.exists(self.CHECKPOINT_FILE):
            try:
                with open(self.CHECKPOINT_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def _save_checkpoint(self, checkpoint: dict):
        with open(self.CHECKPOINT_FILE, "w") as f:
            json.dump(checkpoint, f, indent=2)

    def _clear_checkpoint(self):
        if os.path.exists(self.CHECKPOINT_FILE):
            os.remove(self.CHECKPOINT_FILE)

    def _get_mssql_engine(self):
        return create_engine(
            settings.mssql.connection_url,
            pool_pre_ping=True,
        )

    def load_citations_from_db(self) -> dict:
        try:
            import time

            from neo4j import GraphDatabase

            engine = self._get_mssql_engine()

            driver = GraphDatabase.driver(
                settings.neo4j.uri, auth=(settings.neo4j.username, settings.neo4j.password)
            )

            batch_size_papers = settings.neo4j.batch_size_papers
            batch_size_authors = settings.neo4j.batch_size_authors
            batch_size_citations = settings.neo4j.batch_size_citations
            batch_size_writes = settings.neo4j.batch_size_writes
            batch_size_collaborations = settings.neo4j.batch_size_collaborations

            stats = {
                "papers": 0,
                "authors": 0,
                "citations": 0,
                "writes": 0,
                "coauthorships": 0,
            }

            checkpoint = self._load_checkpoint()

            print("Starting Neo4j data load...")
            start_time = time.time()

            with driver.session() as session:
                if checkpoint is None:
                    print("Clearing existing Neo4j data...")
                    session.run("MATCH (n) DETACH DELETE n")
                    current_phase = "papers"
                    offsets = {"papers": 0, "authors": 0, "citations": 0, "writes": 0, "coauthorships": 0}
                else:
                    print(f"Resuming from checkpoint (papers: {checkpoint['stats']['papers']:,})...")
                    current_phase = checkpoint.get("phase", "papers")
                    offsets = checkpoint.get("offsets", {"papers": 0, "authors": 0, "citations": 0, "writes": 0, "coauthorships": 0})
                    stats.update(checkpoint.get("stats", stats))

                if current_phase in ("papers", "authors", "citations", "writes", "coauthorships"):
                    checkpoint = {
                        "phase": current_phase,
                        "offsets": offsets,
                        "stats": stats,
                    }
                    self._save_checkpoint(checkpoint)

                if current_phase == "papers":
                    print("Loading Paper nodes...")
                    with engine.connect() as conn:
                        offset = offsets["papers"]
                        while True:
                            result = conn.execute(
                                text(f"""
                                    SELECT id, title FROM papers
                                    ORDER BY id
                                    OFFSET {offset} ROWS
                                    FETCH NEXT {batch_size_papers} ROWS ONLY
                                """)
                            )
                            rows = result.fetchall()
                            if not rows:
                                break

                            for row in rows:
                                session.run(
                                    "MERGE (p:Paper {id: $id}) SET p.title = $title",
                                    id=row[0],
                                    title=row[1],
                                )
                                stats["papers"] += 1

                            offset += batch_size_papers
                            offsets["papers"] = offset
                            elapsed = time.time() - start_time
                            rate = stats["papers"] / elapsed if elapsed > 0 else 0
                            print(f"  Loaded {stats['papers']:,} papers... ({rate:.0f}/sec)")

                            checkpoint["offsets"] = offsets
                            checkpoint["stats"] = stats.copy()
                            self._save_checkpoint(checkpoint)

                    current_phase = "authors"
                    offsets["authors"] = 0
                    checkpoint["phase"] = current_phase
                    checkpoint["offsets"] = offsets
                    self._save_checkpoint(checkpoint)

                if current_phase == "authors":
                    print("Loading Author nodes...")
                    with engine.connect() as conn:
                        offset = offsets["authors"]
                        while True:
                            result = conn.execute(
                                text(f"""
                                    SELECT id, name, affiliation FROM authors
                                    ORDER BY id
                                    OFFSET {offset} ROWS
                                    FETCH NEXT {batch_size_authors} ROWS ONLY
                                """)
                            )
                            rows = result.fetchall()
                            if not rows:
                                break

                            for row in rows:
                                session.run(
                                    "MERGE (a:Author {id: $id}) SET a.name = $name, a.affiliation = $affiliation",
                                    id=row[0],
                                    name=row[1],
                                    affiliation=row[2],
                                )
                                stats["authors"] += 1

                            offset += batch_size_authors
                            offsets["authors"] = offset
                            elapsed = time.time() - start_time
                            rate = stats["authors"] / elapsed if elapsed > 0 else 0
                            print(f"  Loaded {stats['authors']:,} authors... ({rate:.0f}/sec)")

                            checkpoint["offsets"] = offsets
                            checkpoint["stats"] = stats.copy()
                            self._save_checkpoint(checkpoint)

                    current_phase = "citations"
                    offsets["citations"] = 0
                    checkpoint["phase"] = current_phase
                    checkpoint["offsets"] = offsets
                    self._save_checkpoint(checkpoint)

                if current_phase == "citations":
                    print("Loading CITES relationships...")
                    with engine.connect() as conn:
                        offset = offsets["citations"]
                        while True:
                            result = conn.execute(
                                text(f"""
                                    SELECT DISTINCT paper_id, referenced_paper_id
                                    FROM paper_references
                                    WHERE referenced_paper_id IS NOT NULL
                                    ORDER BY paper_id
                                    OFFSET {offset} ROWS
                                    FETCH NEXT {batch_size_citations} ROWS ONLY
                                """)
                            )
                            rows = result.fetchall()
                            if not rows:
                                break

                            for row in rows:
                                session.run(
                                    """
                                    MATCH (p1:Paper {id: $src}), (p2:Paper {id: $tgt})
                                    MERGE (p1)-[:CITES]->(p2)
                                    """,
                                    src=row[0],
                                    tgt=row[1],
                                )
                                stats["citations"] += 1

                            offset += batch_size_citations
                            offsets["citations"] = offset
                            elapsed = time.time() - start_time
                            rate = stats["citations"] / elapsed if elapsed > 0 else 0
                            print(f"  Loaded {stats['citations']:,} citations... ({rate:.0f}/sec)")

                            checkpoint["offsets"] = offsets
                            checkpoint["stats"] = stats.copy()
                            self._save_checkpoint(checkpoint)

                    current_phase = "writes"
                    offsets["writes"] = 0
                    checkpoint["phase"] = current_phase
                    checkpoint["offsets"] = offsets
                    self._save_checkpoint(checkpoint)

                if current_phase == "writes":
                    print("Loading WRITES relationships...")
                    with engine.connect() as conn:
                        offset = offsets["writes"]
                        while True:
                            result = conn.execute(
                                text(f"""
                                    SELECT pa.paper_id, pa.author_id, pa.author_order
                                    FROM paper_authors pa
                                    ORDER BY pa.paper_id, pa.author_order
                                    OFFSET {offset} ROWS
                                    FETCH NEXT {batch_size_writes} ROWS ONLY
                                """)
                            )
                            rows = result.fetchall()
                            if not rows:
                                break

                            for row in rows:
                                session.run(
                                    """
                                    MATCH (a:Author {id: $author_id}), (p:Paper {id: $paper_id})
                                    MERGE (a)-[:WRITES {order: $order}]->(p)
                                    """,
                                    author_id=row[1],
                                    paper_id=row[0],
                                    order=row[2],
                                )
                                stats["writes"] += 1

                            offset += batch_size_writes
                            offsets["writes"] = offset
                            elapsed = time.time() - start_time
                            rate = stats["writes"] / elapsed if elapsed > 0 else 0
                            print(f"  Loaded {stats['writes']:,} writes... ({rate:.0f}/sec)")

                            checkpoint["offsets"] = offsets
                            checkpoint["stats"] = stats.copy()
                            self._save_checkpoint(checkpoint)

                    current_phase = "coauthorships"
                    offsets["coauthorships"] = 0
                    checkpoint["phase"] = current_phase
                    checkpoint["offsets"] = offsets
                    self._save_checkpoint(checkpoint)

                if current_phase == "coauthorships":
                    print("Loading COLLABORATES relationships...")
                    with engine.connect() as conn:
                        offset = offsets["coauthorships"]
                        while True:
                            result = conn.execute(
                                text(f"""
                                    SELECT pa1.author_id, pa2.author_id, COUNT(*) as collab_count
                                    FROM paper_authors pa1
                                    JOIN paper_authors pa2 ON pa1.paper_id = pa2.paper_id
                                    WHERE pa1.author_id < pa2.author_id
                                    GROUP BY pa1.author_id, pa2.author_id
                                    HAVING COUNT(*) >= 2
                                    ORDER BY pa1.author_id
                                    OFFSET {offset} ROWS
                                    FETCH NEXT {batch_size_collaborations} ROWS ONLY
                                """)
                            )
                            rows = result.fetchall()
                            if not rows:
                                break

                            for row in rows:
                                session.run(
                                    """
                                    MATCH (a1:Author {id: $a1}), (a2:Author {id: $a2})
                                    MERGE (a1)-[:COLLABORATES {count: $count}]-(a2)
                                    """,
                                    a1=row[0],
                                    a2=row[1],
                                    count=row[2],
                                )
                                stats["coauthorships"] += 1

                            offset += batch_size_collaborations
                            offsets["coauthorships"] = offset
                            elapsed = time.time() - start_time
                            rate = stats["coauthorships"] / elapsed if elapsed > 0 else 0
                            print(f"  Loaded {stats['coauthorships']:,} coauthorships... ({rate:.0f}/sec)")

                            checkpoint["offsets"] = offsets
                            checkpoint["stats"] = stats.copy()
                            self._save_checkpoint(checkpoint)

            driver.close()
            engine.dispose()

            self._clear_checkpoint()

            elapsed = time.time() - start_time
            print(
                f"Neo4j: Loaded {stats['papers']:,} papers, {stats['authors']:,} authors, "
                f"{stats['citations']:,} citations, {stats['writes']:,} writes, "
                f"{stats['coauthorships']:,} coauthorships ({elapsed:.1f}s)"
            )
            return stats

        except Exception as e:
            print(f"Neo4j loading failed: {e}")
            import traceback
            traceback.print_exc()
            return {
                "papers": 0,
                "authors": 0,
                "citations": 0,
                "writes": 0,
                "coauthorships": 0,
            }

    def initialize(self, data_path: Optional[str] = None) -> InitResult:
        try:
            result = self.load_citations_from_db()
            return InitResult(
                tables_created=True,
                message=f"Neo4j: Loaded {result['papers']:,} papers, {result['authors']:,} authors, "
                f"{result['citations']:,} citations, {result['writes']:,} writes, "
                f"{result['coauthorships']:,} coauthorships",
            )
        except Exception as e:
            return InitResult(
                tables_created=False,
                message=f"Neo4j initialization failed: {str(e)}",
            )


class HBaseInitializer(BaseDBInitializer):
    def check_connection(self) -> bool:
        return False

    def create_tables(self) -> bool:
        return True

    def initialize(self, data_path: Optional[str] = None) -> InitResult:
        return InitResult(
            tables_created=True,
            message="HBase: Placeholder initialized (metrics table to be implemented)",
        )
