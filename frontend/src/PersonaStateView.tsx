import type { FleshChunk } from "./types";

const labels: Record<string, string> = {
  valence: "情绪正向度", arousal: "情绪唤醒", stress: "压力",
  salience: "事件显著性", perceived_control: "感知控制",
  routine: "维持惯例", risk_aversion: "规避风险", social_support: "寻求支持",
  learning: "学习倾向", directness: "直述程度", reflection: "反思程度", detail: "表达细节",
};
const memoryNames: Record<string, string> = {
  synthetic_episodic: "合成情景记忆",
  early_life_reconstruction: "早年重建",
  historical_context: "历史背景",
  archive_boundary: "档案边界",
};

export default function PersonaStateView({ chunk }: { chunk: FleshChunk }) {
  const state = chunk.profile;
  const explanation = state?.interpretation;
  const prose = [
    ["场景", explanation?.scene],
    ["动机", explanation?.motivation],
    ["情绪线索", explanation?.emotion_note],
    ["后来反思", explanation?.reflection],
    ["回忆触发", explanation?.recall_cue],
    ["表达示例", explanation?.speaking_sample],
  ].filter(([, value]) => Boolean(value));
  const groups = [
    ["情绪", state?.emotion], ["事件评价", state?.appraisal],
    ["应对倾向", state?.tendencies], ["表达风格", state?.voice],
  ] as const;

  return (
    <div className="persona-state">
      <p className="persona-narrative">{chunk.narrative}</p>
      {chunk.memory && <div className="persona-memory"><h4>记忆线索</h4><p>{chunk.memory}</p></div>}
      {state && (
        <>
          <div className="persona-scope">
            <strong>{state.scope === "synthesized" ? "合成状态" : state.scope === "context" ? "背景候选" : "档案边界"}</strong>
            <span>时点 {state.valid_from}</span>
            {state.memory && <span>{memoryNames[state.memory.kind] ?? state.memory.kind}</span>}
          </div>
          {state.scope === "synthesized" && (
            <div className="persona-metrics">
              <p>0–100 模拟量表，描述该段经历中的状态与倾向。</p>
              {groups.map(([heading, values]) => values && (
                <section key={heading}>
                  <h4>{heading}</h4>
                  <dl>
                    {Object.entries(values).map(([key, score]) => typeof score === "number" && (
                      <div key={key}>
                        <dt>{labels[key] ?? key}</dt>
                        <dd><meter min={0} max={100} value={score} aria-label={labels[key] ?? key} /> {score}</dd>
                      </div>
                    ))}
                    {heading === "事件评价" && state.memory?.clarity != null && (
                      <div><dt>记忆清晰度</dt><dd><meter min={0} max={100} value={state.memory.clarity} aria-label="记忆清晰度" /> {state.memory.clarity}</dd></div>
                    )}
                  </dl>
                </section>
              ))}
            </div>
          )}
          {prose.length > 0 && <dl className="persona-interpretation">
            {prose.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
          </dl>}
        </>
      )}
      {!!chunk.evidence?.length && <p className="persona-evidence">证据锚点 · {chunk.evidence.map(({ label }) => label).join("、")}</p>}
    </div>
  );
}
