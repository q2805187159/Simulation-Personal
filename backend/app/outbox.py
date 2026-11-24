from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from .artifacts import WorkflowError
from .db import (
    DetailChunk,
    DetailRevision,
    ExactEventProjection,
    ExactProjectionReceipt,
    GraphNode,
    GraphRevision,
    PublishOutbox,
    Release,
)


async def enqueue_publication(session: AsyncSession, release: Release) -> PublishOutbox:
    """Freeze the release identity in the caller's locked publication transaction."""
    message = await session.get(PublishOutbox, release.id)
    if message is None:
        message = PublishOutbox(
            release_id=release.id,
            snapshot_id=release.snapshot_id,
            graph_id=release.graph_id,
            detail_id=release.detail_id,
            person_id=release.person_id,
        )
        session.add(message)
        await session.flush()
    return message


def _matching_projection():
    return (
        select(ExactProjectionReceipt)
        .join(
            PublishOutbox, PublishOutbox.release_id == ExactProjectionReceipt.release_id
        )
        .where(
            PublishOutbox.release_id == Release.id,
            PublishOutbox.snapshot_id == Release.snapshot_id,
            PublishOutbox.graph_id == Release.graph_id,
            PublishOutbox.detail_id == Release.detail_id,
            PublishOutbox.person_id == Release.person_id,
            ExactProjectionReceipt.message_id == PublishOutbox.message_id,
            ExactProjectionReceipt.snapshot_id == PublishOutbox.snapshot_id,
            ExactProjectionReceipt.graph_id == PublishOutbox.graph_id,
            ExactProjectionReceipt.detail_id == PublishOutbox.detail_id,
            ExactProjectionReceipt.person_id == PublishOutbox.person_id,
        )
    )


def archive_release_visible() -> ColumnElement[bool]:
    queued = select(PublishOutbox.release_id).where(
        PublishOutbox.release_id == Release.id
    )
    return and_(
        Release.status == "published",
        or_(
            ~queued.correlate(Release).exists(),
            _matching_projection().correlate(Release).exists(),
        ),
    )


async def get_projection_receipt(
    session: AsyncSession, release_id: str
) -> ExactProjectionReceipt | None:
    return await session.scalar(_matching_projection().where(Release.id == release_id))


async def project_publication(
    session: AsyncSession, release_id: str
) -> ExactProjectionReceipt | None:
    """Consume a committed publication message under a lock in a write transaction."""
    message = await session.scalar(
        select(PublishOutbox)
        .where(PublishOutbox.release_id == release_id)
        .with_for_update()
    )
    if message is None:
        return None

    release = await session.get(Release, release_id)
    if (
        release is None
        or release.status not in {"accepted", "published"}
        or message.snapshot_id != release.snapshot_id
        or message.graph_id != release.graph_id
        or message.detail_id != release.detail_id
        or message.person_id != release.person_id
    ):
        raise WorkflowError(409, "发布清单与当前档案版本不一致。")

    existing_receipt = await session.get(ExactProjectionReceipt, release_id)
    if existing_receipt is not None:
        if await get_projection_receipt(session, release_id) is None:
            raise WorkflowError(409, "精确检索回执与发布清单不一致。")
        return existing_receipt

    revision = await session.get(GraphRevision, message.graph_id)
    detail = await session.get(DetailRevision, message.detail_id)
    if revision is None or detail is None or detail.graph_id != revision.id:
        raise WorkflowError(409, "发布清单中的骨架与细节版本无法对齐。")

    nodes = (
        await session.execute(
            select(GraphNode.event_id, GraphNode.position).where(
                GraphNode.graph_id == revision.id
            )
        )
    ).all()
    chunks = (
        await session.execute(
            select(
                DetailChunk.payload["event_id"].as_string(), DetailChunk.position
            ).where(DetailChunk.detail_id == detail.id)
        )
    ).all()
    chunk_positions = dict(chunks)
    if (
        not nodes
        or len(chunk_positions) != len(chunks)
        or chunk_positions.keys() != {event_id for event_id, _ in nodes}
    ):
        raise WorkflowError(409, "发布清单中的细节未能逐一对应骨架事件。")

    receipt = ExactProjectionReceipt(
        release_id=message.release_id,
        message_id=message.message_id,
        snapshot_id=message.snapshot_id,
        graph_id=message.graph_id,
        detail_id=message.detail_id,
        person_id=message.person_id,
        event_count=len(nodes),
    )
    session.add(receipt)
    await session.flush()
    session.add_all(
        ExactEventProjection(
            release_id=message.release_id,
            event_id=event_id,
            graph_position=graph_position,
            detail_position=chunk_positions[event_id],
        )
        for event_id, graph_position in nodes
    )
    await session.flush()
    await session.refresh(receipt)
    return receipt


async def read_exact_detail_chunks(
    session: AsyncSession, receipt: ExactProjectionReceipt, event_ids: Sequence[str]
) -> list[dict[str, Any]]:
    return list(
        await session.scalars(
            select(DetailChunk.payload)
            .select_from(ExactEventProjection)
            .join(
                GraphNode,
                and_(
                    GraphNode.graph_id == receipt.graph_id,
                    GraphNode.position == ExactEventProjection.graph_position,
                    GraphNode.event_id == ExactEventProjection.event_id,
                ),
            )
            .join(
                DetailChunk,
                and_(
                    DetailChunk.detail_id == receipt.detail_id,
                    DetailChunk.position == ExactEventProjection.detail_position,
                ),
            )
            .where(
                ExactEventProjection.release_id == receipt.release_id,
                ExactEventProjection.event_id.in_(event_ids),
            )
            .order_by(ExactEventProjection.detail_position)
        )
    )
