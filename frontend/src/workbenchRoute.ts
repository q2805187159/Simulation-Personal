import type { Stage } from "./types";

export type Route = {
  stage: Stage;
  job?: string;
  revision?: string;
  release?: string;
  session?: string;
  profile?: string;
};

const stages: readonly Stage[] = ["generate", "skeleton", "flesh", "review", "archive"];

export function readRoute(): Route {
  const params = new URLSearchParams(window.location.search);
  const rawStage = params.get("stage");
  const stage = stages.find((value) => value === rawStage) ?? "generate";
  const id = (key: string) => {
    const value = params.get(key);
    return value && /^[a-zA-Z0-9_-]{1,128}$/.test(value) ? value : undefined;
  };
  return {
    stage,
    job: id("job"),
    revision: id("revision"),
    release: stage === "archive" ? id("release") : undefined,
    session: stage === "archive" ? id("session") : undefined,
    profile: stage === "archive" ? id("profile") : undefined,
  };
}

export function updateRoute(route: Route, replace = false) {
  const path = new URL(window.location.href);
  for (const key of ["stage", "job", "revision", "release", "session", "profile"] as const) {
    const value = route[key];
    if (value) path.searchParams.set(key, value);
    else path.searchParams.delete(key);
  }
  if (path.href !== window.location.href) {
    if (replace) history.replaceState(null, "", path);
    else history.pushState(null, "", path);
  }
}
