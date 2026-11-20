// What the run page shows, worked out from the engine's public view of a run (GET /runs/ID): the
// state each need's row shows, the plan's summary, the time a need has been in its state, the
// states of its machine, and what the journal reads. No drawing here: RunPage draws it.
import type { JournalEntry, NeedState } from "@sustenaut/ui";

export type Need = {
  state: string;
  mode: "check" | "fulfil";
  grade: string;
  type: string;
  stop: "signal" | "shorten" | "continue";
  stop_warning: string;
  after: string[];
  since: string;
  result: string;
};

export type Event = {
  seq: number;
  at: string;
  need: string;
  from: string;
  to: string;
  by?: string;
  result?: string;
};

export type PlanItem = { need: string; name: string; type: string; state: string; grade: string; change: string };

export type Run = {
  id: string;
  repository: string;
  unit: string;
  mode: "check" | "fulfil";
  state: string;
  machine: { states?: { running?: { states?: Record<string, { meta?: { need?: string }; states?: Record<string, unknown> }> } } } | null;
  plan: PlanItem[];
  plan_key: string;
  needs: Record<string, Need>;
  journal: Event[];
  github_run_url: string;
  expires_at: string;
  stopping?: boolean;
  poll_ms: number;
};

/** The run as the page holds it after a poll: the new view, with the journal it had plus the events
 *  after `since`. */
export function merged(held: Run, polled: Run): Run {
  const seen = new Set(held.journal.map((e) => e.seq));
  return { ...polled, journal: [...held.journal, ...polled.journal.filter((e) => !seen.has(e.seq))] };
}

/** The last event's number: what the next poll asks for the journal after. */
export function lastSeq(run: Run): number {
  return run.journal.reduce((n, e) => Math.max(n, e.seq), 0);
}

/** Whether a need has been fulfilled in this run: its journal moved it out of fulfilling to a check. */
function fulfilled(run: Run, name: string): boolean {
  return run.journal.some((e) => e.need === name && e.from === "fulfilling" && e.to === "checking");
}

/** The state a need's row shows, and its word when it is not the state's own name. */
export function rowState(run: Run, name: string): { state: NeedState; label?: string } {
  const need = run.needs[name];
  const found = run.plan.find((p) => p.need === name)?.state;
  switch (need.state) {
    case "ready":
      return { state: fulfilled(run, name) ? "done" : "ok" };
    case "found":
      return { state: found === "drift" ? "drift" : "missing" };
    case "fulfilling":
      return { state: run.stopping && need.stop === "shorten" ? "finishing" : "fulfilling" };
    case "failed":
    case "blocked":
    case "stopped":
      return { state: need.state };
    case "unknown":
      return { state: "drift", label: "unknown" };
    case "checking":
      return { state: "fulfilling", label: "checking" };
    default: // waiting: before approval, what the plan walk found; after it, not started yet
      if (run.state === "planning" || run.state === "awaiting") {
        return found ? { state: found === "drift" ? "drift" : "missing", label: found } : { state: "ok" };
      }
      return { state: "blocked", label: "waiting" };
  }
}

/** Whether a need can be fulfilled by the engine: grade auto. A need of grade human is a person's. */
export function switchable(need: Need): boolean {
  return need.grade === "auto";
}

/** The plan's summary line, as the design words it. */
export function summary(run: Run): { needs: number; fulfil: number; check: number; person: number } {
  const names = Object.keys(run.needs);
  const planned = new Set(run.plan.map((p) => p.need));
  let fulfil = 0;
  let check = 0;
  let person = 0;
  for (const name of names) {
    const need = run.needs[name];
    if (!switchable(need)) person += 1;
    else if (need.mode === "fulfil" && planned.has(name)) fulfil += 1;
    else check += 1;
  }
  return { needs: names.length, fulfil, check, person };
}

/** How long ago `since` was, as a person reads it, from `now`. */
export function age(since: string, now: number): string {
  if (!since) return "";
  const s = Math.max(0, Math.floor((now - Date.parse(since)) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** The states of a need's machine, in the order the machine lists them. */
export function machineStates(run: Run, name: string): string[] {
  const regions = run.machine?.states?.running?.states ?? {};
  const region = Object.values(regions).find((r) => r.meta?.need === name);
  return Object.keys(region?.states ?? {});
}

/** The journal as the page reads it: time, the need or the run, the move, and who made it. */
export function journal(run: Run): JournalEntry[] {
  return run.journal.map((e) => ({
    at: e.at.slice(11, 19),
    need: e.need || "run",
    ...(e.from && e.to && e.from !== e.to
      ? { from: e.from, to: e.result ? `${e.to}: ${e.result}` : e.to }
      : { text: e.result || e.to }),
    by: e.by || undefined,
  }));
}

/** Why an ended run ended, as its last event says it: "" when it says nothing. */
export function endedBecause(run: Run): string {
  const last = [...run.journal].reverse().find((e) => !e.need && e.to === run.state);
  return last?.result ?? "";
}

/** The run's states in which Stop can be pressed. */
export const STOPPABLE = ["planning", "awaiting", "approved", "running"];

/** The needs that run to their end after a Stop: stop class continue, still fulfilling. */
export function continuing(run: Run): string[] {
  return run.stopping
    ? Object.entries(run.needs)
        .filter(([, n]) => n.stop === "continue" && n.state === "fulfilling")
        .map(([name]) => name)
    : [];
}
