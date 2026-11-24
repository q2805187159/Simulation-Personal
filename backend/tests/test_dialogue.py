import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.dialogue import DialoguePlan, project_detail
from app.provider import MockProvider, ProviderResult


class RecordingProvider(MockProvider):
    def __init__(self):
        self.calls = []
        self.plan_override = None
        self.answer_override = None

    async def run(self, role, instruction, context, fallback):
        self.calls.append((role, context))
        override = {
            "dialogue_planner": self.plan_override,
            "persona_qa": self.answer_override,
        }.get(role)
        if override is not None:
            return ProviderResult(override, self.name)
        return await super().run(role, instruction, context, fallback)


@pytest.fixture
def published(tmp_path):
    provider = RecordingProvider()
    url = f"sqlite+aiosqlite:///{(tmp_path / 'dialogue.db').as_posix()}"
    with TestClient(create_app(url, provider)) as client:
        options = {
            "region": "51",
            "ethnicity": "01",
            "birth_year": 1984,
            "as_of": "2025-01-01",
            "entry": "birth_cohort",
            "household": "family",
            "detail": "standard",
            "seed": 7,
            "life_status": "living",
        }
        job = _post(client, "/api/generations", options)
        graph_id = job["graph_revision_id"]
        candidate = _post(client, f"/api/graphs/{graph_id}/continue-flesh")
        release_id = candidate["release_id"]
        _post(client, f"/api/reviews/{release_id}/run")
        _post(client, f"/api/releases/{release_id}/publish")
        yield (
            client,
            provider,
            {
                "person_id": job["person"]["person_id"],
                "release_id": release_id,
            },
        )


def _post(client, path, payload=None):
    response = client.post(path, json=payload)
    assert response.is_success, response.text
    return response.json()


def _session(client, release, as_of):
    return _post(client, "/api/chat/sessions", {**release, "as_of": as_of})


def _turn(client, session, question):
    return _post(
        client,
        f"/api/chat/sessions/{session['session_id']}/turns",
        {"question": question},
    )


def test_first_social_life_question_uses_published_detail_in_one_model_call(published):
    client, provider, release = published
    session = _session(client, release, "2025-01-01")
    detail = client.get(f"/api/flesh/{session['detail_revision_id']}").json()
    work = next(chunk for chunk in detail["chunks"] if chunk["event_id"] == "work")
    provider.calls.clear()

    turn = _turn(client, session, "能说说你初入社会时的生活吗？")

    assert turn["disclosure"] == "brief"
    roles = [role for role, _ in provider.calls]
    assert roles == ["persona_qa"]
    evidence = turn["evidence"][0]
    assert evidence["event_id"] == "work"
    assert evidence["chunk_id"] == work["chunk_id"]
    assert evidence["excerpt"] == work["narrative"][:350]
    context = provider.calls[0][1]
    assert context["memory"][0]["narrative"] == work["narrative"]
    assert context["memory"][0]["memory"] == work["memory"]
    assert work["memory"] in turn["answer"]
    assert any(tool["name"] == "memory.get" for tool in turn["tools"])
    assert turn["duration_ms"] >= 0
    assert all(tool["duration_ms"] >= 0 for tool in turn["tools"])


def test_first_social_life_question_respects_cutoff_before_work(published):
    client, provider, release = published
    session = _session(client, release, "2003-12-31")
    provider.calls.clear()

    turn = _turn(client, session, "能说说你初入社会时的生活吗？")

    assert turn["evidence"] == []
    assert provider.calls == []


def test_published_edit_keeps_valid_knowledge_and_recording_times_distinct(published):
    client, provider, release = published
    original_session = _session(client, release, "2011-12-31")
    original_graph = client.get(
        f"/api/graphs/{original_session['graph_revision_id']}"
    ).json()
    original_nodes = {node["event_id"]: node for node in original_graph["nodes"]}
    edited = _post(
        client,
        f"/api/graphs/{original_session['graph_revision_id']}/edits",
        {
            "source": "manual",
            "operations": [
                {
                    "op": "update",
                    "event_id": "work",
                    "known_at": {release["person_id"]: "2012-01-01"},
                },
            ],
        },
    )
    graph_id = edited["graph_revision_id"]
    graph = client.get(f"/api/graphs/{graph_id}").json()
    nodes = {node["event_id"]: node for node in graph["nodes"]}
    assert nodes["work"]["start"] == original_nodes["work"]["start"]
    assert nodes["work"]["known_at"] == {release["person_id"]: "2012-01-01"}
    assert nodes["work"]["recorded_at"] > original_nodes["work"]["recorded_at"]
    assert nodes["birth"]["recorded_at"] == original_nodes["birth"]["recorded_at"]

    candidate = _post(client, f"/api/graphs/{graph_id}/continue-flesh")
    new_release_id = candidate["release_id"]
    _post(client, f"/api/reviews/{new_release_id}/run")
    _post(client, f"/api/releases/{new_release_id}/publish")
    updated_release = {**release, "release_id": new_release_id}
    question = "能说说你初入社会时的生活吗？"
    early = _session(client, updated_release, "2011-12-31")
    provider.calls.clear()
    assert _turn(client, early, question)["evidence"] == []
    assert provider.calls == []
    after_knowledge = _session(client, updated_release, "2012-01-01")
    evidence = _turn(client, after_knowledge, question)["evidence"][0]
    assert evidence["event_id"] == "work"
    assert evidence["start"] == original_nodes["work"]["start"]
    assert evidence["known_at"] == "2012-01-01"
    assert evidence["recorded_at"] == nodes["work"]["recorded_at"]
    assert (
        _turn(client, original_session, question)["evidence"][0]["event_id"] == "work"
    )


def test_follow_up_reuses_prior_anchors_and_discloses_event_detail(published):
    client, provider, release = published
    session = _session(client, release, "2025-01-01")
    provider.answer_override = {
        "answer": "我当时有一段紧凑的日程。",
        "cited_event_ids": ["work"],
    }
    initial = _turn(client, session, "你进入劳动或继续教育路径是什么时候？")
    provider.answer_override = None
    follow_up = _turn(client, session, "那段经历能展开讲讲吗？当时有什么感受？")

    assert initial["evidence"][0]["event_id"] == "work"
    assert initial["disclosure"] == "brief"
    assert follow_up["follow_up_to"] == initial["turn_id"]
    assert follow_up["disclosure"] == "expanded"
    assert follow_up["evidence"][0]["event_id"] == "work"
    assert any(
        tool["name"] == "memory.get" and "work" in tool["event_ids"]
        for tool in follow_up["tools"]
    )
    assert any(
        tool["name"] == "dialogue.plan"
        and tool.get("strategy") == "conversation_anchor"
        for tool in follow_up["tools"]
    )
    arguments = [ctx for role, ctx in provider.calls if role == "persona_qa"][-1]
    assert any(item["event_id"] == "work" for item in arguments["memory"])
    assert initial["answer"] not in repr(arguments)
    restored = client.get(f"/api/chat/sessions/{session['session_id']}").json()
    assert restored["turns"] == [initial, follow_up]


def test_model_cannot_select_out_of_scope_events_or_invent_citations(published):
    client, provider, release = published
    session = _session(client, release, "2007-12-31")
    provider.plan_override = {"event_ids": ["DIS-WC-2008"]}
    provider.answer_override = {
        "answer": "我可以肯定自己亲眼看见了地震。",
        "cited_event_ids": ["DIS-WC-2008"],
    }
    turn = _turn(client, session, "小时候的教育经历呢？")

    planner = [ctx for role, ctx in provider.calls if role == "dialogue_planner"][-1]
    assert "DIS-WC-2008" not in {node["event_id"] for node in planner["catalog"]}
    assert turn["cited_event_ids"]
    assert "DIS-WC-2008" not in turn["cited_event_ids"]
    assert "亲眼看见" not in turn["answer"]
    assert any(
        tool["name"] == "dialogue.plan" and tool["status"] == "fallback"
        for tool in turn["tools"]
    )


def test_earlier_snapshot_omits_unfinished_event_detail_and_context_recall(published):
    client, provider, release = published
    early = _session(client, release, "2005-07-02")
    _turn(client, early, "展开讲讲进入劳动或继续教育路径时的经历和感受")
    arguments = [ctx for role, ctx in provider.calls if role == "persona_qa"][-1]
    assert any(item["scope"] == "partial_record" for item in arguments["evidence"])
    assert all(item["event_id"] != "work" for item in arguments["memory"])

    later = _session(client, release, "2025-01-01")
    result = _turn(client, later, "展开讲讲你对汶川地震的记忆")
    assert result["evidence"][0]["scope"] == "context"
    assert "没有证据证明我亲历现场" in result["answer"]
    assert all(
        row["event_id"] != "DIS-WC-2008"
        for role, ctx in provider.calls
        if role == "persona_qa"
        for row in ctx["memory"]
    )


def test_detail_projection_filters_by_valid_time_intent_and_memory_type():
    chunk = {
        "event_id": "work",
        "narrative": "初次上班场景",
        "memory": "当时我记得工位还很陌生。",
        "profile": {
            "event_id": "work",
            "scope": "synthesized",
            "valid_from": "2004-07-01",
            "emotion": {"stress": 70, "undeclared_value": 123},
            "tendencies": {"risk_aversion": 58},
            "voice": {"reflection": 80},
            "memory": {
                "kind": "synthetic_episodic",
                "known_from": "2005-01-01",
                "clarity": 70,
            },
            "interpretation": {"recall_cue": "陌生的工位", "emotion_note": "有点紧张"},
        },
    }
    plan = DialoguePlan(["work"], None, "focused", "memory", False)
    assert project_detail(chunk, "2003-12-31", plan) == {"event_id": "work"}
    before_recall = project_detail(chunk, "2004-12-31", plan)
    assert "memory" not in before_recall
    assert "recall_cue" not in before_recall["interpretation"]
    after_recall = project_detail(chunk, "2005-01-01", plan)
    assert after_recall["memory"] == chunk["memory"]
    assert after_recall["recall"] == {"clarity": 70}
    assert after_recall["emotion"] == {"stress": 70}
    assert "tendencies" not in after_recall


def test_follow_up_synthesis_has_prior_topic_but_not_prior_generated_text(published):
    client, provider, release = published
    session = _session(client, release, "2025-01-01")
    initial = _turn(client, session, "你进入劳动或继续教育路径是什么时候？")
    _turn(client, session, "为什么会这样选择？")
    latest = [ctx for role, ctx in provider.calls if role == "persona_qa"][-1]
    assert latest["previous_question"] == initial["question"]
    assert "answer" not in latest
