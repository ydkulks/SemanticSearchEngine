from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Venue(Base):
    __tablename__ = "venues"

    id = Column(String(36), primary_key=True)
    name = Column(String(500), nullable=False)
    type = Column(String(50), nullable=True)

    papers = relationship("Paper", back_populates="venue")


class Author(Base):
    __tablename__ = "authors"

    id = Column(String(36), primary_key=True)
    name = Column(String(500), nullable=False)
    affiliation = Column(Text, nullable=True)
    orcid = Column(String(50), nullable=True)

    papers = relationship(
        "Paper",
        secondary="paper_authors",
        back_populates="authors",
    )


class Paper(Base):
    __tablename__ = "papers"

    id = Column(String(36), primary_key=True)
    title = Column(Text, nullable=False)
    abstract = Column(Text, nullable=True)
    year = Column(Integer, nullable=True)
    venue_id = Column(String(36), ForeignKey("venues.id"), nullable=True)
    keywords = Column(JSON, nullable=True)
    doi = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    venue = relationship("Venue", back_populates="papers")
    authors = relationship(
        "Author",
        secondary="paper_authors",
        back_populates="papers",
    )


class PaperAuthor(Base):
    __tablename__ = "paper_authors"

    id = Column(String(100), primary_key=True, default=lambda: str(uuid4()))
    paper_id = Column(String(36), ForeignKey("papers.id"), nullable=False)
    author_id = Column(String(36), ForeignKey("authors.id"), nullable=False)
    author_order = Column(Integer, nullable=True)


class PaperReference(Base):
    __tablename__ = "paper_references"

    id = Column(String(100), primary_key=True, default=lambda: str(uuid4()))
    paper_id = Column(String(36), ForeignKey("papers.id"), nullable=False)
    referenced_paper_id = Column(String(36), ForeignKey("papers.id"), nullable=True)
    reference_external_id = Column(String(100), nullable=True)


class Embedding(Base):
    __tablename__ = "embeddings"

    paper_id = Column(String(36), ForeignKey("papers.id"), primary_key=True)
    embedding = Column(Text, nullable=False)
    model_name = Column(String(100), default="all-MiniLM-L6-v2")
    created_at = Column(DateTime, default=datetime.utcnow)


class SearchHistory(Base):
    __tablename__ = "search_history"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    query = Column(Text, nullable=False)
    sources_queried = Column(String(100), nullable=True)
    results_count = Column(String(10), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
