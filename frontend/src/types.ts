export type Stage = "generate" | "skeleton" | "flesh" | "review" | "archive";

export type GenerationOptions = {
  region: string | null;
  ethnicity: string | null;
  population_frame?: string | null;
  birth_year: number | null;
  as_of: string;
  entry: "birth_cohort" | "living_at_as_of";
  household: "individual" | "family" | null;
  detail: "standard" | "rich";
  seed: number;
  life_status: "auto" | "living" | "deceased";
};

export type GenerationSpec = GenerationOptions & {
  region: string;
  ethnicity: string;
  birth_year: number;
  life_status: "living" | "deceased";
  household: NonNullable<GenerationOptions["household"]>;
  population?: { conditioning: string; right_censored: boolean };
  population_selection?: {
    frame_id: string;
    filters: { region: string | null; ethnicity: string | null };
    eligible_population: number;
    selection_probability: number;
  };
  themes: string[];
  history_selection?: Array<{ id: string; label: string; reason: string }>;
  sampling?: {
    birth_year: { source: "provided" | "sampled"; range?: [number, number] };
    household: { source: "provided" | "sampled" };
  };
};

export type Catalog = {
  regions: Array<{ id: string; label: string }>;
  ethnicities: Array<{ id: string; label: string; region_ids?: string[]; frame_id?: string }>;
  scenarios: Array<{ id: string; label: string; region_ids?: string[] }>;
  defaults?: GenerationOptions;
  population_evidence?: {
    frames: Array<{ id: string; label: string; reference_date: string; region_ids: string[]; has_ethnicity_distribution: boolean }>;
  };
};

export type Person = {
  person_id: string;
  name: string;
  region: string;
  ethnicity: string;
  birth_year: number;
  life_status: string;
  death_date?: string | null;
  as_of: string;
  summary: string;
  family?: Array<{ id: string; name: string; relation: string }>;
};

export type GraphNode = {
  event_id: string;
  title: string;
  category: string;
  start: string;
  end?: string | null;
  precision?: string;
  location?: string;
  participants?: string[];
  notes?: string;
  state_delta?: Record<string, string | number>;
  evidence?: Array<{ id: string; label: string; kind: string; url?: string }>;
  summary_node?: boolean;
  parent_event_id?: string;
  decomposition_state_delta?: Record<string, unknown>;
  known_at?: Record<string, string>;
  recorded_at?: string;
  historical_assessment?: {
    opportunity: { status: string };
    eligibility: { status: string };
    channels: Array<{ channel: string; status: string }>;
    policy_gates: Array<{ id: string; stage: string; status: string }>;
    pathway: string;
    record_check: string;
    outcomes: Array<{ id: string; label: string }>;
    negative_controls: Array<{ id: string; label: string }>;
  };
  dimension_updates?: Array<{
    dim_id: string;
    value: unknown;
    valid_time: DimensionTime | null;
    provenance: DimensionProvenance;
  }>;
};

export type DimensionTime = { start: string; end: string | null; precision: string };

type LifeStateValue = string | number | boolean | null;

export type LifeStateFact = {
  field: string;
  value: LifeStateValue;
  value_type: string;
  status: "known" | "incomplete" | "conflict";
  valid_from: string | null;
  candidate_values?: LifeStateValue[];
  provenance: Array<{
    event_id: string;
    role: string;
    valid_time: { start: string; end: string | null; precision: string | null };
    known_at: string | null;
    recorded_at: string | null;
    evidence_ids: string[];
    asserted_value?: LifeStateValue;
  }>;
};

export type LifeStateProjection = {
  schema: "life-state/1";
  graph_revision_id: string;
  as_of: string;
  coverage: "observed_fields_only";
  participants: Array<{
    person_id: string;
    status: "consistent" | "incomplete" | "conflict";
    facts: LifeStateFact[];
    missing_fields: string[];
    conflict_fields: string[];
  }>;
  issues: string[];
};

type DimensionProvenance = {
  kind: string;
  rule_id?: string | null;
  source_ids: string[];
};

export type SkeletonDimensionStatus =
  | "constraint"
  | "derived"
  | "event_anchored"
  | "unresolved"
  | "not_applicable";

export type SkeletonDimension = {
  dim_id: string;
  family_id: string;
  name: string;
  definition: string;
  value_type: string;
  time_nature: string;
  state_layer: string;
  source_suggestions: string;
  related_family_ids: string[];
  missing_policy: string;
  forbidden_inference: string;
  status: SkeletonDimensionStatus;
  reason?: string;
  value: unknown;
  valid_time: DimensionTime | null;
  event_ids: string[];
  provenance: DimensionProvenance;
};

export type SkeletonDimensionLedger = {
  schema_version: number;
  registry_version: string;
  registry_sha256: string;
  reference_date: string;
  summary: {
    family_count: number;
    dimension_count: number;
    configured: number;
    resolved: number;
    unknown: number;
    not_applicable: number;
  };
  families: Array<{
    family_id: string;
    name: string;
    dimensions: SkeletonDimension[];
  }>;
};

export type Graph = {
  graph_revision_id: string;
  parent_revision_id?: string | null;
  branch?: string;
  person: Person;
  nodes: GraphNode[];
  edges: Array<{ source: string; target: string; kind: string }>;
  diff?: {
    added: string[];
    modified: string[];
    removed: string[];
    recomputed: string[];
  };
  validation?: { valid: boolean; issues: string[] };
  dimensions?: SkeletonDimensionLedger;
  case_entities?: {
    relationships: Array<{
      relationship_id: string; kind: string; source_entity_id: string; target_entity_id: string;
      valid_time: { start: string | null; end: string | null };
    }>;
    entities: Array<{ entity_id: string; kind: string; name: string | null }>;
    participant_ledger: Record<string, Array<{
      person_id: string; role: string; delta: Record<string, unknown>;
    }>>;
  };
  simulation?: {
    engine: { name: string; version: string; mode: string; model?: string };
    exchange: { validator: string };
    parameter_pack: { version: string };
    world_version?: string;
    population_evidence?: {
      joint_observation: {
        count: number; denominator: number; reference_date: string;
        source_title: string; source_url: string; scope: string;
      } | null;
    };
    run_id: string;
    candidates: Array<{ code: string; status: string }>;
    constraints?: { status: string; validator: string | null; event_ids: string[] };
    replay?: { strategy: string; retained_event_ids: string[] };
  };
};

export type Job = {
  job_id: string;
  run_id?: string;
  status: string;
  progress: number;
  graph_revision_id?: string;
  command_graph_revision_id?: string;
  detail_revision_id?: string;
  release_id?: string;
  spec?: GenerationSpec;
  person?: Person;
  error?: string | null;
  model?: {
    provider: string;
    model: string;
    prompt_version: string;
    temperature: number;
  };
  warnings?: string[];
  actions?: string[];
};

export type RecentJob = {
  job_id: string;
  status: string;
  person?: Pick<Person, "name" | "region" | "birth_year"> | null;
  graph_revision_id?: string | null;
  created_at?: string | null;
};

export type GraphRevision = {
  graph_revision_id: string;
  parent_revision_id: string | null;
  branch: string;
  created_at: string;
};

export type JobEvent = {
  id: string;
  stage: string;
  tool: string;
  message: string;
  created_at: string;
};
export type GraphOperation =
  | { op: "delete"; event_id: string }
  | { op: "split"; event_id: string; children: Array<{
    event_id: string; title: string; start: string; end?: string;
    state_delta?: Record<string, string | number | boolean>;
    participant_deltas?: Array<{ person_id: string; role: string; delta: Record<string, string | number | boolean> }>;
  }> }
  | {
      op: "update";
      event_id: string;
      title: string;
      start: string;
      end: string | null;
      notes: string;
    }
  | {
      op: "add";
      event_id: string;
      title: string;
      start: string;
      end: string | null;
      notes: string;
      category: string;
      precision: string;
      location: string;
      depends_on: string[];
    };

export type GraphEdit =
  | { source: "manual"; operations: GraphOperation[] }
  | { source: "llm"; suggestion: string };

export type PersonaState = {
  schema_version: number;
  event_id: string;
  valid_from: string;
  scope: "synthesized" | "context" | "boundary";
  emotion?: { valence: number; arousal: number; stress: number } | null;
  appraisal?: { salience: number; perceived_control: number } | null;
  tendencies?: { routine: number; risk_aversion: number; social_support: number; learning: number } | null;
  memory?: { kind: string; clarity: number | null; known_from: string };
  voice?: { directness: number; reflection: number; detail: number } | null;
  interpretation?: {
    scene?: string; motivation?: string; emotion_note?: string;
    reflection?: string; recall_cue?: string; speaking_sample?: string;
  };
  provenance?: { basis: string[] | string; anchors: string[]; method: string; model?: string };
};

export type FleshChunk = {
  chunk_id: string;
  event_id: string;
  phase: string;
  title: string;
  narrative: string;
  memory?: string;
  evidence?: Array<{ id: string; label: string; kind: string; url?: string }>;
  status: string;
  profile?: PersonaState;
};

export type ReviewFinding = {
  id: string;
  severity: string;
  kind: string;
  event_id?: string;
  message: string;
  repair: string;
  status: string;
};
export type ReviewReport = {
  release_id: string;
  status: string;
  findings: ReviewFinding[];
};

export type ArchivePerson = Person & {
  release_id: string;
  graph_revision_id: string;
  detail_revision_id: string;
  snapshot_id: string;
  published_at: string;
};

export type ArchiveBundle = {
  release_id: string;
  snapshot_id: string;
  published_at: string;
  synthetic: boolean;
  graph: Graph;
  detail: {
    detail_revision_id: string;
    graph_revision_id: string;
    chunks: FleshChunk[];
  };
  review: ReviewReport;
};

export type ChatEvidence = {
  event_id: string;
  title: string;
  start: string;
  excerpt: string;
  scope?: "context" | "boundary" | "partial_record" | "direct_record";
};
export type ChatTurn = {
  turn_id: string;
  question: string;
  answer: string;
  query_type: string;
  evidence: ChatEvidence[];
  tools: Array<{ name: string; message?: string }>;
  model: string;
};

export type ChatSession = {
  session_id: string;
  person_id: string;
  release_id: string;
  graph_revision_id: string;
  detail_revision_id: string;
  snapshot_id: string;
  as_of: string;
  archive_as_of: string;
  role_scope?: string;
  turns: ChatTurn[];
};
