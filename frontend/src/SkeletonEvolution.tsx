import { useState } from "react";
import type { Graph } from "./types";

export default function SkeletonEvolution({ graph, disabled, onEvolve }: {
  graph: Graph;
  disabled: boolean;
  onEvolve: (mode: "upgrade" | "extend", asOf?: string) => Promise<boolean>;
}) {
  const [asOf, setAsOf] = useState("");
  const cutoff = graph.person.as_of;
  return (
    <section className="panel action-panel" aria-label="骨架演进">
      <h2>演进骨架</h2>
      <p className="action-copy">使用当前模型和资料继续推演，父版本仍可单独查看。</p>
      <button type="button" className="secondary-button full" disabled={disabled}
        onClick={() => void onEvolve("upgrade")}>按最新规则升级</button>
      {graph.person.life_status === "living" && (
        <details className="dimension-browser">
          <summary>续写到新日期</summary>
          <form onSubmit={(event) => {
            event.preventDefault();
            if (asOf > cutoff) void onEvolve("extend", asOf);
          }}>
            <label>新截止日期
              <input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} required />
            </label>
            <button type="submit" className="secondary-button full" disabled={disabled || asOf <= cutoff}>
              生成续写版本
            </button>
          </form>
        </details>
      )}
    </section>
  );
}
