import os
from asyncio import to_thread
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import domain
from .artifacts import read_detail, read_graph
from .chat import ChatService
from .db import Database, DetailRevision, GraphRevision, Job, JobEvent, Person, Release
from .life_state import project_life_state
from .provider import LLMProvider, ProviderError, get_provider
from .outbox import archive_release_visible, project_publication
from .skeleton import EngineError, configured_runner, validate_exchange
from .schemas import (
    ChatSessionRequest,
    ChatTurnRequest,
    GenerationOptions,
    GraphEditRequest,
    GraphEvolutionRequest,
    HistoricalAssertionRequest,
    strict_iso_date,
)
from .workflow import Workflow, WorkflowError


def create_app(
    database_url: str | None = None, provider_override: LLMProvider | None = None
) -> FastAPI:
    database = Database(database_url)
    provider = provider_override or get_provider()
    workflow = Workflow(provider)
    chat = ChatService(provider)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await database.initialize()
        yield
        await database.engine.dispose()

    app = FastAPI(
        title="Simulation Personal API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url="/api/redoc",
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
    )
    app.state.database = database
    app.state.provider = provider
    app.state.workflow = workflow
    app.state.chat = chat
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get(
            "CORS_ORIGINS", "http://127.0.0.1:5178,http://localhost:5178"
        ).split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def error(exc: Exception) -> HTTPException:
        if isinstance(exc, WorkflowError):
            return HTTPException(exc.status_code, str(exc))
        if isinstance(exc, ProviderError):
            return HTTPException(503, str(exc))
        return HTTPException(500, "服务暂时无法完成该操作，可以重试。")

    @app.exception_handler(WorkflowError)
    async def workflow_error(_request: Request, exc: WorkflowError):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        try:
            await database.health()
        except Exception as exc:
            raise HTTPException(503, "数据库不可用。") from exc
        try:
            engine = await to_thread(configured_runner().identity)
        except EngineError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {
            "status": "ok",
            "database": "ok",
            "llm": provider.name,
            "model": getattr(provider, "model", provider.name),
            "skeleton_engine": engine,
        }

    @app.get("/api/catalog")
    async def catalog() -> dict:
        return domain.catalog()

    @app.get("/api/jobs")
    async def recent_jobs(limit: int = Query(default=12, ge=1, le=50)) -> dict:
        async with database.transaction() as db:
            rows = await db.execute(
                select(
                    Job.id,
                    Job.status,
                    Job.graph_id,
                    Person.profile,
                    JobEvent.created_at,
                )
                .join(Person, Person.id == Job.person_id)
                .join(JobEvent, (JobEvent.job_id == Job.id) & (JobEvent.sequence == 1))
                .order_by(JobEvent.created_at.desc(), Job.id.desc())
                .limit(limit)
            )
            return {
                "jobs": [
                    {
                        "job_id": job_id,
                        "status": status,
                        "graph_revision_id": graph_id,
                        "person": {
                            key: profile.get(key)
                            for key in ("name", "region", "birth_year")
                        },
                        "created_at": created_at.isoformat(),
                    }
                    for job_id, status, graph_id, profile, created_at in rows
                ]
            }

    @app.post("/api/generations/spec/validate")
    async def validate_spec(options: GenerationOptions) -> dict:
        try:
            result = domain.compile_spec(options.model_dump(mode="json"))
            exchange = await to_thread(validate_exchange, result["spec"])
            return {**result, "exchange": exchange}
        except EngineError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            return {
                "valid": False,
                "spec": options.model_dump(mode="json"),
                "warnings": [str(exc)],
            }

    @app.post("/api/generations", status_code=201)
    async def generate(
        options: GenerationOptions,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key", max_length=128
        ),
    ):
        try:
            async with database.transaction(write=True) as db:
                result, fresh = await workflow.generate(
                    db, options.model_dump(mode="json"), idempotency_key
                )
                if fresh:
                    return result
                return JSONResponse(status_code=200, content=result)
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        try:
            async with database.transaction() as db:
                job = await db.get(Job, job_id)
                if job is None:
                    raise WorkflowError(404, "找不到生成任务。")
                return await workflow.view(db, job)
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str):
        try:
            async with database.transaction() as db:
                return await workflow.events(db, job_id)
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/jobs/{job_id}/graphs")
    async def job_graphs(job_id: str):
        try:
            async with database.transaction() as db:
                return await workflow.versions(db, job_id)
        except Exception as exc:
            raise error(exc) from exc

    async def load_graph(graph_id: str) -> dict:
        try:
            async with database.transaction() as db:
                revision = await db.get(GraphRevision, graph_id)
                if revision is None:
                    raise WorkflowError(404, "找不到骨架版本。")
                return await read_graph(db, revision)
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/graphs/{graph_id}")
    async def get_graph(graph_id: str):
        return await load_graph(graph_id)

    @app.get("/api/graphs/{graph_id}/state")
    async def graph_state(
        graph_id: str,
        as_of: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
        participant_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> dict:
        try:
            day = strict_iso_date(as_of)
        except ValueError as exc:
            raise HTTPException(422, "参照日期必须是有效的 YYYY-MM-DD。") from exc
        graph = await load_graph(graph_id)
        cutoff = (
            graph["person"].get("death_date")
            or graph.get("as_of")
            or graph["person"]["as_of"]
        )
        if day.isoformat() > cutoff:
            raise HTTPException(422, "参照日期超出该骨架的档案截止日。")
        try:
            return project_life_state(graph, day, participant_id=participant_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/graphs/{graph_id}/export")
    async def export_graph(graph_id: str):
        graph = await load_graph(graph_id)
        return JSONResponse(
            graph,
            headers={
                "Content-Disposition": f'attachment; filename="skeleton-{graph["graph_revision_id"]}.json"'
            },
        )

    @app.post("/api/graphs/{graph_id}/edits", status_code=201)
    async def edit_graph(
        request: GraphEditRequest,
        graph_id: str,
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key", max_length=128
        ),
    ):
        try:
            payload = request.model_dump(mode="json", exclude_unset=True)
            async with database.transaction(write=True) as db:
                result, fresh = await workflow.edit(
                    db, graph_id, payload, idempotency_key
                )
                return (
                    result if fresh else JSONResponse(status_code=200, content=result)
                )
        except Exception as exc:
            raise error(exc) from exc

    @app.post("/api/graphs/{graph_id}/history-assertions", status_code=201)
    async def add_history_assertion(
        graph_id: str,
        request: HistoricalAssertionRequest,
        idempotency_key: str | None = Header(default=None, max_length=128),
    ):
        try:
            async with database.transaction(write=True) as db:
                result, fresh = await workflow.add_history_assertion(
                    db,
                    graph_id,
                    request.model_dump(mode="json", exclude_none=True),
                    idempotency_key,
                )
                return (
                    result if fresh else JSONResponse(status_code=200, content=result)
                )
        except Exception as exc:
            raise error(exc) from exc

    @app.post("/api/graphs/{graph_id}/evolve", status_code=201)
    async def evolve_graph(
        graph_id: str,
        request: GraphEvolutionRequest,
        idempotency_key: str | None = Header(default=None, max_length=128),
    ):
        try:
            async with database.transaction(write=True) as db:
                result, fresh = await workflow.evolve(
                    db, graph_id, request.mode, request.as_of, idempotency_key
                )
                return (
                    result if fresh else JSONResponse(status_code=200, content=result)
                )
        except Exception as exc:
            raise error(exc) from exc

    @app.post("/api/graphs/{graph_id}/continue-flesh", status_code=201)
    async def continue_flesh(graph_id: str):
        try:
            async with database.transaction(write=True) as db:
                result, fresh = await workflow.continue_flesh(db, graph_id)
                return (
                    result if fresh else JSONResponse(status_code=200, content=result)
                )
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/flesh/{detail_id}")
    async def get_flesh(detail_id: str):
        try:
            async with database.transaction() as db:
                detail = await db.get(DetailRevision, detail_id)
                if detail is None:
                    raise WorkflowError(404, "找不到细节版本。")
                return await read_detail(db, detail)
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/reviews/{release_id}")
    async def get_review(release_id: str):
        try:
            async with database.transaction() as db:
                return await workflow.review_status(db, release_id)
        except Exception as exc:
            raise error(exc) from exc

    @app.post("/api/reviews/{release_id}/run")
    async def run_review(release_id: str):
        try:
            async with database.transaction(write=True) as db:
                return await workflow.review(db, release_id)
        except Exception as exc:
            raise error(exc) from exc

    @app.post("/api/releases/{release_id}/publish")
    async def publish(release_id: str):
        try:
            async with database.transaction(write=True) as db:
                result = await workflow.publish(db, release_id)
            async with database.transaction(write=True) as db:
                await project_publication(db, release_id)
            return result
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/archive/persons")
    async def archive():
        try:
            async with database.transaction() as db:
                return await workflow.archive(db)
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/archive/releases/{release_id}")
    async def archive_release(release_id: str):
        async with database.transaction() as db:
            release = await db.scalar(
                select(Release).where(
                    Release.id == release_id, archive_release_visible()
                )
            )
            if not release:
                raise WorkflowError(404, "找不到已发布档案。")
            graph = await db.get(GraphRevision, release.graph_id)
            detail = await db.get(DetailRevision, release.detail_id)
            if not graph or not detail or detail.graph_id != graph.id:
                raise WorkflowError(409, "档案的骨架与细节版本无法对齐。")
            return {
                "release_id": release_id,
                "snapshot_id": release.snapshot_id,
                "published_at": release.published_at.isoformat(),
                "synthetic": True,
                "graph": await read_graph(db, graph),
                "detail": await read_detail(db, detail),
                "review": await workflow.review_status(db, release_id),
            }

    @app.post("/api/chat/sessions", status_code=201)
    async def create_chat(request: ChatSessionRequest):
        try:
            async with database.transaction(write=True) as db:
                return await chat.create(
                    db,
                    request.person_id,
                    request.release_id,
                    request.as_of,
                    request.role_scope,
                )
        except ValueError as exc:
            raise HTTPException(
                422, "聊天会话的 person_id、release_id 和 as_of 必须有效。"
            ) from exc
        except Exception as exc:
            raise error(exc) from exc

    @app.get("/api/chat/sessions/{session_id}")
    async def get_chat(session_id: str):
        try:
            async with database.transaction() as db:
                return await chat.get(db, session_id)
        except Exception as exc:
            raise error(exc) from exc

    @app.post("/api/chat/sessions/{session_id}/turns", status_code=201)
    async def chat_turn(session_id: str, request: ChatTurnRequest):
        try:
            async with database.transaction(write=True) as db:
                return await chat.turn(db, session_id, request.question)
        except Exception as exc:
            raise error(exc) from exc

    return app


app = create_app()
