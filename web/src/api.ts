// The engine's endpoints the run page calls, all on this site (connect-src 'self'). A POST carries
// the page's Origin, which the engine checks; a refusal comes back as {"refused": why}.
import type { Run } from "./view";

export class Refused extends Error {}

/** `path` as a URL on this site, and only on it: the page calls nothing else. */
function here(path: string): string {
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin) throw new Refused("the page calls only this site");
  return url.pathname + url.search;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const answer = await fetch(here(path), {
    ...init,
    headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
    credentials: "same-origin",
  });
  const text = await answer.text();
  const body = text ? JSON.parse(text) : {};
  if (!answer.ok) throw new Refused(body.refused ?? body.detail ?? `the engine answered ${answer.status}`);
  return body as T;
}

const post = <T>(path: string, body: unknown) => call<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  poll: (id: string, since: number) => call<Run>(`/runs/${encodeURIComponent(id)}?since=${since}`),
  mode: (id: string, need: string, mode: string) =>
    post<{ plan_key: string }>(`/runs/${encodeURIComponent(id)}/needs/${encodeURIComponent(need)}/mode`, { mode }),
  approve: (id: string, plan_key: string) => post<Run>(`/runs/${encodeURIComponent(id)}/approve`, { plan_key }),
  stop: (id: string) => post<Run | Record<string, never>>(`/runs/${encodeURIComponent(id)}/stop`, {}),
  cancel: (id: string, need: string) =>
    post<Run>(`/runs/${encodeURIComponent(id)}/needs/${encodeURIComponent(need)}/cancel`, { confirm: need }),
  needs: (repository: string, unit: string, mode: string) =>
    call<{ needs: { name: string; grade: string; mode: "check" | "fulfil" }[] }>(
      `/runs/new/needs?repository=${encodeURIComponent(repository)}&unit=${encodeURIComponent(unit)}&mode=${mode}`,
    ),
  start: async (body: unknown): Promise<string> => {
    const run = await post<Run>("/runs", body);
    return `/runs/${run.id}`;
  },
};
