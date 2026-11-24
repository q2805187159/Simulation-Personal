import { useState } from "react";
import PersonaStateView from "./PersonaStateView";
import { EventDimensions } from "./SkeletonDimensions";
import SplitEventPanel from "./SplitEventPanel";
import type { Catalog, FleshChunk, GenerationOptions, GenerationSpec, Graph, GraphNode, GraphOperation, Job, JobEvent, ReviewFinding, Stage } from "./types";

function normalizeDate(value: string): string {
  if (/^\d{4}$/.test(value)) return value + "-01-01";
  if (/^\d{4}-\d{2}$/.test(value)) return value + "-01";
  return value;
}

const categoryLabels: Record<string, string> = {
  origin: "出生", education: "教育", work: "工作", household: "家庭",
  history: "历史背景", digital: "数字生活", health: "健康", life_end: "生命终点",
  user_added: "研究者添加",
};
const precisionLabels: Record<string, string> = { day: "日", month: "月", year: "年", range: "区间" };
const edgeLabels: Record<string, string> = { temporal: "时间先后", depends_on: "依赖", decomposes: "节点细化" };
const isHistorical = (node: GraphNode) => node.category === "history" || node.category === "历史暴露";
const eventDate = (node: GraphNode) => node.start.slice(0, node.precision === "year" ? 4 : node.precision === "month" ? 7 : 10);


export function Passport({ graph }: { graph: Graph }) {
  return (
    <section className="panel passport-panel">
      <div className="panel-label">
        PERSON PASSPORT <span>{graph.person.person_id}</span>
      </div>
      <div className="passport-name">
        <div className="avatar">{graph.person.name.slice(0, 1)}</div>
        <div>
          <h2>{graph.person.name}</h2>
          <p>{graph.person.region}</p>
          <p>
            {graph.person.ethnicity} · {graph.person.birth_year} 年生
          </p>
        </div>
      </div>
      <div className="passport-summary">{graph.person.summary}</div>
      <dl className="metric-list">
        <div>
          <dt>生命周期</dt>
          <dd>
            {graph.person.life_status === "living"
              ? `动态 · 截止 ${graph.person.as_of}`
              : "静态档案"}
          </dd>
        </div>
        <div>
          <dt>图版本</dt>
          <dd>{graph.graph_revision_id}</dd>
        </div>
        <div>
          <dt>可信状态</dt>
          <dd className={graph.validation?.valid ? "good" : "warning"}>
            {graph.validation?.valid ? "结构通过" : "待修复"}
          </dd>
        </div>
      </dl>
    </section>
  );
}

export function GenerationStartView({
  spec,
  catalog,
  updateSpec,
  onGenerate,
  loading,
  model,
  recentJobs,
  onResume,
}: {
  spec: GenerationOptions;
  catalog: Catalog;
  updateSpec: <K extends keyof GenerationOptions>(key: K, value: GenerationOptions[K]) => void;
  onGenerate: () => void;
  loading: boolean;
  model?: string;
  recentJobs: Array<{ job_id: string; status: string; person?: { name?: string; region?: string; birth_year?: number } | null }>;
  onResume: (job: { job_id: string; status: string; graph_revision_id?: string | null }) => void;
}) {
  const available = catalog.scenarios.length;
  const cards = [
    ["输入约束", "地区、民族、观察日期和人口入口会形成第一层边界。出生年份与家庭范围可以交给系统采样。"],
    ["自动匹配", `${available} 条历史规则由服务端按时空、年龄和生命状态筛选。生成后会保留每个候选的理由。`],
    ["下一断点", "系统只会先生成可审阅骨架。你确认事件、依赖和候选暴露后，才会进入细节和量化状态。"],
  ];
  return (
    <div className="generation-start">
      <section className="generation-intro">
        <div>
          <span className="eyebrow">START / GENERATION WORKBENCH</span>
          <h2>先定义生成边界，再生成骨架</h2>
          <p>这里还没有人物和人生记录。提交后，系统会建立任务并进入可编辑的 LifeGraph 骨架审阅。</p>
        </div>
        <div className="generation-intro-meta"><span>模型通道</span><strong>{model || "等待服务"}</strong><small>服务端受控调用</small></div>
      </section>
      <div className="generation-grid">
        <SpecPanel spec={spec} catalog={catalog} updateSpec={updateSpec} onGenerate={onGenerate} loading={loading} />
        <section className="panel generation-board" aria-labelledby="generation-board-title">
          <div className="section-heading"><div><span className="eyebrow">HOW IT MOVES</span><h2 id="generation-board-title">生成动线</h2></div><span className="count-badge">3 个断点</span></div>
          <ol className="generation-steps">
            {cards.map(([title, copy], index) => <li key={title}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{title}</strong><p>{copy}</p></div></li>)}
          </ol>
          <div className="generation-empty"><span className="empty-mark">○</span><div><strong>等待创建人物任务</strong><p>生成前不会展示人物、节点、血肉或虚假的进度。</p></div></div>
        </section>
      </div>
      <section className="panel generation-tasks" aria-labelledby="generation-tasks-title">
        <details>
          <summary><span><span className="eyebrow">RESUME</span><strong id="generation-tasks-title">恢复已有任务</strong></span><span className="count-badge">{recentJobs.length} 项</span></summary>
          {recentJobs.length ? <ul>{recentJobs.map((saved) => <li key={saved.job_id}><button type="button" onClick={() => onResume(saved)}><strong>{saved.person?.name ?? "未命名人物"}</strong><span>{saved.person?.region ?? "待解析"} · {saved.person?.birth_year ?? "自动年份"}</span><small>{saved.status}</small><b aria-hidden="true">→</b></button></li>)}</ul> : <p className="panel-empty">完成第一次生成后，可从这里恢复未完成任务。</p>}
        </details>
      </section>
    </div>
  );
}
export function SpecPanel({
  spec,
  resolved,
  catalog,
  updateSpec,
  onGenerate,
  loading,
}: {
  spec: GenerationOptions;
  resolved?: GenerationSpec;
  catalog: Catalog;
  updateSpec: <K extends keyof GenerationOptions>(
    key: K,
    value: GenerationOptions[K],
  ) => void;
  onGenerate: () => void;
  loading: boolean;
}) {
  const scenarios = new Map(catalog.scenarios.map(({ id, label }) => [id, label]));
  const matched = resolved?.history_selection ??
    resolved?.themes.map((id) => ({ id, label: scenarios.get(id) ?? id }));
  const birthRange = resolved?.sampling?.birth_year.range;
  const frames = catalog.population_evidence?.frames.filter((frame) => frame.has_ethnicity_distribution) ?? [];
  const selectedFrame = frames.find((frame) => frame.id === spec.population_frame);
  const regions = catalog.regions.filter((region) => !selectedFrame || selectedFrame.region_ids.includes(region.id));
  const ethnicities = catalog.ethnicities.filter((item) => (
    (!item.region_ids || !spec.region || item.region_ids.includes(spec.region))
      && (!selectedFrame || (!item.region_ids && selectedFrame.id === "mainland_2020") || item.frame_id === selectedFrame.id)
  ));

  return (
    <form
      className="panel spec-panel"
      aria-labelledby="generation-heading"
      onSubmit={(event) => {
        event.preventDefault();
        if (!loading) onGenerate();
      }}
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">01 / SPEC</span>
          <h2 id="generation-heading">生成规格</h2>
        </div>
      </div>
      {frames.length > 0 && (
        <label>
          地区与族别来源
          <select value={spec.population_frame ?? ""} onChange={(event) => updateSpec("population_frame", event.target.value || null)}>
            <option value="">手动指定</option>
            {frames.map((frame) => <option key={frame.id} value={frame.id}>{frame.label} · {frame.reference_date.slice(0, 4)}</option>)}
          </select>
        </label>
      )}
      <label>
        地区
        <select
          name="region"
          value={spec.region ?? ""}
          onChange={(e) => updateSpec("region", e.target.value || null)}
        >
          {selectedFrame && <option value="">在统计框内抽样</option>}
          {regions.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        民族
        <select
          name="ethnicity"
          value={spec.ethnicity ?? ""}
          onChange={(e) => updateSpec("ethnicity", e.target.value || null)}
        >
          {selectedFrame && <option value="">在统计框内抽样</option>}
          {ethnicities.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <div className="field-row">
        <label>
          家庭范围
          <select
            name="household"
            value={spec.household ?? ""}
            onChange={(event) =>
              updateSpec(
                "household",
                event.target.value === ""
                  ? null
                  : event.target.value as NonNullable<GenerationOptions["household"]>,
              )
            }
          >
            <option value="">自动生成</option>
            <option value="individual">仅本人</option>
            <option value="family">含家庭关系</option>
          </select>
        </label>
        <label>
          生存状态
          <select
            name="life_status"
            value={spec.life_status}
            onChange={(event) => {
              const lifeStatus = event.target.value as GenerationOptions["life_status"];
              updateSpec("life_status", lifeStatus);
              if (lifeStatus === "deceased" && spec.entry === "living_at_as_of") {
                updateSpec("entry", "birth_cohort");
              }
            }}
          >
            <option value="auto">自动判断</option>
            <option value="living">在世</option>
            <option value="deceased">已过世</option>
          </select>
        </label>
      </div>
      <div className="field-row">
        <div>
          <label htmlFor="birth-year">出生年份</label>
          <input
            id="birth-year"
            name="birth_year"
            type="number"
            min={1880}
            max={spec.as_of ? Math.min(2100, Number(spec.as_of.slice(0, 4))) : 2100}
            value={spec.birth_year ?? ""}
            aria-describedby="birth-year-help"
            onChange={(event) =>
              updateSpec("birth_year", event.target.value === "" ? null : event.target.valueAsNumber)
            }
          />
          <small className="spec-hint" id="birth-year-help">留空则按种子采样</small>
        </div>
        <label>
          种子
          <input
            name="seed"
            type="number"
            value={spec.seed}
            onChange={(e) => updateSpec("seed", Number(e.target.value))}
          />
        </label>
      </div>
      <div className="field-row">
        <label>
          入口
          <select
            name="entry"
            value={spec.entry}
            onChange={(e) =>
              updateSpec("entry", e.target.value as GenerationOptions["entry"])
            }
          >
            <option value="birth_cohort">出生队列</option>
            <option value="living_at_as_of">在世截面</option>
          </select>
        </label>
        <label>
          细节
          <select
            name="detail"
            value={spec.detail}
            onChange={(e) =>
              updateSpec(
                "detail",
                e.target.value as GenerationOptions["detail"],
              )
            }
          >
            <option value="standard">标准</option>
            <option value="rich">丰富</option>
          </select>
        </label>
      </div>
      <label>
        截止日期
        <input
          name="as_of"
          type="date"
          required
          value={spec.as_of}
          onChange={(e) => updateSpec("as_of", e.target.value)}
        />
      </label>
      <p className="spec-hint">历史事件由系统根据地区和人物生命时段自动匹配。</p>
      <button
        className="primary-button full"
        type="submit"
        disabled={loading}
      >
        <span>✦</span>
        {loading ? "编排中…" : "生成新骨架"}
      </button>
      {resolved && (
        <div className="spec-resolution">
          <h3>当前骨架的生成结果</h3>
          <dl className="metric-list">
            {resolved.population_selection && (
              <div>
                <dt>统计框抽样结果</dt>
                <dd>{regions.find((item) => item.id === resolved.region)?.label ?? resolved.region} · {catalog.ethnicities.find((item) => item.id === resolved.ethnicity)?.label ?? resolved.ethnicity}
                  <small className="spec-hint">筛选后分母 {resolved.population_selection.eligible_population.toLocaleString("zh-CN")} 人</small>
                </dd>
              </div>
            )}
            <div>
              <dt>出生年份</dt>
              <dd>
                {resolved.birth_year}
                {resolved.sampling?.birth_year.source === "sampled" && " · 自动采样"}
                {birthRange && <small className="spec-hint">采样范围 {birthRange.join("–")}</small>}
              </dd>
            </div>
            <div>
              <dt>家庭范围</dt>
              <dd>
                {resolved.household === "family" ? "家庭与共享事件" : "仅本人"}
                {resolved.sampling?.household.source === "sampled" && " · 自动采样"}
              </dd>
            </div>
          </dl>
          <details>
            <summary>历史背景候选（{matched?.length ?? 0} 项）</summary>
            {matched?.length ? (
              <ul>
                {matched.map((match) => (
                  <li key={match.id}>
                    <strong>{match.label}</strong>
                    {"reason" in match && typeof match.reason === "string" && (
                      <small className="spec-hint">{match.reason}</small>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="spec-hint">此档案未匹配到历史背景候选。</p>
            )}
          </details>
        </div>
      )}
    </form>
  );
}
export function Progress({ job }: { job: Job }) {
  const completed = job.status === "ready" ? 5 : job.status === "publish_pending" ? 4
    : job.status === "chain_review" ? 3 : job.status === "skeleton_review" ? 2 : 0;
  return (
    <section className="panel progress-panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">PIPELINE</span>
          <h2>生成进度</h2>
        </div>
        <span className="progress-number">{job.progress}%</span>
      </div>
      <progress className="workflow-progress" max={100} value={job.progress} aria-label="生成任务进度" />
      <ol className="stage-list">
        {["规格校验", "骨架生成", "血肉填充", "独立审查", "发布档案"].map(
          (label, index) => (
            <li key={label} className={index < completed ? "done" : ""} aria-current={index === completed ? "step" : undefined}>
              <span aria-hidden="true">{index < completed ? "✓" : index + 1}</span>
              {label}
            </li>
          ),
        )}
      </ol>
    </section>
  );
}
export function Timeline({
  graph,
  selectedId,
  setSelectedId,
}: {
  graph: Graph;
  selectedId: string;
  setSelectedId: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("all");
  const [newest, setNewest] = useState(false);
  const needle = query.trim().toLocaleLowerCase();
  const matches = graph.nodes.filter((node) => {
    if (scope === "life" && isHistorical(node)) return false;
    if (scope === "history" && !isHistorical(node)) return false;
    return !needle || [node.title, node.event_id, node.location, node.notes, node.start, categoryLabels[node.category] ?? node.category]
      .some((value) => value?.toLocaleLowerCase().includes(needle));
  });
  const nodes = newest ? [...matches].reverse() : matches;
  return (
    <section className="panel timeline-panel">
      <div className="timeline-header">
        <div>
          <span className="eyebrow">02 / LIFEGRAPH</span>
          <h2>
            人生记录{" "}
            <span className="subdued">· {nodes.length} / {graph.nodes.length} 个节点</span>
          </h2>
        </div>
        <a className="quiet-button" href="#event-detail">节点详情</a>
      </div>
      <div className="timeline-toolbar" role="search" aria-label="筛选人生记录">
        <label htmlFor="event-query" className="sr-only">搜索事件</label>
        <input id="event-query" type="search" placeholder="按名称、地点或年份搜索" value={query} onChange={(event) => setQuery(event.target.value)} />
        <label htmlFor="event-scope" className="sr-only">事件范围</label>
        <select id="event-scope" value={scope} onChange={(event) => setScope(event.target.value)}>
          <option value="all">全部事件</option>
          <option value="life">人生事件</option>
          <option value="history">历史背景</option>
        </select>
        <button className="quiet-button" type="button" onClick={() => setNewest(!newest)} aria-label={newest ? "按时间从早到晚排序" : "按时间从晚到早排序"}>{newest ? "最新优先 ↓" : "最早优先 ↑"}</button>
      </div>
      <div className="timeline-legend">
        <span>
          <i className="legend-dot event" />
          事件节点
        </span>
        <span>
          <i className="legend-dot exposure" />
          历史暴露
        </span>
        <span>
          <i className="legend-line" aria-hidden="true" />
          时间线
        </span>
      </div>
      {nodes.length === 0 && (
        <div className="panel-empty">
          <p>{graph.nodes.length ? "没有符合条件的事件。" : "生成骨架后可浏览事件。"}</p>
          {graph.nodes.length > 0 && <button type="button" className="quiet-button" onClick={() => { setQuery(""); setScope("all"); }}>清除筛选</button>}
        </div>
      )}
      <ol className="timeline">
        {nodes.map((node, index) => (
          <li key={node.event_id}>
          <button
            type="button"
            className={
              selectedId === node.event_id
                ? "timeline-node selected"
                : "timeline-node"
            }
            onClick={() => setSelectedId(node.event_id)}
            aria-pressed={selectedId === node.event_id}
          >
            <time className="node-date" dateTime={eventDate(node)}>{eventDate(node)}</time>
            <span className="node-stem" aria-hidden="true">
              <i
                className={
                  isHistorical(node)
                    ? "node-dot exposure"
                    : "node-dot"
                }
              />
              <i className="stem-line" />
            </span>
            <span className="node-body">
              <strong>{node.title}</strong>
              <span className="node-meta">
                {categoryLabels[node.category] ?? node.category} · {node.location ?? "位置待补"} ·{" "}
                {precisionLabels[node.precision ?? ""] ?? "区间"}
              </span>
              <span className="node-note">{node.notes}</span>
              {isHistorical(node) && <span className="node-tags"><em>背景候选</em></span>}
            </span>
            <span className="node-index" aria-hidden="true">
              {String(index + 1).padStart(2, "0")}
            </span>
          </button>
          </li>
        ))}
      </ol>
    </section>
  );
}
export function NodeDetail({
  graph,
  selectedNode,
  chunks,
  revisions,
  onSelectNode,
  onEdit,
  onSave,
  onCancel,
  onDelete,
  onAdd,
  onSplit,
  onHistoryAssertion,
  onRevision,
  canEdit,
  loading,
  editing,
  draft,
  setDraft,
}: {
  graph: Graph;
  selectedNode?: GraphNode;
  chunks: FleshChunk[];
  revisions: Array<{
    graph_revision_id: string;
    parent_revision_id: string | null;
    branch: string;
    created_at: string;
  }>;
  onSelectNode: (id: string) => void;
  onEdit: () => void;
  onSave: () => void;
  onCancel: () => void;
  onDelete: () => void;
  onAdd: () => void;
  onSplit?: (children: Extract<GraphOperation, { op: "split" }>["children"]) => Promise<boolean>;
  onHistoryAssertion?: (body: Record<string, unknown>) => Promise<boolean>;
  onRevision: (id: string) => void;
  canEdit: boolean;
  loading: boolean;
  editing: false | "edit" | "add";
  draft: { title: string; start: string; end: string; notes: string };
  setDraft: (value: {
    title: string;
    start: string;
    end: string;
    notes: string;
  }) => void;
}) {
  const [removeTarget, setRemoveTarget] = useState<string>();
  const confirmingDelete = editing === "edit" && removeTarget === selectedNode?.event_id;
  const parents = graph.edges.filter(
    (edge) => edge.target === selectedNode?.event_id,
  );
  const children = graph.edges.filter(
    (edge) => edge.source === selectedNode?.event_id,
  );
  const chunk = chunks.find((item) => item.event_id === selectedNode?.event_id);
  const diff = graph.diff;
  return (
    <section className="panel node-detail" id="event-detail" tabIndex={-1}>
      <div className="section-heading">
        <div>
          <span className="eyebrow">SELECTED EVENT</span>
          <h2>{editing === "add" ? "添加事件节点" : selectedNode?.title ?? "选择一个事件"}</h2>
        </div>
        <div className="detail-actions">
          {selectedNode && (
            <button
              className="quiet-button"
              onClick={() => { setRemoveTarget(undefined); onEdit(); }}
              disabled={!canEdit || loading}
            >
              编辑节点
            </button>
          )}
          <button
            className="quiet-button"
            onClick={onAdd}
            disabled={!canEdit || loading}
          >
            添加事件节点
          </button>
          <span className="event-id">{selectedNode?.event_id}</span>
        </div>
      </div>
      {(selectedNode || editing === "add") && (
        <div className="detail-grid">
          {editing ? (
            <form className="detail-editor detail-wide" aria-label={editing === "add" ? "添加事件节点" : "编辑事件节点"} onSubmit={(event) => { event.preventDefault(); onSave(); }}>
              <label>
                节点标题
                <input
                  name="title"
                  required
                  maxLength={180}
                  value={draft.title}
                  onChange={(event) =>
                    setDraft({ ...draft, title: event.target.value })
                  }
                />
              </label>
              <div className="field-row">
                <label>
                  开始时间
                  <input
                    type="date"
                    value={normalizeDate(draft.start)}
                    name="start"
                    required
                    min={`${graph.person.birth_year}-01-01`}
                    max={graph.person.death_date ?? graph.person.as_of}
                    disabled={(editing === "edit" && selectedNode?.summary_node) || loading}
                    onChange={(event) =>
                      setDraft({ ...draft, start: event.target.value })
                    }
                  />
                </label>
                <label>
                  结束时间
                  <input
                    type="date"
                    value={draft.end ? normalizeDate(draft.end) : ""}
                    name="end"
                    min={normalizeDate(draft.start)}
                    max={graph.person.death_date ?? graph.person.as_of}
                    disabled={(editing === "edit" && selectedNode?.summary_node) || loading}
                    onChange={(event) =>
                      setDraft({ ...draft, end: event.target.value })
                    }
                  />
                </label>
              </div>
              <label>
                节点备注
                <textarea
                  name="notes"
                  maxLength={1200}
                  rows={3}
                  value={draft.notes}
                  onChange={(event) =>
                    setDraft({ ...draft, notes: event.target.value })
                  }
                />
              </label>
              <div className="editor-actions">
                <button
                  className="primary-button"
                  type="submit"
                  disabled={loading || !draft.title.trim() || !draft.start}
                >
                  保存子版本
                </button>
                <button type="button" className="quiet-button" onClick={() => { setRemoveTarget(undefined); onCancel(); }}>
                  取消
                </button>
                {editing === "edit" && selectedNode && !["birth", "death"].includes(selectedNode.event_id) && <button
                  className="delete-button"
                  type="button"
                  onClick={() => setRemoveTarget(selectedNode.event_id)}
                  disabled={loading}
                >
                  删除节点并重算
                </button>}
              </div>
              {confirmingDelete && <div className="remove-confirm">
                <p>删除此节点将创建一个子版本，原版本仍可查看。</p>
                <button type="button" className="delete-button" disabled={loading} onClick={onDelete}>确认删除</button>
                <button type="button" className="quiet-button" onClick={() => setRemoveTarget(undefined)}>保留节点</button>
              </div>}
            </form>
          ) : selectedNode && (
            <>
              <div>
                <span className="detail-label">时间窗口</span>
                <strong>
                  {selectedNode.start}{" "}
                  {selectedNode.end ? `→ ${selectedNode.end}` : ""}
                </strong>
              </div>
              <div>
                <span className="detail-label">地点</span>
                <strong>{selectedNode.location ?? "待匹配"}</strong>
              </div>
              {(selectedNode.recorded_at || selectedNode.known_at) && (
                <div>
                  <span className="detail-label">获知与登记</span>
                  <strong>
                    获知 {selectedNode.known_at?.[graph.person.person_id] ?? "未明确登记"}
                    {" · "}登记 {selectedNode.recorded_at?.slice(0, 10) ?? "未知"}
                  </strong>
                </div>
              )}
              <div>
                <span className="detail-label">参与者</span>
                <strong>
                  {selectedNode.participants?.map((id) => id === graph.person.person_id ? "本人" : graph.person.family?.find((member) => member.id === id)?.name ?? id).join(" · ") ?? "本人"}
                </strong>
              </div>
              <div>
                <span className="detail-label">证据锚点</span>
                <ul className="event-sources">
                  {selectedNode.evidence?.length ? selectedNode.evidence.map(({ id, label, url }) => (
                    <li key={id}>{url && /^https?:\/\//.test(url) ? <a href={url} target="_blank" rel="noreferrer">{label}</a> : label}</li>
                  )) : <li>待补证据</li>}
                </ul>
              </div>
              <div className="detail-wide">
                <span className="detail-label">节点备注</span>
                <p>{selectedNode.notes}</p>
              </div>
            </>
          )}
          {selectedNode?.historical_assessment && <HistoricalAssessment node={selectedNode} onSubmit={onHistoryAssertion} />}
          {selectedNode && <EventDimensions node={selectedNode} graph={graph} />}
          {selectedNode && canEdit && !editing && !selectedNode.summary_node && onSplit
            && selectedNode.event_id !== "birth" && selectedNode.event_id !== "death"
            && !graph.simulation?.candidates.some((candidate) => candidate.code === selectedNode.event_id) && (
            <details className="detail-wide flesh-detail">
              <summary>拆分事件节点</summary>
              <SplitEventPanel key={selectedNode.event_id} graph={graph} node={selectedNode} disabled={loading} onSplit={onSplit} />
            </details>
          )}
          <div className="dependency-block detail-wide">
            <span className="detail-label">有向依赖关系</span>
            <div className="dependency-links">
              {parents.length > 0 ? (
                parents.map((edge) => (
                  <button
                    key={`${edge.kind}:${edge.source}`}
                    onClick={() => onSelectNode(edge.source)}
                  >
                    ← {graph.nodes.find((node) => node.event_id === edge.source)?.title ?? edge.source} · {edgeLabels[edge.kind] ?? edge.kind}
                  </button>
                ))
              ) : (
                <span>无上游节点</span>
              )}
              {children.length > 0 ? (
                children.map((edge) => (
                  <button
                    key={`${edge.kind}:${edge.target}`}
                    onClick={() => onSelectNode(edge.target)}
                  >
                    {graph.nodes.find((node) => node.event_id === edge.target)?.title ?? edge.target} · {edgeLabels[edge.kind] ?? edge.kind} →
                  </button>
                ))
              ) : (
                <span>无下游节点</span>
              )}
            </div>
          </div>
          {chunk && (
            <details className="detail-wide flesh-detail">
              <summary>血肉锚定块 · 叙事、记忆与状态</summary>
              <PersonaStateView chunk={chunk} />
            </details>
          )}
          {diff?.added.length ||
          diff?.modified.length ||
          diff?.removed.length ||
          diff?.recomputed.length ? (
            <div className="detail-wide diff-detail">
              <span className="detail-label">本版本差异</span>
              <p>
                新增 {diff?.added.join(", ") || "无"} · 修改{" "}
                {diff?.modified.join(", ") || "无"} · 删除{" "}
                {diff?.removed.join(", ") || "无"} · 重算{" "}
                {diff?.recomputed.join(", ") || "无"}
              </p>
            </div>
          ) : null}
          {revisions.length > 0 && (
            <div className="detail-wide revision-detail">
              <span className="detail-label">版本分支</span>
              {revisions.map((revision) => (
                <button
                  key={revision.graph_revision_id}
                  className={
                    revision.graph_revision_id === graph.graph_revision_id
                      ? "revision-chip current"
                      : "revision-chip"
                  }
                  type="button"
                  disabled={loading}
                  onClick={() => onRevision(revision.graph_revision_id)}
                  aria-current={
                    revision.graph_revision_id === graph.graph_revision_id
                      ? "page"
                      : undefined
                  }
                >
                  {revision.graph_revision_id} · {revision.branch}
                  {revision.parent_revision_id
                    ? ` ← ${revision.parent_revision_id}`
                    : ""}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

const historyStatuses: Record<string, string> = {
  potential: "待核对", unknown: "未知", eligible: "符合资格", ineligible: "不符合资格",
  not_applicable: "不适用", not_met: "未满足", met: "满足", confirmed: "已证实",
  excluded: "已排除", conflict: "存在冲突", outside_scope: "超出适用范围",
};

function HistoricalAssessment({ node, onSubmit }: { node: GraphNode; onSubmit?: (body: Record<string, unknown>) => Promise<boolean> }) {
  const assessment = node.historical_assessment;
  if (!assessment) return null;
  const status = (value: string) => historyStatuses[value] ?? value;
  return (
    <details className="detail-wide flesh-detail">
      <summary>历史机会、制度门槛与个人暴露</summary>
      <p>机会 {status(assessment.opportunity.status)} · 资格 {status(assessment.eligibility.status)}</p>
      <p>{assessment.pathway}</p>
      <dl className="dimension-values">
        {assessment.channels.map((channel) => <div key={channel.channel}>
          <dt>{channel.channel}</dt><dd>{status(channel.status)}</dd>
        </div>)}
        {assessment.policy_gates.map((gate) => <div key={gate.id}>
          <dt>{gate.id}</dt><dd>{status(gate.status)}</dd>
        </div>)}
      </dl>
      <p>{assessment.record_check}</p>
      <h4>候选路径</h4>
      <ul>{assessment.outcomes.map((item) => <li key={item.id}>{item.label}</li>)}</ul>
      <h4>对照路径</h4>
      <ul>{assessment.negative_controls.map((item) => <li key={item.id}>{item.label}</li>)}</ul>
      {onSubmit && (
        <details>
          <summary>补录研究者证据</summary>
          <form onSubmit={async (event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            await onSubmit({
              kind: String(form.get("kind")),
              key: String(form.get("key")),
              status: String(form.get("status")),
              source_ref: String(form.get("source_ref")),
              source_note: String(form.get("source_note")),
              date: String(form.get("date")),
              region: String(form.get("region") || ""),
              location_ref: String(form.get("location_ref") || ""),
            });
          }}>
            <div className="field-row">
              <label>类型<select name="kind"><option value="exposure">个人暴露</option><option value="gate">资格门槛</option></select></label>
              <label>字段<input name="key" required placeholder="direct / eligibility_gate" /></label>
            </div>
            <div className="field-row">
              <label>状态<select name="status"><option value="confirmed">已证实</option><option value="probable">较可能</option><option value="unknown">未知</option><option value="excluded">排除</option></select></label>
              <label>发生日期<input type="date" name="date" required /></label>
            </div>
            <label>来源编号<input name="source_ref" required maxLength={300} /></label>
            <label>来源说明<textarea name="source_note" required maxLength={1200} rows={2} /></label>
            <div className="field-row">
              <label>发生地区<input name="region" maxLength={12} /></label>
              <label>位置记录<input name="location_ref" maxLength={180} /></label>
            </div>
            <button type="submit" className="secondary-button">保存为新骨架版本</button>
          </form>
        </details>
      )}
    </details>
  );
}
export function LlmPanel({
  events,
  model,
  suggestion,
  setSuggestion,
  onSuggest,
  loading,
  canSuggest,
}: {
  events: JobEvent[];
  model?: string;
  suggestion: string;
  setSuggestion: (value: string) => void;
  onSuggest: () => void;
  loading: boolean;
  canSuggest: boolean;
}) {
  return (
    <section className="panel llm-panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">03 / ORCHESTRATOR</span>
          <h2>LLM 工作台</h2>
        </div>
      </div>
      <div className="model-row">
        <span className="model-icon">✦</span>
        <div>
          <strong>{model || "等待连接"}</strong>
          <small>结构化工具调用 · 输出受 DSL 约束</small>
        </div>
      </div>
      <details className="tool-activity" open={events.length > 0 && events.length <= 4}>
        <summary>执行记录（{events.length}）</summary>
      <ol className="tool-log">
        {[...events].reverse().map((event) => (
          <li className="tool-entry" key={event.id}>
            <span className="tool-time">{event.created_at.slice(11, 19)}</span>
            <span className="tool-name">{event.tool}</span>
            <p>{event.message}</p>
          </li>
        ))}
      </ol>
      </details>
      <form aria-label="提交骨架修订建议" onSubmit={(event) => { event.preventDefault(); onSuggest(); }}>
      <label className="suggestion-label" htmlFor="suggestion">
        给骨架编辑器的建议
      </label>
      <textarea
        id="suggestion"
        name="suggestion"
        rows={3}
        maxLength={2000}
        disabled={!canSuggest || loading}
        value={suggestion}
        onChange={(e) => setSuggestion(e.target.value)}
        placeholder="例如：把地震节点改为候选暴露，并补充家庭照护影响…"
      />
      <button
        className="secondary-button full"
        type="submit"
          disabled={!canSuggest || !suggestion.trim() || loading}
      >
        <span>↳</span>提交建议并创建子版本
      </button>
      </form>
    </section>
  );
}
export function ActionPanel({
  job,
  graph,
  loading,
  onFlesh,
  onReview,
  onPublish,
  findings,
  setStage,
}: {
  job: Job;
  graph: Graph;
  loading: boolean;
  onFlesh: () => void;
  onReview: () => void;
  onPublish: () => void;
  findings: ReviewFinding[];
  setStage: (stage: Stage) => void;
}) {
  const canFlesh =
    job.actions?.includes("continue_flesh") &&
    graph.graph_revision_id === job.graph_revision_id &&
    graph.validation?.valid;
  const isActiveRevision = graph.graph_revision_id === job.graph_revision_id;
  const canReview = isActiveRevision && job.actions?.includes("review");
  const canPublish = isActiveRevision && job.actions?.includes("publish");
  return (
    <section className="panel action-panel" id="workflow-actions" tabIndex={-1}>
      <div className="section-heading">
        <div>
          <span className="eyebrow">NEXT ACTION</span>
          <h2>继续工作流</h2>
        </div>
      </div>
      <p className="action-copy">
        {!isActiveRevision && job.graph_revision_id ? "浏览历史版本时，可以查看或另建分支。主流程以当前版本为准。"
          : canFlesh ? "确认时间、依赖和证据后生成叙事与人物状态。"
          : canReview ? "核查状态量表、事件叙事和引用的一致性。"
          : canPublish ? "审查已通过，可发布供角色问答使用的固定版本。"
          : job.status === "ready" ? "当前版本已发布。可以进入档案问答。"
          : "生成骨架后，此处会显示下一项可执行操作。"}
      </p>
      <div className="action-stack">
        <button
          className={canFlesh ? "primary-button full" : "quiet-button full"}
          onClick={onFlesh}
          disabled={loading || !canFlesh}
        >
          <span>→</span>确认骨架并填充血肉
        </button>
        <button
          className={canReview ? "primary-button full" : "quiet-button full"}
          onClick={onReview}
          disabled={loading || !canReview}
        >
          <span>◈</span>运行全链路审查
        </button>
        <button
          className={canPublish ? "primary-button full" : "quiet-button full"}
          onClick={onPublish}
          disabled={loading || !canPublish}
        >
          <span>↗</span>发布到档案库
        </button>
      </div>
      {job.status === "ready" && (
        <button className="primary-button full" type="button" onClick={() => setStage("archive")}>进入档案问答</button>
      )}
      {findings.length > 0 && (
        <div className="finding-summary">
          <strong>{findings.length} 条审查结果</strong>
          <span>{findings[0].message}</span>
          <button onClick={() => setStage("review")}>查看详情 →</button>
        </div>
      )}
    </section>
  );
}
export function Guardrails() {
  return (
    <section className="panel review-panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">GUARDRAILS</span>
          <h2>当前约束</h2>
        </div>
      </div>
      <ul className="guardrail-list">
        <li>
          <span>✓</span>事实与推测分层
        </li>
        <li>
          <span>✓</span>时间窗口可追溯
        </li>
        <li>
          <span>✓</span>聊天不写回人生图
        </li>
        <li>
          <span>✓</span>回答固定 release 快照
        </li>
      </ul>
    </section>
  );
}
export function FleshStrip({ chunks, onSelectNode }: { chunks: FleshChunk[]; onSelectNode: (id: string) => void }) {
  return (
    <section className="full-width-panel flesh-strip">
      <div className="section-heading">
        <div>
          <span className="eyebrow">04 / FLESH REVISION</span>
          <h2>事件叙事与状态</h2>
        </div>
        <span className="count-badge">{chunks.length} 个事件</span>
      </div>
      {!chunks.length && <p className="panel-empty">当前骨架版本暂无可查看的细节。</p>}
      <div className="flesh-grid">
        {chunks.map((chunk) => (
          <details key={chunk.chunk_id} className="flesh-card">
            <summary><strong>{chunk.title}</strong><span>{categoryLabels[chunk.phase] ?? chunk.phase}</span></summary>
            <PersonaStateView chunk={chunk} />
            <a href="#event-detail" className="quiet-button" onClick={() => onSelectNode(chunk.event_id)}>查看骨架节点</a>
          </details>
        ))}
      </div>
    </section>
  );
}
export function ReviewStrip({
  findings,
  status,
}: {
  findings: ReviewFinding[];
  status?: string;
}) {
  const passed = status === "passed" || status === "repaired";
  const statusLabels: Record<string, string> = {
    passed: "审查通过",
    repaired: "修复后通过",
    blocked: "有待处理项",
  };
  const statusLabel = statusLabels[status ?? ""] ?? "等待审查";
  return (
    <section className="full-width-panel review-strip">
      <div className="section-heading">
        <div>
          <span className="eyebrow">05 / INDEPENDENT REVIEW</span>
          <h2>全链路审查</h2>
        </div>
        <span
          className={
            status === "blocked" ? "review-status warning" : "review-status"
          }
        >
          {statusLabel}
        </span>
      </div>
      {findings.length === 0 ? (
        <div className="empty-review">
          <span>◌</span>
          <div>
            <strong>{passed ? "未发现需要处理的问题" : "当前版本暂无审查结果"}</strong>
          </div>
        </div>
      ) : (
        <div className="findings-list">
          {findings.map((finding) => (
            <div className="finding-row" key={finding.id}>
              <span className="severity">{finding.status === "repaired" ? "已修复" : finding.severity}</span>
              <div>
                <strong>{finding.message}</strong>
                <p>{finding.repair}</p>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
