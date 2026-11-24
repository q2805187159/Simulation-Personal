import json
from copy import deepcopy

import httpx
from fastapi.testclient import TestClient

from app import domain
from app.main import create_app
from app.provider import MockProvider, OpenAICompatibleProvider, ProviderResult


def fixture_graph():
    options = {
        **domain.catalog()["defaults"],
        "birth_year": 1984,
        "household": "family",
        "detail": "rich",
    }
    spec = domain.compile_spec(options)["spec"]
    return domain.generate_graph(spec), spec, options


def test_seeded_states_follow_event_time_and_exclude_historical_context():
    from app.persona import build_profiles

    graph, spec, _ = fixture_graph()
    profiles = build_profiles(graph, spec["seed"])
    assert profiles == build_profiles(graph, spec["seed"])
    assert profiles.keys() == {node["event_id"] for node in graph["nodes"]}
    assert profiles["DIS-WC-2008"]["scope"] == "context"
    assert profiles["DIS-WC-2008"]["emotion"] is None
    assert profiles["DIS-WC-2008"]["memory"]["clarity"] is None
    assert profiles["birth"]["memory"]["kind"] == "archive_boundary"
    assert profiles["education"]["valid_from"] == "2002-06-30"
    assert profiles["work"]["provenance"]["previous_event_id"] == "education"
    assert profiles["work"]["emotion"] != profiles["education"]["emotion"]
    assert profiles["work"]["voice"] != profiles["work"]["tendencies"]
    for field in ("emotion", "appraisal", "tendencies", "voice"):
        assert all(0 <= value <= 100 for value in profiles["work"][field].values())
    changed = deepcopy(graph)
    changed["person"]["ethnicity"] = "另一民族输入"
    assert profiles == build_profiles(changed, spec["seed"])


def test_profiles_reject_out_of_range_and_unstructured_interpretation():
    from app.persona import build_profiles, profile_problem

    graph, spec, _ = fixture_graph()
    profiles = build_profiles(graph, spec["seed"])
    node = next(node for node in graph["nodes"] if node["event_id"] == "work")
    profile = profiles["work"]
    assert profile_problem({"profile": profile}, node) is None
    bad_metric = deepcopy(profile)
    bad_metric["emotion"]["valence"] = 101
    assert "0–100" in profile_problem({"profile": bad_metric}, node)
    bad_interpretation = deepcopy(profile)
    bad_interpretation["interpretation"]["emotion_note"] = ""
    assert "人物视角" in profile_problem({"profile": bad_interpretation}, node)


class RichProvider(MockProvider):
    name = "test-enrichment"

    def __init__(self, invalid=False):
        self.invalid = invalid
        self.seen = []

    async def run(self, role, instruction, context, fallback):
        if role == "flesh_generator":
            entries = deepcopy(fallback["entries"])
            self.seen += [item["event"]["event_id"] for item in context["entries"]]
            entries[0]["interpretation"]["scene"] = "这是一次生活节奏改变的情境。"
            if self.invalid:
                entries[0]["event_id"] = "made-up-event"
            return ProviderResult({"entries": entries}, self.name)
        return await super().run(role, instruction, context, fallback)


def test_generated_profiles_persist_with_version_and_support_model_enrichment(tmp_path):
    _, _, options = fixture_graph()
    provider = RichProvider()
    with TestClient(
        create_app(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/rich.db", provider)
    ) as client:
        job = client.post("/api/generations", json=options).json()
        response = client.post(f"/api/graphs/{job['graph_revision_id']}/continue-flesh")
        assert response.status_code == 201
        candidate = response.json()
        chunks = client.get(f"/api/flesh/{candidate['detail_revision_id']}").json()[
            "chunks"
        ]
        personal = [c for c in chunks if c["profile"]["scope"] == "synthesized"]
        assert {c["event_id"] for c in personal} == set(provider.seen)
        assert any(
            "生活节奏" in c["profile"]["interpretation"]["scene"] for c in personal
        )
        assert all(
            c["profile"]["provenance"]["model"] == provider.name for c in personal
        )
        assert all(c["profile"]["event_id"] == c["event_id"] for c in chunks)
        release = candidate["release_id"]
        assert (
            client.post(f"/api/reviews/{release}/run").json()["status"]
            == "publish_pending"
        )
        assert (
            client.post(f"/api/releases/{release}/publish").json()["status"] == "ready"
        )


def test_enrichment_rejects_model_added_events_without_advancing_the_job(tmp_path):
    _, _, options = fixture_graph()
    with TestClient(
        create_app(
            f"sqlite+aiosqlite:///{tmp_path.as_posix()}/invalid.db",
            RichProvider(invalid=True),
        )
    ) as client:
        job = client.post("/api/generations", json=options).json()
        response = client.post(f"/api/graphs/{job['graph_revision_id']}/continue-flesh")
        assert response.status_code == 503
        unchanged = client.get(f"/api/jobs/{job['job_id']}").json()
        assert unchanged["status"] == "skeleton_review"
        assert unchanged["detail_revision_id"] is None


def test_flesh_resumes_truncated_batches_and_records_single_event_fallback(
    tmp_path, monkeypatch
):
    _, _, options = fixture_graph()
    monkeypatch.setenv("LLM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("LLM_MODEL", "glm-5.3-flash")
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)
    requests = []

    def upstream(request):
        messages = json.loads(request.content)["messages"]
        entries = json.loads(messages[1]["content"])["entries"]
        event_ids = [entry["event"]["event_id"] for entry in entries]
        requests.append(event_ids)
        truncated = len(event_ids) > 1 or event_ids == ["work"]
        payload = {
            "entries": [
                {
                    "event_id": event_id,
                    "narrative": "模型根据当前事件补充了日常场景。",
                    "memory": "我还记得当时在适应节奏。",
                    "interpretation": {
                        "scene": "这是当前事件的生活场景。",
                        "motivation": "先适应眼前的变化。",
                        "emotion_note": "既紧张也期待。",
                        "reflection": "变化需要时间。",
                        "recall_cue": "追问当时的日常节奏。",
                        "speaking_sample": "我当时慢慢适应。",
                    },
                }
                for event_id in event_ids
            ]
        }
        event = {
            "choices": [
                {
                    "delta": {
                        "content": '{"entries":[' if truncated else json.dumps(payload)
                    },
                    "finish_reason": "length" if truncated else "stop",
                }
            ]
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n",
        )

    provider = OpenAICompatibleProvider(transport=httpx.MockTransport(upstream))

    class FleshProvider(MockProvider):
        name = provider.name

        async def run(self, role, instruction, context, fallback):
            if role == "flesh_generator":
                return await provider.run(role, instruction, context, fallback)
            return await super().run(role, instruction, context, fallback)

    database_url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/truncated.db"
    with TestClient(create_app(database_url, FleshProvider())) as client:
        job = client.post("/api/generations", json=options).json()
        response = client.post(f"/api/graphs/{job['graph_revision_id']}/continue-flesh")
        assert response.status_code == 201, response.json()
        candidate = response.json()
        assert candidate["status"] == "chain_review"
        chunks = client.get(f"/api/flesh/{candidate['detail_revision_id']}").json()[
            "chunks"
        ]
        personal = [
            chunk for chunk in chunks if chunk["profile"]["scope"] == "synthesized"
        ]
        assert len(requests[0]) > 1
        assert sorted(batch[0] for batch in requests if len(batch) == 1) == sorted(
            chunk["event_id"] for chunk in personal
        )
        work = next(chunk for chunk in personal if chunk["event_id"] == "work")
        assert work["profile"]["provenance"]["fallback_reason"] == "output_length"
        assert "model" not in work["profile"]["provenance"]
        assert "模型根据当前事件" not in work["narrative"]
        assert all(
            chunk["profile"]["provenance"]["model"] == provider.model
            for chunk in personal
            if chunk["event_id"] != "work"
        )
        events = client.get(f"/api/jobs/{job['job_id']}/events").json()["events"]
        assert [
            event["details"]["event_id"]
            for event in events
            if event["tool"] == "flesh.fallback"
        ] == ["work"]


class RepairingProvider(RichProvider):
    def __init__(self):
        super().__init__()
        self.reviews = 0
        self.repaired_event_ids = []

    async def run(self, role, instruction, context, fallback):
        if role == "reviewer":
            self.reviews += 1
            if self.reviews == 1:
                return ProviderResult(
                    {
                        "issues": [
                            {
                                "event_id": "work",
                                "kind": "narrative",
                                "message": "工作处境缺少与锚点相符的叙述",
                            }
                        ]
                    },
                    self.name,
                )
        if role == "flesh_repairer":
            self.repaired_event_ids = [
                entry["event"]["event_id"] for entry in context["entries"]
            ]
            entries = deepcopy(fallback["entries"])
            entries[0]["narrative"] = "刚进入劳动或学习路径时，先弄清每天要做什么。"
            return ProviderResult({"entries": entries}, self.name)
        return await super().run(role, instruction, context, fallback)


def test_repair_enriches_only_the_flagged_event_and_preserves_numeric_state(tmp_path):
    _, _, options = fixture_graph()
    provider = RepairingProvider()
    with TestClient(
        create_app(f"sqlite+aiosqlite:///{tmp_path.as_posix()}/repair.db", provider)
    ) as client:
        job = client.post("/api/generations", json=options).json()
        candidate = client.post(
            f"/api/graphs/{job['graph_revision_id']}/continue-flesh"
        ).json()
        initial_id = candidate["detail_revision_id"]
        initial = client.get(f"/api/flesh/{initial_id}").json()["chunks"]
        reviewed = client.post(f"/api/reviews/{candidate['release_id']}/run").json()
        assert reviewed["review_status"] == "repaired"
        repaired_id = reviewed["detail_revision_id"]
        assert repaired_id != initial_id
        repaired = client.get(f"/api/flesh/{repaired_id}").json()["chunks"]
        assert provider.reviews == 2
        assert provider.repaired_event_ids == ["work"]
        original_work = next(c for c in initial if c["event_id"] == "work")
        new_work = next(c for c in repaired if c["event_id"] == "work")
        assert "先弄清" in new_work["narrative"]
        assert new_work["profile"]["emotion"] == original_work["profile"]["emotion"]
        assert (
            new_work["profile"]["valid_from"] == original_work["profile"]["valid_from"]
        )
        initial_other = next(c for c in initial if c["event_id"] == "education")
        repaired_other = next(c for c in repaired if c["event_id"] == "education")
        assert repaired_other["profile"] == initial_other["profile"]
