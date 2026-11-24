import { useId, useState, type FormEvent } from "react";
import type { Graph, GraphNode } from "./types";
import "./split-event-panel.css";

type Scalar = string | number | boolean;
type ParticipantDelta = {
  person_id: string;
  role: string;
  delta: Record<string, Scalar>;
};

type SplitChild = {
  event_id: string;
  title: string;
  start: string;
  end?: string;
  state_delta?: Record<string, Scalar>;
  participant_deltas?: ParticipantDelta[];
};

type ChildDraft = {
  event_id: string;
  title: string;
  start: string;
  end: string;
};

type SplitNode = GraphNode & {
  participant_deltas?: ParticipantDelta[];
};

type SplitEventPanelProps = {
  graph: Graph;
  node: GraphNode;
  disabled: boolean;
  onSplit: (children: SplitChild[]) => Promise<boolean>;
};

const scalarText = (value: Scalar) => {
  if (typeof value === "boolean") return value ? "是" : "否";
  return String(value);
};

function initialChildren(graph: Graph, node: GraphNode): [ChildDraft, ChildDraft] {
  const start = node.start.slice(0, 10);
  const end = node.end?.slice(0, 10) ?? "";
  const occupied = new Set(graph.nodes.map((event) => event.event_id));
  const prefix = node.event_id.slice(0, 110);
  let suffix = 1;
  const nextId = () => {
    let id: string;
    do {
      id = `${prefix}-part-${suffix++}`;
    } while (occupied.has(id));
    return id;
  };
  const day = 86_400_000;
  const span = end ? (Date.parse(end) - Date.parse(start)) / day : 0;
  const boundary = Math.floor(span / 2);
  const divideAt = (offset: number) => new Date(Date.parse(start) + offset * day).toISOString().slice(0, 10);
  return [
    {
      event_id: nextId(),
      title: `${node.title.slice(0, 175)} · 前段`,
      start,
      end: span > 0 ? divideAt(boundary) : start,
    },
    {
      event_id: nextId(),
      title: `${node.title.slice(0, 175)} · 后段`,
      start: span > 0 ? divideAt(boundary + 1) : start,
      end,
    },
  ];
}

function assignOwner(
  owners: Record<string, number>,
  key: string,
  value: string,
) {
  return { ...owners, [key]: Number(value) };
}

export default function SplitEventPanel({
  graph,
  node,
  disabled,
  onSplit,
}: SplitEventPanelProps) {
  const headingId = useId();
  const errorId = `${headingId}-error`;
  const splitNode = node as SplitNode;
  const [children, setChildren] = useState<[ChildDraft, ChildDraft]>(() => initialChildren(graph, node));
  const [owners, setOwners] = useState<Record<string, number>>(() =>
    Object.fromEntries(Object.keys(node.state_delta ?? {}).map((key) => [key, 0])),
  );
  const [participantOwners, setParticipantOwners] = useState<Record<string, number>>(() =>
    Object.fromEntries((splitNode.participant_deltas ?? []).map((_, index) => [`participant-${index}`, 0])),
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const stateEntries = Object.entries(node.state_delta ?? {});
  const participantEntries = splitNode.participant_deltas ?? [];
  const parentStart = node.start.slice(0, 10);
  const parentEnd = node.end?.slice(0, 10) ?? (node.precision === "day" ? parentStart : "");
  const cutoff = graph.person.death_date ?? graph.person.as_of;
  const latestDate = parentEnd && parentEnd < cutoff ? parentEnd : cutoff;
  const parentRange = parentEnd === parentStart
    ? parentStart
    : parentEnd ? `${parentStart} 至 ${parentEnd}` : `${parentStart} 起，结束时间未知`;

  const updateChild = (index: number, patch: Partial<ChildDraft>) => {
    setError("");
    setChildren((current) => current.map((child, childIndex) =>
      childIndex === index ? { ...child, ...patch } : child,
    ) as [ChildDraft, ChildDraft]);
  };

  const validate = () => {
    const errors: string[] = [];
    const ids = new Set(graph.nodes.map((event) => event.event_id));
    for (const [index, child] of children.entries()) {
      const label = index === 0 ? "前段" : "后段";
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(child.event_id.trim())) {
        errors.push(`${label}的事件编号应使用英文字母、数字、连字符或下划线。`);
      }
      if (ids.has(child.event_id.trim())) errors.push(`${label}的事件编号已被使用。`);
      ids.add(child.event_id.trim());
      if (!child.title.trim()) errors.push(`${label}需要标题。`);
      if (!child.start) errors.push(`${label}需要开始日期。`);
      if (child.start && child.start < parentStart) errors.push(`${label}的开始日期不能早于父节点。`);
      if (child.start > latestDate) errors.push(`${label}的开始日期超出可用时间窗口。`);
      if (child.end && child.end < child.start) errors.push(`${label}的结束日期不能早于开始日期。`);
      if (child.end > latestDate) errors.push(`${label}的结束日期超出可用时间窗口。`);
    }
    return errors;
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (disabled || pending) return;
    const errors = validate();
    if (errors.length) {
      setError(errors.join(" "));
      return;
    }
    setError("");
    setPending(true);
    const stateByChild = children.map(() => ({} as Record<string, Scalar>));
    for (const [key, value] of stateEntries) {
      stateByChild[owners[key] ?? 0][key] = value;
    }
    const participantByChild = children.map(() => [] as ParticipantDelta[]);
    participantEntries.forEach((entry, index) => {
      participantByChild[participantOwners[`participant-${index}`] ?? 0].push(entry);
    });
    const payload = children.map((child, index) => ({
      event_id: child.event_id.trim(),
      title: child.title.trim(),
      start: child.start,
      ...(child.end ? { end: child.end } : {}),
      ...(Object.keys(stateByChild[index]).length ? { state_delta: stateByChild[index] } : {}),
      ...(participantByChild[index].length ? { participant_deltas: participantByChild[index] } : {}),
    }));
    try {
      await onSplit(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "拆分未能保存，请重试。");
    } finally {
      setPending(false);
    }
  };

  return (
    <section className="panel split-event-panel" aria-labelledby={headingId}>
      <div className="section-heading">
        <div>
          <h2 id={headingId}>拆分为两个可核对节点</h2>
        </div>
        <span className="event-id">{node.event_id}</span>
      </div>
      <p className="split-event-intro">
        父节点保留为汇总锚点。两个子节点必须落在父节点时间窗口内，原有状态与参与者增量由下方选择的节点承接。
      </p>
      <div className="split-event-parent">
        <span>父节点</span>
        <strong>{node.title}</strong>
        <span className="split-event-range">{parentRange}</span>
      </div>
      <form onSubmit={submit} aria-describedby={error ? errorId : undefined} aria-busy={pending}>
        {error && <p id={errorId} className="split-event-error" role="alert">{error}</p>}
        <div className="split-event-grid">
          {children.map((child, index) => {
            const label = index === 0 ? "前段" : "后段";
            const prefix = `${headingId}-${index}`;
            return (
              <fieldset className="split-event-child" key={prefix}>
                <legend>{label}</legend>
                <label htmlFor={`${prefix}-id`}>事件编号</label>
                <input id={`${prefix}-id`} required maxLength={128} pattern="[A-Za-z0-9_\-]+" value={child.event_id}
                  onChange={(event) => updateChild(index, { event_id: event.target.value })}
                  disabled={disabled || pending} />
                <label htmlFor={`${prefix}-title`}>节点标题</label>
                <input id={`${prefix}-title`} required maxLength={180} value={child.title}
                  onChange={(event) => updateChild(index, { title: event.target.value })}
                  disabled={disabled || pending} />
                <div className="field-row">
                  <div>
                    <label htmlFor={`${prefix}-start`}>开始日期</label>
                    <input id={`${prefix}-start`} type="date" required min={parentStart} max={latestDate}
                      value={child.start} onChange={(event) => updateChild(index, { start: event.target.value })}
                      disabled={disabled || pending} />
                  </div>
                  <div>
                    <label htmlFor={`${prefix}-end`}>结束日期</label>
                    <input id={`${prefix}-end`} type="date" min={child.start || parentStart} max={latestDate}
                      value={child.end} onChange={(event) => updateChild(index, { end: event.target.value })}
                      disabled={disabled || pending} />
                  </div>
                </div>
              </fieldset>
            );
          })}
        </div>
        {(stateEntries.length > 0 || participantEntries.length > 0) && (
          <div className="split-event-ownership">
            <h3>原有影响归属</h3>
            <p>默认全部归入前段。切换后会把每项影响写入所选子节点，父锚点只保留守恒摘要。</p>
            {stateEntries.map(([key, value]) => (
              <label className="split-event-owner" key={key}>
                <span><code>{key}</code> · {scalarText(value)}</span>
                <select value={owners[key] ?? 0} onChange={(event) => setOwners(assignOwner(owners, key, event.target.value))}
                  disabled={disabled || pending} aria-label={`${key} 的归属`}>
                  <option value={0}>前段</option><option value={1}>后段</option>
                </select>
              </label>
            ))}
            {participantEntries.map((entry, index) => (
              <label className="split-event-owner" key={`participant-${index}`}>
                <span>{entry.person_id} · {entry.role} · {Object.keys(entry.delta).join("、")}</span>
                <select value={participantOwners[`participant-${index}`] ?? 0}
                  onChange={(event) => setParticipantOwners(assignOwner(participantOwners, `participant-${index}`, event.target.value))}
                  disabled={disabled || pending} aria-label={`${entry.person_id} ${entry.role} 的归属`}>
                  <option value={0}>前段</option><option value={1}>后段</option>
                </select>
              </label>
            ))}
          </div>
        )}
        <div className="editor-actions split-event-actions">
          <button className="primary-button" type="submit" disabled={disabled || pending}>
            {pending ? "正在创建子版本" : "创建拆分子版本"}
          </button>
          <span className="split-event-hint">提交后将执行守恒检查、时间校验和依赖重算。</span>
        </div>
      </form>
    </section>
  );
}
