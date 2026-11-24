from datetime import date

from fastapi.testclient import TestClient

from app.domain import compile_spec
from app.main import create_app
from app.provider import MockProvider
from app.skeleton import (
    ReferenceRunner,
    revise_skeleton,
    run_skeleton,
    upgrade_skeleton,
)


def test_official_frame_reaches_generation_and_export(tmp_path):
    with TestClient(
        create_app(f"sqlite+aiosqlite:///{tmp_path / 'frame.db'}", MockProvider())
    ) as client:
        options = {
            **client.get("/api/catalog").json()["defaults"],
            "population_frame": "mainland_2020",
            "region": "51",
            "ethnicity": None,
            "birth_year": 1984,
        }
        response = client.post("/api/generations", json=options)
        assert response.status_code == 201, response.text
        job = response.json()
        selection = job["spec"]["population_selection"]
        assert selection["filters"] == {"region": "51", "ethnicity": None}
        assert selection["selection_probability"] == (
            selection["cell_count"] / selection["eligible_population"]
        )
        graph = client.get(f"/api/graphs/{job['graph_revision_id']}/export").json()
        assert graph["simulation"]["population_selection"] == selection
        assert graph["simulation"]["population"]["analysis_weight"] is None


def test_evolution_is_idempotent_and_previous_release_stays_pinned(tmp_path):
    with TestClient(
        create_app(f"sqlite+aiosqlite:///{tmp_path / 'evolve.db'}", MockProvider())
    ) as client:
        options = {
            **client.get("/api/catalog").json()["defaults"],
            "region": "81",
            "ethnicity": "hk-chinese",
            "birth_year": 1984,
            "as_of": "2020-01-01",
        }
        response = client.post("/api/generations", json=options)
        assert response.status_code == 201, response.text
        original_id = response.json()["graph_revision_id"]
        url = f"/api/graphs/{original_id}"
        original = client.get(url).json()
        assert original["simulation"]["population_evidence"]["joint_observation"]
        release_id = client.post(f"{url}/continue-flesh").json()["release_id"]
        assert client.post(f"/api/reviews/{release_id}/run").status_code == 200
        assert client.post(f"/api/releases/{release_id}/publish").status_code == 200
        body = {"mode": "extend", "as_of": "2027-01-01"}
        headers = {"Idempotency-Key": "extend-case"}
        child = client.post(f"{url}/evolve", json=body, headers=headers)
        assert child.status_code == 201, child.text
        child_id = child.json()["command_graph_revision_id"]
        replay = client.post(f"{url}/evolve", json=body, headers=headers)
        assert (
            replay.status_code == 200
            and replay.json()["command_graph_revision_id"] == child_id
        )
        graph = client.get(f"/api/graphs/{child_id}").json()
        assert (
            graph["as_of"] == "2027-01-01"
            and graph["parent_revision_id"] == original_id
        )
        assert graph["case_entities"]["case_id"] == original["case_entities"]["case_id"]
        restored_job = client.get(f"/api/jobs/{child.json()['job_id']}").json()
        assert (
            restored_job["spec"]["as_of"]
            == restored_job["person"]["as_of"]
            == "2027-01-01"
        )
        assert client.get(url).json() == original
        assert (
            client.get("/api/archive/persons").json()["persons"][0]["as_of"]
            == "2020-01-01"
        )


def test_multi_step_extension_retains_original_population_draw_and_deleted_history():
    spec = compile_spec(
        {"birth_year": 1984, "as_of": "2020-01-01", "household": "family"}
    )["spec"]
    runner = ReferenceRunner()
    graph = run_skeleton(spec, runner, "subject")
    graph = revise_skeleton(
        graph, spec, [{"op": "delete", "event_id": "DIS-WC-2008"}], runner
    )
    through = date(2055, 1, 1)
    direct = upgrade_skeleton(graph, spec, runner, through=through)
    step = upgrade_skeleton(graph, spec, runner, through=date(2021, 1, 1))
    sequential = upgrade_skeleton(
        step, step["simulation"]["spec"], runner, through=through
    )
    assert direct["person"]["death_date"] == sequential["person"]["death_date"]
    assert direct["simulation"]["events"] == sequential["simulation"]["events"]
    assert "DIS-WC-2008" not in {node["event_id"] for node in sequential["nodes"]}
