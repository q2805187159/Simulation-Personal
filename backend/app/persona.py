"""Event-bound simulated states and the contract for their narrative enrichment."""

from datetime import date
import random
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError


PROFILE_VERSION = 1
TENDENCIES = ("routine", "risk_aversion", "social_support", "learning")
VOICE = ("directness", "reflection", "detail")
METRICS = {
    "emotion": ("valence", "arousal", "stress"),
    "appraisal": ("salience", "perceived_control"),
    "tendencies": TENDENCIES,
    "voice": VOICE,
}

# Scenario effects relative to the character's baseline. They are POC
# simulation parameters, with no psychometric or population interpretation.
EVENT_EFFECTS: dict[str, tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = {
    #                         valence / arousal / stress, salience / control, tendency shifts
    "education": ((2, 3, 4), (17, -7), (4, 0, 1, 9)),
    "work": ((1, 10, 13), (24, -9), (4, 4, 2, 3)),
    "household": ((5, 4, 8), (20, -3), (6, 2, 9, 0)),
    "health": ((-1, 0, 6), (12, 3), (4, 3, 1, 0)),
    "digital": ((3, 6, 3), (9, 3), (1, -2, 2, 5)),
    "community": ((6, 5, 2), (11, 4), (2, -1, 5, 3)),
}

SCENE_CUES = {
    "education": (
        "把注意力放在学习任务与渐渐熟悉的日程上。",
        "课业与日常节奏",
    ),
    "work": (
        "在路径刚开始时，先理清每天需要完成的事。",
        "初到新阶段时的日常安排",
    ),
    "household": (
        "生活安排变化时，先留意时间如何分配。",
        "居住与照料带来的日程变化",
    ),
    "health": ("这一阶段适合回顾对健康的留意程度。", "健康检查"),
    "digital": (
        "面对新的工具，先处理和生活有关的具体用途。",
        "开始使用线上服务时的变化",
    ),
    "community": ("从可以维持的日常联系与互助谈起。", "日常来往和相互照应"),
}

InterpretationText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=240),
]
NarrativeText = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=1200
    ),
]
MemoryText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=600),
]


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: InterpretationText
    motivation: InterpretationText
    emotion_note: InterpretationText
    reflection: InterpretationText
    recall_cue: InterpretationText
    speaking_sample: InterpretationText


class DetailDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    narrative: NarrativeText
    memory: MemoryText
    interpretation: Interpretation


class DetailBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[DetailDraft] = Field(min_length=1, max_length=3)


def _score(value: float) -> int:
    return min(100, max(0, round(value)))


def _valid_from(node: dict) -> str:
    return node["end"] or node["start"]


def _scope(node: dict) -> str:
    if "decomposition_state_delta" in node:
        return "boundary"
    if node.get("summary_node"):
        return "context"
    if node["event_id"] in {"birth", "death"}:
        return "boundary"
    return "synthesized"


def _ordinal_effect(node: dict, key: str) -> int:
    value = node.get("state_delta", {}).get(key)
    return value if type(value) is int and -2 <= value <= 2 else 0


def _interpretation(node: dict, memory_kind: str, state: dict) -> dict[str, str] | None:
    if _scope(node) != "synthesized":
        return None
    cue, topic = SCENE_CUES.get(
        node["category"], ("先了解当时的具体情况。", node["title"])
    )
    stress = state["emotion"]["stress"]
    regulation = state["appraisal"]["perceived_control"]
    return {
        "scene": cue,
        "motivation": "先让眼前的安排变得可做，再考虑下一步。",
        "emotion_note": "对变化有些警惕，需要时间理清安排。"
        if stress >= 60
        else "心情随日常变化，有余力时再处理更远的打算。",
        "reflection": "能掌握的地方就自己做决定。"
        if regulation >= 55
        else "回头看，当时能做的决定要受环境和手头资源限制。",
        "recall_cue": topic
        if memory_kind == "synthetic_episodic"
        else "早年重建，直接回忆未知。",
        "speaking_sample": "我当时先顾眼前能做的事。具体的，得看你问的是哪一段。",
    }


def build_profiles(graph: dict[str, Any], seed: int) -> dict[str, dict]:
    """Project state once per anchored event in valid-time order."""
    person = graph["person"]
    rng = random.Random(f"{seed}:persona:v{PROFILE_VERSION}")
    tendencies = {name: rng.randint(36, 64) for name in TENDENCIES}
    voice = {name: rng.randint(35, 70) for name in VOICE}
    baseline = (rng.randint(43, 60), rng.randint(37, 55), rng.randint(31, 48))
    previous_affect = dict(zip(METRICS["emotion"], baseline))
    previous_time = None
    previous_id = None
    profiles = {}

    for node in sorted(
        graph["nodes"],
        key=lambda item: (_valid_from(item), item["start"], item["event_id"]),
    ):
        event_id = node["event_id"]
        scope = _scope(node)
        valid_from = _valid_from(node)
        observed = date.fromisoformat(valid_from)
        age_at_start = int(node["start"][:4]) - person["birth_year"]
        memory_kind = {
            "context": "historical_context",
            "boundary": "archive_boundary",
            "synthesized": "synthetic_episodic"
            if age_at_start >= 6
            else "early_life_reconstruction",
        }[scope]
        values = dict.fromkeys(METRICS)
        clarity = None
        provenance = {
            "basis": "synthetic_state" if scope == "synthesized" else "archive_context",
            "anchors": list(
                dict.fromkeys(
                    [event_id, *(row["id"] for row in node.get("evidence", []))]
                )
            ),
            "method": f"event-state-v{PROFILE_VERSION}",
        }
        if scope == "synthesized":
            emotion_shift, appraisal_shift, tendency_shift = EVENT_EFFECTS.get(
                node["category"], ((0, 2, 2), (6, 0), (0, 0, 0, 0))
            )
            pressure = _ordinal_effect(node, "pressure_delta")
            support = _ordinal_effect(node, "support_delta")
            valence, arousal, stress = emotion_shift
            emotion_shift = (
                valence - pressure + support,
                arousal + pressure * 2,
                stress + pressure * 4 - support * 2,
            )
            elapsed = 0 if previous_time is None else (observed - previous_time).days
            retention = 1 / (1 + elapsed / 365.25)
            emotion = {
                name: _score(base + retention * (previous_affect[name] - base) + effect)
                for name, base, effect in zip(
                    METRICS["emotion"], baseline, emotion_shift
                )
            }
            tendencies = {
                name: _score(tendencies[name] + effect)
                for name, effect in zip(TENDENCIES, tendency_shift)
            }
            tendencies["social_support"] = _score(
                tendencies["social_support"] + support * 3
            )
            appraisal = {
                "salience": _score(
                    43 + appraisal_shift[0] + abs(emotion["valence"] - 50) / 3
                ),
                "perceived_control": _score(
                    50
                    + appraisal_shift[1]
                    + (tendencies["routine"] - 50) / 3
                    + support * 2
                    - pressure * 2
                ),
            }
            values.update(
                emotion=emotion,
                appraisal=appraisal,
                tendencies=tendencies,
                voice=voice.copy(),
            )
            if memory_kind == "synthetic_episodic":
                clarity = _score(
                    38 + appraisal["salience"] * 0.55 - emotion["stress"] * 0.2
                )
            provenance["previous_event_id"] = previous_id
            previous_affect, previous_time, previous_id = emotion, observed, event_id
        profiles[event_id] = {
            "schema_version": PROFILE_VERSION,
            "event_id": event_id,
            "valid_from": valid_from,
            "scope": scope,
            **values,
            "memory": {
                "kind": memory_kind,
                "clarity": clarity,
                "known_from": valid_from,
            },
            "interpretation": _interpretation(node, memory_kind, values),
            "provenance": provenance,
        }
    return profiles


def profile_problem(chunk: dict, node: dict) -> str | None:
    """Validate new detail records; pre-profile releases remain readable."""
    profile = chunk.get("profile")
    if "profile" not in chunk:
        return None
    if not isinstance(profile, dict):
        return "人物状态格式无效。"
    scope = _scope(node)
    valid_from = _valid_from(node)
    memory = profile.get("memory")
    provenance = profile.get("provenance")
    if (
        profile.get("schema_version") != PROFILE_VERSION
        or profile.get("event_id") != node["event_id"]
        or profile.get("scope") != scope
        or profile.get("valid_from") != valid_from
        or not isinstance(memory, dict)
        or memory.get("known_from") != valid_from
        or not isinstance(provenance, dict)
        or not isinstance(provenance.get("anchors"), list)
        or node["event_id"] not in provenance["anchors"]
    ):
        return "人物状态、认知时间或来源与事件锚点不一致。"
    for key, names in METRICS.items():
        values = profile.get(key)
        if scope != "synthesized" and values is None:
            continue
        if (
            scope != "synthesized"
            or not isinstance(values, dict)
            or set(values) != set(names)
        ):
            return "人物状态量表与事件证据类型不一致。"
        if any(
            type(value) is not int or not 0 <= value <= 100 for value in values.values()
        ):
            return "人物状态超出 0–100 模拟量表。"
    clarity = memory.get("clarity")
    if scope == "synthesized":
        try:
            Interpretation.model_validate(profile.get("interpretation"))
        except ValidationError:
            return "人物视角细节缺少必需字段或超过大小限制。"
        if memory.get("kind") == "synthetic_episodic":
            if type(clarity) is not int or not 0 <= clarity <= 100:
                return "回忆清晰度超出 0–100 模拟量表。"
        elif memory.get("kind") != "early_life_reconstruction" or clarity is not None:
            return "早年记忆类型与可用的清晰度不一致。"
    else:
        memory_kind = "historical_context" if scope == "context" else "archive_boundary"
        if (
            memory.get("kind") != memory_kind
            or clarity is not None
            or profile.get("interpretation") is not None
        ):
            return "历史背景或生命边界不得写成个人回忆。"
    return None
