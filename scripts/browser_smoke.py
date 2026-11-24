"""Playwright acceptance flow for the local Digital Human POC."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from secrets import randbelow
from typing import Any
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Page, expect, sync_playwright


SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / ".playwright-mcp"
BREAKPOINTS = ((1440, 1000, "desktop"), (390, 844, "mobile"), (320, 720, "compact"))
QUESTION = "汶川地震发生时，我经历了什么？"
FOLLOW_UP = "那段经历能展开讲讲吗？当时有什么感受？"


def api_get(page: Page, path: str) -> Any:
    response = page.request.get(path)
    assert response.ok, f"{path}: {response.status} {response.text()}"
    return response.json()


def submit(page: Page, button: str, path: str) -> Any:
    with page.expect_response(
        lambda response: urlsplit(response.url).path == path
        and response.request.method == "POST",
        timeout=420_000,
    ) as submitted:
        page.get_by_role("button", name=re.compile(button)).click()
    response = submitted.value
    assert response.ok, f"{path}: {response.status} {response.text()}"
    return response


def verify_route(page: Page, **expected: str):
    page.wait_for_url(
        lambda url: all(
            parse_qs(urlsplit(url).query).get(key) == [value]
            for key, value in expected.items()
        )
    )


def verify_timeline_slices(page: Page, earthquake: dict, work: dict):
    search = page.get_by_label("搜索事件", exact=True)
    scope = page.get_by_label("事件范围", exact=True)
    rows = page.locator(".timeline-node")
    scope.select_option("history")
    search.fill("DIS-WC-2008")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text(earthquake["title"])
    scope.select_option("life")
    expect(rows).to_have_count(0)
    page.get_by_role("button", name="清除筛选", exact=True).click()
    expect(search).to_have_value("")
    expect(scope).to_have_value("all")
    expect(rows.filter(has_text=work["title"]).first).to_be_visible()


def capture_layouts(page: Page, stage: str) -> dict[str, dict[str, int]]:
    layouts = {}
    for width, height, label in BREAKPOINTS:
        page.set_viewport_size({"width": width, "height": height})
        dimensions = page.evaluate(
            """() => ({
                viewport_width: document.documentElement.clientWidth,
                scroll_width: document.documentElement.scrollWidth,
                body_scroll_width: document.body.scrollWidth
            })"""
        )
        assert dimensions["scroll_width"] <= width, dimensions
        assert dimensions["body_scroll_width"] <= width, dimensions
        layouts[label] = dimensions
        page.screenshot(
            path=str(SCREENSHOT_DIR / f"poc-{stage}-{label}.png"),
            full_page=True,
            animations="disabled",
        )
    page.set_viewport_size({"width": 1440, "height": 1000})
    return layouts


def generate(page: Page, seed: int, **expected_inputs: Any) -> tuple[dict, dict]:
    page.get_by_label("种子", exact=True).fill(str(seed))
    response = submit(page, "生成新骨架", "/api/generations")
    assert response.status == 201
    body = response.request.post_data_json
    assert "themes" not in body, body
    assert body["seed"] == seed
    assert all(body.get(key) == value for key, value in expected_inputs.items()), body

    job = response.json()
    assert job["status"] == "skeleton_review", job
    verify_route(page, stage="skeleton", job=job["job_id"])
    expect(page.locator(".meta-id")).to_contain_text(job["graph_revision_id"])
    graph = api_get(page, f"/api/graphs/{job['graph_revision_id']}")
    assert graph["validation"]["valid"]
    return job, graph


def verify_skeleton_download(page: Page, graph: dict) -> None:
    revision = graph["graph_revision_id"]
    with page.expect_download() as downloaded:
        page.get_by_role("link", name="导出骨架 JSON", exact=True).click()
    document = downloaded.value
    assert document.suggested_filename == f"skeleton-{revision}.json"
    assert json.loads(document.path().read_text(encoding="utf-8")) == graph


def assert_automatic_generation(page: Page, seed: int) -> dict:
    year = page.get_by_label(re.compile("出生年份"))
    household = page.get_by_role("combobox", name=re.compile("家庭范围"))
    expect(year).to_have_value("")
    expect(household).to_have_value("")
    expect(page.locator(".spec-panel").get_by_role("checkbox")).to_have_count(0)

    job, graph = generate(page, seed, birth_year=None, household=None)
    spec = job["spec"]
    assert isinstance(spec["birth_year"], int)
    assert spec["household"] in {"family", "individual"}
    year_sampling = spec["sampling"]["birth_year"]
    assert year_sampling["source"] == "sampled"
    lower, upper = year_sampling["range"]
    assert lower <= spec["birth_year"] <= upper <= int(spec["as_of"][:4])
    assert spec["sampling"]["household"]["source"] == "sampled"
    assert graph["person"]["birth_year"] == spec["birth_year"]
    assert bool(graph["person"]["family"]) == (spec["household"] == "family")
    assert {item["id"] for item in spec["history_selection"]} == set(spec["themes"])
    assert all(item["reason"] for item in spec["history_selection"])
    assert {
        node["event_id"] for node in graph["nodes"] if node["category"] == "history"
    } == set(spec["themes"])
    expect(year).to_have_value("")
    expect(household).to_have_value("")
    return spec


def publish_through_ui(page: Page, seed: int) -> tuple[dict, dict, str]:
    page.get_by_role("combobox", name="地区", exact=True).select_option("51")
    page.get_by_label(re.compile("出生年份")).fill("1984")
    page.get_by_role("combobox", name=re.compile("家庭范围")).select_option("family")
    job, original = generate(page, seed, birth_year=1984, household="family")
    assert not job["detail_revision_id"]
    expect(page.get_by_text("55 族 / 440 项", exact=True)).to_be_visible()
    verify_skeleton_download(page, original)
    page.get_by_text("浏览与筛选维度", exact=True).click()
    page.get_by_label("搜索维度", exact=True).fill("WORK.01")
    page.locator(".dimension-families > details > summary").click()
    expect(page.locator(".dimension-entry")).to_have_count(1)
    page.get_by_text("浏览与筛选维度", exact=True).click()
    spec = job["spec"]
    assert spec["birth_year"] == original["person"]["birth_year"] == 1984
    assert spec["household"] == "family" and original["person"]["family"]
    assert spec["sampling"]["birth_year"]["source"] == "provided"
    assert spec["sampling"]["household"]["source"] == "provided"
    assert "DIS-WC-2008" in spec["themes"]
    earthquake = next(
        node for node in original["nodes"] if node["event_id"] == "DIS-WC-2008"
    )
    assert earthquake["start"] == "2008-05-12"
    assert earthquake["summary_node"]
    history = page.locator("details").filter(
        has=page.get_by_text(re.compile(r"历史背景候选（\d+ 项）"))
    )
    history.locator("summary").click()
    expect(history).to_contain_text("汶川地震")

    work = next(node for node in original["nodes"] if node["event_id"] == "work")
    verify_timeline_slices(page, earthquake, work)
    page.locator(".timeline-node").filter(has_text=work["title"]).first.click()
    page.get_by_role("button", name="编辑节点", exact=True).click()
    label = "进入劳动路径（研究者修订）"
    page.get_by_label("节点标题", exact=True).fill(label)
    original_id = job["graph_revision_id"]
    manual_job = submit(page, "保存子版本", f"/api/graphs/{original_id}/edits").json()
    manual_id = manual_job["graph_revision_id"]
    manual = api_get(page, f"/api/graphs/{manual_id}")
    assert manual["parent_revision_id"] == original_id
    assert (
        next(node for node in manual["nodes"] if node["event_id"] == "work")["title"]
        == label
    )
    expect(page.locator(".meta-id")).to_contain_text(manual_id)
    verify_skeleton_download(page, manual)
    verify_route(page, stage="skeleton", job=job["job_id"], revision=manual_id)
    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".meta-id")).to_contain_text(manual_id)
    expect(page.locator(".timeline-node").filter(has_text=label)).to_be_visible()

    page.get_by_label(re.compile("骨架编辑器.*建议")).fill(
        "把工作节点延后 1 年，并保留父版本"
    )
    edited_job = submit(page, "提交建议", f"/api/graphs/{manual_id}/edits").json()
    edited_id = edited_job["graph_revision_id"]
    edited = api_get(page, f"/api/graphs/{edited_id}")
    assert edited["parent_revision_id"] == manual_id
    assert edited["validation"]["valid"]
    assert (
        next(node for node in edited["nodes"] if node["event_id"] == "work")["start"]
        > work["start"]
    )
    assert api_get(page, f"/api/graphs/{original_id}") == original
    assert api_get(page, f"/api/graphs/{manual_id}") == manual
    revisions = api_get(page, f"/api/jobs/{job['job_id']}/graphs")["revisions"]
    assert {item["graph_revision_id"] for item in revisions} == {
        original_id,
        manual_id,
        edited_id,
    }

    expect(page.locator(".meta-id")).to_contain_text(edited_id)
    verify_route(page, stage="skeleton", job=job["job_id"], revision=edited_id)
    page.go_back()
    verify_route(page, stage="skeleton", job=job["job_id"], revision=manual_id)
    expect(page.locator(".meta-id")).to_contain_text(manual_id)
    verify_skeleton_download(page, manual)
    page.go_forward()
    verify_route(page, stage="skeleton", job=job["job_id"], revision=edited_id)
    expect(page.locator(".meta-id")).to_contain_text(edited_id)
    layouts = capture_layouts(page, "workbench")
    flesh_job = submit(
        page, "确认骨架并填充血肉", f"/api/graphs/{edited_id}/continue-flesh"
    ).json()
    flesh = api_get(page, f"/api/flesh/{flesh_job['detail_revision_id']}")
    assert flesh["graph_revision_id"] == edited_id
    assert {chunk["event_id"] for chunk in flesh["chunks"]} == {
        node["event_id"] for node in edited["nodes"]
    }
    detail = page.locator(".node-detail")
    expect(detail.get_by_text(re.compile("血肉锚定块"))).to_be_visible()
    work_row = page.locator(".timeline-node").filter(has_text=label)
    work_row.click()
    detail.locator(".flesh-detail > summary").click()
    expect(detail.get_by_text("情绪正向度", exact=True)).to_be_visible()
    detail.get_by_role("button", name=re.compile(re.escape(original_id))).last.click()
    expect(page.locator(".meta-id")).to_contain_text(original_id)
    expect(detail.get_by_text(re.compile("血肉锚定块"))).to_have_count(0, timeout=3000)
    detail.get_by_role("button", name=re.compile(re.escape(edited_id))).first.click()
    expect(page.locator(".meta-id")).to_contain_text(edited_id)
    expect(detail.get_by_text(re.compile("血肉锚定块"))).to_be_visible()
    release_id = flesh_job["release_id"]
    expect(page.get_by_role("button", name=re.compile("发布到档案库"))).to_be_disabled()
    submit(page, "运行全链路审查", f"/api/reviews/{release_id}/run")
    report = api_get(page, f"/api/reviews/{release_id}")
    assert report["status"] in {"passed", "repaired"}
    publish_job = submit(
        page, "发布到档案库", f"/api/releases/{release_id}/publish"
    ).json()
    assert publish_job["status"] == "ready"
    archive = api_get(page, "/api/archive/persons")
    person = next(
        item for item in archive["persons"] if item["release_id"] == release_id
    )
    assert person["graph_revision_id"] == edited_id
    return person, layouts, label


def chat_through_ui(page: Page, person: dict, work_title: str) -> int:
    search = page.get_by_label("搜索档案", exact=True)
    search.fill("此档案不应该匹配任何记录")
    expect(page.locator(".archive-release")).to_have_count(0)
    search.fill(person["release_id"])
    archive_button = page.get_by_role(
        "button", name=re.compile(re.escape(person["release_id"]))
    )
    expect(archive_button).to_be_visible()
    archive_button.click()
    page.get_by_role("button", name="查看完整档案", exact=True).click()
    verify_route(
        page,
        stage="archive",
        release=person["release_id"],
        profile=person["release_id"],
    )
    profile_page = page.locator(".archive-profile-page")
    expect(
        profile_page.get_by_role("heading", name=re.compile("完整档案")).first
    ).to_be_visible()
    expect(profile_page).to_contain_text(person["snapshot_id"])
    expect(profile_page.get_by_role("heading", name="人生图谱")).to_be_visible()
    expect(profile_page.get_by_role("heading", name="人生状态快照")).to_be_visible()
    profile_page.get_by_role("button", name="返回档案问答", exact=True).click()
    verify_route(page, stage="archive", release=person["release_id"])
    expect(page.get_by_role("heading", name=re.compile("对话")).first).to_be_visible()
    cutoff = page.get_by_label("问答截止日期", exact=True)
    cutoff.fill(person["as_of"])
    latest = submit(page, "开始问答", "/api/chat/sessions").json()
    assert latest["release_id"] == person["release_id"]
    assert latest["as_of"] == person["as_of"]
    verify_route(
        page,
        stage="archive",
        release=person["release_id"],
        session=latest["session_id"],
    )
    question = page.get_by_label("向数字人提问", exact=True)
    question.fill(QUESTION)
    result = submit(
        page, "发送", f"/api/chat/sessions/{latest['session_id']}/turns"
    ).json()
    assert any(item["event_id"] == "DIS-WC-2008" for item in result["evidence"])
    expect(page.locator(".turn").last).to_contain_text("证据")
    expect(page.get_by_text("合成角色模拟", exact=True)).to_be_visible()

    question.fill(f"谈谈「{work_title}」时的经历。")
    first_work = submit(
        page, "发送", f"/api/chat/sessions/{latest['session_id']}/turns"
    ).json()
    assert "work" in {item["event_id"] for item in first_work["evidence"]}
    question.fill(FOLLOW_UP)
    follow_up = submit(
        page, "发送", f"/api/chat/sessions/{latest['session_id']}/turns"
    ).json()
    assert follow_up["follow_up_to"] == first_work["turn_id"]
    assert follow_up["disclosure"] == "expanded"
    assert "work" in {item["event_id"] for item in follow_up["evidence"]}
    expect(page.locator(".turn").last).to_contain_text("第 2 轮追问")
    outer = page.locator(".turn").last.locator(".archive-evidence")
    trace = outer.locator(".archive-trace")
    expect(trace.locator("summary")).not_to_be_visible()
    outer.locator(":scope > summary").click()
    expect(trace.locator("summary")).to_be_visible()
    expect(trace.locator("code").first).not_to_be_visible()
    trace.locator("summary").click()
    expect(trace.locator("code").first).to_be_visible()

    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".turn")).to_have_count(3)
    expect(page.locator(".turn").last).to_contain_text(FOLLOW_UP)

    cutoff.fill("2007-12-31")
    earlier = submit(page, "使用此日期创建新会话", "/api/chat/sessions").json()
    assert earlier["release_id"] == latest["release_id"]
    assert earlier["as_of"] == "2007-12-31"
    assert earlier["snapshot_id"] != latest["snapshot_id"]
    question.fill(QUESTION)
    result = submit(
        page, "发送", f"/api/chat/sessions/{earlier['session_id']}/turns"
    ).json()
    assert all(item["start"] <= earlier["as_of"] for item in result["evidence"])
    assert all(item["event_id"] != "DIS-WC-2008" for item in result["evidence"])
    expect(page.locator(".turn").last).to_contain_text("证据")
    return len(result["evidence"])


def run(base_url: str, headed: bool = False) -> dict[str, Any]:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        executable = os.environ.get(
            "POC_BROWSER",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        )
        browser = playwright.chromium.launch(
            headless=not headed, executable_path=executable
        )
        page = browser.new_page(
            base_url=base_url, viewport={"width": 1440, "height": 1000}
        )
        errors: list[str] = []
        page.on(
            "console",
            lambda message: errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url, wait_until="domcontentloaded", timeout=30_000)
        expect(page).to_have_title(re.compile("数字人|LifeGraph"))
        expect(page.locator(".passport-panel, .timeline-panel")).to_have_count(0)
        expect(
            page.get_by_role("link", name="导出骨架 JSON", exact=True)
        ).to_have_count(0)
        catalog = api_get(page, "/api/catalog")
        region = page.get_by_role("combobox", name="地区", exact=True)
        expect(region.locator("option")).to_have_count(
            len(catalog["regions"]), timeout=10_000
        )

        seed = randbelow(1_000_000_000) + 1
        spec = assert_automatic_generation(page, seed)
        page.get_by_role("button", name="生成规格", exact=True).click()
        expect(page.locator(".passport-panel, .timeline-panel")).to_have_count(0)
        person, workbench, work_title = publish_through_ui(page, seed)
        earlier_evidence = chat_through_ui(page, person, work_title)
        archive = capture_layouts(page, "archive")
        page.get_by_role("button", name="生成规格", exact=True).click()
        _, new_graph = generate(page, seed + 1, birth_year=1984, household="family")
        assert new_graph["person"]["person_id"] != person["person_id"]
        expect(
            page.locator(".node-detail").get_by_text(re.compile("血肉锚定块"))
        ).to_have_count(0)
        assert not errors, errors
        browser.close()

    result = {
        "sampled_birth_year": spec["birth_year"],
        "sampled_household": spec["household"],
        "automatically_matched_scenarios": len(spec["themes"]),
        "tested_release_id": person["release_id"],
        "early_snapshot_evidence": earlier_evidence,
        "workbench": workbench,
        "archive": archive,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5178")
    parser.add_argument("--headed", action="store_true")
    arguments = parser.parse_args()
    run(arguments.base_url.rstrip("/"), arguments.headed)
