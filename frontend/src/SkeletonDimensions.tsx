import { useId, useState } from "react";
import { graphExportUrl } from "./api";
import type {
  DimensionTime,
  Graph,
  GraphNode,
  SkeletonDimension,
  SkeletonDimensionStatus,
} from "./types";
import "./skeleton-dimensions.css";

const statuses: Record<SkeletonDimensionStatus, string> = {
  constraint: "生成条件",
  derived: "规则派生",
  event_anchored: "事件锚定",
  unresolved: "待补证",
  not_applicable: "不适用",
};

const valueLabels: Record<string, string> = {
  min: "最小值", max: "最大值", unit: "单位", scope: "范围",
  start: "开始", end: "结束", precision: "时间粒度", date: "日期",
  id: "编号", event_id: "事件", value: "值", valid_time: "有效时间",
  roles: "参与者", relation: "关系", year: "年份", label: "名称",
  status: "状态", selected: "选择", plans: "安排", location: "地点",
};

const precisionLengths: Record<string, number> = { year: 4, month: 7 };
const candidateLabels: Record<string, string> = {
  "job-exit": "离开原有岗位", relocation: "迁居",
  "health-transition": "健康需求变化", "shared-care": "共享照护",
  partnership: "伴侣关系", death: "生命终止",
};

function timeLabel({ start, end, precision }: DimensionTime): string {
  const length = precisionLengths[precision] ?? 10;
  const first = start.slice(0, length);
  const last = end?.slice(0, length);
  return last && last !== first ? `${first} – ${last}` : first;
}

function valueLabel(value: unknown): string {
  if (value == null) return "未记录";
  if (typeof value === "boolean") return value ? "是" : "否";
  return typeof value === "number" || typeof value === "string" ? String(value) : "";
}

function DimensionValue({ value, eventTitles }: {
  value: unknown;
  eventTitles: ReadonlyMap<string, string>;
}) {
  if (Array.isArray(value)) {
    if (!value.length) return <span>无条目</span>;
    return <ul className="dimension-values">{value.map((item, index) =>
      <li key={index}><DimensionValue value={item} eventTitles={eventTitles} /></li>
    )}</ul>;
  }
  if (typeof value === "object" && value !== null) {
    const fields = Object.entries(value);
    if (!fields.length) return <span>未记录</span>;
    return <dl className="dimension-values">{fields.map(([key, item]) => (
      <div key={key}>
        <dt>{valueLabels[key] ?? key}</dt>
        <dd>
          <DimensionValue
            value={key === "event_id" && typeof item === "string" ? eventTitles.get(item) ?? item : item}
            eventTitles={eventTitles}
          />
        </dd>
      </div>
    ))}</dl>;
  }
  return <span>{valueLabel(value)}</span>;
}

function DimensionItem({ entry, eventTitles, onSelectNode }: {
  entry: SkeletonDimension;
  eventTitles: ReadonlyMap<string, string>;
  onSelectNode: (id: string) => void;
}) {
  const hasValue = entry.status !== "unresolved" && entry.status !== "not_applicable" && entry.value != null;
  return (
    <li className="dimension-entry">
      <div className="dimension-entry-title">
        <strong>{entry.name}</strong>
        <code>{entry.dim_id}</code>
        <span className="dimension-status" data-status={entry.status}>{statuses[entry.status]}</span>
      </div>
      {hasValue && <DimensionValue value={entry.value} eventTitles={eventTitles} />}
      {entry.reason && <p>{entry.reason}</p>}
      {entry.valid_time && <p>有效时间 {timeLabel(entry.valid_time)}</p>}
      {!!entry.event_ids.length && (
        <ul className="dimension-anchors" aria-label="关联事件">
          {entry.event_ids.map((id) => (
            <li key={id}>
              {eventTitles.has(id) ? (
                <a href="#event-detail" onClick={() => onSelectNode(id)}>{eventTitles.get(id)}</a>
              ) : <code>{id}</code>}
            </li>
          ))}
        </ul>
      )}
      <details className="dimension-definition">
        <summary>定义与依据</summary>
        <p>{entry.definition}</p>
        <dl>
          <div><dt>字段语义</dt><dd>{entry.state_layer} · {entry.value_type} · {entry.time_nature}</dd></div>
          {entry.provenance.rule_id && <div><dt>规则</dt><dd><code>{entry.provenance.rule_id}</code></dd></div>}
          {!!entry.provenance.source_ids.length && <div><dt>依据</dt><dd>{entry.provenance.source_ids.join("、")}</dd></div>}
          <div><dt>设计建议来源</dt><dd>{entry.source_suggestions}</dd></div>
          <div><dt>缺失处理</dt><dd>{entry.missing_policy}</dd></div>
          {entry.forbidden_inference && <div><dt>推断边界</dt><dd>{entry.forbidden_inference}</dd></div>}
        </dl>
      </details>
    </li>
  );
}

export function EventDimensions({ node, graph }: { node: GraphNode; graph: Graph }) {
  if (!node.dimension_updates?.length || !graph.dimensions) return null;
  const dimensions = new Map(graph.dimensions.families.flatMap((family) =>
    family.dimensions.map((entry) => [entry.dim_id, entry] as const)
  ));
  const eventTitles = new Map(graph.nodes.map((event) => [event.event_id, event.title]));
  return (
    <details className="dimension-event-updates detail-wide">
      <summary>维度变化（{node.dimension_updates.length} 项）</summary>
      <dl className="dimension-values">
        {node.dimension_updates.map((entry) => (
          <div key={entry.dim_id}>
            <dt>{dimensions.get(entry.dim_id)?.name ?? entry.dim_id} <code>{entry.dim_id}</code></dt>
            <dd><DimensionValue value={entry.value} eventTitles={eventTitles} /></dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export default function SkeletonDimensions({ graph, onSelectNode }: {
  graph: Graph;
  onSelectNode: (id: string) => void;
}) {
  const headingId = useId();
  const filterId = useId();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const ledger = graph.dimensions;
  const run = graph.simulation;
  const filename = `skeleton-${graph.graph_revision_id}.json`;
  const term = query.trim().toLocaleLowerCase();
  const families = ledger?.families.flatMap((family) => {
    const dimensions = family.dimensions.filter((entry) =>
      (!status || status === entry.status) &&
      (!term || [family.name, entry.dim_id, entry.name, entry.definition].some((text) =>
        text.toLocaleLowerCase().includes(term)
      ))
    );
    return dimensions.length ? [{ ...family, dimensions }] : [];
  }) ?? [];
  const eventTitles = new Map(graph.nodes.map((node) => [node.event_id, node.title]));
  const resultCount = families.reduce((count, family) => count + family.dimensions.length, 0);

  return (
    <section className="panel skeleton-dimensions" aria-labelledby={headingId}>
      <header className="skeleton-dimensions-heading">
        <div>
          <h2 id={headingId}>骨架维度账本</h2>
          {ledger && <p>字典 {ledger.registry_version} · 参照日 <time dateTime={ledger.reference_date}>{ledger.reference_date}</time></p>}
        </div>
        <a className="secondary-button dimension-export" href={graphExportUrl(graph.graph_revision_id)} download={filename}>
          导出骨架 JSON
        </a>
      </header>
      {run && (
        <details className="dimension-browser">
          <summary>
            仿真运行 · {run.engine.name === "openmpp" ? "OpenM++" : "Python 参考内核"} {run.engine.version}
          </summary>
          <dl className="dimension-values">
            <div><dt>参数包</dt><dd>{run.parameter_pack.version}</dd></div>
            {run.world_version && <div><dt>历史规则</dt><dd>{run.world_version}</dd></div>}
            <div><dt>规格校验</dt><dd>{run.exchange.validator}</dd></div>
            {run.constraints && (
              <div><dt>资源硬约束</dt><dd>{run.constraints.validator
                ? `通过 · ${run.constraints.validator}` : "本版本没有资源预约需要校验"}</dd></div>
            )}
            <div><dt>运行摘要</dt><dd><code title={run.run_id}>{run.run_id.slice(0, 16)}</code></dd></div>
            {run.replay && (
              <div><dt>子版本复算</dt><dd>保留 {run.replay.retained_event_ids.length} 条未变化记录</dd></div>
            )}
            {run.population_evidence?.joint_observation && (() => {
              const evidence = run.population_evidence.joint_observation;
              return <div><dt>地区族别统计</dt><dd>
                <a href={evidence.source_url} target="_blank" rel="noreferrer">{evidence.source_title}</a>
                {" · "}{evidence.reference_date} · {evidence.count.toLocaleString("zh-CN")} / {evidence.denominator.toLocaleString("zh-CN")} 人
                <p>{evidence.scope}</p>
              </dd></div>;
            })()}
          </dl>
          <ul className="dimension-anchors" aria-label="模型候选结果">
            {run.candidates.map((candidate) => (
              <li key={candidate.code}>
                {candidateLabels[candidate.code] ?? candidate.code} · {candidate.status === "selected" ? "已发生" : "未入选"}
              </li>
            ))}
          </ul>
        </details>
      )}
      {graph.case_entities && (
        <details className="dimension-browser">
          <summary>人物、家庭与关联账本 · {graph.case_entities.entities.length} 个实体 · {graph.case_entities.relationships.length} 条当前关系</summary>
          <p>人物 ID、家庭户、地区与机构实体分开管理。未识别的机构身份和未证实的同住状态保持未知。</p>
          <ul className="dimension-anchors">
            {graph.case_entities.entities.map((entity) => (
              <li key={entity.entity_id}>
                {entity.kind} · {entity.name ?? "身份未确认"} · <code>{entity.entity_id}</code>
              </li>
            ))}
          </ul>
          <dl className="dimension-values">
            {graph.case_entities.relationships.map((relation) => (
              <div key={relation.relationship_id}>
                <dt>{relation.kind} · {relation.valid_time.start ?? "时间未知"}</dt>
                <dd><code>{relation.source_entity_id}</code> → <code>{relation.target_entity_id}</code></dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {ledger ? (
        <>
          <dl className="dimension-counts">
            <div><dt>字典覆盖</dt><dd>{ledger.summary.family_count} 族 / {ledger.summary.dimension_count} 项</dd></div>
            <div><dt>生成条件</dt><dd>{ledger.summary.configured} 项</dd></div>
            <div><dt>已派生或锚定</dt><dd>{ledger.summary.resolved} 项</dd></div>
            <div><dt>待补证</dt><dd>{ledger.summary.unknown} 项</dd></div>
            <div><dt>不适用</dt><dd>{ledger.summary.not_applicable} 项</dd></div>
          </dl>
          <details className="dimension-browser">
            <summary>浏览与筛选维度</summary>
            <div className="dimension-filters" role="search" aria-label="筛选骨架维度">
              <div>
                <label htmlFor={filterId}>搜索维度</label>
                <input id={filterId} type="search" placeholder="输入维度名称、族名或编号" value={query} onChange={(event) => setQuery(event.target.value)} />
              </div>
              <div>
                <label htmlFor={`${filterId}-status`}>取值状态</label>
                <select id={`${filterId}-status`} value={status} onChange={(event) => setStatus(event.target.value)}>
                  <option value="">全部状态</option>
                  {Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </select>
              </div>
            </div>
            <p className="dimension-results" role="status">{families.length ? `${families.length} 个维度族 · ${resultCount} 项` : "未找到符合条件的维度。"}</p>
            <div className="dimension-families">
              {families.map((family) => (
                <details key={family.family_id}>
                  <summary><strong>{family.name}</strong> <code>{family.family_id}</code> <small>{family.dimensions.length} 项</small></summary>
                  <ul className="dimension-entries">
                    {family.dimensions.map((entry) => <DimensionItem key={entry.dim_id} entry={entry} eventTitles={eventTitles} onSelectNode={onSelectNode} />)}
                  </ul>
                </details>
              ))}
            </div>
          </details>
        </>
      ) : <p className="dimension-results">此版本发布于维度账本上线前，可导出原有骨架。</p>}
    </section>
  );
}
