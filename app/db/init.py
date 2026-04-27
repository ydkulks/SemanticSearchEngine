import json
import os
import re
import time
from collections import defaultdict
from typing import Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

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
    CHECKPOINT_FILE = ".hbase_init_checkpoint.json"
    COLUMN_FAMILY = "m"
    BATCH_SIZE = 1000

    TABLE_SCHEMAS = {
        "paper_metrics": {
            COLUMN_FAMILY: {"max_versions": 1}
        },
        "author_metrics": {
            COLUMN_FAMILY: {"max_versions": 1}
        },
        "venue_metrics": {
            COLUMN_FAMILY: {"max_versions": 1}
        },
        "paper_citation_velocity": {
            COLUMN_FAMILY: {"max_versions": 1}
        },
        "keyword_metrics": {
            COLUMN_FAMILY: {"max_versions": 1}
        },
        "institution_metrics": {
            COLUMN_FAMILY: {"max_versions": 1}
        },
    }

    INSTITUTION_MAP = {
        "mit": "Massachusetts Institute of Technology",
        "massachusetts institute of technology": "Massachusetts Institute of Technology",
        "stanford": "Stanford University",
        "stanford university": "Stanford University",
        "cmu": "Carnegie Mellon University",
        "carnegie mellon university": "Carnegie Mellon University",
        "berkeley": "University of California Berkeley",
        "uc berkeley": "University of California Berkeley",
        "university of california berkeley": "University of California Berkeley",
        "caltech": "California Institute of Technology",
        "california institute of technology": "California Institute of Technology",
        "gatech": "Georgia Institute of Technology",
        "georgia institute of technology": "Georgia Institute of Technology",
        "uiuc": "University of Illinois Urbana-Champaign",
        "university of illinois": "University of Illinois Urbana-Champaign",
        "princeton": "Princeton University",
        "harvard": "Harvard University",
        "yale": "Yale University",
        "cornell": "Cornell University",
        "Columbia": "Columbia University",
        "umass": "University of Massachusetts",
        "university of massachusetts": "University of Massachusetts",
        "utexas": "University of Texas at Austin",
        "university of texas": "University of Texas at Austin",
        "washington": "University of Washington",
        "uw": "University of Washington",
        "university of washington": "University of Washington",
        "umich": "University of Michigan",
        "university of michigan": "University of Michigan",
        "utexas": "University of Texas",
    }

    def check_connection(self) -> bool:
        from app.db.hbase_ import hbase_conn
        return hbase_conn.check_connection()

    def create_tables(self) -> bool:
        from app.db.hbase_ import hbase_conn

        tables = hbase_conn.list_tables()
        existing = set(t.get("name") if isinstance(t, dict) else t for t in tables)

        to_create = [name for name in self.TABLE_SCHEMAS if name not in existing]

        for table_name in to_create:
            try:
                hbase_conn.create_table(table_name, self.TABLE_SCHEMAS[table_name])
                print(f"Created table: {table_name}")
            except Exception as e:
                pass

        return len(to_create) > 0

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

    def _normalize_keyword(self, keyword: str) -> str:
        kw = keyword.lower()
        kw = re.sub(r"[^\w\s]", "", kw)
        kw = re.sub(r"[\-–—]", " ", kw)
        kw = re.sub(r"\s+", " ", kw).strip()
        return kw

    def _normalize_institution(self, raw: str) -> str:
        if not raw:
            return "unknown"
        normalized = raw.lower().strip()
        return self.INSTITUTION_MAP.get(normalized, raw.strip())

    def _get_mssql_engine(self):
        return create_engine(
            settings.mssql.connection_url,
            pool_pre_ping=True,
        )

    def _get_neo4j_driver(self):
        from neo4j import GraphDatabase

        return GraphDatabase.driver(
            settings.neo4j.uri,
            auth=(settings.neo4j.username, settings.neo4j.password),
        )

    def _calculate_h_index(self, citation_counts: list[int]) -> int:
        sorted_counts = sorted(citation_counts, reverse=True)
        h_index = 0
        for i, count in enumerate(sorted_counts, start=1):
            if count >= i:
                h_index = i
            else:
                break
        return h_index

    def populate_all_metrics(self) -> dict:
        from app.db.hbase_ import hbase_conn

        checkpoint = self._load_checkpoint()
        stats = {
            "paper_metrics": 0,
            "author_metrics": 0,
            "venue_metrics": 0,
            "paper_citation_velocity": 0,
            "keyword_metrics": 0,
            "institution_metrics": 0,
        }

        start_time = time.time()

        if checkpoint is None:
            print("Starting HBase metrics population...")
            current_phase = "paper_citations"
        else:
            print(f"Resuming from checkpoint: {checkpoint.get('phase', 'unknown')}")
            current_phase = checkpoint.get("phase", "paper_citations")
            stats.update(checkpoint.get("stats", stats))

        if current_phase in ("paper_citations", "author_metrics", "venue_metrics", "paper_citation_velocity", "keyword_metrics", "institution_metrics"):
            if current_phase == "paper_citations":
                stats = self._populate_paper_citations(stats, checkpoint, start_time)
            if current_phase == "author_metrics":
                stats = self._populate_author_metrics(stats, checkpoint, start_time)
            if current_phase == "venue_metrics":
                stats = self._populate_venue_metrics(stats, checkpoint, start_time)
            if current_phase == "paper_citation_velocity":
                stats = self._populate_paper_citation_velocity(stats, checkpoint, start_time)
            if current_phase == "keyword_metrics":
                stats = self._populate_keyword_metrics(stats, checkpoint, start_time)
            if current_phase == "institution_metrics":
                stats = self._populate_institution_metrics(stats, checkpoint, start_time)

        elapsed = time.time() - start_time
        print(f"HBase metrics population complete: {stats} ({elapsed:.1f}s)")
        return stats

    def _populate_paper_citations(self, stats: dict, checkpoint: Optional[dict], start_time: float) -> dict:
        from neo4j import GraphDatabase
        from app.db.hbase_ import hbase_conn

        engine = self._get_mssql_engine()
        driver = self._get_neo4j_driver()

        print("Fetching citation data from Neo4j and MSSQL...")

        paper_years = {}
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id, year FROM papers WHERE year IS NOT NULL"))
            for row in result:
                paper_years[row[0]] = row[1]

        citation_data = defaultdict(lambda: {"citations": set(), "years": defaultdict(int)})

        with driver.session() as session:
            result = session.run("""
                MATCH (citing:Paper)-[:CITES]->(cited:Paper)
                RETURN cited.id AS paper_id, citing.id AS citing_id
            """)
            for record in result:
                paper_id = record["paper_id"]
                citing_id = record["citing_id"]
                if paper_id and citing_id:
                    citation_data[paper_id]["citations"].add(citing_id)
                    citing_year = paper_years.get(citing_id)
                    if citing_year:
                        citation_data[paper_id]["years"][citing_year] += 1

        driver.close()
        engine.dispose()

        print(f"Processing {len(citation_data)} papers with citations...")

        paper_table = hbase_conn.get_table("paper_metrics")
        processed = 0

        for paper_id, data in citation_data.items():
            total_citations = len(data["citations"])
            years = list(data["years"].keys())
            first_year = min(years) if years else None
            last_year = max(years) if years else None

            row = {
                "m:total_citations": str(total_citations),
                "m:first_cited_year": str(first_year) if first_year else "",
                "m:last_cited_year": str(last_year) if last_year else "",
                "m:citing_papers_count": str(total_citations),
            }
            paper_table.put(paper_id, row)

            processed += 1
            stats["paper_metrics"] += 1

            if processed % 1000 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  Written {stats['paper_metrics']:,} paper_metrics... ({rate:.0f}/sec)")

                self._save_checkpoint({"phase": "paper_citations", "stats": stats})

        print(f"paper_metrics: {stats['paper_metrics']:,} rows")

        self._save_checkpoint({"phase": "author_metrics", "stats": stats})
        return stats

    def _populate_author_metrics(self, stats: dict, checkpoint: Optional[dict], start_time: float) -> dict:
        from app.db.hbase_ import hbase_conn

        engine = self._get_mssql_engine()

        print("Computing author metrics from MSSQL...")

        author_papers = defaultdict(list)
        author_citations = defaultdict(list)

        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT pa.author_id, p.id, p.year, pr.citation_count
                FROM paper_authors pa
                JOIN papers p ON pa.paper_id = p.id
                LEFT JOIN (
                    SELECT referenced_paper_id, COUNT(*) AS citation_count
                    FROM paper_references
                    WHERE referenced_paper_id IS NOT NULL
                    GROUP BY referenced_paper_id
                ) pr ON p.id = pr.referenced_paper_id
            """))
            for row in result:
                author_id = row[0]
                paper_id = row[1]
                year = row[2]
                citations = row[3] or 0
                author_papers[author_id].append((paper_id, year))
                author_citations[author_id].append(citations)

        author_table = hbase_conn.get_table("author_metrics")
        processed = 0

        for author_id, papers in author_papers.items():
            citations = author_citations[author_id]
            h_index = self._calculate_h_index(citations)
            years = [p[1] for p in papers if p[1]]
            first_year = min(years) if years else None
            last_year = max(years) if years else None

            row = {
                "m:h_index": str(h_index),
                "m:total_citations": str(sum(citations)),
                "m:paper_count": str(len(papers)),
                "m:first_year": str(first_year) if first_year else "",
                "m:last_year": str(last_year) if last_year else "",
            }
            author_table.put(author_id, row)

            processed += 1
            stats["author_metrics"] += 1

            if processed % 10000 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  Written {stats['author_metrics']:,} author_metrics... ({rate:.0f}/sec)")

                self._save_checkpoint({"phase": "author_metrics", "stats": stats})

        engine.dispose()

        print(f"author_metrics: {stats['author_metrics']:,} rows")

        self._save_checkpoint({"phase": "venue_metrics", "stats": stats})
        return stats

    def _populate_venue_metrics(self, stats: dict, checkpoint: Optional[dict], start_time: float) -> dict:
        from app.db.hbase_ import hbase_conn

        engine = self._get_mssql_engine()

        print("Computing venue metrics from MSSQL...")

        venue_papers = defaultdict(list)
        venue_citations = defaultdict(list)

        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT p.venue_id, p.id, p.year,
                    (SELECT COUNT(*) FROM paper_references WHERE referenced_paper_id = p.id) AS citations
                FROM papers p
                WHERE p.venue_id IS NOT NULL
            """))
            for row in result:
                venue_id = row[0]
                paper_id = row[1]
                year = row[2]
                citations = row[3] or 0
                if venue_id:
                    venue_papers[venue_id].append((paper_id, year, citations))
                    venue_citations[venue_id].append(citations)

        venue_table = hbase_conn.get_table("venue_metrics")
        processed = 0

        for venue_id, papers in venue_papers.items():
            citations = venue_citations[venue_id]
            paper_count = len(papers)
            total_citations = sum(citations)
            avg_citations = total_citations / paper_count if paper_count > 0 else 0
            sorted_citations = sorted(citations)
            mid = len(sorted_citations) // 2
            median_citations = sorted_citations[mid] if sorted_citations else 0
            years = [p[1] for p in papers if p[1]]
            top_year = max(years) if years else None
            year_range = max(years) - min(years) if len(years) > 1 else 0

            row = {
                "m:paper_count": str(paper_count),
                "m:avg_citations": str(int(avg_citations)),
                "m:median_citations": str(median_citations),
                "m:total_citations": str(total_citations),
                "m:top_year": str(top_year) if top_year else "",
                "m:year_range": str(year_range),
            }
            venue_table.put(venue_id, row)

            processed += 1
            stats["venue_metrics"] += 1

            if processed % 10000 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  Written {stats['venue_metrics']:,} venue_metrics... ({rate:.0f}/sec)")

                self._save_checkpoint({"phase": "venue_metrics", "stats": stats})

        engine.dispose()

        print(f"venue_metrics: {stats['venue_metrics']:,} rows")

        self._save_checkpoint({"phase": "paper_citation_velocity", "stats": stats})
        return stats

    def _populate_paper_citation_velocity(self, stats: dict, checkpoint: Optional[dict], start_time: float) -> dict:
        from app.db.hbase_ import hbase_conn

        engine = self._get_mssql_engine()
        driver = self._get_neo4j_driver()

        print("Fetching citation data for velocity...")

        paper_years = {}
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id, year FROM papers WHERE year IS NOT NULL"))
            for row in result:
                paper_years[row[0]] = row[1]

        velocity_data = defaultdict(lambda: defaultdict(int))

        with driver.session() as session:
            result = session.run("""
                MATCH (citing:Paper)-[:CITES]->(cited:Paper)
                RETURN cited.id AS paper_id, citing.id AS citing_id
            """)
            for record in result:
                paper_id = record["paper_id"]
                citing_id = record["citing_id"]
                if paper_id and citing_id:
                    citing_year = paper_years.get(citing_id)
                    if citing_year:
                        velocity_data[paper_id][citing_year] += 1

        driver.close()
        engine.dispose()

        print(f"Processing {len(velocity_data)} papers for velocity...")
        velocity_table = hbase_conn.get_table("paper_citation_velocity")
        processed = 0

        for paper_id, year_counts in velocity_data.items():
            row = {f"m:citations_{year}": str(count) for year, count in year_counts.items()}
            velocity_table.put(paper_id, row)

            processed += 1
            stats["paper_citation_velocity"] += 1

            if processed % 1000 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  Written {stats['paper_citation_velocity']:,} paper_citation_velocity... ({rate:.0f}/sec)")

                self._save_checkpoint({"phase": "paper_citation_velocity", "stats": stats})

        print(f"paper_citation_velocity: {stats['paper_citation_velocity']:,} rows")

        self._save_checkpoint({"phase": "keyword_metrics", "stats": stats})
        return stats

    def _populate_keyword_metrics(self, stats: dict, checkpoint: Optional[dict], start_time: float) -> dict:
        from app.db.hbase_ import hbase_conn

        engine = self._get_mssql_engine()

        print("Computing keyword metrics from MSSQL...")

        keyword_papers = defaultdict(set)
        keyword_citations = defaultdict(int)
        keyword_years = defaultdict(list)
        keyword_cooccurrence = defaultdict(lambda: defaultdict(int))

        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT p.id, p.keywords, p.year,
                    (SELECT COUNT(*) FROM paper_references WHERE referenced_paper_id = p.id) AS citations
                FROM papers p
                WHERE p.keywords IS NOT NULL AND p.keywords != '[]'
            """))
            for row in result:
                paper_id = row[0]
                keywords_json = row[1]
                year = row[2]
                citations = row[3] or 0

                try:
                    keywords = json.loads(keywords_json)
                except Exception:
                    continue

                normalized = [self._normalize_keyword(k) for k in keywords if k]
                for kw in normalized:
                    keyword_papers[kw].add(paper_id)
                    keyword_citations[kw] += citations
                    if year:
                        keyword_years[kw].append(year)

                for i, kw1 in enumerate(normalized):
                    for kw2 in normalized[i + 1:]:
                        if kw1 and kw2:
                            keyword_cooccurrence[kw1][kw2] += 1
                            keyword_cooccurrence[kw2][kw1] += 1

        keyword_table = hbase_conn.get_table("keyword_metrics")
        processed = 0

        for keyword, papers in keyword_papers.items():
            paper_count = len(papers)
            total_citations = keyword_citations[keyword]
            avg_year = sum(keyword_years[keyword]) / paper_count if paper_count > 0 else 0

            row = {
                "m:paper_count": str(paper_count),
                "m:total_citations": str(total_citations),
                "m:avg_year": str(int(avg_year)),
            }
            keyword_table.put(keyword, row)

            cooccurring = keyword_cooccurrence[keyword]
            if cooccurring:
                top_related = sorted(cooccurring.items(), key=lambda x: x[1], reverse=True)[:100]
                related_row = {
                    f"m:co_{rel_kw}": str(count)
                    for rel_kw, count in top_related
                }
                keyword_table.put(f"{keyword}#related", related_row)

            processed += 2
            stats["keyword_metrics"] += 2

            if processed % 20000 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  Written {stats['keyword_metrics']:,} keyword_metrics... ({rate:.0f}/sec)")

                self._save_checkpoint({"phase": "keyword_metrics", "stats": stats})

        engine.dispose()

        print(f"keyword_metrics: {stats['keyword_metrics']:,} rows")

        self._save_checkpoint({"phase": "institution_metrics", "stats": stats})
        return stats

    def _populate_institution_metrics(self, stats: dict, checkpoint: Optional[dict], start_time: float) -> dict:
        from app.db.hbase_ import hbase_conn

        engine = self._get_mssql_engine()

        print("Computing institution metrics from MSSQL...")

        institution_authors = defaultdict(set)
        institution_papers = defaultdict(set)
        institution_citations = defaultdict(int)

        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT a.id, a.affiliation,
                    p.id AS paper_id,
                    (SELECT COUNT(*) FROM paper_references WHERE referenced_paper_id = p.id) AS citations
                FROM authors a
                LEFT JOIN paper_authors pa ON a.id = pa.author_id
                LEFT JOIN papers p ON pa.paper_id = p.id
            """))
            for row in result:
                author_id = row[0]
                affiliation = row[1]
                paper_id = row[2]
                citations = row[3] or 0

                if not affiliation:
                    continue

                normalized = self._normalize_institution(affiliation)
                institution_authors[normalized].add(author_id)
                if paper_id:
                    institution_papers[normalized].add(paper_id)
                    institution_citations[normalized] += citations

        institution_table = hbase_conn.get_table("institution_metrics")
        processed = 0

        for institution_id, authors in institution_authors.items():
            author_count = len(authors)
            papers = institution_papers[institution_id]
            paper_count = len(papers)
            total_citations = institution_citations[institution_id]
            avg_citations = total_citations / paper_count if paper_count > 0 else 0

            row = {
                "m:author_count": str(author_count),
                "m:paper_count": str(paper_count),
                "m:total_citations": str(total_citations),
                "m:avg_citations": str(int(avg_citations)),
            }
            institution_table.put(institution_id, row)

            processed += 1
            stats["institution_metrics"] += 1

            if processed % 10000 == 0:
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                print(f"  Written {stats['institution_metrics']:,} institution_metrics... ({rate:.0f}/sec)")

                self._save_checkpoint({"phase": "institution_metrics", "stats": stats})

        engine.dispose()

        print(f"institution_metrics: {stats['institution_metrics']:,} rows")

        self._clear_checkpoint()
        return stats

    def initialize(self, data_path=None) -> InitResult:
        try:
            tables_created = self.create_tables()

            if data_path or True:
                stats = self.populate_all_metrics()

            message = f"HBase: Tables {'created' if tables_created else 'already exist'}, "
            message += f"metrics: paper={stats['paper_metrics']:,}, author={stats['author_metrics']:,}, "
            message += f"venue={stats['venue_metrics']:,}, velocity={stats['paper_citation_velocity']:,}, "
            message += f"keyword={stats['keyword_metrics']:,}, institution={stats['institution_metrics']:,}"

            return InitResult(
                tables_created=tables_created,
                message=message,
            )
        except Exception as e:
            return InitResult(
                tables_created=False,
                message=f"HBase initialization failed: {str(e)}",
            )
