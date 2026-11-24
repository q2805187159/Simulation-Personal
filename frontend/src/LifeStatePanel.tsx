import { useEffect, useId, useState } from "react";
import { api } from "./api";
import type { Graph, LifeStateFact, LifeStateProjection } from "./types";
import "./life-state-panel.css";

const fieldLabels: Record<string, string> = {
  education_stage: "教育阶段",
  household_role: "家庭角色",
  work_status: "工作状态",
  work_context: "工作领域",
  work_task: "工作任务",
  work_arrangement: "劳动安排",
  partnership_status: "伴侣关系",
  residence_changed: "居住变动",
  residence_region: "居住地",
  health_review: "健康检查",
  care_need: "照料需求",
  care_arrangement: "照料安排",
  support_commitment: "照料承诺",
  pressure_delta: "压力变化",
  support_delta: "支持变化",
  entry_kind: "初入社会类别",
  entry_route: "初入社会路径",
  path: "生活路径",
};

function valueText(value: LifeStateFact["value"]): string {
  if (value === null) return "未知";
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
}

export default function LifeStatePanel({ graph, onSelectNode }: {
  graph: Graph;
  onSelectNode?: (id: string) => void;
}) {
  const headingId = useId();
  const cutoff = graph.person.death_date ?? graph.person.as_of;
  const [asOf, setAsOf] = useState(cutoff);
  const [participantId, setParticipantId] = useState(graph.person.person_id);
  const [query, setQuery] = useState({ asOf, participantId });
  const [pending, setPending] = useState(true);
  const [result, setResult] = useState<LifeStateProjection | null>(null);
  const [error, setError] = useState("");
  const selected = result?.participants[0];
  const eventTitles = new Map(graph.nodes.map((node) => [node.event_id, node.title]));

  useEffect(() => {
    let active = true;
    api.lifeState(graph.graph_revision_id, query.asOf, query.participantId)
      .then((projection) => {
        if (active) setResult(projection);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "无法读取状态快照");
      })
      .finally(() => {
        if (active) setPending(false);
      });
    return () => { active = false; };
  }, [graph.graph_revision_id, query]);

  return (
    <section className="panel life-state-panel" aria-labelledby={headingId}>
      <h2 id={headingId}>人生状态快照</h2>
      <form
        className="life-state-form"
        aria-labelledby={headingId}
        onSubmit={(event) => {
          event.preventDefault();
          setResult(null);
          setError("");
          setPending(true);
          setQuery({ asOf, participantId });
        }}
      >
        <label htmlFor={`${headingId}-date`}>
          参照日期
          <input
            id={`${headingId}-date`}
            type="date"
            required
            max={cutoff}
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
          />
        </label>
        <label htmlFor={`${headingId}-participant`}>
          参与者
          <select
            id={`${headingId}-participant`}
            value={participantId}
            onChange={(event) => setParticipantId(event.target.value)}
          >
            <option value={graph.person.person_id}>{graph.person.name}（本人）</option>
            {graph.person.family?.map((person) => (
              <option key={person.id} value={person.id}>{person.name}</option>
            ))}
          </select>
        </label>
        <button className="quiet-button" type="submit" disabled={pending}>查询</button>
      </form>
      <div className="life-state-result" aria-busy={pending}>
        {pending && <p role="status">正在读取状态快照</p>}
        {error && <p className="warning" role="alert">{error}</p>}
        {result && selected && (
          <>
            <p role="status">
              截至 {result.as_of} · {selected.facts.length} 项状态
              {selected.status === "conflict" && ` · ${selected.conflict_fields.length} 项有冲突`}
            </p>
            {selected.facts.length ? (
              <dl className="life-state-facts">
                {selected.facts.map((fact) => (
                  <div key={fact.field}>
                    <dt title={fact.field}>{fieldLabels[fact.field] ?? fact.field}</dt>
                    <dd>
                      <strong>
                        {fact.status === "conflict"
                          ? `有冲突 · ${(fact.candidate_values ?? []).map(valueText).join(" / ")}`
                          : valueText(fact.value)}
                      </strong>
                      <ul>
                        {fact.provenance.map((source, index) => {
                          const title = eventTitles.get(source.event_id) ?? source.event_id;
                          return (
                            <li key={`${source.event_id}-${source.role}-${index}`}>
                              {onSelectNode ? (
                                <a href="#event-detail" onClick={() => onSelectNode(source.event_id)}>{title}</a>
                              ) : title}
                              <span> · {source.valid_time.start}</span>
                              {source.known_at && <span> · 获知 {source.known_at}</span>}
                              {source.asserted_value !== undefined && <span> · {valueText(source.asserted_value)}</span>}
                            </li>
                          );
                        })}
                      </ul>
                    </dd>
                  </div>
                ))}
              </dl>
            ) : <p>该参与者在参照日期缺少可用状态记录。</p>}
          </>
        )}
      </div>
    </section>
  );
}
