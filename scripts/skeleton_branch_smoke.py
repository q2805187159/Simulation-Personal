"""Verify concurrent skeleton edits through two browser views of one parent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from urllib.parse import urlencode, urlsplit

from playwright.async_api import (
    APIResponse,
    Page,
    Response,
    Route,
    async_playwright,
    expect,
)


MANUAL_TITLE = "进入劳动路径（并发人工修订）"
SUGGESTION = "把工作节点延后 1 年，并保留父版本"
CONTINUE = "确认骨架并填充血肉"


async def expect_status(response: APIResponse | Response, status: int) -> dict:
    assert response.status == status, (
        response.url,
        response.status,
        await response.text(),
    )
    return await response.json()


async def click_post(page: Page, button: str, path: str) -> Response:
    async with page.expect_response(
        lambda reply: urlsplit(reply.url).path == path
        and reply.request.method == "POST",
        timeout=60_000,
    ) as submitted:
        await page.get_by_role("button", name=re.compile(button)).click()
    return await submitted.value


async def open_revision(page: Page, job: str, revision: str) -> None:
    query = urlencode({"stage": "skeleton", "job": job, "revision": revision})
    await page.goto(f"/?{query}", wait_until="domcontentloaded")
    await expect(page.locator(".meta-id")).to_contain_text(revision)


async def prepare_edit(page: Page, source: str, original_work_title: str) -> str:
    if source == "manual":
        await (
            page.locator(".timeline-node")
            .filter(has_text=original_work_title)
            .first.click()
        )
        await page.get_by_role("button", name="编辑节点", exact=True).click()
        await page.get_by_label("节点标题", exact=True).fill(MANUAL_TITLE)
        return "保存子版本"
    await page.get_by_label(re.compile("骨架编辑器.*建议")).fill(SUGGESTION)
    return "提交建议"


def work_node(graph: dict) -> dict:
    return next(node for node in graph["nodes"] if node["event_id"] == "work")


async def verify_branches(manual: Page, llm: Page) -> dict:
    api = manual.request
    health = await expect_status(await api.get("/api/health"), 200)
    assert health["llm"] == "deterministic-mock", health["llm"]
    job = await expect_status(
        await api.post(
            "/api/generations",
            data={
                "region": "51",
                "ethnicity": "01",
                "birth_year": 1984,
                "as_of": "2025-01-01",
                "entry": "birth_cohort",
                "household": "family",
                "detail": "rich",
                "seed": 7,
                "life_status": "living",
            },
        ),
        201,
    )
    job_id = job["job_id"]
    parent_id = job["graph_revision_id"]
    graph_path = f"/api/graphs/{parent_id}"
    edit_path = f"{graph_path}/edits"
    parent = await expect_status(await api.get(graph_path), 200)
    parent_work = work_node(parent)
    await asyncio.gather(
        open_revision(manual, job_id, parent_id), open_revision(llm, job_id, parent_id)
    )
    pages = {"manual": manual, "llm": llm}
    manual_button, llm_button = await asyncio.gather(
        prepare_edit(manual, "manual", parent_work["title"]),
        prepare_edit(llm, "llm", parent_work["title"]),
    )

    ready = asyncio.Event()
    arrivals: set[str] = set()

    async def release_when_both_ready(route: Route) -> None:
        arrivals.add(route.request.post_data_json["source"])
        if arrivals == {"manual", "llm"}:
            ready.set()
        await asyncio.wait_for(ready.wait(), 20)
        await route.continue_()

    await manual.context.route(f"**{edit_path}", release_when_both_ready)
    manual_response, llm_response = await asyncio.gather(
        click_post(manual, manual_button, edit_path),
        click_post(llm, llm_button, edit_path),
    )
    await manual.context.unroute(f"**{edit_path}", release_when_both_ready)

    responses = {"manual": manual_response, "llm": llm_response}
    children = {}
    for source, response in responses.items():
        command = await expect_status(response, 201)
        assert command["parent_revision_id"] == parent_id
        child_id = command.get(
            "command_graph_revision_id", command["graph_revision_id"]
        )
        await expect(pages[source].locator(".meta-id")).to_contain_text(child_id)
        child = await expect_status(await api.get(f"/api/graphs/{child_id}"), 200)
        assert child["parent_revision_id"] == parent_id
        assert child["validation"]["valid"]
        children[source] = child

    manual_graph, llm_graph = children["manual"], children["llm"]
    assert manual_graph["graph_revision_id"] != llm_graph["graph_revision_id"]
    assert manual_graph["branch"] != llm_graph["branch"]
    assert work_node(manual_graph)["title"] == MANUAL_TITLE
    assert work_node(manual_graph)["start"] == parent_work["start"]
    assert work_node(llm_graph)["title"] == parent_work["title"]
    assert work_node(llm_graph)["start"] > parent_work["start"]
    assert await expect_status(await api.get(graph_path), 200) == parent

    job_path = f"/api/jobs/{job_id}"
    active_id = (await expect_status(await api.get(job_path), 200))["graph_revision_id"]
    stale_source, stale_id = next(
        (source, child["graph_revision_id"])
        for source, child in children.items()
        if child["graph_revision_id"] != active_id
    )
    stale_page = pages[stale_source]
    await open_revision(stale_page, job_id, parent_id)
    retry_button = await prepare_edit(stale_page, stale_source, parent_work["title"])
    retry = await click_post(stale_page, retry_button, edit_path)
    original_key = await responses[stale_source].request.header_value("Idempotency-Key")
    assert (
        original_key
        and await retry.request.header_value("Idempotency-Key") == original_key
    )
    replay = await expect_status(retry, 200)
    assert replay["command_graph_revision_id"] == stale_id
    assert replay["graph_revision_id"] == active_id
    await expect(stale_page.locator(".meta-id")).to_contain_text(stale_id)
    await expect(stale_page).to_have_url(re.compile(rf"[?&]revision={stale_id}(?:&|$)"))
    await expect(
        stale_page.get_by_role("button", name=re.compile(CONTINUE))
    ).to_be_disabled()

    revisions = (await expect_status(await api.get(f"{job_path}/graphs"), 200))[
        "revisions"
    ]
    assert {
        item["graph_revision_id"]: item["parent_revision_id"] for item in revisions
    } == {
        parent_id: None,
        stale_id: parent_id,
        active_id: parent_id,
    }
    for revision in (parent_id, stale_id):
        await expect_status(
            await api.post(f"/api/graphs/{revision}/continue-flesh"), 409
        )
    active_page = llm if stale_source == "manual" else manual
    continuation = await click_post(
        active_page, CONTINUE, f"/api/graphs/{active_id}/continue-flesh"
    )
    continued = await expect_status(continuation, 201)
    detail_id = continued["detail_revision_id"]
    detail = await expect_status(await api.get(f"/api/flesh/{detail_id}"), 200)
    assert detail["graph_revision_id"] == active_id

    return {
        "job_id": job_id,
        "parent_revision_id": parent_id,
        "manual_revision_id": manual_graph["graph_revision_id"],
        "llm_revision_id": llm_graph["graph_revision_id"],
        "active_revision_id": active_id,
        "retried_revision_id": stale_id,
        "detail_revision_id": detail_id,
    }


async def run(base_url: str) -> dict:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=os.environ.get(
                "POC_BROWSER",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            )
        )
        context = await browser.new_context(base_url=base_url)
        manual = await context.new_page()
        llm = await context.new_page()
        return await verify_branches(manual, llm)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5184")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.base_url)), indent=2))
