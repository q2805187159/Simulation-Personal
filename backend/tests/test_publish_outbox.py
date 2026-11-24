import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main
from app.artifacts import WorkflowError
from app.chat import ChatService
from app.db import (
    Database,
    DetailChunk,
    DetailRevision,
    GraphNode,
    GraphRevision,
    Job,
    Person,
    PublishOutbox,
    Release,
    Spec,
    utcnow,
)
from app.outbox import (
    archive_release_visible,
    enqueue_publication,
    get_projection_receipt,
    project_publication,
    read_exact_detail_chunks,
)
from app.provider import MockProvider


async def seed_release(database):
    await database.initialize()
    async with database.transaction(write=True) as session:
        session.add_all(
            [
                Person(id="person-1", profile={}),
                Spec(id="spec-1", payload={}, warnings=[]),
            ]
        )
        await session.flush()
        session.add(
            Job(
                id="job-1",
                spec_id="spec-1",
                person_id="person-1",
                input_digest="digest",
                status="publish_pending",
                progress=90,
                model={},
            )
        )
        await session.flush()
        session.add(
            GraphRevision(
                id="graph-1", job_id="job-1", branch="main", ordinal=1, metadata_json={}
            )
        )
        await session.flush()
        session.add_all(
            [
                DetailRevision(id="detail-1", graph_id="graph-1"),
                DetailRevision(id="detail-2", graph_id="graph-1"),
                GraphNode(
                    graph_id="graph-1",
                    position=0,
                    event_id="event-1",
                    payload={"event_id": "event-1"},
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Release(
                    id="release-1",
                    graph_id="graph-1",
                    detail_id="detail-1",
                    person_id="person-1",
                    snapshot_id="snapshot-1",
                    status="accepted",
                ),
                DetailChunk(
                    detail_id="detail-1",
                    position=0,
                    payload={"event_id": "event-1", "narrative": "published version"},
                ),
                DetailChunk(
                    detail_id="detail-2",
                    position=0,
                    payload={"event_id": "event-1", "narrative": "later version"},
                ),
            ]
        )


async def visible_release_ids(session):
    return list(
        await session.scalars(select(Release.id).where(archive_release_visible()))
    )


def test_release_and_message_rollback_together(tmp_path):
    async def run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
        try:
            await seed_release(database)
            with pytest.raises(RuntimeError, match="abort publication"):
                async with database.transaction(write=True) as session:
                    release = await session.get(Release, "release-1")
                    release.status = "published"
                    release.published_at = utcnow()
                    message = await enqueue_publication(session, release)
                    assert message.manifest == {
                        "release_id": "release-1",
                        "snapshot_id": "snapshot-1",
                        "graph_revision_id": "graph-1",
                        "detail_revision_id": "detail-1",
                        "person_id": "person-1",
                    }
                    raise RuntimeError("abort publication")
            async with database.transaction() as session:
                release = await session.get(Release, "release-1")
                assert release.status == "accepted"
                assert release.published_at is None
                assert list(await session.scalars(select(PublishOutbox))) == []
        finally:
            await database.engine.dispose()

    asyncio.run(run())


def test_exact_projection_retries_reuse_receipt_and_pinned_detail(tmp_path):
    async def run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'projection.db'}")
        try:
            await seed_release(database)
            async with database.transaction(write=True) as session:
                release = await session.get(Release, "release-1")
                release.status = "published"
                release.published_at = utcnow()
                message = await enqueue_publication(session, release)
                assert (
                    await enqueue_publication(session, release)
                ).message_id == message.message_id

            async with database.transaction() as session:
                assert await visible_release_ids(session) == []
                assert await get_projection_receipt(session, "release-1") is None
            async with database.transaction(write=True) as session:
                chat = ChatService(MockProvider())
                with pytest.raises(WorkflowError) as exc:
                    await chat.create(
                        session, "person-1", "release-1", utcnow().date(), "self"
                    )
                assert exc.value.status_code == 404

            async def project():
                async with database.transaction(write=True) as session:
                    return await project_publication(session, "release-1")

            first, replay = await asyncio.gather(project(), project())
            assert first.message_id == replay.message_id == message.message_id
            assert first.projected_at == replay.projected_at
            assert first.event_count == 1
            async with database.transaction() as session:
                assert await visible_release_ids(session) == ["release-1"]
                receipt = await get_projection_receipt(session, "release-1")
                assert receipt.snapshot_id == "snapshot-1"
                assert await read_exact_detail_chunks(
                    session, receipt, ["unknown-event", "event-1"]
                ) == [{"event_id": "event-1", "narrative": "published version"}]
        finally:
            await database.engine.dispose()

    asyncio.run(run())


def test_incomplete_projection_can_resume_after_new_transaction(tmp_path):
    async def run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
        try:
            await seed_release(database)
            async with database.transaction(write=True) as session:
                release = await session.get(Release, "release-1")
                release.status = "published"
                release.published_at = utcnow()
                message = await enqueue_publication(session, release)
                await session.delete(await session.get(DetailChunk, ("detail-1", 0)))

            with pytest.raises(WorkflowError):
                async with database.transaction(write=True) as session:
                    await project_publication(session, "release-1")
            async with database.transaction(write=True) as session:
                assert await visible_release_ids(session) == []
                assert await get_projection_receipt(session, "release-1") is None
                session.add(
                    DetailChunk(
                        detail_id="detail-1",
                        position=0,
                        payload={"event_id": "event-1"},
                    )
                )
            async with database.transaction(write=True) as session:
                receipt = await project_publication(session, "release-1")
                assert receipt.message_id == message.message_id
            async with database.transaction() as session:
                assert await visible_release_ids(session) == ["release-1"]
        finally:
            await database.engine.dispose()

    asyncio.run(run())


def test_legacy_published_release_is_visible(tmp_path):
    async def run():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        try:
            await seed_release(database)
            async with database.transaction(write=True) as session:
                release = await session.get(Release, "release-1")
                release.status = "published"
                release.published_at = utcnow()
            async with database.transaction() as session:
                assert await visible_release_ids(session) == ["release-1"]
        finally:
            await database.engine.dispose()

    asyncio.run(run())


def test_publish_api_retries_the_committed_message_after_projection_failure(
    tmp_path, monkeypatch
):
    app = main.create_app(
        f"sqlite+aiosqlite:///{tmp_path / 'publish-api.db'}",
        provider_override=MockProvider(),
    )

    async def read_publication(release_id):
        async with app.state.database.transaction() as session:
            message = await session.get(PublishOutbox, release_id)
            receipt = await get_projection_receipt(session, release_id)
            return message.message_id if message else None, receipt

    async def unavailable_projection(_session, release_id):
        message_id, receipt = await read_publication(release_id)
        assert message_id and receipt is None
        raise RuntimeError("projection backend temporarily unavailable")

    with TestClient(app) as client:
        options = client.get("/api/catalog").json()["defaults"]
        job = client.post("/api/generations", json=options).json()
        graph_id = job["graph_revision_id"]
        detail = client.post(f"/api/graphs/{graph_id}/continue-flesh").json()
        release_id = detail["release_id"]
        assert client.post(f"/api/reviews/{release_id}/run").is_success

        publish_url = f"/api/releases/{release_id}/publish"
        release_url = f"/api/archive/releases/{release_id}"
        chat_request = {
            "person_id": job["person"]["person_id"],
            "release_id": release_id,
            "as_of": job["person"]["as_of"],
        }
        with monkeypatch.context() as fault:
            fault.setattr(main, "project_publication", unavailable_projection)
            assert client.post(publish_url).status_code >= 500

        message_id, receipt = client.portal.call(read_publication, release_id)
        assert message_id and receipt is None
        assert client.get("/api/archive/persons").json()["persons"] == []
        assert client.get(release_url).status_code == 404
        assert client.post("/api/chat/sessions", json=chat_request).status_code == 404

        retry = client.post(publish_url)
        assert retry.is_success
        assert retry.json()["status"] == "ready"
        retry_message_id, receipt = client.portal.call(read_publication, release_id)
        assert retry_message_id == receipt.message_id == message_id
        assert client.get(release_url).is_success
        assert client.post("/api/chat/sessions", json=chat_request).status_code == 201
