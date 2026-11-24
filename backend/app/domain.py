"""Pure LifeGraph rules used by the POC.

The domain deliberately returns plain dictionaries. Persistence assigns revision IDs,
while this module is responsible for deterministic candidate generation, safe edits,
and temporal/dependency validation.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import hashlib
import random
import re
from typing import Any

from .dimensions import attach_dimension_ledger
from .evidence_data import population_evidence_catalog, sample_frame
from .historical_mechanisms import assess_history
from .population import sample_population
from .world_rules import SCENARIOS, WORLD_VERSION, history_window


REGIONS = [
    ("11", "北京"),
    ("12", "天津"),
    ("13", "河北"),
    ("14", "山西"),
    ("15", "内蒙古"),
    ("21", "辽宁"),
    ("22", "吉林"),
    ("23", "黑龙江"),
    ("31", "上海"),
    ("32", "江苏"),
    ("33", "浙江"),
    ("34", "安徽"),
    ("35", "福建"),
    ("36", "江西"),
    ("37", "山东"),
    ("41", "河南"),
    ("42", "湖北"),
    ("43", "湖南"),
    ("44", "广东"),
    ("45", "广西"),
    ("46", "海南"),
    ("50", "重庆"),
    ("51", "四川"),
    ("52", "贵州"),
    ("53", "云南"),
    ("54", "西藏"),
    ("61", "陕西"),
    ("62", "甘肃"),
    ("63", "青海"),
    ("64", "宁夏"),
    ("65", "新疆"),
    ("71", "台湾"),
    ("81", "香港"),
    ("82", "澳门"),
]

ETHNICITIES = [
    ("01", "汉族"),
    ("02", "蒙古族"),
    ("03", "回族"),
    ("04", "藏族"),
    ("05", "维吾尔族"),
    ("06", "苗族"),
    ("07", "彝族"),
    ("08", "壮族"),
    ("09", "布依族"),
    ("10", "朝鲜族"),
    ("11", "满族"),
    ("12", "侗族"),
    ("13", "瑶族"),
    ("14", "白族"),
    ("15", "土家族"),
    ("16", "哈尼族"),
    ("17", "哈萨克族"),
    ("18", "傣族"),
    ("19", "黎族"),
    ("20", "傈僳族"),
    ("21", "佤族"),
    ("22", "畲族"),
    ("23", "高山族"),
    ("24", "拉祜族"),
    ("25", "水族"),
    ("26", "东乡族"),
    ("27", "纳西族"),
    ("28", "景颇族"),
    ("29", "柯尔克孜族"),
    ("30", "土族"),
    ("31", "达斡尔族"),
    ("32", "仫佬族"),
    ("33", "羌族"),
    ("34", "布朗族"),
    ("35", "撒拉族"),
    ("36", "毛南族"),
    ("37", "仡佬族"),
    ("38", "锡伯族"),
    ("39", "阿昌族"),
    ("40", "普米族"),
    ("41", "塔吉克族"),
    ("42", "怒族"),
    ("43", "乌孜别克族"),
    ("44", "俄罗斯族"),
    ("45", "鄂温克族"),
    ("46", "德昂族"),
    ("47", "保安族"),
    ("48", "裕固族"),
    ("49", "京族"),
    ("50", "塔塔尔族"),
    ("51", "独龙族"),
    ("52", "鄂伦春族"),
    ("53", "赫哲族"),
    ("54", "门巴族"),
    ("55", "珞巴族"),
    ("56", "基诺族"),
]


DEFAULTS = {
    "region": "51",
    "ethnicity": "01",
    "birth_year": None,
    "as_of": "2026-11-23",
    "entry": "birth_cohort",
    "household": None,
    "detail": "standard",
    "seed": 2026,
    "life_status": "living",
}

REGION_MAP = dict(REGIONS)
ETHNICITY_MAP = dict(ETHNICITIES)
WORK_CONTEXTS = ("服务岗位", "技术岗位", "经营活动", "生产岗位")


def catalog() -> dict[str, Any]:
    """Return the selectable world catalog without exposing mutable module state."""
    evidence = population_evidence_catalog()
    return {
        "world_version": WORLD_VERSION,
        "population_evidence": evidence,
        "regions": [{"id": key, "label": value} for key, value in REGIONS],
        "ethnicities": [
            *({"id": key, "label": value} for key, value in ETHNICITIES),
            *evidence["additional_ethnicities"],
        ],
        "scenarios": [
            {
                "id": key,
                "label": value["label"],
                "region_ids": list(value["regions"]),
                "start": value["start"],
                "end": value.get("end"),
            }
            for key, value in SCENARIOS.items()
        ],
        "defaults": deepcopy(DEFAULTS),
    }


def _parse_day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be ISO date") from exc


def _death_cutoff(spec: dict[str, Any]) -> date:
    if spec.get("death_date"):
        return date.fromisoformat(spec["death_date"])
    rng = random.Random(spec["seed"])
    # Keep the original draw order for existing seeds.
    rng.choice(WORK_CONTEXTS)
    return min(
        date.fromisoformat(spec["as_of"]),
        date(spec["birth_year"] + rng.randint(58, 82), 7, 1),
    )


def _history_window(
    scenario_id: str, spec: dict[str, Any], cutoff: date
) -> tuple[date, date] | None:
    return history_window(scenario_id, spec["region"], spec["birth_year"], cutoff)


def _match_history(spec: dict[str, Any], cutoff: date) -> list[dict[str, str]]:
    matches = []
    for scenario_id, item in SCENARIOS.items():
        window = _history_window(scenario_id, spec, cutoff)
        if window is None:
            continue
        start, end = window
        scope = (
            f"地区范围涵盖{spec['region_label']}"
            if item["regions"]
            else "适用目录中的中国大陆背景"
        )
        period = (
            start.isoformat()
            if start == end
            else f"{start.isoformat()} 至 {end.isoformat()}"
        )
        matches.append(
            (
                start,
                {
                    "id": scenario_id,
                    "label": item["label"],
                    "reason": f"{scope}，{period}与生平时段及适用年龄重合。",
                },
            )
        )
    return [
        match
        for _, match in sorted(matches, key=lambda entry: (entry[0], entry[1]["id"]))
    ]


def compile_spec(options: dict[str, Any]) -> dict[str, Any]:
    """Normalize and validate user choices before a graph can be generated."""
    if "themes" in options:
        raise ValueError("themes are matched automatically")
    raw = {**DEFAULTS, **options}
    try:
        seed = int(raw["seed"])
    except (TypeError, ValueError) as exc:
        raise ValueError("seed must be an integer") from exc
    selection = None
    if frame := raw.get("population_frame"):
        selection = sample_frame(
            frame,
            region=options.get("region"),
            ethnicity=options.get("ethnicity"),
            seed=seed,
        )
        raw.update(region=selection["region"], ethnicity=selection["ethnicity"])
    if raw["region"] not in REGION_MAP:
        raise ValueError("region is unknown")
    ethnicity_label = ETHNICITY_MAP.get(raw["ethnicity"])
    if ethnicity_label is None:
        local_categories = population_evidence_catalog()["additional_ethnicities"]
        category = next(
            (item for item in local_categories if item["id"] == raw["ethnicity"]), None
        )
        if not category or raw["region"] not in category["region_ids"]:
            raise ValueError("所选族别分类不适用于当前地区。")
        ethnicity_label = category["label"]
    as_of = _parse_day(raw["as_of"], "as_of")
    if raw["entry"] not in {"birth_cohort", "living_at_as_of"}:
        raise ValueError("entry is invalid")
    if raw["household"] not in {None, "individual", "family"}:
        raise ValueError("household is invalid")
    if raw["detail"] not in {"standard", "rich"}:
        raise ValueError("detail is invalid")
    population = sample_population(
        birth_year=raw["birth_year"],
        as_of=as_of,
        entry=raw["entry"],
        life_status=raw["life_status"],
        seed=seed,
    )
    household = raw["household"]
    household_sampling = {"source": "provided"}
    if household is None:
        household = random.Random(f"{seed}:household").choice(("individual", "family"))
        household_sampling = {"source": "sampled"}
    spec = {
        "region": raw["region"],
        "region_label": REGION_MAP[raw["region"]],
        "ethnicity": raw["ethnicity"],
        "ethnicity_label": ethnicity_label,
        "birth_year": population["birth_year"],
        "as_of": as_of.isoformat(),
        "entry": raw["entry"],
        "household": household,
        "detail": raw["detail"],
        "seed": seed,
        "life_status": population["life_status"],
        "death_date": population["death_date"],
        "sampling": {
            "birth_year": population["sampling"]["birth_year"],
            "household": household_sampling,
        },
        "population": population["sampling"],
    }
    cutoff = _death_cutoff(spec) if spec["life_status"] == "deceased" else as_of
    spec["history_selection"] = _match_history(spec, cutoff)
    spec["themes"] = [item["id"] for item in spec["history_selection"]]
    if selection:
        spec["population_selection"] = {
            key: selection[key]
            for key in (
                "version",
                "frame_id",
                "cell_count",
                "eligible_population",
                "frame_population",
                "selection_probability",
                "filters",
            )
        }
    return {"valid": True, "spec": spec, "warnings": []}


def _stable_name(spec: dict[str, Any]) -> str:
    surnames = "李王张刘陈杨黄赵周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤黎易常武乔贺赖龚文"
    given = "安宁晨远昕妍然清禾川明悦嘉言知行屿航亦凡思源子涵若曦景行书言以恒乐天星河"
    digest = hashlib.sha256(
        f"{spec['seed']}:{spec['region']}:{spec['ethnicity']}".encode()
    ).digest()
    return (
        surnames[digest[0] % len(surnames)]
        + given[digest[1] % len(given)]
        + given[digest[2] % len(given)]
    )


def _node(
    event_id: str,
    title: str,
    category: str,
    start: str,
    end: str | None,
    precision: str,
    location: str,
    *,
    depends_on: list[str] | None = None,
    participants: list[str] | None = None,
    notes: str = "",
    state_delta: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    summary_node: bool = False,
    participant_deltas: list[dict[str, Any]] | None = None,
    known_at: dict[str, str] | None = None,
) -> dict[str, Any]:
    node = {
        "event_id": event_id,
        "title": title,
        "category": category,
        "start": start,
        "end": end,
        "precision": precision,
        "location": location,
        "participants": participants or ["primary"],
        "notes": notes,
        "state_delta": state_delta or {},
        "evidence": evidence or [],
        "summary_node": summary_node,
        "depends_on": list(depends_on or []),
    }
    if participant_deltas:
        node["participant_deltas"] = participant_deltas
    if known_at:
        node["known_at"] = known_at
    return node


def _timeline_key(node: dict[str, Any]) -> tuple[str, str]:
    return (node.get("start", ""), node["event_id"])


def _latest_day(node: dict[str, Any]) -> str | None:
    return node.get("end") or (node["start"] if node["precision"] == "day" else None)


def _edges(nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    dependencies = [
        {"source": dependency, "target": node["event_id"], "kind": "depends_on"}
        for node in nodes
        for dependency in node.get("depends_on", [])
    ]
    decomposition = [
        {
            "source": node["event_id"],
            "target": node["parent_event_id"],
            "kind": "decomposes",
        }
        for node in nodes
        if node.get("parent_event_id")
    ]
    ordered = sorted(nodes, key=_timeline_key)
    timeline = []
    for index, first in enumerate(ordered):
        if not (end := _latest_day(first)):
            continue
        earliest_intermediate_end = None
        for second in ordered[index + 1 :]:
            if second["start"] <= end:
                continue
            # A definite intervening event already connects the same pair.
            if (
                earliest_intermediate_end
                and earliest_intermediate_end < second["start"]
            ):
                break
            timeline.append(
                {
                    "source": first["event_id"],
                    "target": second["event_id"],
                    "kind": "temporal",
                }
            )
            latest = _latest_day(second)
            if latest and (
                earliest_intermediate_end is None or latest < earliest_intermediate_end
            ):
                earliest_intermediate_end = latest
    return dependencies + decomposition + timeline


def _event_for_scenario(
    scenario_id: str, spec: dict[str, Any], person_id: str, cutoff: date
) -> dict[str, Any] | None:
    item = SCENARIOS[scenario_id]
    window = _history_window(scenario_id, spec, cutoff)
    if window is None:
        return None
    start, end = window
    title = item["label"] + " · 背景候选"
    evidence = [
        {"id": scenario_id if index == 0 else f"{scenario_id}:{index + 1}", **source}
        for index, source in enumerate(item["sources"])
    ]
    node = _node(
        scenario_id,
        title,
        "history",
        start.isoformat(),
        end.isoformat(),
        item["precision"],
        spec["region_label"],
        depends_on=["birth"],
        participants=[person_id],
        notes="世界事件已索引，个人接触仍需匹配当时的行踪或家庭记录。",
        state_delta={
            "exposure_phase": item["phase"],
            "world_version": WORLD_VERSION,
            "historical_node_version": item["version"],
            "exposure_status": "candidate",
            "exposure_channel": "possible_local_context",
        },
        evidence=evidence,
        summary_node=True,
    )
    node["historical_assessment"] = assess_history(
        scenario_id, as_of=cutoff, region=spec["region"], window=window
    )
    return node


def _path_choice(
    spec: dict[str, Any],
    rng: random.Random,
    event_id: str,
    title: str,
    category: str,
    age: int,
    dependency: str,
    options: tuple[tuple[str, int, int], ...],
    condition: str,
    *,
    participants: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve one synthetic alternative and retain its generation provenance."""
    selected, pressure, support = rng.choice(options)
    option_names = [option[0] for option in options]
    year = spec["birth_year"] + age
    node = _node(
        event_id,
        title,
        category,
        f"{year:04d}-01-01",
        f"{year:04d}-12-31",
        "year",
        spec["region_label"],
        depends_on=[dependency],
        participants=participants,
        notes=f"{condition}。该合成路径选择「{selected}」，其他选项保留为未采用的情景。",
        state_delta={
            "path": selected,
            "pressure_delta": pressure,
            "support_delta": support,
        },
        evidence=[
            {
                "id": f"life-path-v1:{event_id}",
                "label": "人生阶段情景规则 · 版本 1",
                "kind": "synthetic_rule",
            }
        ],
    )
    node["decision"] = {
        "selected": selected,
        "options": option_names,
        "condition": condition,
        "basis": "seeded_scenario",
    }
    return node


def _life_path(
    spec: dict[str, Any], employment: str, employment_age: int
) -> list[dict[str, Any]]:
    """Sample bounded life transitions from stage, place and household context."""
    rng = random.Random(f"{spec['seed']}:life-path-v1")
    family = spec["household"] == "family"
    region = spec["region"]
    school_year = spec["birth_year"] + 15
    regional_commutes = {
        "11": "乘公共交通通勤",
        "12": "乘公共交通通勤",
        "31": "乘公共交通通勤",
        "81": "乘公共交通通勤",
        "82": "乘公共交通通勤",
    }
    commute = regional_commutes.get(region, "在周边城镇间通勤")
    school_routes = (
        (("继续在校学习", -1, 1), ("逐步进入手艺或实务学习", 1, 0))
        if school_year < 1986
        else (("继续普通中学教育", 0, 1), ("探索职业教育或实务培训", 1, 0))
    )
    commute_routes = (
        ("就近工作", -1, 0),
        ("靠近工作点暂住", 0, -1),
        (commute, 1, 0),
    )
    support_routes = (
        (("亲属分时分担日常事务", -1, 2), ("由本人承担主要协调", 1, 1))
        if family
        else (("保留本人安排并定期检视", -1, 0), ("通过熟人互助补充支持", 0, 1))
    )
    return [
        _path_choice(
            spec,
            random.Random(f"{spec['seed']}:early-routine-v1"),
            "early-routine",
            "学龄初期的同伴活动",
            "development",
            7,
            "education",
            (
                ("和同伴做简单的轮流游戏", 0, 1),
                ("和同伴用手边材料一起做游戏", 0, 1),
                ("在熟悉的空间与同伴做结伴游戏", 0, 0),
            ),
            "只记录设定中的主要活动，具体地点、同伴身份和频次未知",
        ),
        _path_choice(
            spec,
            random.Random(f"{spec['seed']}:community-contact-v1"),
            "community-contact",
            "形成日常社区往来",
            "community",
            27,
            "household",
            (
                ("在住处周边的日常办事中认识熟人", 0, 1),
                ("在共用生活空间里与邻近熟人保持往来", 0, 1),
            ),
            "以实际生活半径为条件，往来频次和参与组织情况未知",
        ),
        _path_choice(
            spec,
            rng,
            "school-path",
            "继续学习的路径选择",
            "education",
            15,
            "education",
            school_routes,
            "取决于当时的学习机会、时间开销与交通条件，具体学校和完成情况未知",
        ),
        _path_choice(
            spec,
            rng,
            "commute-path",
            "通勤与居住选择",
            "work",
            employment_age + 1,
            employment,
            commute_routes,
            "比较工作点、居住成本与往返时间，具体路线和单位未知",
        ),
        _path_choice(
            spec,
            rng,
            "daily-routine",
            "建立日常作息",
            "daily",
            employment_age + 1,
            "commute-path",
            (
                ("先固定起居与往返时间，再安排零散事务", -1, 0),
                ("按当周劳动和往返安排调整起居", 1, 0),
                ("集中处理家务，保留固定休息时段", 0, 1),
            ),
            "在工作、往返和休息之间分配可支配时间",
        ),
        _path_choice(
            spec,
            rng,
            "budget-routine",
            "形成日常开支安排",
            "finance",
            employment_age + 2,
            "daily-routine",
            (
                ("先留基本生活费用，再决定其他开支", -1, 0),
                ("逐周记录零散开支，按需要调整", 0, 0),
                ("优先预留通勤和生活必需品费用", 0, 1),
            ),
            "实际收入和支出金额未知，按合成生活路径记录预算取舍",
        ),
        _path_choice(
            spec,
            rng,
            "leisure-routine",
            "形成一项日常休闲活动",
            "leisure",
            employment_age + 3,
            "daily-routine",
            (
                ("在附近散步", -1, 0),
                ("与熟人闲谈", -1, 1),
                ("读能借到的书报", 0, 0),
            ),
            "受空闲时间、可达地点和日常开支约束",
        ),
        _path_choice(
            spec,
            rng,
            "work-adjustment",
            "劳动安排的阶段调整",
            "work",
            rng.randint(28, 32),
            "commute-path",
            (
                ("维持原劳动安排，调整日常节奏", -1, 0),
                ("保留收入来源并尝试补充技能", 1, 0),
                ("寻找时间安排更稳定的岗位", 1, 1),
            ),
            "以当时收入连续性及可支配时间为约束，岗位资历与合同条款未知",
        ),
        _path_choice(
            spec,
            rng,
            "support-plan",
            "日常支持与时间分配",
            "household",
            rng.randint(29, 34),
            "household",
            support_routes,
            "配合已纳入模拟的家庭范围调整事务安排，未设定具体疾病或收入",
            participants=["primary", "family-sibling"] if family else ["primary"],
        ),
        _path_choice(
            spec,
            rng,
            "later-life",
            "重排劳动节奏与日常活动",
            "community",
            rng.randint(54, 62),
            "work-adjustment",
            (
                ("维持部分日常劳动，并重新分配活动时间", 0, 0),
                ("减少密集劳动，留出更多生活事务时间", -1, 0),
                ("维持既有劳动节奏并寻求熟人协助", 0, 1),
            ),
            "长期活动安排可以逐步变化，退休资格、福利和实际健康状态未知",
        ),
    ]


def _early_adult_path(spec: dict[str, Any], context: str) -> list[dict[str, Any]]:
    year = spec["birth_year"] + 20
    region = spec["region_label"]
    task = {
        "服务岗位": "按日接待、整理物品并协助交接",
        "技术岗位": "熟悉实务流程、检查结果并协助日常维护",
        "经营活动": "盘点物品、处理订单并记录日常收支",
        "生产岗位": "准备材料、参与生产并按流程交接",
    }[context]
    arrangement = {
        "服务岗位": "受雇服务工作",
        "技术岗位": "受雇技术实务",
        "经营活动": "小规模自营",
        "生产岗位": "受雇生产劳动",
    }[context]
    study_first = random.Random(f"{spec['seed']}:work-entry-v1").randrange(3) == 0
    options = ["开始劳动", "继续实务学习"]
    route = "继续实务学习" if study_first else "开始劳动"
    entry = _node(
        "work",
        route,
        "education" if study_first else "work",
        f"{year}-07-01",
        None,
        "year",
        region,
        depends_on=["education"],
        notes=(
            "继续以学习实务方法和安排练习为主，具体机构和结业记录未知。"
            if study_first
            else f"初期在{context}中{task}，按实际任务逐步熟悉日常节奏。单位和薪酬未知。"
        ),
        state_delta=(
            {"entry_kind": "education", "entry_route": route}
            if study_first
            else {
                "entry_kind": "work",
                "entry_route": route,
                "work_status": "参加劳动",
                "work_context": context,
                "work_task": task,
                "work_arrangement": arrangement,
            }
        ),
        evidence=[
            {
                "id": "life-path-v2:entry",
                "label": "初入社会情景规则",
                "kind": "synthetic_rule",
            }
        ],
    )
    entry["decision"] = {
        "selected": route,
        "options": options,
        "condition": "按学习延续机会、当前生活开支和初期可参与的劳动安排选择",
        "basis": "seeded_scenario",
    }
    entry["state_delta"].update(pressure_delta=1, support_delta=0)
    if not study_first:
        return [entry]

    entry["end"] = f"{year + 1}-12-31"
    employment = _node(
        "employment-entry",
        "实务学习后开始劳动",
        "work",
        f"{year + 2}-01-01",
        None,
        "year",
        region,
        depends_on=["work"],
        notes=f"此前安排以学习为主，转入{context}后开始{task}。单位与合同形式未知。",
        state_delta={
            "entry_kind": "work",
            "entry_route": "实务学习后开始劳动",
            "work_status": "参加劳动",
            "work_context": context,
            "work_task": task,
            "work_arrangement": arrangement,
        },
        evidence=[
            {
                "id": "life-path-v2:employment-entry",
                "label": "学习转劳动情景规则",
                "kind": "synthetic_rule",
            }
        ],
    )
    return [entry, employment]


def generate_graph(
    spec: dict[str, Any], *, person_id: str = "primary"
) -> dict[str, Any]:
    """Generate one deterministic graph candidate from a validated spec."""
    birth = spec["birth_year"]
    as_of = date.fromisoformat(spec["as_of"])
    cutoff = _death_cutoff(spec) if spec["life_status"] == "deceased" else as_of
    rng = random.Random(spec["seed"])
    name = _stable_name(spec)
    person = {
        "person_id": person_id,
        "name": name,
        "display_name": f"{name}（合成角色）",
        "region": spec["region_label"],
        "region_id": spec["region"],
        "ethnicity": spec["ethnicity_label"],
        "ethnicity_id": spec["ethnicity"],
        "birth_year": birth,
        "life_status": spec["life_status"],
        "as_of": spec["as_of"],
        "summary": f"{birth} 年出生于{spec['region_label']}，按{'现存人口' if spec['entry'] == 'living_at_as_of' else '出生队列'}入口建档。",
        "synthetic": True,
        "family": [],
        "death_date": None,
    }
    nodes = [
        _node(
            "birth",
            "出生与家庭起点",
            "origin",
            f"{birth:04d}-01-01",
            f"{birth:04d}-12-31",
            "year",
            spec["region_label"],
            notes="出生日期仅确定到年份，下一阶段由合成档案和后续事件建模。",
        ),
        _node(
            "education",
            "基础教育阶段",
            "education",
            f"{birth + 6:04d}-09-01",
            f"{birth + 18:04d}-06-30",
            "year",
            spec["region_label"],
            depends_on=["birth"],
            notes="这是学龄期的档案区间，出勤、学校和最后学历仍待更细的事件锚定。",
            state_delta={"education_stage": "基础教育"},
        ),
        _node(
            "household",
            "居住与照料安排",
            "household",
            f"{birth + 26:04d}-01-01",
            None,
            "year",
            spec["region_label"],
            depends_on=["birth"],
            notes="按生活半径和可支配时间安排常住点与日常事务，具体住址未知。",
            state_delta={"household_role": "形成或调整"},
        ),
    ]
    if spec["household"] == "family":
        person["family"] = [
            {"id": "family-elder", "name": "家庭长辈", "relation": "长辈"},
            {"id": "family-sibling", "name": "同辈亲属", "relation": "同辈"},
        ]
        nodes[0]["participants"].append("family-elder")
        nodes[2]["participants"].append("family-sibling")
        nodes[2]["notes"] = (
            "已有同辈亲属参与居住和日常事务安排，共同生活状况及分工细节未知。"
        )
    early_adult = _early_adult_path(spec, rng.choice(WORK_CONTEXTS))
    nodes.extend(early_adult)
    employment = early_adult[-1]["event_id"]
    nodes.extend(_life_path(spec, employment, 22 if len(early_adult) == 2 else 20))
    for theme in spec["themes"]:
        event = _event_for_scenario(theme, spec, person_id, cutoff)
        if event:
            nodes.append(event)
    if spec["detail"] == "rich":
        nodes.extend(
            [
                _node(
                    "health-check",
                    "成年健康与照护检查",
                    "health",
                    f"{birth + 30:04d}-01-01",
                    None,
                    "year",
                    spec["region_label"],
                    depends_on=["work", "household"],
                    state_delta={"health_review": "periodic"},
                ),
                _path_choice(
                    spec,
                    random.Random(f"{spec['seed']}:digital-practice-v1"),
                    "digital-learning",
                    "练习使用数字工具",
                    "digital",
                    max(birth + 23, 2011) - birth,
                    "digital-adoption",
                    (
                        ("通过熟人演示学习基本操作", 0, 1),
                        ("在日常事务中反复练习操作", 1, 0),
                    ),
                    "按使用场景和可获得的帮助建立合成学习经历",
                ),
            ]
        )
    nodes.append(
        _path_choice(
            spec,
            random.Random(f"{spec['seed']}:digital-access-v1"),
            "digital-adoption",
            "形成数字服务使用方式",
            "digital",
            max(birth + 22, 2010) - birth,
            "education",
            (
                ("主要用联网手机保持联络", 0, 1),
                ("主要通过家人或公共设备间歇使用网络", 0, 1),
                ("使用电脑查询信息和办理部分事务", 0, 0),
            ),
            "按设备接入和日常用途建立合成情景，具体平台和账号未知",
        )
    )
    if spec["life_status"] == "deceased":
        death = cutoff.isoformat()
        person["death_date"] = death
        nodes.append(
            _node(
                "death",
                "死亡与生平结束",
                "life_end",
                death,
                death,
                "day",
                spec["region_label"],
                depends_on=["birth"],
                notes="合成的生命终点，死因未指定。",
            )
        )
    # A snapshot never contains an event that starts after its as-of date.
    visible_as_of = person["death_date"] or spec["as_of"]
    nodes = [node for node in nodes if node["start"] <= visible_as_of]
    for node in nodes:
        if node.get("end") and node["end"] > visible_as_of:
            node["end"] = visible_as_of
    for node in nodes:
        node["participants"] = [
            person_id if item == "primary" else item for item in node["participants"]
        ]
    nodes.sort(key=_timeline_key)
    graph = {
        "graph_revision_id": None,
        "parent_revision_id": None,
        "branch": "main",
        "person": person,
        "as_of": spec["as_of"],
        "nodes": nodes,
        "edges": _edges(nodes),
        "diff": {
            "added": [node["event_id"] for node in nodes],
            "modified": [],
            "removed": [],
            "recomputed": [],
        },
    }
    graph["validation"] = validate_graph(graph)
    if graph["validation"]["valid"]:
        attach_dimension_ledger(graph)
    return graph


def _cycle(deps: dict[str, list[str]]) -> bool:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(key: str) -> bool:
        if key in active:
            return True
        if key in visited:
            return False
        active.add(key)
        for dep in deps.get(key, []):
            if visit(dep):
                return True
        active.remove(key)
        visited.add(key)
        return False

    return any(visit(key) for key in deps)


def _dependencies(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {node["event_id"]: node.get("depends_on", []) for node in nodes}


def _participant_effects(
    node: dict[str, Any], field: str
) -> dict[tuple[str, str, str], Any]:
    effects = {}
    for row in node.get(field, []):
        person_id, role = row["person_id"], row["role"]
        if person_id not in node["participants"]:
            raise ValueError(f"unknown participant for effect: {person_id}")
        for key, value in row["delta"].items():
            effect = person_id, role, key
            if effect in effects:
                raise ValueError(
                    f"duplicate participant effect: {person_id}/{role}/{key}"
                )
            effects[effect] = value
    return effects


def _effect_matches(total: Any, parts: list[Any]) -> bool:
    if not parts:
        return False
    if type(total) not in (int, float):
        return len(parts) == 1 and type(parts[0]) is type(total) and parts[0] == total
    if any(type(part) not in (int, float) for part in parts):
        return False
    try:
        expected = Fraction(str(total))
        amounts = [Fraction(str(part)) for part in parts]
    except ValueError:
        return False
    if expected == 0:
        return not any(amounts)
    return sum(amounts) == expected and all(
        amount * expected >= 0 for amount in amounts
    )


def _check_effects(anchor_id: str, original: dict, portions: list[dict]) -> list[str]:
    allocations: dict[Any, list[Any]] = defaultdict(list)
    for portion in portions:
        for key, value in portion.items():
            allocations[key].append(value)
    return [
        f"decomposition effect must be conserved: {anchor_id} -> {key}"
        for key in dict.fromkeys([*original, *allocations])
        if key not in original or not _effect_matches(original[key], allocations[key])
    ]


def _decomposition_issues(nodes: list[dict[str, Any]]) -> list[str]:
    issues = []
    anchors = {
        node["event_id"]: node for node in nodes if "decomposition_state_delta" in node
    }
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ancestry = {}
    for node in nodes:
        if not (parent := node.get("parent_event_id")):
            continue
        if parent not in anchors:
            issues.append(f"unknown decomposition anchor: {parent}")
            continue
        ancestry[node["event_id"]] = [parent]
        children[parent].append(node)
    if _cycle(ancestry):
        issues.append("cycle detected in decomposition")
    for anchor_id, anchor in anchors.items():
        if (
            not anchor.get("summary_node")
            or anchor.get("state_delta")
            or anchor.get("participant_deltas")
        ):
            issues.append(f"decomposition anchor cannot own effects: {anchor_id}")
        subdivisions = children[anchor_id]
        anchor_end = _latest_day(anchor)
        for node in subdivisions:
            event_end = _latest_day(node)
            if node["start"] < anchor["start"] or (
                anchor_end and (event_end is None or event_end > anchor_end)
            ):
                issues.append(
                    f"event outside decomposition anchor: {node['event_id']} -> {anchor_id}"
                )
        issues.extend(
            _check_effects(
                anchor_id,
                anchor["decomposition_state_delta"],
                [
                    node.get("decomposition_state_delta", node["state_delta"])
                    for node in subdivisions
                ],
            )
        )
        try:
            issues.extend(
                _check_effects(
                    anchor_id,
                    _participant_effects(anchor, "decomposition_participant_deltas"),
                    [
                        _participant_effects(
                            node,
                            "decomposition_participant_deltas"
                            if "decomposition_state_delta" in node
                            else "participant_deltas",
                        )
                        for node in subdivisions
                    ],
                )
            )
        except ValueError as exc:
            issues.append(str(exc))
        if len(subdivisions) < 2:
            issues.append(f"decomposition needs at least two children: {anchor_id}")
    return issues


def validate_graph(graph: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    nodes = graph.get("nodes", [])
    ids = [node.get("event_id") for node in nodes]
    if len(ids) != len(set(ids)):
        issues.append("duplicate event_id")
    known = set(ids)
    person = graph.get("person", {})
    as_of = person.get("death_date") or graph.get("as_of") or person.get("as_of")
    snapshot_day = None
    if as_of:
        try:
            snapshot_day = date.fromisoformat(as_of)
        except (TypeError, ValueError):
            issues.append("invalid graph as_of")
    if "birth" not in known:
        issues.append("missing birth event")
    if person.get("life_status") == "deceased" and "death" not in known:
        issues.append("missing death event")
    starts: dict[str, date] = {}
    for node in nodes:
        if not node.get("event_id"):
            issues.append("missing event_id")
            continue
        try:
            start = date.fromisoformat(node["start"])
            starts[node["event_id"]] = start
            end = date.fromisoformat(node["end"]) if node.get("end") else None
            if end and end < start:
                issues.append(f"interval reversed: {node['event_id']}")
            if snapshot_day and (start > snapshot_day or (end and end > snapshot_day)):
                issues.append(f"event after as_of: {node['event_id']}")
            if person.get("birth_year") and start.year < person["birth_year"]:
                issues.append(f"event before birth: {node['event_id']}")
        except (KeyError, TypeError, ValueError):
            issues.append(f"invalid date: {node.get('event_id')}")
        try:
            for participant_id, known_day in node.get("known_at", {}).items():
                if participant_id not in node.get("participants", []):
                    issues.append(f"unknown participant for known_at: {participant_id}")
                if (start := starts.get(node["event_id"])) and _parse_day(
                    known_day, "known_at"
                ) < start:
                    issues.append(f"known_at before event: {node['event_id']}")
        except (AttributeError, ValueError):
            issues.append(f"invalid known_at: {node['event_id']}")
        for dep in node.get("depends_on", []):
            if dep not in known:
                issues.append(f"unknown event dependency: {node['event_id']} -> {dep}")
        try:
            _participant_effects(node, "participant_deltas")
        except ValueError as exc:
            issues.append(str(exc))
    for node in nodes:
        target = starts.get(node["event_id"])
        for dep in node.get("depends_on", []):
            if target and dep in starts and starts[dep] > target:
                issues.append(
                    f"dependency after dependent: {dep} -> {node['event_id']}"
                )
    if _cycle(_dependencies(nodes)):
        issues.append("cycle detected in dependencies")
    if not issues:
        issues.extend(_decomposition_issues(nodes))
    return {"valid": not issues, "issues": issues}


EDITABLE_FIELDS = {
    "title",
    "category",
    "start",
    "end",
    "precision",
    "location",
    "notes",
    "state_delta",
    "depends_on",
    "participant_deltas",
    "known_at",
}
LANDMARKS = {"birth", "death"}


def _edit_fields(operation: dict[str, Any]) -> dict[str, Any]:
    fields = {
        key: deepcopy(value)
        for key, value in operation.items()
        if key in EDITABLE_FIELDS
    }
    for key, value in fields.items():
        if key in {"start", "end"}:
            if key == "start" or value is not None:
                fields[key] = _parse_day(value, key).isoformat()
        elif key == "depends_on":
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError("depends_on must be an event ID list")
            fields[key] = list(dict.fromkeys(value))
        elif key == "state_delta":
            if not isinstance(value, dict):
                raise ValueError("state_delta must be an object")
        elif key == "participant_deltas":
            if not isinstance(value, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("person_id"), str)
                or not isinstance(item.get("role"), str)
                or not isinstance(item.get("delta"), dict)
                for item in value
            ):
                raise ValueError("invalid participant_deltas")
        elif key == "known_at":
            if not isinstance(value, dict):
                raise ValueError("known_at must be a participant date mapping")
            fields[key] = {
                person_id: _parse_day(day, key).isoformat()
                for person_id, day in value.items()
            }
        elif not isinstance(value, str) or (
            key in {"title", "category"} and not value.strip()
        ):
            raise ValueError(f"invalid {key}")
    if fields.get("precision", "day") not in {"day", "month", "year", "range"}:
        raise ValueError("invalid precision")
    return fields


def _event_id(operation: dict[str, Any]) -> str:
    event_id = operation.get("event_id")
    if not isinstance(event_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}", event_id
    ):
        raise ValueError("invalid event ID")
    return event_id


def _add_event(operation: dict, reserved: set[str], defaults: dict) -> dict:
    event_id = _event_id(operation)
    if event_id in reserved or event_id in LANDMARKS or event_id in SCENARIOS:
        raise ValueError(f"event already exists or is reserved: {event_id}")
    fields = _edit_fields(operation)
    if not {"title", "start"} <= fields.keys():
        raise ValueError("added event requires title and start")
    reserved.add(event_id)
    return _node(event_id, **{"end": None, "precision": "day", **defaults, **fields})


def _split_event(operation: dict, nodes: dict[str, dict], reserved: set[str]) -> None:
    anchor = nodes[operation["event_id"]]
    event_id = anchor["event_id"]
    if event_id in LANDMARKS or anchor.get("summary_node"):
        raise ValueError(f"cannot split an anchored event: {event_id}")
    subdivisions = operation.get("children")
    if not isinstance(subdivisions, list) or len(subdivisions) < 2:
        raise ValueError("split requires at least two child events")
    defaults = {
        "category": anchor["category"],
        "location": anchor["location"],
        "participants": list(anchor["participants"]),
        "depends_on": anchor["depends_on"],
    }
    for subdivision in subdivisions:
        if not isinstance(subdivision, dict):
            raise ValueError("split child must be an event object")
        node = _add_event(subdivision, reserved, defaults)
        if "known_at" not in subdivision and anchor.get("known_at"):
            node["known_at"] = {
                person_id: max(day, node["start"])
                for person_id, day in anchor["known_at"].items()
            }
        node["participants"] = list(node["participants"])
        node["parent_event_id"] = event_id
        nodes[node["event_id"]] = node
    anchor.update(
        summary_node=True,
        decomposition_state_delta=anchor["state_delta"],
        decomposition_participant_deltas=anchor.pop("participant_deltas", []),
        state_delta={},
    )


def _recompute(
    nodes: dict[str, dict[str, Any]],
    original: dict[str, dict[str, Any]],
    modified: set[str],
    moved: set[str],
    reconnected: set[str],
) -> set[str]:
    affected = modified | reconnected
    recomputed = set(reconnected)
    deltas: dict[str, timedelta] = {}
    pending = dict(nodes)
    while pending:
        for event_id, node in list(pending.items()):
            dependencies = node["depends_on"]
            if any(dep in pending for dep in dependencies):
                continue
            if any(dep in affected for dep in dependencies):
                affected.add(event_id)
                recomputed.add(event_id)
            if event_id in original:
                old_start = _parse_day(original[event_id]["start"], "previous start")
                current_start = _parse_day(node["start"], "start")
                if (
                    event_id not in moved
                    and event_id not in LANDMARKS
                    and not node["summary_node"]
                ):
                    shift = max(
                        (deltas[dep] for dep in dependencies if dep in deltas),
                        key=abs,
                        default=timedelta(),
                    )
                    if shift:
                        node["start"] = (current_start + shift).isoformat()
                        if node["end"]:
                            node["end"] = (
                                _parse_day(node["end"], "end") + shift
                            ).isoformat()
                        current_start += shift
                if current_start != old_start:
                    deltas[event_id] = current_start - old_start
            del pending[event_id]
    return recomputed


def apply_edits(
    graph: dict[str, Any],
    operations: list[dict[str, Any]],
    *,
    before_simulation: bool = False,
) -> dict[str, Any]:
    """Create an independent child and recompute the edited dependency paths."""
    child = deepcopy(graph)
    child.update(
        parent_revision_id=graph.get("graph_revision_id"),
        graph_revision_id=None,
        branch="edit",
    )
    original = {node["event_id"]: node for node in graph["nodes"]}
    nodes = {node["event_id"]: node for node in child["nodes"]}
    reserved = set(original)
    reconnected: set[str] = set()
    moved: set[str] = set()
    for operation in operations:
        op = operation.get("op")
        event_id = _event_id(operation)
        if op in {"update", "delete", "split"} and event_id not in nodes:
            raise ValueError(f"unknown event: {event_id}")
        if op == "delete":
            if event_id in LANDMARKS:
                raise ValueError(f"cannot delete life boundary: {event_id}")
            removed = nodes.pop(event_id)
            for dependent in nodes.values():
                if event_id in dependent["depends_on"]:
                    deps = [
                        value for value in dependent["depends_on"] if value != event_id
                    ]
                    dependent["depends_on"] = list(
                        dict.fromkeys(deps + removed["depends_on"])
                    )
                    reconnected.add(dependent["event_id"])
        elif op == "update":
            fields = _edit_fields(operation)
            node = nodes[event_id]
            if (event_id in LANDMARKS or node["summary_node"]) and {
                "start",
                "end",
                "precision",
            } & fields.keys():
                if any(
                    fields[key] != node[key]
                    for key in {"start", "end", "precision"} & fields.keys()
                ):
                    raise ValueError(f"cannot move an anchored event: {event_id}")
            if "start" in fields and fields["start"] != node["start"]:
                moved.add(event_id)
            node.update(fields)
        elif op == "add":
            nodes[event_id] = _add_event(
                operation,
                reserved,
                {
                    "category": "custom",
                    "location": child["person"]["region"],
                    "depends_on": ["birth"],
                    "participants": [child["person"]["person_id"]],
                },
            )
        elif op == "split":
            _split_event(operation, nodes, reserved)
        else:
            raise ValueError(f"unsupported operation: {op}")
    if _cycle(_dependencies(list(nodes.values()))):
        raise ValueError("cycle detected in dependencies")
    added = nodes.keys() - original.keys()
    removed = original.keys() - nodes.keys()
    modified = {
        key for key in nodes.keys() & original.keys() if nodes[key] != original[key]
    }
    anchor_ids = set()
    for key in added | removed | modified:
        parent = (nodes.get(key) or original[key]).get("parent_event_id")
        while parent in nodes and parent not in anchor_ids:
            anchor_ids.add(parent)
            parent = nodes[parent].get("parent_event_id")
    recomputed = _recompute(nodes, original, modified | anchor_ids, moved, reconnected)
    child["nodes"] = sorted(nodes.values(), key=_timeline_key)
    child["edges"] = _edges(child["nodes"])
    child["diff"] = {
        "added": sorted(added),
        "modified": sorted(modified - reconnected),
        "removed": sorted(removed),
        "recomputed": sorted(recomputed - removed),
    }
    if before_simulation:
        return child
    child["validation"] = validate_graph(child)
    if not child["validation"]["valid"]:
        raise ValueError("；".join(child["validation"]["issues"]))
    attach_dimension_ledger(child)
    return child


def suggest_operations(graph: dict[str, Any], suggestion: str) -> list[dict[str, Any]]:
    """Map a small, transparent suggestion vocabulary to graph operations."""
    text = suggestion.strip()
    nodes = {node["event_id"]: node for node in graph["nodes"]}
    if "地震" in text and "候选暴露" in text:
        earthquake_nodes = [
            node
            for node in graph["nodes"]
            if node["category"] == "history" and "地震" in node["title"]
        ]
        if "汶川" in text:
            earthquake_nodes = [
                node for node in earthquake_nodes if node["event_id"] == "DIS-WC-2008"
            ]
        if len(earthquake_nodes) == 1:
            event = earthquake_nodes[0]
            state_delta = {
                **event.get("state_delta", {}),
                "exposure_status": "candidate",
                "exposure_channel": "requires_personal_evidence",
            }
            operation: dict[str, Any] = {
                "op": "update",
                "event_id": event["event_id"],
                "state_delta": state_delta,
            }
            if "家庭照护影响" in text or "照护安排" in text:
                operation["state_delta"]["family_care_impact"] = (
                    "possible_shared_event_channel"
                )
                operation["notes"] = (
                    "家庭照护影响仍是候选机制，需同住关系与家庭记录支持。"
                )
            return [operation]
    aliases = {
        "education": "教育|上学",
        "work": "工作|就业",
        "household": "居住|家庭",
        "health-check": "健康|体检",
        "digital-adoption": "数字设备|数字服务",
    }
    years = dict(zip("一二三四五六七八九十两", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 2)))
    operations: list[dict[str, Any]] = []
    for clause in re.split(r"[，,；;。]", text):
        for event_id, node in nodes.items():
            names = [
                re.escape(node["title"]),
                re.escape(event_id),
                aliases.get(event_id, "(?!)"),
            ]
            target = rf"(?:{'|'.join(names)})(?:节点)?"
            if re.search(rf"(?:删除|移除)\s*{target}\s*$", clause):
                operations.append({"op": "delete", "event_id": event_id})
            elif rename := re.search(
                rf"{target}(?:的)?(?:标题)?(?:改为|改成|更名为)\s*(.+?)\s*$", clause
            ):
                title = rename[1].strip().strip("“”\"'")
                operations.append(
                    {"op": "update", "event_id": event_id, "title": title}
                )
            elif move := re.search(
                rf"{target}(?:延后|推迟)\s*(\d{{1,2}}|[一二三四五六七八九十两])\s*年$",
                clause,
            ):
                start = _parse_day(node["start"], "start")
                amount = int(move[1]) if move[1].isdigit() else years[move[1]]
                new_year = start.year + amount
                if not amount or new_year > 9999:
                    raise ValueError("延后年数超出范围")
                try:
                    shifted = start.replace(year=new_year)
                except ValueError:
                    shifted = start.replace(year=new_year, day=28)
                operations.append(
                    {"op": "update", "event_id": event_id, "start": shifted.isoformat()}
                )
    if not operations:
        raise ValueError(
            "本地编辑器支持改名、删除和按年延后，例如「把工作节点延后一年」"
        )
    return operations
