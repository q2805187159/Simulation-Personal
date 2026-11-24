"""Exercise the running POC over HTTP, including a scoped archive conversation."""

import argparse
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def api(base, path, body=None, *, post=False, headers=None):
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    method = "POST" if post or body is not None else "GET"
    request = Request(
        f"{base}/api{path}", data=data, method=method, headers=request_headers
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(
            f"{method} {path}: {error.code} {error.read().decode()}"
        ) from error


def verify_persisted(base, release_id):
    archive = api(base, "/archive/persons")["persons"]
    person = next(item for item in archive if item["release_id"] == release_id)
    graph = api(base, f"/graphs/{person['graph_revision_id']}")
    flesh = api(base, f"/flesh/{person['detail_revision_id']}")
    assert graph["person"]["person_id"] == person["person_id"]
    assert flesh["graph_revision_id"] == graph["graph_revision_id"]
    return person


def run(base):
    api(base, "/health")
    catalog = api(base, "/catalog")
    options = catalog["defaults"]
    assert options["birth_year"] is None and options["household"] is None
    assert "themes" not in options
    validated = api(base, "/generations/spec/validate", options)
    assert validated["valid"]
    headers = {"Idempotency-Key": f"smoke-{uuid4()}"}
    job = api(base, "/generations", options, headers=headers)
    retry = api(base, "/generations", options, headers=headers)
    assert job["job_id"] == retry["job_id"]
    assert job["status"] == "skeleton_review"
    spec = job["spec"]
    assert spec == validated["spec"]
    assert {entry["source"] for entry in spec["sampling"].values()} == {"sampled"}
    assert spec["themes"] == [match["id"] for match in spec["history_selection"]]
    parent_id = job["graph_revision_id"]
    before = api(base, f"/graphs/{parent_id}")
    assert set(spec["themes"]) == {
        node["event_id"] for node in before["nodes"] if node["summary_node"]
    }
    event = next(node for node in before["nodes"] if node["event_id"] == "education")
    title = f"{event['title']}（验收修订）"
    job = api(
        base,
        f"/graphs/{parent_id}/edits",
        {
            "source": "manual",
            "operations": [
                {"op": "update", "event_id": event["event_id"], "title": title}
            ],
        },
    )
    graph_id = job["graph_revision_id"]
    graph = api(base, f"/graphs/{graph_id}")
    assert graph["parent_revision_id"] == parent_id
    assert graph["validation"]["valid"]
    assert event["event_id"] in graph["diff"]["modified"]
    assert api(base, f"/graphs/{parent_id}") == before
    revisions = api(base, f"/jobs/{job['job_id']}/graphs")["revisions"]
    assert {parent_id, graph_id} <= {r["graph_revision_id"] for r in revisions}

    job = api(base, f"/graphs/{graph_id}/continue-flesh", post=True)
    same_job = api(base, f"/graphs/{graph_id}/continue-flesh", post=True)
    assert same_job["detail_revision_id"] == job["detail_revision_id"]
    assert same_job["release_id"] == job["release_id"]
    release_id = job["release_id"]
    assert job["status"] == "chain_review"
    flesh = api(base, f"/flesh/{job['detail_revision_id']}")
    assert flesh["graph_revision_id"] == graph_id
    assert {chunk["event_id"] for chunk in flesh["chunks"]} <= {
        n["event_id"] for n in graph["nodes"]
    }
    assert flesh["chunks"]

    job = api(base, f"/reviews/{release_id}/run", post=True)
    assert job["status"] == "publish_pending"
    assert api(base, f"/reviews/{release_id}")["status"] in {"passed", "repaired"}
    published = api(base, f"/releases/{release_id}/publish", post=True)
    assert published["status"] == "ready"
    person = verify_persisted(base, release_id)
    earlier = api(
        base,
        "/chat/sessions",
        {
            "person_id": person["person_id"],
            "release_id": release_id,
            "as_of": f"{int(person['birth_year']) + 16}-12-31",
        },
    )
    assert (
        earlier["release_id"] == release_id and earlier["graph_revision_id"] == graph_id
    )
    future_turn = api(
        base,
        f"/chat/sessions/{earlier['session_id']}/turns",
        {"question": "疫情期间我的工作和生活怎么样？"},
    )
    assert (
        future_turn["answer"] and future_turn["tools"] and not future_turn["evidence"]
    )

    session = api(
        base,
        "/chat/sessions",
        {
            "person_id": person["person_id"],
            "release_id": release_id,
            "as_of": person["as_of"],
        },
    )
    assert (
        session["release_id"] == release_id and session["graph_revision_id"] == graph_id
    )
    assert session["snapshot_id"] != earlier["snapshot_id"]
    turn = api(
        base,
        f"/chat/sessions/{session['session_id']}/turns",
        {"question": f"请说说{title}这段经历。"},
    )
    assert turn["answer"] and turn["evidence"] and turn["tools"]
    assert all(item["start"] <= session["as_of"] for item in turn["evidence"])
    restored = api(base, f"/chat/sessions/{session['session_id']}")
    assert restored["turns"][-1]["turn_id"] == turn["turn_id"]
    assert api(base, f"/graphs/{graph_id}") == graph
    events = api(base, f"/jobs/{job['job_id']}/events")["events"]
    assert {"skeleton_review", "chain_review", "ready"} <= {
        entry["stage"] for entry in events
    }
    print(
        json.dumps(
            {
                "job_id": job["job_id"],
                "release_id": release_id,
                "session_id": session["session_id"],
                "person": person["name"],
                "revisions": len(revisions),
                "chunks": len(flesh["chunks"]),
                "workflow_events": len(events),
                "answer_evidence": len(turn["evidence"]),
                "future_evidence_excluded": not future_turn["evidence"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:5178")
    parser.add_argument(
        "--release", help="Verify an existing release after a stack restart."
    )
    args = parser.parse_args()
    if args.release:
        print(
            json.dumps(
                verify_persisted(args.base.rstrip("/"), args.release),
                ensure_ascii=False,
            )
        )
    else:
        run(args.base.rstrip("/"))
