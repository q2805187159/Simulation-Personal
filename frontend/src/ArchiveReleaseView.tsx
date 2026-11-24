import { useEffect, useId, useRef, useState } from "react";
import PersonaStateView from "./PersonaStateView";
import type { ArchiveBundle, ArchivePerson, ChatEvidence, ChatSession, ChatTurn } from "./types";
import "./archive.css";

type Props = {
  archive: ArchivePerson[];
  session: ChatSession | null;
  selectedPerson: ArchivePerson | null;
  chatAsOf: string;
  setChatAsOf: (value: string) => void;
  input: string;
  setInput: (value: string) => void;
  onSelect: (person: ArchivePerson) => void;
  onOpen: () => void;
  onSend: () => void;
  loading: boolean;
  opening?: boolean;
  pendingQuestion?: string;
  onViewProfile: (person: ArchivePerson) => void;
};

type EvidenceSource = { id: string; label: string; kind: string; url?: string };
type ArchiveEvidence = ChatEvidence & {
  chunk_id?: string;
  source_kind?: string;
  sources?: EvidenceSource[];
};
type ToolStep = ChatTurn["tools"][number] & {
  event_ids?: string[];
  status?: "ok" | "fallback";
  duration_ms?: number;
};
type DialogueTurn = ChatTurn & {
  disclosure?: "brief" | "focused" | "expanded";
  follow_up_to?: string | null;
  cited_event_ids?: string[];
  duration_ms?: number;
};

const STARTER_QUESTIONS = [
  "能说说你初入社会时的生活吗？",
  "你平时怎样决定工作和家庭之间的取舍？",
  "有没有哪段经历改变了你看待生活的方式？",
];

const EVIDENCE_SCOPE: Record<string, string> = {
  context: "历史背景候选",
  boundary: "档案时间边界",
  partial_record: "时点内部分记录",
  direct_record: "个人档案记录",
};

const DISCLOSURE: Record<string, string> = {
  brief: "简要说明",
  focused: "针对性说明",
  expanded: "展开说明",
};

function releaseToken(releaseId: string) {
  return releaseId.slice(0, 8);
}

function sourceLink(url?: string) {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch {
    return undefined;
  }
}

function showDuration(ms: number) {
  return ms < 1000 ? `${Math.round(ms)} 毫秒` : `${(ms / 1000).toFixed(1)} 秒`;
}

function TurnEvidence({ turn }: { turn: DialogueTurn }) {
  const cited = new Set(turn.cited_event_ids ?? turn.evidence.map(({ event_id }) => event_id));
  const orderedEvidence = [...turn.evidence].sort(
    (left, right) => Number(cited.has(right.event_id)) - Number(cited.has(left.event_id)),
  );
  return (
    <details className="archive-evidence">
      <summary>
        {cited.size ? `查看 ${cited.size} 条引用证据` : "查看证据范围"}
        <span>{turn.tools.length} 个步骤{turn.duration_ms != null && ` · ${showDuration(turn.duration_ms)}`}</span>
      </summary>
      <div className="archive-evidence-body">
        {!orderedEvidence.length && <p>当前会话时点内没有检索到与本轮问题匹配的记录。</p>}
        {orderedEvidence.length > 0 && (
          <ol className="archive-evidence-list">
            {orderedEvidence.map((item: ArchiveEvidence) => (
              <li key={item.event_id}>
                <div className="archive-evidence-heading">
                  <strong>{item.title}</strong>
                  <time dateTime={item.start}>{item.start}</time>
                </div>
                <div className="archive-evidence-tags">
                  <span>{EVIDENCE_SCOPE[item.scope ?? ""] ?? "档案引用"}</span>
                  <span>{cited.has(item.event_id) ? "回答引用" : "检索候选"}</span>
                </div>
                {item.excerpt && <p>{item.excerpt}</p>}
                {!!item.sources?.length && (
                  <ul aria-label="原始来源">
                    {item.sources.map((source) => {
                      const url = sourceLink(source.url);
                      return (
                        <li key={source.id}>
                          {url ? (
                            <a href={url} target="_blank" rel="noreferrer">{source.label}</a>
                          ) : source.label}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
        <details className="archive-trace">
          <summary>检索过程</summary>
          <ol>
            {turn.tools.map((tool: ToolStep, index) => (
              <li key={`${tool.name}-${index}`}>
                <code>{tool.name}</code>
                {tool.status === "fallback" && <small>已使用备用检索路径</small>}
                {tool.message && <span>{tool.message}</span>}
                {tool.duration_ms != null && <small>耗时 {showDuration(tool.duration_ms)}</small>}
                {!!tool.event_ids?.length && <small>{tool.event_ids.join(" · ")}</small>}
              </li>
            ))}
          </ol>
          <p className="archive-model">回答模型 <code>{turn.model}</code></p>
        </details>
      </div>
    </details>
  );
}

function SessionScope({ session }: { session: ChatSession }) {
  return (
    <div className="archive-scope">
      <p>视角截止 <time dateTime={session.as_of}>{session.as_of}</time></p>
      <details className="archive-scope-detail">
        <summary>会话版本与快照</summary>
        <dl>
          <div><dt>发布版本</dt><dd>{session.release_id}</dd></div>
          <div><dt>骨架版本</dt><dd>{session.graph_revision_id}</dd></div>
          <div><dt>细节版本</dt><dd>{session.detail_revision_id}</dd></div>
          <div><dt>查询快照</dt><dd>{session.snapshot_id}</dd></div>
        </dl>
      </details>
    </div>
  );
}

export function ArchiveProfilePanel({ bundle }: { bundle: ArchiveBundle }) {
  const person = bundle.graph.person;
  const chunks = bundle.detail.chunks;
  const personal = chunks.filter((chunk) => chunk.profile?.scope === "synthesized");
  const visibleEvents = bundle.graph.nodes.slice().sort((a, b) => a.start.localeCompare(b.start));
  return (
    <div className="archive-profile-panel">
      <div className="archive-profile-hero">
        <div className="archive-profile-avatar" aria-hidden="true">{person.name.slice(0, 1)}</div>
        <div><h3>{person.name} · 合成档案</h3><p>{person.region} · {person.ethnicity} · {person.birth_year} 年生</p><p>{person.summary}</p></div>
      </div>
      <dl className="archive-profile-facts">
        <div><dt>生命周期</dt><dd>{person.life_status === "living" ? `在世，截止 ${person.as_of}` : `静态档案，终点 ${person.death_date ?? "已记录"}`}</dd></div>
        <div><dt>家庭范围</dt><dd>{person.family?.length ? `含 ${person.family.length} 位关系参与者` : "仅本人视角"}</dd></div>
        <div><dt>发布版本</dt><dd>{bundle.release_id}</dd></div>
        <div><dt>发布快照</dt><dd>{bundle.snapshot_id}</dd></div>
        <div><dt>骨架 / 细节</dt><dd>{bundle.graph.graph_revision_id} / {bundle.detail.detail_revision_id}</dd></div>
        <div><dt>审查状态</dt><dd>{bundle.review.status === "passed" ? "通过" : bundle.review.status === "repaired" ? "修复后通过" : bundle.review.status}</dd></div>
      </dl>
      {!!person.family?.length && <section><h4>家庭与关系</h4><ul className="archive-profile-family">{person.family.map((member) => <li key={member.id}><strong>{member.name}</strong><span>{member.relation}</span></li>)}</ul></section>}
      <section><h4>人生骨架摘要 · {visibleEvents.length} 个节点</h4><ol className="archive-profile-timeline">{visibleEvents.map((node) => <li key={node.event_id}><time>{node.start.slice(0, 7)}</time><div><strong>{node.title}</strong><span>{node.location ?? "地点待匹配"} · {node.summary_node ? "背景候选" : "个人事件"}</span><p>{node.notes}</p></div></li>)}</ol></section>
      <section>
        <h4>事件细节与量化状态</h4>
        <p>{chunks.length} 段事件细节 · {personal.length} 段有量化状态</p>
        <div className="archive-profile-state-grid">
          {chunks.map((chunk) => (
            <details key={chunk.chunk_id}>
              <summary>{chunk.title}</summary>
              <PersonaStateView chunk={chunk} />
            </details>
          ))}
        </div>
      </section>
      <p className="archive-profile-footnote">表达倾向是合成状态估计，须查看各字段的生成依据与时间锚点。它不代表经心理测量校准的量表。</p>
    </div>
  );
}

export default function ArchiveReleaseView({
  archive,
  session,
  selectedPerson,
  chatAsOf,
  setChatAsOf,
  input,
  setInput,
  onSelect,
  onOpen,
  onSend,
  loading,
  opening = false,
  pendingQuestion,
  onViewProfile,
}: Props) {
  const [search, setSearch] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const scopeForm = useRef<HTMLFormElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const history = useRef<HTMLDivElement>(null);
  const nearEnd = useRef(true);
  const openingFromForm = useRef(false);
  const selectingRelease = useRef(false);
  const scopeHintId = useId();
  const questionHintId = useId();
  const searchTerm = search.trim().toLowerCase();
  const filtered = archive.filter((person) =>
    [person.name, person.region, person.ethnicity, String(person.birth_year), person.release_id]
      .some((value) => value.toLowerCase().includes(searchTerm)),
  );
  const activeSession = session?.release_id === selectedPerson?.release_id ? session : null;
  const birthDay = selectedPerson ? `${selectedPerson.birth_year}-01-01` : "";
  const latestDay = selectedPerson?.death_date ?? selectedPerson?.as_of ?? "";
  const turnCount = activeSession?.turns.length ?? 0;
  const turnPositions = new Map(activeSession?.turns.map(({ turn_id }, index) => [turn_id, index + 1]));
  const sessionId = activeSession?.session_id;

  useEffect(() => {
    if (selectingRelease.current && selectedPerson) {
      selectingRelease.current = false;
      scopeForm.current?.focus();
    }
  }, [selectedPerson]);

  useEffect(() => {
    if (!sessionId) return;
    nearEnd.current = true;
    if (openingFromForm.current) {
      openingFromForm.current = false;
      composer.current?.focus();
    }
  }, [sessionId]);

  useEffect(() => {
    const log = history.current;
    if (log && nearEnd.current) log.scrollTo({ top: log.scrollHeight });
  }, [pendingQuestion, sessionId, turnCount]);

  useEffect(() => {
    if (!opening) openingFromForm.current = false;
  }, [opening]);

  function selectRelease(person: ArchivePerson) {
    if (person.release_id === selectedPerson?.release_id) {
      scopeForm.current?.focus();
      return;
    }
    selectingRelease.current = true;
    onSelect(person);
  }

  function useStarter(question: string) {
    setInput(question);
    composer.current?.focus();
  }

  return (
    <div className="archive-workspace">
      <aside className="panel archive-index" aria-labelledby="archive-index-title">
        <div className="archive-index-header">
          <h2 id="archive-index-title">已发布档案</h2>
          <span>{archive.length} 份</span>
        </div>
        {!!archive.length && (
          <form role="search" aria-label="查找已发布档案" className="archive-search" onSubmit={(event) => event.preventDefault()}>
            <label className="sr-only" htmlFor="archive-search">搜索档案</label>
            <input
              id="archive-search"
              ref={searchInput}
              name="q"
              type="search"
              value={search}
              autoComplete="off"
              placeholder="按姓名、地区、年份或版本搜索"
              onChange={(event) => setSearch(event.target.value)}
            />
            {search && (
              <button
                type="button"
                className="archive-text-button"
                onClick={() => {
                  setSearch("");
                  searchInput.current?.focus();
                }}
              >
                清空
              </button>
            )}
          </form>
        )}
        {searchTerm && <p className="archive-result-count" role="status">找到 {filtered.length} 份档案</p>}
        {!archive.length && <p className="archive-index-empty">发布人物后，档案会出现在这里。</p>}
        {searchTerm && !filtered.length && <p className="archive-index-empty">没有匹配的档案，试试姓名或出生年份。</p>}
        {!!filtered.length && (
          <ul className="archive-release-list">
            {filtered.map((person) => {
              const selected = selectedPerson?.release_id === person.release_id;
              return (
                <li key={person.release_id}>
                  <button
                    type="button"
                    className={`archive-release${selected ? " is-selected" : ""}`}
                    aria-pressed={selected}
                    disabled={loading}
                    onClick={() => selectRelease(person)}
                  >
                    <span className="archive-monogram" aria-hidden="true">{person.name.slice(0, 1)}</span>
                    <span className="archive-release-copy">
                      <strong>{person.name}</strong>
                      <small>{person.region} · {person.birth_year} 年生</small>
                      <small>
                        {person.life_status === "living" ? "在世档案" : "已故档案"} ·{" "}
                        <span aria-hidden="true">版本 {releaseToken(person.release_id)}</span>
                        <span className="sr-only">版本 {person.release_id}</span>
                      </small>
                    </span>
                    <span className="archive-release-marker" aria-hidden="true">{selected ? "✓" : "↗"}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </aside>

      <section className="panel archive-conversation" aria-labelledby="archive-chat-title">
        <header className="archive-conversation-header">
          <div>
            <h2 id="archive-chat-title">
              {selectedPerson ? `与${selectedPerson.name}对话` : "档案问答"}
            </h2>
            {selectedPerson && (
              <p>{selectedPerson.region} · {selectedPerson.birth_year} 年生 · 发布版本 {releaseToken(selectedPerson.release_id)}</p>
            )}
          </div>
          <div className="archive-header-actions">
            <span className="synthetic-badge">合成角色模拟</span>
            {selectedPerson && (
              <button type="button" className="quiet-button" onClick={() => onViewProfile(selectedPerson)}>
                查看完整档案
              </button>
            )}
            {selectedPerson && (
              <a
                href={`/api/archive/releases/${encodeURIComponent(selectedPerson.release_id)}`}
                target="_blank"
                rel="noreferrer"
              >
                查看档案 JSON
              </a>
            )}
          </div>
        </header>

        {selectedPerson && (
          <form
            ref={scopeForm}
            className="archive-session-form"
            aria-label="设置会话时点"
            tabIndex={-1}
            onSubmit={(event) => {
              event.preventDefault();
              if (loading) return;
              openingFromForm.current = true;
              onOpen();
            }}
          >
            <div>
              <label htmlFor="archive-as-of">问答截止日期</label>
              <input
                id="archive-as-of"
                name="as_of"
                type="date"
                min={birthDay}
                max={latestDay}
                value={chatAsOf}
                required
                disabled={loading}
                aria-describedby={scopeHintId}
                onChange={(event) => setChatAsOf(event.target.value)}
              />
            </div>
            <p id={scopeHintId}>
              {birthDay} 至 {latestDay}
              {activeSession && <small>更改日期后，可开始独立的新会话。</small>}
            </p>
            <button type="submit" className="primary-button" disabled={loading || !chatAsOf}>
              {opening ? "正在建立会话…" : activeSession ? "使用此日期创建新会话" : "开始问答"}
            </button>
          </form>
        )}

        {activeSession ? (
          <>
            <SessionScope session={activeSession} />
            <div
              className="archive-history"
              role="log"
              aria-label="对话记录"
              ref={history}
              onScroll={(event) => {
                const current = event.currentTarget;
                nearEnd.current = current.scrollHeight - current.scrollTop - current.clientHeight < 100;
              }}
            >
              {!turnCount && !pendingQuestion && (
                <div className="archive-starters">
                  <h3>从一段经历开始</h3>
                  <div>
                    {STARTER_QUESTIONS.map((question) => (
                      <button key={question} type="button" className="quiet-button" onClick={() => useStarter(question)}>
                        {question}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {activeSession.turns.map((turn: DialogueTurn) => {
                const parent = turn.follow_up_to && turnPositions.get(turn.follow_up_to);
                return (
                  <article className="turn archive-turn" key={turn.turn_id} aria-label={`第 ${turnPositions.get(turn.turn_id)} 轮`}>
                    <div className="archive-question">
                      <span>你</span>
                      <p>{turn.question}</p>
                    </div>
                    <div className="archive-answer">
                      <span className="archive-monogram" aria-hidden="true">{selectedPerson?.name.slice(0, 1)}</span>
                      <div>
                        <div className="archive-answer-meta">
                          <strong>{selectedPerson?.name}</strong>
                          {turn.disclosure && <span>{DISCLOSURE[turn.disclosure]}</span>}
                          {parent && <span>第 {parent} 轮追问</span>}
                        </div>
                        <p>{turn.answer}</p>
                        <TurnEvidence turn={turn} />
                      </div>
                    </div>
                  </article>
                );
              })}
              {pendingQuestion && (
                <div className="archive-pending">
                  <div className="archive-question">
                    <span>你</span>
                    <p>{pendingQuestion}</p>
                  </div>
                  <p className="archive-progress" role="status">正在检索此时点的档案并组织回答…</p>
                </div>
              )}
            </div>
            <form
              className="archive-composer"
              aria-label="发送问题"
              onSubmit={(event) => {
                event.preventDefault();
                if (!loading && input.trim()) {
                  nearEnd.current = true;
                  onSend();
                }
              }}
            >
              <label htmlFor="chat-input" className="sr-only">向数字人提问</label>
              <textarea
                id="chat-input"
                ref={composer}
                name="question"
                rows={2}
                value={input}
                maxLength={2000}
                aria-describedby={questionHintId}
                placeholder={turnCount ? "就刚才的回答继续追问，或换一个话题…" : "问问那时的经历、想法或感受…"}
                onChange={(event) => setInput(event.target.value)}
              />
              <button className="primary-button" type="submit" disabled={loading || !input.trim()}>
                {loading && !opening ? "回答中…" : "发送"}
              </button>
              <small id={questionHintId}>会话将保留上下文，可连续追问。</small>
            </form>
          </>
        ) : (
          <div className="archive-conversation-empty">
            <h3>{selectedPerson ? "确定谈话时点" : "选择一份档案"}</h3>
            <p>
              {selectedPerson ? "会话会固定所选日期、发布版本和人物视角。" : "选择已发布的人物版本，即可按特定时间与其对话。"}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
