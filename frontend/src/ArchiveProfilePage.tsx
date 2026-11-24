import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { ArchiveProfilePanel } from "./ArchiveReleaseView";
import LifeGraphExplorer from "./LifeGraphExplorer";
const LifeGraph3D = lazy(() => import("./LifeGraph3D"));
import LifeStatePanel from "./LifeStatePanel";
import SkeletonDimensions from "./SkeletonDimensions";
import type { ArchiveBundle, ArchivePerson } from "./types";
import "./archive-profile-page.css";

type Props = {
  bundle: ArchiveBundle | null;
  person: ArchivePerson | null;
  onBack: () => void;
  onSelectNode: (id: string) => void;
};

export default function ArchiveProfilePage({ bundle, person, onBack, onSelectNode }: Props) {
  const firstEventId = useMemo(() => bundle?.graph.nodes[0]?.event_id ?? "", [bundle]);
  const [selectedId, setSelectedId] = useState(firstEventId);
  useEffect(() => {
    if (firstEventId && !selectedId) setSelectedId(firstEventId);
  }, [firstEventId, selectedId]);
  const selectNode = useCallback((id: string) => {
    setSelectedId(id);
    onSelectNode(id);
  }, [onSelectNode]);

  if (!person || !bundle) {
    return (
      <section className="archive-profile-page panel" aria-busy="true">
        <button type="button" className="quiet-button" onClick={onBack}>返回档案问答</button>
        <p role="status">正在读取发布版本的完整档案…</p>
      </section>
    );
  }

  return (
    <section className="archive-profile-page" aria-labelledby="archive-profile-page-title">
      <header className="archive-profile-page-header">
        <div>
          <span className="eyebrow">PUBLISHED PROFILE / LIFEGRAPH</span>
          <h1 id="archive-profile-page-title">{bundle.graph.person.name} 的完整档案</h1>
          <p>{bundle.release_id} · 发布快照 {bundle.snapshot_id}</p>
        </div>
        <div className="archive-profile-page-actions">
          <button type="button" className="quiet-button" onClick={onBack}>返回档案问答</button>
          <a className="secondary-button" href={`/api/archive/releases/${encodeURIComponent(bundle.release_id)}`} target="_blank" rel="noreferrer">查看档案 JSON</a>
        </div>
      </header>

      <section className="archive-profile-page-grid">
        <aside className="archive-profile-page-identity">
          <ArchiveProfilePanel bundle={bundle} />
        </aside>
        <div className="archive-profile-page-main">
          <Suspense fallback={<section className="life-graph-3d-loading panel" aria-busy="true">正在加载三维图谱…</section>}>
            <LifeGraph3D graph={bundle.graph} selectedId={selectedId} onSelectNode={selectNode} />
          </Suspense>
          <LifeGraphExplorer graph={bundle.graph} selectedId={selectedId} onSelectNode={selectNode} />
          {(() => {
            const node = bundle.graph.nodes.find((item) => item.event_id === selectedId);
            if (!node) return null;
            return (
              <section id="event-detail" className="profile-event-detail panel" aria-labelledby="profile-event-title">
                <div className="profile-event-detail-header">
                  <div>
                    <span className="eyebrow">EVENT RECORD</span>
                    <h2 id="profile-event-title">{node.title}</h2>
                  </div>
                  <code>{node.event_id}</code>
                </div>
                <dl className="profile-event-facts">
                  <div><dt>有效时间</dt><dd>{node.start}{node.end ? ` → ${node.end}` : ""} · {node.precision}</dd></div>
                  <div><dt>地点</dt><dd>{node.location ?? "未知"}</dd></div>
                  <div><dt>参与者</dt><dd>{(node.participants ?? []).join(" · ")}</dd></div>
                  <div><dt>登记时间</dt><dd>{node.recorded_at?.slice(0, 10) ?? "未知"}</dd></div>
                </dl>
                <p>{node.notes || "该节点没有补充说明。"}</p>
                {!!node.evidence?.length && <ul className="profile-event-sources">{node.evidence.map((source) => <li key={source.id}>{source.label}</li>)}</ul>}
              </section>
            );
          })()}
          <LifeStatePanel key={`profile-state-${bundle.graph.graph_revision_id}`} graph={bundle.graph} onSelectNode={selectNode} />
          <SkeletonDimensions key={`profile-dimensions-${bundle.graph.graph_revision_id}`} graph={bundle.graph} onSelectNode={selectNode} />
        </div>
      </section>
    </section>
  );
}
