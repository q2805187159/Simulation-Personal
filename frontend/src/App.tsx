import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import ArchiveReleaseView from "./ArchiveReleaseView";
import ArchiveProfilePage from "./ArchiveProfilePage";
import SkeletonDimensions from "./SkeletonDimensions";
import LifeGraphExplorer from "./LifeGraphExplorer";
const LifeGraph3D = lazy(() => import("./LifeGraph3D"));
import LifeStatePanel from "./LifeStatePanel";
import SkeletonEvolution from "./SkeletonEvolution";
import { demoCatalog, defaultSpec, demoGraph, demoEvents } from "./workbenchDefaults";
import { ActionPanel, FleshStrip, GenerationStartView, LlmPanel, NodeDetail, Passport, Progress, ReviewStrip, SpecPanel, Timeline } from "./WorkbenchPanels";
import { readRoute, updateRoute, type Route } from "./workbenchRoute";
import type {
  ArchivePerson,
  ArchiveBundle,
  Catalog,
  ChatSession,
  GenerationOptions,
  Graph,
  GraphEdit,
  GraphOperation,
  Job,
  JobEvent,
  RecentJob,
  Stage,
} from "./types";

const stageText: Record<Stage, string> = {
  generate: "生成规格",
  skeleton: "骨架审阅",
  flesh: "血肉填充",
  review: "链路审查",
  archive: "档案问答",
};

const stageCopy: Record<Stage, [string, string]> = {
  generate: ["创建一份人物档案", "设定地区与观察时点，系统会采样人物条件并匹配历史背景。"],
  skeleton: ["梳理人物一生的事件", "查看来源与依赖，手动编辑或交给模型修订，确认后再生成细节。"],
  flesh: ["查看事件与人物状态", "逐个检查事件叙事、记忆线索及在该时点生成的量化状态。"],
  review: ["复核后发布", "核对发现的问题及修复结果，确认通过后发布到档案库。"],
  archive: ["档案库与角色问答", "固定人物版本和观察时点，循着档案证据开展多轮访谈。"],
};

function jobStage(job: RecentJob): Stage {
  if (job.status === "ready" || job.status === "publish_pending") return "review";
  if (job.status === "chain_review" || job.status === "flesh_running") return "flesh";
  return "skeleton";
}

function normalizeDate(value: string): string {
  if (/^\d{4}$/.test(value)) return value + "-01-01";
  if (/^\d{4}-\d{2}$/.test(value)) return value + "-01";
  return value;
}

async function idempotencyKey(value: unknown): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(value)),
  );
  const hex = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return "poc-" + hex;
}

function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const routedSearch = useRef(window.location.search);
  const { stage } = route;
  const [catalog, setCatalog] = useState<Catalog>(demoCatalog);
  const [spec, setSpec] = useState<GenerationOptions>(defaultSpec);
  const [job, setJob] = useState<Job>({
    job_id: "PREVIEW",
    status: "preview",
    progress: 0,
    graph_revision_id: demoGraph.graph_revision_id,
    person: demoGraph.person,
    actions: [],
  });
  const [graph, setGraph] = useState<Graph>(demoGraph);
  const [events, setEvents] = useState<JobEvent[]>(demoEvents);
  const [detailRevision, setDetailRevision] = useState<
    Awaited<ReturnType<typeof api.flesh>> | null
  >(null);
  const [reviewReport, setReviewReport] = useState<
    Awaited<ReturnType<typeof api.reviewDetails>> | null
  >(null);
  const [archive, setArchive] = useState<ArchivePerson[]>([]);
  const [session, setSession] = useState<ChatSession | null>(null);
  const [selectedId, setSelectedId] = useState("EV-EARTHQUAKE");
  const [suggestion, setSuggestion] = useState("");
  const [chatInput, setChatInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] =
    useState("配置已加载。提交后才会创建人物和人生骨架。");
  const [error, setError] = useState("");
  const [revisions, setRevisions] = useState<
    Array<{
      graph_revision_id: string;
      parent_revision_id: string | null;
      branch: string;
      created_at: string;
    }>
  >([]);
  const [editing, setEditing] = useState<false | "edit" | "add">(false);
  const addEventId = useRef("");
  const [editDraft, setEditDraft] = useState({
    title: "",
    start: "",
    end: "",
    notes: "",
  });
  const [archivePerson, setArchivePerson] = useState<ArchivePerson | null>(
    null,
  );
  const [archiveBundle, setArchiveBundle] = useState<ArchiveBundle | null>(null);
  const [chatAsOf, setChatAsOf] = useState("");
  const [sessionLoading, setSessionLoading] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [connection, setConnection] = useState<"connecting" | "ready" | "offline">("connecting");
  const [connectedModel, setConnectedModel] = useState("");
  const [recentJobs, setRecentJobs] = useState<RecentJob[]>([]);
  const [restoring, setRestoring] = useState(Boolean(route.job || route.session || route.release));
  const heading = useRef<HTMLHeadingElement>(null);
  const lastStage = useRef(stage);
  const restoreRoute = useRef<() => void>(() => {});
  const generateAttempt = useRef<{ input: string; key: string } | null>(null);
  const busy = loading || sessionLoading || restoring;
  const chunks =
    detailRevision &&
    detailRevision.graph_revision_id === graph.graph_revision_id &&
    detailRevision.detail_revision_id === job.detail_revision_id
      ? detailRevision.chunks
      : [];
  const activeReview =
    graph.graph_revision_id === job.graph_revision_id &&
    reviewReport?.release_id === job.release_id
      ? reviewReport
      : null;
  const findings = activeReview?.findings ?? [];
  const selectedNode = useMemo(
    () =>
      graph.nodes.find((node) => node.event_id === selectedId) ??
      graph.nodes[0],
    [graph.nodes, selectedId],
  );

  const loadJob = useCallback(async (next: Job, revision?: string, isCurrent: () => boolean = () => true) => {
    const [activeGraph, eventResult, revisionResult, nextDetail, nextReport] =
      await Promise.all([
        next.graph_revision_id ? api.graph(next.graph_revision_id) : null,
        api.events(next.job_id),
        api.graphs(next.job_id),
        next.detail_revision_id ? api.flesh(next.detail_revision_id) : null,
        next.release_id ? api.reviewDetails(next.release_id) : null,
      ]);
    if (revision && !revisionResult.revisions.some((item) => item.graph_revision_id === revision))
      throw new ApiError("该骨架版本不属于此生成任务。");
    const nextGraph = revision && revision !== next.graph_revision_id
      ? await api.graph(revision)
      : activeGraph;
    if (!isCurrent()) return null;
    setJob(next);
    if (nextGraph) setGraph(nextGraph);
    setEvents(eventResult.events);
    setRevisions(revisionResult.revisions);
    setDetailRevision(nextDetail);
    setReviewReport(nextReport);
    setEditing(false);
    setSelectedId((current) => nextGraph?.nodes.some((node) => node.event_id === current)
      ? current
      : nextGraph?.nodes[0]?.event_id ?? "");
    return nextReport;
  }, []);

  const navigate = useCallback((next: Route, replace = false) => {
    updateRoute(next, replace);
    routedSearch.current = window.location.search;
    setRoute(next);
  }, []);
  const setStage = (nextStage: Stage) => {
    navigate({
      ...route,
      stage: nextStage,
      release: nextStage === "archive" ? archivePerson?.release_id : undefined,
      session: nextStage === "archive" ? session?.session_id : undefined,
      profile: undefined,
    });
  };
  const openProfile = (person: ArchivePerson) => {
    setArchivePerson(person);
    navigate({
      stage: "archive",
      release: person.release_id,
      session: session?.release_id === person.release_id ? session.session_id : undefined,
      profile: person.release_id,
    });
  };
  const closeProfile = () => {
    navigate({
      stage: "archive",
      release: archivePerson?.release_id,
      session: session && session.release_id === archivePerson?.release_id ? session.session_id : undefined,
    });
  };
  useEffect(() => {
    if (lastStage.current !== stage && !restoring) {
      heading.current?.focus();
      lastStage.current = stage;
    }
  }, [stage, restoring]);

  const connect = useCallback(async () => {
    setConnection("connecting");
    const [c, a, health] = await Promise.allSettled([
      api.catalog(),
      api.archive(),
      api.health(),
    ]);
      if (c.status === "fulfilled") {
        setCatalog(c.value);
        const defaults = c.value.defaults;
        if (defaults) {
          setSpec((current) => current === defaultSpec ? defaults : current);
        }
      }
      if (a.status === "fulfilled") setArchive(a.value.persons);
      setConnection(c.status === "fulfilled" && health.status === "fulfilled" ? "ready" : "offline");
      if (health.status === "fulfilled") setConnectedModel(health.value.model ?? health.value.llm);
  }, []);

  useEffect(() => { void connect(); }, [connect]);
  useEffect(() => {
    let active = true;
    setArchiveBundle(null);
    if (!archivePerson) return () => { active = false; };
    void api.archiveBundle(archivePerson.release_id)
      .then((bundle) => { if (active) setArchiveBundle(bundle); })
      .catch(() => { if (active) setArchiveBundle(null); });
    return () => { active = false; };
  }, [archivePerson?.release_id]);
  useEffect(() => {
    if (connection !== "ready" || stage !== "generate") return;
    let stale = false;
    void api.recentJobs().then(({ jobs }) => { if (!stale) setRecentJobs(jobs); }).catch(() => {
      if (!stale) setRecentJobs([]);
    });
    return () => { stale = true; };
  }, [connection, stage]);
  useEffect(() => {
    let version = 0;
    const restore = async () => {
      const request = ++version;
      routedSearch.current = window.location.search;
      const saved = readRoute();
      setRoute(saved);
      setError("");
      setRestoring(Boolean(saved.job || saved.session || saved.release));
      try {
        if (saved.job) {
          const previous = await api.job(saved.job);
          if (request !== version) return;
          await loadJob(previous, saved.revision, () => request === version);
          if (request !== version) return;
          if (previous.spec) {
            const { region, ethnicity, birth_year, as_of, entry, household, detail, seed, life_status, sampling, population_selection } = previous.spec;
            setSpec({
              region: population_selection ? population_selection.filters.region : region,
              ethnicity: population_selection ? population_selection.filters.ethnicity : ethnicity,
              population_frame: population_selection?.frame_id,
              birth_year: sampling?.birth_year.source === "sampled" ? null : birth_year,
              as_of, entry, household: sampling?.household.source === "sampled" ? null : household, detail, seed,
              life_status: previous.spec.population?.conditioning === "unconditional" ? "auto" : life_status,
            });
          }
          setMessage("已恢复生成任务。");
        } else {
          setJob({ job_id: "PREVIEW", status: "preview", progress: 0, actions: [] });
          setGraph(demoGraph);
          setEvents(demoEvents);
          setRevisions([]);
          setDetailRevision(null);
          setReviewReport(null);
          setSelectedId(demoGraph.nodes[0]?.event_id ?? "");
          setEditing(false);
        }
        if (saved.stage === "archive" && (saved.session || saved.release || saved.profile)) {
          const [restored, library] = await Promise.all([
            saved.session ? api.chat(saved.session) : null,
            api.archive(),
          ]);
          if (request !== version) return;
          const person = library.persons.find((item) => item.release_id === (restored?.release_id ?? saved.release));
          if (!person) throw new ApiError("所选发布版本已不可用。");
          setArchive(library.persons);
          setArchivePerson(person);
          setSession(restored);
          setChatAsOf(restored?.as_of ?? person.death_date ?? person.as_of);
        } else if (saved.stage === "archive") {
          setArchivePerson(null);
          setSession(null);
        }
      } catch (cause) {
        if (request === version)
          setError(cause instanceof Error ? cause.message : "无法恢复工作台。");
      } finally {
        if (request === version) setRestoring(false);
      }
    };
    const onPopState = () => {
      if (window.location.search !== routedSearch.current) void restore();
    };
    window.addEventListener("popstate", onPopState);
    restoreRoute.current = () => { void restore(); };
    void restore();
    return () => {
      version += 1;
      window.removeEventListener("popstate", onPopState);
      restoreRoute.current = () => {};
    };
  }, [loadJob]);

  const updateSpec = <K extends keyof GenerationOptions>(
    key: K,
    value: GenerationOptions[K],
  ) => setSpec((old) => {
    const next = { ...old, [key]: value };
    if (key === "population_frame") {
      next.region = value ? null : catalog.defaults?.region ?? "51";
      next.ethnicity = value ? null : catalog.defaults?.ethnicity ?? "01";
    }
    const ethnicity = catalog.ethnicities.find((item) => item.id === next.ethnicity);
    if (ethnicity?.region_ids && next.region && !ethnicity.region_ids.includes(next.region)) {
      next.ethnicity = next.population_frame ? null : "01";
    }
    return next;
  });
  const generate = async () => {
    setLoading(true);
    setPendingAction("正在编排生成规则并建立骨架");
    setError("");
    try {
      const valid = await api.validateSpec(spec);
      if (!valid.valid)
        throw new ApiError(valid.warnings.join("；") || "规格校验失败");
      const input = JSON.stringify(spec);
      if (generateAttempt.current?.input !== input)
        generateAttempt.current = { input, key: crypto.randomUUID() };
      const next = await api.generate(spec, generateAttempt.current.key);
      await loadJob(next);
      generateAttempt.current = null;
      navigate({ stage: "skeleton", job: next.job_id });
      setMessage("骨架已生成，等待你的审阅。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "生成失败");
      setMessage("生成未完成。输入已保留，可以重试。");
    } finally {
      setPendingAction("");
      setLoading(false);
    }
  };
  const edit = async (payload: GraphEdit): Promise<boolean> => {
    setLoading(true);
    setPendingAction(payload.source === "llm" ? "正在生成并校验修订建议" : "正在保存新的骨架版本");
    setError("");
    try {
      if (
        job.status === "preview" ||
        !["skeleton_review", "ready"].includes(job.status)
      )
        throw new ApiError("请先生成骨架并进入可编辑阶段");
      const next = await api.editGraph(
        graph.graph_revision_id,
        payload,
        await idempotencyKey({
          parent_graph_revision_id: graph.graph_revision_id,
          payload,
        }),
      );
      const revision = next.command_graph_revision_id ?? next.graph_revision_id;
      await loadJob(next, revision);
      navigate({ stage: "skeleton", job: next.job_id, revision });
      setMessage("已保存子版本并完成依赖重算。");
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "版本编辑失败");
      return false;
    } finally {
      setPendingAction("");
      setLoading(false);
    }
  };
  const applySuggestion = async () => {
    if (!suggestion.trim()) return;
    if (await edit({ source: "llm", suggestion: suggestion.trim() }))
      setSuggestion("");
  };
  const evolve = async (mode: "upgrade" | "extend", asOf?: string) => {
    if (!job || !graph.graph_revision_id) return false;
    setLoading(true);
    setPendingAction(mode === "extend" ? "正在推进人生轨迹" : "正在应用当前规则与模型");
    setError("");
    try {
      const body = { mode, as_of: asOf };
      const next = await api.evolveGraph(
        graph.graph_revision_id, body,
        await idempotencyKey({ parent: graph.graph_revision_id, ...body }),
      );
      const revision = next.command_graph_revision_id ?? next.graph_revision_id;
      await loadJob(next, revision);
      navigate({ stage: "skeleton", job: next.job_id, revision });
      setMessage(mode === "extend" ? "续写已保存为新版本。" : "升级已保存为新版本。");
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "未能生成新版本");
      return false;
    } finally {
      setPendingAction("");
      setLoading(false);
    }
  };
  const saveNodeEdit = async () => {
    if (editing !== "add" && !selectedNode) return;
    const eventId = editing === "add" ? addEventId.current : selectedNode!.event_id;
    const fields = {
      event_id: eventId,
      title: editDraft.title.trim(),
      start: normalizeDate(editDraft.start),
      end: editDraft.end ? normalizeDate(editDraft.end) : null,
      notes: editDraft.notes.trim(),
    };
    const saved = await edit({
      source: "manual",
      operations: editing === "add"
        ? [{ op: "add", ...fields, category: "user_added", precision: "day", location: graph.person.region, depends_on: [] }]
        : [{ op: "update", ...fields }],
    });
    if (saved) setSelectedId(eventId);
  };
  const splitNode = async (children: Extract<GraphOperation, { op: "split" }>["children"]) => {
    if (!selectedNode) return false;
    return edit({
      source: "manual",
      operations: [{ op: "split", event_id: selectedNode.event_id, children }],
    });
  };
  const addHistoryAssertion = async (body: Record<string, unknown>) => {
    if (!selectedNode) return false;
    const next = await api.addHistoryAssertion(
      graph.graph_revision_id, { ...body, event_id: selectedNode.event_id },
      await idempotencyKey({ graph: graph.graph_revision_id, body }),
    );
    const revision = next.command_graph_revision_id ?? next.graph_revision_id;
    await loadJob(next, revision);
    navigate({ stage: "skeleton", job: next.job_id, revision });
    return true;
  };
  const addNode = () => {
    addEventId.current = "manual-" + crypto.randomUUID().slice(0, 8);
    setEditDraft({
      title: "",
      start: selectedNode ? normalizeDate(selectedNode.start) : (graph.person.death_date ?? graph.person.as_of),
      end: "",
      notes: "",
    });
    setEditing("add");
  };
  const selectRevision = async (id: string) => {
    setLoading(true);
    setError("");
    try {
      const selected = await api.graph(id);
      setGraph(selected);
      setSelectedId(selected.nodes[0]?.event_id ?? "");
      setEditing(false);
      navigate({ ...route, job: job.job_id, revision: id });
      setMessage(id === job.graph_revision_id ? "已返回当前骨架版本。" : "正在查看历史骨架版本。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "版本读取失败");
    } finally {
      setLoading(false);
    }
  };
  const continueFlesh = async () => {
    setLoading(true);
    setPendingAction("正在生成事件叙事与人物状态");
    setError("");
    try {
      if (
        !job.graph_revision_id ||
        graph.graph_revision_id !== job.graph_revision_id ||
        !job.actions?.includes("continue_flesh")
      )
        throw new ApiError("当前阶段不可填充血肉");
      const next = await api.continueFlesh(job.graph_revision_id);
      await loadJob(next);
      setStage("flesh");
      setMessage("血肉已按当前图版本生成。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "血肉生成失败");
      setMessage("血肉没有生成，工作流停留在当前阶段。");
    } finally {
      setPendingAction("");
      setLoading(false);
    }
  };
  const runReview = async () => {
    setLoading(true);
    setPendingAction("正在审查事件、状态与证据");
    setError("");
    try {
      if (
        !job.release_id ||
        graph.graph_revision_id !== job.graph_revision_id ||
        !job.actions?.includes("review")
      )
        throw new ApiError("当前没有可审查的 release candidate");
      const next = await api.review(job.release_id);
      const report = await loadJob(next);
      setStage("review");
      setMessage(`审查完成：${report?.status ?? next.status}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "审查失败");
      setMessage("审查没有完成，发布动作保持禁用。");
    } finally {
      setPendingAction("");
      setLoading(false);
    }
  };
  const publish = async () => {
    setLoading(true);
    setPendingAction("正在保存发布版本");
    setError("");
    try {
      if (
        !job.release_id ||
        graph.graph_revision_id !== job.graph_revision_id ||
        !job.actions?.includes("publish")
      )
        throw new ApiError("当前没有可发布的 release candidate");
      const next = await api.publish(job.release_id);
      await loadJob(next);
      const library = await api.archive();
      const published = library.persons.find((item) => item.release_id === next.release_id) ?? null;
      setArchive(library.persons);
      setArchivePerson(published);
      setSession(null);
      setChatAsOf(published?.death_date ?? published?.as_of ?? "");
      navigate({ ...route, stage: "archive", release: published?.release_id, session: undefined });
      setMessage("档案已发布，图、血肉、报告和快照已绑定。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "发布失败");
      setMessage("发布没有完成，档案库只显示已发布版本。");
    } finally {
      setPendingAction("");
      setLoading(false);
    }
  };
  const choosePerson = (person: ArchivePerson) => {
    if (archivePerson?.release_id === person.release_id) return;
    setArchivePerson(person);
    setChatAsOf(person.death_date ?? person.as_of);
    setSession(null);
    setError("");
    navigate({ ...route, release: person.release_id, session: undefined });
  };
  const openPerson = async () => {
    if (!archivePerson) return;
    setSessionLoading(true);
    try {
      const nextSession = await api.createChat({
          person_id: archivePerson.person_id,
          release_id: archivePerson.release_id,
          as_of: chatAsOf,
          role_scope: "self",
      });
      setSession(nextSession);
      navigate({ ...route, release: nextSession.release_id, session: nextSession.session_id });
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "会话创建失败");
    } finally {
      setSessionLoading(false);
    }
  };
  const sendChat = async () => {
    if (!session || !chatInput.trim()) return;
    const question = chatInput.trim();
    const sessionId = session.session_id;
    setPendingQuestion(question);
    setError("");
    setLoading(true);
    try {
      const turn = await api.chatTurn(session.session_id, question);
      setSession((old) =>
        old?.session_id === sessionId ? { ...old, turns: [...old.turns, turn] } : old,
      );
      setChatInput((current) => current.trim() === question ? "" : current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "问答未完成");
    } finally {
      setPendingQuestion("");
      setLoading(false);
    }
  };

  const selectNode = useCallback((id: string) => {
    setSelectedId(id);
    setEditing(false);
  }, []);
  const resumeJob = (saved: RecentJob) => {
    if (!saved.graph_revision_id) return;
    navigate({ stage: jobStage(saved), job: saved.job_id });
    restoreRoute.current();
  };
  const stageAvailable = (item: Stage) => {
    if (item === "generate" || item === "archive") return true;
    if (item === "skeleton") return job.status !== "preview";
    if (item === "flesh") return Boolean(job.detail_revision_id);
    return Boolean(job.release_id);
  };
  const hasRouteGraph = job.status !== "preview" && route.job === job.job_id;
  const missingStage = stage !== "generate" && stage !== "archive"
    && (!hasRouteGraph || !stageAvailable(stage));

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className="topbar">
        <div className="brand-mark">
          <span className="brand-kicker">LIFEGRAPH</span>
          <strong>数字人生成工作台</strong>
        </div>
        <nav className="topnav" aria-label="主导航">
          {(Object.keys(stageText) as Stage[]).map((item) => (
            <button
              key={item}
              className={stage === item ? "nav-link active" : "nav-link"}
              aria-current={stage === item ? "page" : undefined}
              disabled={busy || !stageAvailable(item)}
              onClick={() => setStage(item)}
            >
              {stageText[item]}
            </button>
          ))}
        </nav>
        <div className="status-chip">
          <i className={`status-dot ${connection}`} aria-hidden="true" />{" "}
          {connection === "ready" ? "服务就绪" : connection === "connecting" ? "连接中" : "服务离线"}
        </div>
      </header>
      <main id="main-content" className="page-frame">
        <div className="page-heading">
          <div>
            <h1 ref={heading} tabIndex={-1}>{stage === "archive" && route.profile ? "完整角色档案" : stageCopy[stage][0]}</h1>
            <p className="lede">{stage === "archive" && route.profile ? "查看人物、家庭、事件、状态、维度和证据的完整发布快照。" : stageCopy[stage][1]}</p>
          </div>
          <div className="heading-meta">
            <span className="meta-label">{stage === "archive" && route.profile ? "档案页面" : stage === "archive" ? "档案范围" : "当前阶段"}</span>
            <strong>{stage === "archive" ? (archivePerson?.name ?? "已发布人物") : stageText[stage]}</strong>
            <span className="meta-id">{stage === "archive" ? (archivePerson?.release_id ?? "") : stage === "generate" || missingStage ? "等待生成" : graph.graph_revision_id}</span>
          </div>
        </div>
        {stage !== "archive" && stage !== "generate" && !missingStage && <a className="mobile-action-link" href="#workflow-actions">前往下一步 ↓</a>}
        {connection === "offline" && (
          <div className="alert error" role="alert">
            <span>工作台暂时无法连接到服务。请重试连接。</span>
            <button type="button" className="retry-button" onClick={() => void connect()}>重新连接</button>
          </div>
        )}
        {error && (
          <div className="alert error" role="alert">
            <strong>需要留意</strong>
            <span>{error}</span>
            <button aria-label="关闭错误" onClick={() => setError("")}>
              ×
            </button>
          </div>
        )}
        <div className={`alert info${pendingAction || restoring ? " pending" : ""}`} role="status">
          <span className="alert-icon">↗</span>
          <span>{restoring ? "正在恢复保存的工作台…" : pendingAction ? `${pendingAction}…` : message}</span>
        </div>
        {restoring ? <div className="restoring-panel" aria-busy="true">正在加载当前记录</div> : stage === "archive" && route.profile ? (
          <ArchiveProfilePage
            bundle={archiveBundle}
            person={archivePerson}
            onBack={closeProfile}
            onSelectNode={() => undefined}
          />
        ) : stage === "archive" ? (
          <ArchiveReleaseView
            archive={archive}
            session={session}
            selectedPerson={archivePerson}
            chatAsOf={chatAsOf}
            setChatAsOf={setChatAsOf}
            input={chatInput}
            setInput={setChatInput}
            onSelect={choosePerson}
            onOpen={openPerson}
            onSend={sendChat}
            loading={busy}
            pendingQuestion={pendingQuestion}
            opening={sessionLoading}
            onViewProfile={openProfile}
          />
        ) : stage === "generate" ? (
          <GenerationStartView
            spec={spec}
            catalog={catalog}
            updateSpec={updateSpec}
            onGenerate={generate}
            loading={busy || connection !== "ready"}
            model={connectedModel}
            recentJobs={recentJobs.filter((saved) => saved.graph_revision_id)}
            onResume={resumeJob}
          />
        ) : missingStage ? (
          <section className="panel" aria-labelledby="missing-stage-title">
            <h2 id="missing-stage-title">此工作阶段暂无可审阅的数据</h2>
            <button className="secondary-button" type="button" onClick={() => navigate({ stage: "generate" })}>返回生成规格</button>
          </section>
        ) : (
          <div className="workspace">
            <aside className="left-rail">
              <Passport graph={graph} />
              <SpecPanel
                spec={spec}
                resolved={job.spec}
                catalog={catalog}
                updateSpec={updateSpec}
                onGenerate={generate}
                loading={busy || connection !== "ready"}
              />
              <Progress job={job} />
            </aside>
            <section className="center-column">
              {stage === "skeleton" && <>
                <Suspense fallback={<section className="life-graph-3d-loading panel" aria-busy="true">正在加载三维图谱…</section>}>
                  <LifeGraph3D graph={graph} selectedId={selectedId} onSelectNode={selectNode} />
                </Suspense>
                <LifeGraphExplorer graph={graph} selectedId={selectedId} onSelectNode={selectNode} />
                <LifeStatePanel key={"life-state-" + graph.graph_revision_id} graph={graph} onSelectNode={selectNode} />
                <SkeletonDimensions key={graph.graph_revision_id} graph={graph} onSelectNode={selectNode} />
              </>}
              {stage === "flesh" && <FleshStrip chunks={chunks} onSelectNode={selectNode} />}
              {stage === "review" && <ReviewStrip findings={findings} status={activeReview?.status} />}
              <Timeline
                graph={graph}
                selectedId={selectedId}
                setSelectedId={selectNode}
              />
              <NodeDetail
                graph={graph}
                selectedNode={selectedNode}
                chunks={chunks}
                revisions={revisions}
                onSelectNode={selectNode}
                onEdit={() => {
                  if (selectedNode)
                    setEditDraft({
                      title: selectedNode.title,
                      start: selectedNode.start,
                      end: selectedNode.end ?? "",
                      notes: selectedNode.notes ?? "",
                    });
                  setEditing("edit");
                }}
                onSave={saveNodeEdit}
                onCancel={() => setEditing(false)}
                onDelete={() =>
                  selectedNode &&
                  edit({
                    source: "manual",
                    operations: [
                      { op: "delete", event_id: selectedNode.event_id },
                    ],
                  })
                }
                onAdd={addNode}
                onSplit={stage === "skeleton" ? splitNode : undefined}
                onHistoryAssertion={stage === "skeleton" ? addHistoryAssertion : undefined}
                onRevision={selectRevision}
                canEdit={
                  Boolean(
                    job.actions?.includes("edit") ||
                      job.actions?.includes("continue_flesh"),
                  ) && ["skeleton_review", "ready"].includes(job.status)
                }
                loading={busy}
                editing={editing}
                draft={editDraft}
                setDraft={setEditDraft}
              />
            </section>
            <aside className="right-rail">
              {stage === "skeleton" && <SkeletonEvolution
                key={graph.graph_revision_id}
                graph={graph}
                disabled={busy || !["skeleton_review", "ready"].includes(job.status)}
                onEvolve={evolve}
              />}
              <ActionPanel
                job={job}
                graph={graph}
                loading={busy}
                onFlesh={continueFlesh}
                onReview={runReview}
                onPublish={publish}
                findings={findings}
                setStage={setStage}
              />
              <LlmPanel
                events={events}
                model={job.model?.model ?? connectedModel}
                suggestion={suggestion}
                setSuggestion={setSuggestion}
                onSuggest={applySuggestion}
                loading={busy}
                canSuggest={
                  job.status === "skeleton_review" || job.status === "ready"
                }
              />
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
