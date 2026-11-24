import { useId, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { Graph, GraphNode } from "./types";
import "./life-graph-explorer.css";

const categories: Record<string, { label: string; color: string }> = {
  origin: { label: "出生", color: "#296952" },
  education: { label: "教育", color: "#3e699e" },
  work: { label: "工作", color: "#376f70" },
  household: { label: "家庭", color: "#834a75" },
  history: { label: "历史背景", color: "#965734" },
  "历史暴露": { label: "历史背景", color: "#965734" },
  digital: { label: "数字生活", color: "#615995" },
  health: { label: "健康", color: "#805f18" },
  life_end: { label: "生命终点", color: "#764b49" },
  user_added: { label: "研究者添加", color: "#59634d" },
};
const edgeLabels: Record<string, string> = {
  temporal: "时间先后", depends_on: "依赖", decomposes: "节点细化",
};
const categoryOrder = Object.keys(categories);
const dateLength: Record<string, number> = { year: 4, month: 7 };
type Interval = { start: number; end: number };
type DatedEvent = Interval & { node: GraphNode };
type RelatedEvent = { node: GraphNode; kinds: string[] };
type View = "timeline" | "relationships";

function categoryInfo(category: string) {
  return categories[category] ?? { label: category || "未分类", color: "#59634d" };
}

function categoryStyle(category: string): CSSProperties {
  return { "--event-color": categoryInfo(category).color } as CSSProperties;
}

function dateLabel(node: GraphNode): string {
  const length = dateLength[node.precision ?? ""] ?? 10;
  const start = node.start.slice(0, length);
  const end = node.end?.slice(0, length);
  return end && end !== start ? `${start} – ${end}` : start;
}

function yearStart(year: number): number {
  return Date.parse(`${String(year).padStart(4, "0")}-01-01`);
}

function getEventInterval(node: GraphNode): DatedEvent | null {
  const start = Date.parse(node.start);
  const end = node.end ? Date.parse(node.end) : start;
  return Number.isFinite(start)
    ? { node, start, end: Number.isFinite(end) ? Math.max(start, end) : start }
    : null;
}

function plotRange(events: DatedEvent[], decade: number | null): Interval {
  if (decade !== null) return { start: yearStart(decade), end: yearStart(decade + 10) };
  const first = events.reduce((start, event) => Math.min(start, event.start), Infinity);
  const last = events.reduce((end, event) => Math.max(end, event.end), -Infinity);
  return {
    start: yearStart(new Date(first).getUTCFullYear()),
    end: yearStart(new Date(last).getUTCFullYear() + 1),
  };
}

function plotTicks({ start, end }: Interval) {
  const first = new Date(start).getUTCFullYear();
  const last = new Date(end).getUTCFullYear();
  const step = [1, 2, 5, 10, 20, 50, 100].find((size) => (last - first) / size <= 7) ?? 200;
  const ticks = [];
  for (let year = Math.ceil(first / step) * step; year <= last; year += step) {
    ticks.push({ year, position: 100 * (yearStart(year) - start) / (end - start) });
  }
  return ticks;
}

function plotLane(events: DatedEvent[], range: Interval) {
  const trackEnd: number[] = [];
  const marks = events.map(({ node, start, end }) => {
    const left = 100 * (Math.max(start, range.start) - range.start) / (range.end - range.start);
    const right = 100 * (Math.min(end, range.end) - range.start) / (range.end - range.start);
    const openTrack = trackEnd.findIndex((endPosition) => endPosition <= left);
    const track = openTrack === -1 ? trackEnd.length : openTrack;
    trackEnd[track] = right + 8;
    return { node, left, width: right - left, track };
  });
  return { marks, tracks: trackEnd.length };
}

function EventTimeline({ events, activeId, range, onSelectNode }: {
  events: DatedEvent[];
  activeId?: string;
  range: Interval;
  onSelectNode: (id: string) => void;
}) {
  const groups = new Map<string, DatedEvent[]>();
  for (const event of events) {
    const lane = groups.get(event.node.category) ?? [];
    lane.push(event);
    groups.set(event.node.category, lane);
  }
  const ticks = plotTicks(range);
  const lanes = [...groups].sort(([a], [b]) => {
    const left = categoryOrder.indexOf(a);
    const right = categoryOrder.indexOf(b);
    return (left < 0 ? categoryOrder.length : left) - (right < 0 ? categoryOrder.length : right);
  });
  return (
    <figure className="life-chart-figure">
      <figcaption className="life-chart-legend">
        <span><i className="life-legend-point" aria-hidden="true" />单点事件</span>
        <span><i className="life-legend-range" aria-hidden="true" />有起止时间的事件</span>
        <span className="life-scroll-hint">横向滚动查看后续年份</span>
      </figcaption>
      <div className="life-chart-scroll">
        <div className="life-timeline">
          <div className="life-timeline-axis" aria-hidden="true">
            <span>类别 / 年份</span>
            <div className="life-plot-ticks">
              {ticks.map(({ year, position }) => <span key={year} style={{ left: `${position}%` }}>{year}</span>)}
            </div>
          </div>
          {lanes.map(([category, lane]) => {
            const { marks, tracks } = plotLane(lane, range);
            return (
              <section
                key={category}
                className="life-timeline-lane"
                style={categoryStyle(category)}
                aria-label={categoryInfo(category).label}
              >
                <div className="life-lane-label" aria-hidden="true">
                  <i />
                  <strong>{categoryInfo(category).label}</strong>
                  <span>{lane.length}</span>
                </div>
                <div className="life-lane-track" style={{ minHeight: `${Math.max(42, tracks * 30 + 8)}px` }}>
                  {ticks.map(({ year, position }) => <i key={year} style={{ left: `${position}%` }} aria-hidden="true" />)}
                  {marks.map(({ node, left, width, track }) => (
                    <button
                      key={node.event_id}
                      className="life-timeline-mark"
                      type="button"
                      aria-pressed={node.event_id === activeId}
                      aria-label={`${node.title}，${dateLabel(node)}`}
                      title={`${node.title} · ${dateLabel(node)}`}
                      data-range={width > 0}
                      style={{ left: `${left}%`, top: `${track * 30 + 6}px`, "--duration": `${width}%` } as CSSProperties}
                      onClick={() => onSelectNode(node.event_id)}
                    >
                      <i aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </figure>
  );
}

function buildRelations(graph: Graph, activeId: string, visible: GraphNode[]) {
  const byId = new Map(visible.map((node) => [node.event_id, node]));
  const incoming = new Map<string, RelatedEvent>();
  const outgoing = new Map<string, RelatedEvent>();
  for (const edge of graph.edges) {
    const isIncoming = edge.target === activeId;
    if (!isIncoming && edge.source !== activeId) continue;
    const neighbor = byId.get(isIncoming ? edge.source : edge.target);
    if (!neighbor || neighbor.event_id === activeId) continue;
    const side = isIncoming ? incoming : outgoing;
    const item = side.get(neighbor.event_id) ?? { node: neighbor, kinds: [] };
    if (!item.kinds.includes(edge.kind)) item.kinds.push(edge.kind);
    side.set(neighbor.event_id, item);
  }
  const ordered = (items: Map<string, RelatedEvent>) =>
    [...items.values()].sort((a, b) => a.node.start.localeCompare(b.node.start));
  return { incoming: ordered(incoming), outgoing: ordered(outgoing) };
}

function RelationSide({ items, direction, onSelectNode }: {
  items: RelatedEvent[];
  direction: "incoming" | "outgoing";
  onSelectNode: (id: string) => void;
}) {
  const incoming = direction === "incoming";
  return (
    <section className={`life-relation-side life-relation-${direction}`}>
      <h3>{incoming ? "指向当前事件" : "当前事件指向"}</h3>
      {items.length ? (
        <ul>
          {items.map(({ node, kinds }) => (
            <li key={node.event_id}>
              <button type="button" style={categoryStyle(node.category)} onClick={() => onSelectNode(node.event_id)}>
                <span>{dateLabel(node)}</span>
                <strong>{node.title}</strong>
                <small>{kinds.map((kind) => edgeLabels[kind] ?? kind).join(" · ")}</small>
              </button>
              <i aria-hidden="true" />
            </li>
          ))}
        </ul>
      ) : <p>当前范围无关联事件</p>}
    </section>
  );
}

export default function LifeGraphExplorer({ graph, selectedId, onSelectNode }: {
  graph: Graph;
  selectedId?: string;
  onSelectNode: (id: string) => void;
}) {
  const headingId = useId();
  const selector = useRef<HTMLSelectElement>(null);
  const [view, setView] = useState<View>("timeline");
  const [category, setCategory] = useState("");
  const [decade, setDecade] = useState("");
  const nodes = useMemo(() => [...graph.nodes].sort((a, b) => a.start.localeCompare(b.start)), [graph.nodes]);
  const dated = useMemo(() => nodes.flatMap((node) => {
    const event = getEventInterval(node);
    return event ? [event] : [];
  }).sort((a, b) => a.start - b.start), [nodes]);
  const categoryOptions = [...new Set(nodes.map((node) => node.category))];
  const decadeOptions = new Set<number>();
  for (const { start, end } of dated) {
    const first = Math.floor(new Date(start).getUTCFullYear() / 10) * 10;
    const last = Math.floor(new Date(end).getUTCFullYear() / 10) * 10;
    for (let period = first; period <= last; period += 10) decadeOptions.add(period);
  }
  const currentDecade = decade === "" ? null : Number(decade);
  const range = dated.length ? plotRange(dated, currentDecade) : null;
  const datedInScope = dated.filter((event) =>
    (!category || event.node.category === category) &&
    (!range || (event.end >= range.start && event.start < range.end))
  );
  const visible = currentDecade === null
    ? nodes.filter((node) => !category || node.category === category)
    : datedInScope.map(({ node }) => node);
  const active = visible.find((node) => node.event_id === selectedId) ?? visible[0];
  const relations = view === "relationships" && active ? buildRelations(graph, active.event_id, visible) : null;
  function selectRelated(id: string) {
    onSelectNode(id);
    selector.current?.focus({ preventScroll: true });
  }

  return (
    <section className="panel life-explorer" aria-labelledby={headingId}>
      <header className="life-explorer-header">
        <div>
          <h2 id={headingId}>人生图谱</h2>
          <p role="status">{visible.length} / {nodes.length} 个事件</p>
        </div>
        <div className="life-view-switch" role="group" aria-label="图谱视图">
          <button type="button" aria-pressed={view === "timeline"} onClick={() => setView("timeline")}>分类时间线</button>
          <button type="button" aria-pressed={view === "relationships"} onClick={() => setView("relationships")}>事件关系图</button>
        </div>
      </header>
      <div className="life-filters">
        <div>
          <label htmlFor={`${headingId}-category`}>类别</label>
          <select id={`${headingId}-category`} value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">全部类别</option>
            {categoryOptions.map((option) => <option key={option} value={option}>{categoryInfo(option).label}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={`${headingId}-decade`}>时段</label>
          <select id={`${headingId}-decade`} value={decade} onChange={(event) => setDecade(event.target.value)}>
            <option value="">全部年代</option>
            {[...decadeOptions].sort((a, b) => a - b).map((year) => (
              <option key={year} value={year}>{year}–{year + 9} 年</option>
            ))}
          </select>
        </div>
        {view === "relationships" && active && (
          <div>
            <label htmlFor={`${headingId}-center`}>中心事件</label>
            <select id={`${headingId}-center`} ref={selector} value={active.event_id} onChange={(event) => onSelectNode(event.target.value)}>
              {visible.map((node) => <option key={node.event_id} value={node.event_id}>{dateLabel(node)} · {node.title}</option>)}
            </select>
          </div>
        )}
      </div>
      {view === "timeline" && range && datedInScope.length > 0 && (
        <EventTimeline events={datedInScope} activeId={active?.event_id} range={range} onSelectNode={onSelectNode} />
      )}
      {active && relations && (
        <div className="life-relations" aria-label="中心事件的直接关系">
          <RelationSide items={relations.incoming} direction="incoming" onSelectNode={selectRelated} />
          <div
            className="life-relation-current"
            data-incoming={relations.incoming.length > 0}
            data-outgoing={relations.outgoing.length > 0}
            style={categoryStyle(active.category)}
          >
            <span>当前事件</span>
            <strong>{active.title}</strong>
            <small>{dateLabel(active)}</small>
          </div>
          <RelationSide items={relations.outgoing} direction="outgoing" onSelectNode={selectRelated} />
        </div>
      )}
      {!active && <p className="life-chart-empty">当前筛选范围内没有事件。</p>}
      {view === "timeline" && visible.length > datedInScope.length && (
        <p className="life-chart-empty">{visible.length - datedInScope.length} 个事件缺少可绘制日期，可在事件关系图中选择。</p>
      )}
      {active && (
        <div className="life-event-readout" style={categoryStyle(active.category)}>
          <div aria-live="polite" aria-atomic="true">
            <span>{categoryInfo(active.category).label} · {dateLabel(active)}{active.location ? ` · ${active.location}` : ""}</span>
            <strong>{active.title}</strong>
          </div>
          <a href="#event-detail" onClick={() => onSelectNode(active.event_id)}>查看事件详情 <span aria-hidden="true">↗</span></a>
        </div>
      )}
    </section>
  );
}
