// The run page drawn in jsdom against an engine faked at fetch: each scenario gives the run as GET
// /runs/ID answers it, the page draws it, the person acts, and the scenario reads what the page then
// shows and what it sent the engine.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, vi } from "vitest";
import { After, Given, Then, When } from "quickpickle";
import { RunPage, SETTLE_MS } from "../src/RunPage";
import type { Event, Need, Run } from "../src/view";

type Sent = { method: string; url: string; body: unknown };
type World = { run: Run; mayAct: boolean; sent: Sent[]; answers: Record<string, unknown>; now: number };

const need = (over: Partial<Need>): Need => ({
  state: "waiting",
  mode: "fulfil",
  grade: "auto",
  type: "k8s.secret",
  stop: "signal",
  stop_warning: "",
  after: [],
  since: "2026-11-20T09:00:00Z",
  result: "",
  ...over,
});

const region = (name: string, states: string[]) => ({ meta: { need: name }, states: Object.fromEntries(states.map((s) => [s, {}])) });

const ev = (seq: number, need: string, from: string, to: string, by = "engine"): Event => ({
  seq,
  at: `2026-11-20T09:0${Math.min(seq, 9)}:00Z`,
  need,
  from,
  to,
  by,
});

function awaitingRun(): Run {
  return {
    id: "r1",
    repository: "Sustenaut/integrations",
    unit: "ingest",
    mode: "fulfil",
    state: "awaiting",
    machine: {
      states: {
        running: {
          states: {
            ingest_token: region("ingest-token", ["waiting", "checking", "found", "fulfilling", "ready"]),
            namespace: region("namespace", ["waiting", "checking", "found", "ready"]),
          },
        },
      },
    },
    plan: [{ need: "ingest-token", name: "", type: "bitwarden.secret", state: "missing", grade: "auto", change: "" }],
    plan_key: "b7e2",
    needs: {
      "ingest-token": need({ type: "bitwarden.secret" }),
      namespace: need({ grade: "human", type: "k8s.namespace", mode: "check" }),
    },
    journal: [ev(1, "", "", "planning", "GitHub run"), ev(2, "", "planning", "awaiting")],
    github_run_url: "https://github.com/Sustenaut/integrations/actions/runs/1",
    expires_at: "2026-11-20T10:00:00Z",
    stopping: false,
    poll_ms: 40,
  };
}

function stoppedMixedRun(): Run {
  const run = awaitingRun();
  return {
    ...run,
    state: "running",
    stopping: true,
    machine: {
      states: {
        running: {
          states: {
            database: region("database", ["waiting", "found", "fulfilling", "ready"]),
            deploy: region("deploy", ["waiting", "found", "fulfilling", "ready"]),
            declared: region("secrets-declared", ["waiting", "stopped"]),
          },
        },
      },
    },
    plan: [
      { need: "database", name: "", type: "pg.database", state: "missing", grade: "auto", change: "" },
      { need: "deploy", name: "", type: "k8s.deploy", state: "drift", grade: "auto", change: "" },
    ],
    needs: {
      database: need({
        type: "pg.database",
        state: "fulfilling",
        stop: "continue",
        stop_warning: "Cancelling a database operation partway can leave a half-applied migration.",
      }),
      deploy: need({ type: "k8s.deploy", state: "fulfilling", stop: "shorten" }),
      "secrets-declared": need({ type: "k8s.secrets-declared", state: "stopped", result: "stopped before it started" }),
    },
    journal: [ev(1, "", "awaiting", "running"), ev(2, "", "running", "stopping", "you")],
  };
}

/** fetch, as the engine answers it: each request is noted, and answered from `answers` by method and
 *  path, else a poll answers the run with no new events. */
function fakeFetch(world: World) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    world.sent.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const path = url.split("?")[0];
    const answer = world.answers[`${method} ${path}`] ?? { ...world.run, journal: [] };
    // The engine keeps what it was told: a later poll answers the new plan_key, mode or state.
    const switched = path.match(/^\/runs\/r1\/needs\/([^/]+)\/mode$/);
    if (method === "POST" && switched) {
      const name = decodeURIComponent(switched[1]);
      const { mode } = JSON.parse(String(init!.body));
      world.run = {
        ...world.run,
        plan_key: (answer as { plan_key: string }).plan_key,
        needs: { ...world.run.needs, [name]: { ...world.run.needs[name], mode } },
      };
    } else if (method === "POST" && answer && typeof answer === "object" && "id" in answer) {
      world.run = { ...(answer as Run), journal: [] };
    }
    return new Response(JSON.stringify(answer), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

const draw = (world: World) =>
  act(async () => {
    vi.stubGlobal("fetch", fakeFetch(world));
    render(<RunPage initial={world.run} mayAct={world.mayAct} now={() => world.now} />);
  });

After(async () => {
  cleanup();
  vi.unstubAllGlobals();
});

{
  Given("a run awaiting approval, with a need to fulfil and a need for a person", (world: World) => {
    Object.assign(world, { run: awaitingRun(), mayAct: true, sent: [], answers: {}, now: Date.parse("2026-11-20T09:01:30Z") });
  });

  Given("a viewer who may not act", (world: World) => {
    world.mayAct = false;
  });

  Given("a running fulfil a person stopped: a need that continues, one that shortens, and one stopped", (world: World) => {
    Object.assign(world, { run: stoppedMixedRun(), mayAct: true, sent: [], answers: {}, now: Date.parse("2026-11-20T09:01:18Z") });
  });

  Given("the engine answers a mode switch with the plan_key {string}", (world: World, key: string) => {
    world.answers["POST /runs/r1/needs/ingest-token/mode"] = { plan_key: key };
  });

  Given("the engine answers Approve with the run running", (world: World) => {
    world.answers["POST /runs/r1/approve"] = { ...world.run, state: "running", journal: [ev(3, "", "awaiting", "running", "you")] };
  });

  Given("the engine has one new event after event 2", (world: World) => {
    world.answers["GET /runs/r1"] = { ...world.run, journal: [ev(3, "ingest-token", "waiting", "checking")] };
  });
}

{
  When("the page is drawn", draw);

  When("the person switches {string} to {string}", async (_w: World, need: string, label: string) => {
    await act(() => userEvent.click(within(screen.getByRole("group", { name: need })).getByRole("radio", { name: label })));
  });

  When("the person presses {string}", async (_w: World, label: string) => {
    await act(() => userEvent.click(screen.getByRole("button", { name: label })));
  });

  When("{int} ms pass", async (_w: World, ms: number) => {
    await act(() => new Promise((r) => setTimeout(r, ms)));
  });

  When("the settle time passes", async () => {
    await act(() => new Promise((r) => setTimeout(r, SETTLE_MS + 100)));
  });

  When("a minute passes", (world: World) => {
    world.now += 60_000;
  });

  When("the person types {string} to cancel {string}", async (_w: World, typed: string, need: string) => {
    await act(async () => {
      fireEvent.change(screen.getByLabelText(`Type ${need} to cancel it`), { target: { value: typed } });
    });
  });
}

{
  Then("the plan_key reads {string}", (_w: World, key: string) => {
    expect(screen.getByTestId("plan-key").textContent).toBe(key);
  });

  Then("the plan reads {string}", (_w: World, text: string) => {
    expect(screen.getByTestId("plan-summary").textContent).toBe(text);
  });

  Then("the page told the engine {string} {string} with {string}", (world: World, method: string, path: string, body: string) => {
    expect(world.sent.some((s) => s.method === method && s.url === path && JSON.stringify(s.body) === body)).toBe(true);
  });

  Then("the button {string} is enabled", (_w: World, label: string) => {
    expect((screen.getByRole("button", { name: label }) as HTMLButtonElement).disabled).toBe(false);
  });

  Then("the button {string} is disabled", (_w: World, label: string) => {
    expect((screen.getByRole("button", { name: label }) as HTMLButtonElement).disabled).toBe(true);
  });

  Then("{string}'s switch is frozen", (_w: World, need: string) => {
    for (const radio of within(screen.getByRole("group", { name: need })).getAllByRole("radio")) {
      expect((radio as HTMLInputElement).disabled).toBe(true);
    }
  });

  Then("{string}'s switch can be changed", (_w: World, need: string) => {
    for (const radio of within(screen.getByRole("group", { name: need })).getAllByRole("radio")) {
      expect((radio as HTMLInputElement).disabled).toBe(false);
    }
  });

  Then("{string} has no switch, only {string}", (_w: World, need: string, shown: string) => {
    expect(screen.queryByRole("group", { name: need })).toBeNull();
    const row = screen.getByRole("rowheader", { name: new RegExp(`^${need}`) }).closest("tr")!;
    expect(row.querySelectorAll("td")[1].textContent).toBe(shown);
  });

  Then("the row of {string} reads {string}", (_w: World, need: string, text: string) => {
    const row = screen.getByRole("rowheader", { name: new RegExp(`^${need}`) }).closest("tr")!;
    expect(row.textContent).toContain(text);
  });

  Then("the row of {string} offers to cancel it, warning {string}", (_w: World, need: string, warning: string) => {
    const row = screen.getByRole("rowheader", { name: new RegExp(`^${need}`) }).closest("tr")!;
    expect(within(row).getByText("Cancel anyway")).toBeTruthy();
    expect(within(row).getByText(warning)).toBeTruthy();
  });

  Then("the row of {string} offers no cancel", (_w: World, need: string) => {
    const row = screen.getByRole("rowheader", { name: new RegExp(`^${need}`) }).closest("tr")!;
    expect(within(row).queryByText("Cancel anyway")).toBeNull();
  });

  Then("the header says {string}", (_w: World, text: string) => {
    expect(screen.getByRole("heading", { level: 1 }).closest("section")!.textContent).toContain(text);
  });

  Then("the page polled the run after event {int}", (world: World, seq: number) => {
    expect(world.sent.some((s) => s.method === "GET" && s.url === `/runs/r1?since=${seq}`)).toBe(true);
  });

  Then("the journal's last line reads {string}", (_w: World, text: string) => {
    const lines = within(screen.getByRole("list", { name: "Journal" })).getAllByRole("listitem");
    expect(lines.at(-1)!.textContent).toBe(text);
  });
}
