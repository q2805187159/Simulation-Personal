import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Spec(Base):
    __tablename__ = "generation_specs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON)


class Person(Base):
    __tablename__ = "persons"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "workflow_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), default=new_id)
    spec_id: Mapped[str] = mapped_column(ForeignKey("generation_specs.id"))
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    input_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    progress: Mapped[int] = mapped_column(Integer)
    graph_id: Mapped[str | None] = mapped_column(String(36))
    detail_id: Mapped[str | None] = mapped_column(String(36))
    release_id: Mapped[str | None] = mapped_column(String(36))
    error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[dict[str, Any]] = mapped_column(JSON)


class JobEvent(Base):
    __tablename__ = "workflow_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("workflow_jobs.id"), index=True)
    sequence: Mapped[int]
    stage: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (UniqueConstraint("job_id", "sequence"),)


class GraphRevision(Base):
    __tablename__ = "graph_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("workflow_jobs.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("graph_revisions.id"))
    branch: Mapped[str] = mapped_column(String(64))
    ordinal: Mapped[int]
    command_key: Mapped[str | None] = mapped_column(String(128))
    command_digest: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    __table_args__ = (
        UniqueConstraint("job_id", "ordinal"),
        UniqueConstraint("parent_id", "command_key"),
    )


class GraphNode(Base):
    __tablename__ = "graph_nodes"
    graph_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("graph_id", "event_id"),)


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    graph_id: Mapped[str] = mapped_column(
        ForeignKey("graph_revisions.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class DetailRevision(Base):
    __tablename__ = "flesh_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graph_revisions.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("flesh_revisions.id"))


class DetailChunk(Base):
    __tablename__ = "flesh_chunks"
    detail_id: Mapped[str] = mapped_column(
        ForeignKey("flesh_revisions.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class Release(Base):
    __tablename__ = "releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(ForeignKey("graph_revisions.id"), unique=True)
    detail_id: Mapped[str] = mapped_column(ForeignKey("flesh_revisions.id"))
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(36), unique=True, default=new_id)
    status: Mapped[str] = mapped_column(String(24))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublishOutbox(Base):
    __tablename__ = "publish_outbox"
    release_id: Mapped[str] = mapped_column(ForeignKey("releases.id"), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), unique=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(String(36))
    graph_id: Mapped[str] = mapped_column(ForeignKey("graph_revisions.id"))
    detail_id: Mapped[str] = mapped_column(ForeignKey("flesh_revisions.id"))
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    @property
    def manifest(self) -> dict[str, str]:
        return {
            "release_id": self.release_id,
            "snapshot_id": self.snapshot_id,
            "graph_revision_id": self.graph_id,
            "detail_revision_id": self.detail_id,
            "person_id": self.person_id,
        }


class ExactProjectionReceipt(Base):
    __tablename__ = "exact_projection_receipts"
    release_id: Mapped[str] = mapped_column(
        ForeignKey("publish_outbox.release_id"), primary_key=True
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("publish_outbox.message_id"), unique=True
    )
    snapshot_id: Mapped[str] = mapped_column(String(36))
    graph_id: Mapped[str] = mapped_column(ForeignKey("graph_revisions.id"))
    detail_id: Mapped[str] = mapped_column(ForeignKey("flesh_revisions.id"))
    person_id: Mapped[str] = mapped_column(ForeignKey("persons.id"))
    event_count: Mapped[int] = mapped_column(Integer)
    projected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ExactEventProjection(Base):
    __tablename__ = "exact_event_projections"
    release_id: Mapped[str] = mapped_column(
        ForeignKey("exact_projection_receipts.release_id"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    graph_position: Mapped[int] = mapped_column(Integer)
    detail_position: Mapped[int] = mapped_column(Integer)


class Finding(Base):
    __tablename__ = "review_findings"
    release_id: Mapped[str] = mapped_column(ForeignKey("releases.id"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    release_id: Mapped[str] = mapped_column(ForeignKey("releases.id"))
    detail_id: Mapped[str] = mapped_column(ForeignKey("flesh_revisions.id"))
    as_of: Mapped[str] = mapped_column(String(10))
    archive_as_of: Mapped[str] = mapped_column(String(10))
    role_scope: Mapped[str] = mapped_column(String(24))


class ChatTurn(Base):
    __tablename__ = "chat_turns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    position: Mapped[int]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("session_id", "position"),)


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get(
            "DATABASE_URL",
            "postgresql+asyncpg://simulation:simulation@127.0.0.1:5432/simulation",
        )
        self.engine = create_async_engine(self.url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.is_sqlite = self.engine.dialect.name == "sqlite"
        if self.is_sqlite:
            event.listen(self.engine.sync_engine, "connect", self._sqlite_setup)

    @staticmethod
    def _sqlite_setup(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            if not self.is_sqlite:
                await conn.execute(text("SELECT pg_advisory_xact_lock(70933688439)"))
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def transaction(self, *, write: bool = False) -> AsyncIterator[AsyncSession]:
        async with self.sessions.begin() as session:
            if write and self.is_sqlite:
                await session.execute(text("BEGIN IMMEDIATE"))
            yield session

    async def health(self) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM workflow_jobs WHERE 1=0"))
