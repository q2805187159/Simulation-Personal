from app.domain import compile_spec
from app.skeleton import ReferenceRunner, run_skeleton, revise_skeleton


OPTIONS = {
    "region": "51",
    "ethnicity": "01",
    "birth_year": 1984,
    "as_of": "2026-11-23",
    "seed": 2026,
    "household": "family",
}


def test_case_manifest_is_replayable_and_tracks_candidates():
    spec = compile_spec(OPTIONS)["spec"]
    first = run_skeleton(spec, ReferenceRunner(), "subject")
    assert first == run_skeleton(spec, ReferenceRunner(), "subject")
    run = first["simulation"]
    assert run["engine"]["name"] == "reference-des"
    assert run["input_sha256"] and run["output_sha256"]
    assert {row["code"] for row in run["candidates"]} >= {
        "death",
        "shared-care",
        "job-exit",
        "relocation",
    }
    assert "death" not in {row["code"] for row in run["events"]}


def test_edit_reruns_risk_inputs_using_same_random_addresses():
    spec = compile_spec(OPTIONS)["spec"]
    runner = ReferenceRunner()
    graph = run_skeleton(spec, runner, "subject")
    new = revise_skeleton(
        graph,
        spec,
        [{"op": "update", "event_id": "work", "start": "2005-07-01"}],
        runner,
    )
    before = {row["code"]: row for row in graph["simulation"]["candidates"]}
    after = {row["code"]: row for row in new["simulation"]["candidates"]}
    assert before["job-exit"]["start_day"] != after["job-exit"]["start_day"]
    assert {key: row["draw"] for key, row in before.items()} == {
        key: row["draw"] for key, row in after.items()
    }
    assert new["simulation"]["input_sha256"] != graph["simulation"]["input_sha256"]
    assert graph["nodes"][0] == new["nodes"][0]
