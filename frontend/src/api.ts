import type {
  ArchivePerson,
  ArchiveBundle,
  Catalog,
  ChatSession,
  ChatTurn,
  GenerationOptions,
  GenerationSpec,
  Graph,
  GraphEdit,
  GraphRevision,
  Job,
  RecentJob,
  JobEvent,
  LifeStateProjection,
  FleshChunk,
  ReviewReport,
} from "./types";

const API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(
    /\/$/,
    "",
  ) ?? "";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function graphExportUrl(id: string): string {
  return `${API_BASE}/api/graphs/${encodeURIComponent(id)}/export`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("无法连接后端服务");
  }
  const text = await response.text();
  let body: unknown = undefined;
  try {
    body = text ? JSON.parse(text) : undefined;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const detail =
      typeof body === "object" && body && "detail" in body
        ? body.detail
        : undefined;
    const messages = Array.isArray(detail)
      ? detail
          .map((item) => item.msg)
          .filter(Boolean)
          .join(" · ")
      : "";
    const message =
      typeof detail === "string"
        ? detail
        : messages || `请求失败（${response.status}）`;
    throw new ApiError(message, response.status);
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string; llm: string; model?: string }>("/api/health"),
  catalog: () => request<Catalog>("/api/catalog"),
  validateSpec: (spec: GenerationOptions) =>
    request<
      | { valid: true; spec: GenerationSpec; warnings: string[] }
      | { valid: false; spec: GenerationOptions; warnings: string[] }
    >(
      "/api/generations/spec/validate",
      { method: "POST", body: JSON.stringify(spec) },
    ),
  generate: (spec: GenerationOptions, key: string) =>
    request<Job>("/api/generations", {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(spec),
    }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  recentJobs: () => request<{ jobs: RecentJob[] }>("/api/jobs?limit=12"),
  events: (id: string) =>
    request<{ events: JobEvent[] }>(`/api/jobs/${id}/events`),
  graphs: (id: string) =>
    request<{ revisions: GraphRevision[] }>(`/api/jobs/${id}/graphs`),
  graph: (id: string) => request<Graph>(`/api/graphs/${id}`),
  lifeState: (id: string, asOf: string, participantId: string) => {
    const query = new URLSearchParams({ as_of: asOf, participant_id: participantId });
    return request<LifeStateProjection>(`/api/graphs/${encodeURIComponent(id)}/state?${query}`);
  },
  editGraph: (id: string, payload: GraphEdit, key: string) =>
    request<Job>(`/api/graphs/${id}/edits`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify(payload),
    }),
  continueFlesh: (id: string) =>
    request<Job>(`/api/graphs/${id}/continue-flesh`, { method: "POST" }),
  evolveGraph: (id: string, body: { mode: "upgrade" | "extend"; as_of?: string }, key: string) =>
    request<Job>(`/api/graphs/${id}/evolve`, {
      method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(body),
    }),
  addHistoryAssertion: (id: string, body: Record<string, unknown>, key: string) =>
    request<Job>(`/api/graphs/${id}/history-assertions`, {
      method: "POST", headers: { "Idempotency-Key": key }, body: JSON.stringify(body),
    }),
  flesh: (id: string) =>
    request<{
      detail_revision_id: string;
      graph_revision_id: string;
      chunks: FleshChunk[];
    }>(`/api/flesh/${id}`),
  review: (id: string) =>
    request<Job>(`/api/reviews/${id}/run`, { method: "POST" }),
  reviewDetails: (id: string) => request<ReviewReport>(`/api/reviews/${id}`),
  publish: (id: string) =>
    request<Job>(`/api/releases/${id}/publish`, { method: "POST" }),
  archive: () => request<{ persons: ArchivePerson[] }>("/api/archive/persons"),
  archiveBundle: (id: string) => request<ArchiveBundle>(`/api/archive/releases/${encodeURIComponent(id)}`),
  createChat: (payload: unknown) =>
    request<ChatSession>("/api/chat/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  chat: (id: string) => request<ChatSession>(`/api/chat/sessions/${id}`),
  chatTurn: (id: string, question: string) =>
    request<ChatTurn>(`/api/chat/sessions/${id}/turns`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
};
