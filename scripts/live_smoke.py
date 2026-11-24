"""Exercise the complete synthetic persona pipeline against a live model.

The script contacts only the running POC. Its provider configuration is
managed by the backend. The report prints artifact IDs and counts only.
"""

from __future__ import annotations

import argparse
import json
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base, path, *, method="GET", body=None, key=None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    req = Request(
        f"{base}/api{path}",
        data=json.dumps(body, ensure_ascii=False).encode()
        if body is not None
        else None,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(req, timeout=410) as response:
            return json.load(response)
    except HTTPError as exc:
        # The workbench API returns sanitized errors and never forwards upstream bodies.
        payload = exc.read()
        try:
            detail = json.loads(payload).get("detail")
        except (ValueError, AttributeError):
            detail = None
        message = f"{method} {path} returned HTTP {exc.code}"
        if isinstance(detail, str):
            message += f" ({detail})"
        raise RuntimeError(message) from None


def run(base, model):
    start = monotonic()
    health = request(base, "/health")
    assert health["llm"] == "openai-compatible" and health["model"] == model, health
    default = request(base, "/catalog")["defaults"]
    options = {
        **default,
        "region": "81",
        "birth_year": 1984,
        "household": "individual",
        "as_of": "2008-01-01",
        "detail": "standard",
        "seed": 741862,
    }
    job = request(
        base, "/generations", method="POST", body=options, key=f"live-smoke-{uuid4()}"
    )
    assert job["status"] == "skeleton_review", job["status"]
    print("骨架已生成", job["job_id"], flush=True)
    graph_id = job["graph_revision_id"]
    graph = request(base, f"/graphs/{graph_id}")
    assert graph["validation"]["valid"]
    assert graph["person"]["synthetic"]
    work = next(node for node in graph["nodes"] if node["event_id"] == "work")

    job = request(base, f"/graphs/{graph_id}/continue-flesh", method="POST")
    assert job["status"] == "chain_review", job["status"]
    release_id = job["release_id"]
    draft = request(base, f"/flesh/{job['detail_revision_id']}")
    assert draft["graph_revision_id"] == graph_id
    assert {chunk["event_id"] for chunk in draft["chunks"]} == {
        node["event_id"] for node in graph["nodes"]
    }
    live_profiles = [
        chunk["profile"]
        for chunk in draft["chunks"]
        if chunk.get("profile", {}).get("provenance", {}).get("model") == model
    ]
    assert live_profiles, "No structured profiles carry upstream model provenance"
    print("模型细节已生成", len(live_profiles), flush=True)
    for profile in live_profiles:
        assert profile["event_id"] in {node["event_id"] for node in graph["nodes"]}
        assert profile["valid_from"] <= options["as_of"]
        for category in ("emotion", "appraisal", "tendencies", "voice"):
            assert profile[category] and all(
                0 <= value <= 100 for value in profile[category].values()
            ), category
        assert all(
            profile["interpretation"].get(key)
            for key in (
                "scene",
                "motivation",
                "reflection",
                "recall_cue",
                "speaking_sample",
            )
        ), "Generated profile composition incomplete"

    job = request(base, f"/reviews/{release_id}/run", method="POST")
    report = request(base, f"/reviews/{release_id}")
    assert report["status"] in {"passed", "repaired"}, (release_id, report["findings"])
    published = request(base, f"/releases/{release_id}/publish", method="POST")
    assert published["status"] == "ready", published["status"]
    print("已发布", release_id, flush=True)
    archive = request(base, "/archive/persons")["persons"]
    person = next(item for item in archive if item["release_id"] == release_id)
    assert person["graph_revision_id"] == graph_id
    detail = request(base, f"/flesh/{person['detail_revision_id']}")
    assert detail["graph_revision_id"] == graph_id
    assert all(chunk.get("profile") for chunk in detail["chunks"])
    bundle = request(base, f"/archive/releases/{release_id}")
    assert bundle["graph"]["graph_revision_id"] == graph_id
    assert bundle["detail"]["detail_revision_id"] == person["detail_revision_id"]
    assert bundle["review"]["status"] == report["status"]

    scope = {"release_id": release_id, "person_id": person["person_id"]}
    session = request(
        base,
        "/chat/sessions",
        method="POST",
        body={
            **scope,
            "as_of": options["as_of"],
        },
    )
    session_id = session["session_id"]
    assert session["graph_revision_id"] == graph_id
    assert session["detail_revision_id"] == person["detail_revision_id"]
    turn_path = f"/chat/sessions/{session_id}/turns"
    question = f"谈谈你「{work['title']}」时的经历。"
    first = request(base, turn_path, method="POST", body={"question": question})
    follow = request(
        base,
        turn_path,
        method="POST",
        body={
            "question": "那段经历能展开讲讲吗？当时有什么感受？",
        },
    )
    for turn in (first, follow):
        assert turn["model"] == model and turn["answer"] and turn["tools"]
        visible = {item["event_id"] for item in turn["evidence"]}
        assert set(turn["cited_event_ids"]) <= visible
        assert "work" in visible
    assert follow["follow_up_to"] == first["turn_id"]
    assert follow["disclosure"] == "expanded"
    reloaded = request(base, f"/chat/sessions/{session_id}")
    assert [item["turn_id"] for item in reloaded["turns"]] == [
        first["turn_id"],
        follow["turn_id"],
    ]

    earlier = request(
        base,
        "/chat/sessions",
        method="POST",
        body={
            **scope,
            "as_of": "1987-01-01",
        },
    )
    assert earlier["snapshot_id"] != session["snapshot_id"]
    early_turn = request(
        base,
        f"/chat/sessions/{earlier['session_id']}/turns",
        method="POST",
        body={"question": question},
    )
    assert "work" not in {item["event_id"] for item in early_turn["evidence"]}
    assert all(item["start"] <= earlier["as_of"] for item in early_turn["evidence"])

    events = request(base, f"/jobs/{job['job_id']}/events")["events"]
    assert {"skeleton_review", "chain_review", "ready"} <= {
        event["stage"] for event in events
    }
    print(
        json.dumps(
            {
                "model": model,
                "job_id": job["job_id"],
                "release_id": release_id,
                "detail_revision_id": detail["detail_revision_id"],
                "event_count": len(graph["nodes"]),
                "provider_profiles": len(live_profiles),
                "review_status": report["status"],
                "session_id": session_id,
                "follow_up_disclosure": follow["disclosure"],
                "follow_up_tools": len(follow["tools"]),
                "earlier_snapshot_excludes_work": True,
                "seconds": round(monotonic() - start, 1),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5178")
    parser.add_argument("--model", default="glm-5.3-flash")
    args = parser.parse_args()
    run(args.base.rstrip("/"), args.model)
