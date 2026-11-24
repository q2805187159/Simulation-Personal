from datetime import timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import (
    DetailChunk,
    DetailRevision,
    GraphEdge,
    GraphNode,
    GraphRevision,
    Job,
    JobEvent,
    new_id,
)


class WorkflowError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


async def lock_command(session: AsyncSession, key: str) -> None:
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )


async def lock_job(session: AsyncSession, job_id: str) -> Job:
    job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise WorkflowError(404, "找不到生成任务。")
    return job


async def record(
    session: AsyncSession, job: Job, stage: str, tool: str, message: str, **details: Any
) -> None:
    count = await session.scalar(
        select(func.max(JobEvent.sequence)).where(JobEvent.job_id == job.id)
    )
    session.add(
        JobEvent(
            job_id=job.id,
            sequence=(count or 0) + 1,
            stage=stage,
            tool=tool,
            message=message,
            details=details,
        )
    )


def _recorded_at(revision: GraphRevision) -> str:
    created = revision.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.astimezone(timezone.utc).isoformat()


def _event_content(node: dict) -> dict:
    return {key: value for key, value in node.items() if key != "recorded_at"}


async def read_graph(session: AsyncSession, revision: GraphRevision) -> dict:
    nodes = await session.scalars(
        select(GraphNode.payload)
        .where(GraphNode.graph_id == revision.id)
        .order_by(GraphNode.position)
    )
    edges = await session.scalars(
        select(GraphEdge.payload)
        .where(GraphEdge.graph_id == revision.id)
        .order_by(GraphEdge.position)
    )
    return {
        **revision.metadata_json,
        "graph_revision_id": revision.id,
        "parent_revision_id": revision.parent_id,
        "branch": revision.branch,
        "nodes": [
            node
            if "recorded_at" in node
            else {**node, "recorded_at": _recorded_at(revision)}
            for node in nodes
        ],
        "edges": list(edges),
    }


async def save_graph(
    session: AsyncSession,
    job: Job,
    graph: dict,
    parent: GraphRevision | None = None,
    *,
    command_key: str | None = None,
    command_digest: str | None = None,
) -> GraphRevision:
    ordinal = (
        await session.scalar(
            select(func.max(GraphRevision.ordinal)).where(
                GraphRevision.job_id == job.id
            )
        )
        or 0
    ) + 1
    revision = GraphRevision(
        job_id=job.id,
        parent_id=parent.id if parent else None,
        branch=f"branch-{ordinal}" if parent else "main",
        ordinal=ordinal,
        command_key=command_key,
        command_digest=command_digest,
        metadata_json={
            key: value for key, value in graph.items() if key not in {"nodes", "edges"}
        },
    )
    session.add(revision)
    await session.flush()
    previous = {}
    if parent:
        previous = {
            node["event_id"]: node
            for node in await session.scalars(
                select(GraphNode.payload).where(GraphNode.graph_id == parent.id)
            )
        }
    recorded_at = _recorded_at(revision)
    payloads = []
    for node in graph["nodes"]:
        old = previous.get(node["event_id"])
        if old is not None and _event_content(old) == _event_content(node):
            event_recorded_at = old.get("recorded_at") or _recorded_at(parent)
        else:
            event_recorded_at = recorded_at
        payloads.append({**node, "recorded_at": event_recorded_at})
    session.add_all(
        GraphNode(
            graph_id=revision.id, position=i, event_id=node["event_id"], payload=node
        )
        for i, node in enumerate(payloads)
    )
    session.add_all(
        GraphEdge(graph_id=revision.id, position=i, payload=edge)
        for i, edge in enumerate(graph["edges"])
    )
    return revision


async def save_detail(
    session: AsyncSession,
    graph_id: str,
    chunks: list[dict],
    parent_id: str | None = None,
) -> DetailRevision:
    revision = DetailRevision(graph_id=graph_id, parent_id=parent_id)
    session.add(revision)
    await session.flush()
    session.add_all(
        DetailChunk(
            detail_id=revision.id, position=i, payload={**chunk, "chunk_id": new_id()}
        )
        for i, chunk in enumerate(chunks)
    )
    return revision


async def read_detail(session: AsyncSession, revision: DetailRevision) -> dict:
    chunks = await session.scalars(
        select(DetailChunk.payload)
        .where(DetailChunk.detail_id == revision.id)
        .order_by(DetailChunk.position)
    )
    return {
        "detail_revision_id": revision.id,
        "graph_revision_id": revision.graph_id,
        "chunks": list(chunks),
    }
