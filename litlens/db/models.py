from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from litlens.config import settings

def now(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class Library(Base):
    __tablename__ = "libraries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200)); owner_id: Mapped[str] = mapped_column(String(200), default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    documents: Mapped[list["Document"]] = relationship(cascade="all, delete-orphan")
class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id"), index=True)
    title: Mapped[str] = mapped_column(String(500)); source: Mapped[str] = mapped_column(String(40), default="upload")
    status: Mapped[str] = mapped_column(String(40), default="queued"); file_path: Mapped[str | None] = mapped_column(Text)
    arxiv_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    chunks: Mapped[list["Chunk"]] = relationship(cascade="all, delete-orphan")
class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer); text: Mapped[str] = mapped_column(Text)
    page: Mapped[int] = mapped_column(Integer, default=1); section: Mapped[str | None] = mapped_column(String(300))
    embedding: Mapped[str] = mapped_column(Text, default="[]")
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True); kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="queued"); error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
def init_db(): Base.metadata.create_all(engine)

