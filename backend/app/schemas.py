from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str | None = None
    ethnicity: str | None = None
    population_frame: str | None = Field(default=None, max_length=64)
    birth_year: int | None = Field(default=None, ge=1880, le=2100)
    as_of: date
    entry: Literal["birth_cohort", "living_at_as_of"]
    household: Literal["individual", "family"] | None = None
    detail: Literal["standard", "rich"]
    seed: int = Field(ge=0, le=2**63 - 1)
    life_status: Literal["auto", "living", "deceased"]

    @field_validator("as_of", mode="before")
    @classmethod
    def strict_as_of(cls, value):
        return strict_iso_date(value)


class GraphEventFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=180)
    category: str | None = Field(default=None, max_length=64)
    start: date | None = None
    end: date | None = None
    precision: Literal["day", "month", "year", "range"] | None = None
    location: str | None = Field(default=None, max_length=180)
    notes: str | None = Field(default=None, max_length=1200)
    state_delta: dict[str, str | int | float | bool] | None = None
    depends_on: list[str] | None = Field(default=None, max_length=30)
    known_at: dict[str, date] = Field(default_factory=dict, max_length=30)

    @field_validator("known_at", mode="before")
    @classmethod
    def validate_knowledge_dates(cls, value):
        if not isinstance(value, dict):
            raise ValueError("获知时间必须按参与者填写")
        return {person_id: strict_iso_date(day) for person_id, day in value.items()}

    @field_validator("start", "end", mode="before")
    @classmethod
    def validate_date(cls, value):
        if value is None:
            return None
        if isinstance(value, date):
            return value
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError("日期必须采用 YYYY-MM-DD 格式")
        return date.fromisoformat(value)


class ParticipantDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=64)
    delta: dict[str, str | int | float | bool] = Field(min_length=1)


class GraphSplitChild(GraphEventFields):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    title: str = Field(min_length=1, max_length=180)
    start: date
    participant_deltas: list[ParticipantDelta] = Field(
        default_factory=list, max_length=30
    )


class GraphOperation(GraphEventFields):
    op: Literal["update", "delete", "add", "split"]
    event_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    children: list[GraphSplitChild] | None = Field(
        default=None, min_length=2, max_length=20
    )
    participant_deltas: list[ParticipantDelta] | None = Field(
        default=None, max_length=30
    )

    @model_validator(mode="after")
    def validate_split_payload(self):
        if self.op == "split":
            if not self.event_id or self.children is None:
                raise ValueError("拆分需要父节点编号和至少两个子节点")
            if self.model_fields_set - {"op", "event_id", "children"}:
                raise ValueError("拆分字段应填写在子节点中")
        elif "children" in self.model_fields_set:
            raise ValueError("只有拆分操作可以包含子节点")
        return self


class GraphEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["manual", "llm"]
    operations: list[GraphOperation] = Field(default_factory=list, max_length=20)
    suggestion: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_source_payload(self):
        if self.source == "manual" and not self.operations:
            raise ValueError("手动编辑至少需要一项操作")
        if self.source == "llm" and not self.suggestion:
            raise ValueError("LLM 编辑需要一条自然语言建议")
        if self.source == "llm" and self.operations:
            raise ValueError("LLM 编辑通过 suggestion 生成操作")
        return self


class HistoricalAssertionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=128)
    kind: Literal["gate", "exposure"]
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,95}$")
    status: Literal[
        "met",
        "not_met",
        "unknown",
        "not_applicable",
        "confirmed",
        "probable",
        "potential",
        "excluded",
    ]
    source_ref: str = Field(min_length=1, max_length=300)
    source_note: str = Field(min_length=1, max_length=1200)
    date: str
    known_at: str | None = None
    region: str | None = Field(default=None, max_length=12)
    location_ref: str | None = Field(default=None, max_length=180)
    subject_ref: str | None = Field(default=None, max_length=128)
    relationship_ref: str | None = Field(default=None, max_length=128)
    contact_event_id: str | None = Field(default=None, max_length=128)

    @field_validator("date", "known_at", mode="before")
    @classmethod
    def validate_day(cls, value):
        return None if value is None else strict_iso_date(value).isoformat()


class GraphEvolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["upgrade", "extend"]
    as_of: date | None = None

    @field_validator("as_of", mode="before")
    @classmethod
    def strict_as_of(cls, value):
        return None if value is None else strict_iso_date(value)

    @model_validator(mode="after")
    def validate_evolution(self):
        if (self.mode == "extend") != (self.as_of is not None):
            raise ValueError("只有续写需要指定新的截止日期")
        return self


class ChatSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: str = Field(min_length=1, max_length=36)
    release_id: str = Field(min_length=1, max_length=36)
    as_of: date
    role_scope: Literal["self"] = "self"

    @field_validator("as_of", mode="before")
    @classmethod
    def strict_as_of(cls, value):
        return strict_iso_date(value)


class ChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def trim_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


def strict_iso_date(value):
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("日期必须采用 YYYY-MM-DD 格式")
    return date.fromisoformat(value)
